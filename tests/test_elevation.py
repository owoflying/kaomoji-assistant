"""UAC 提权申请与计划任务自启动的测试。

## 背景

「UIA 提权」需要在进程令牌上设 `TokenUIAccess=1`，而这一步需要
**`SeTcbPrivilege`**。实测结论（真实 Windows 平台）：

| 身份 | is_admin | 令牌里有 SeTcbPrivilege | `SetTokenInformation(TokenUIAccess)` |
|------|----------|------------------------|--------------------------------------|
| 普通用户 | False | False | 失败，`GetLastError=1314` |
| 管理员（UAC 提权后） | True | False（仍缺！） | 直接设仍失败 |
| 管理员 + 冒充 winlogon(SYSTEM) | True | — | **成功** |

所以"获取 UIAccess"必须两步：
1. **先拿到管理员**（通过 UAC 弹窗，而不是要求用户手动右键"以管理员身份运行"）；
2. 再用既有的 winlogon 冒充流程派生 UIAccess 令牌。

本文件覆盖第 1 步、以及"开机自启自动带管理员"的计划任务实现。

⚠ 这些用例不真的弹 UAC、不真的创建计划任务（需要管理员，且会污染系统），
只验证**决策逻辑与降级行为**。真实链路已在开发期用临时计划任务验证过。
"""
import os
import sys

import pytest

from core import autostart, elevate


# ---------------------------------------------------------------- elevate
def test_elevate_result_truthiness():
    """started=True 才算成功；cancelled 是明确的"用户拒绝"。"""
    assert bool(elevate.ElevateResult(started=True))
    r = elevate.ElevateResult(cancelled=True, error_code=1223)
    assert not bool(r)
    assert r.cancelled
    assert r.error_code == 1223


def test_guard_flag_roundtrip(monkeypatch):
    """防循环标记：提权启动的新进程靠它避免再次弹 UAC。"""
    monkeypatch.delenv(elevate.ELEVATE_GUARD, raising=False)
    assert elevate.guard_is_active() is False

    elevate.mark_guarded()
    assert elevate.guard_is_active() is True

    elevate.clear_guard()
    assert elevate.guard_is_active() is False


def test_quote_args_handles_spaces():
    assert elevate._quote_args(["a", "b c"]) == 'a "b c"'
    assert elevate._quote_args(["--flag"]) == "--flag"
    assert elevate._quote_args([]) == ""


def test_spawn_with_guard_appends_marker(monkeypatch):
    """提权启动时必须在命令行加 `--elevated-by-app`，供 main.py 识别。"""
    captured = {}

    def _fake_request(args=None):
        captured["args"] = list(args or [])
        return elevate.ElevateResult(started=True)

    monkeypatch.setattr(elevate, "request_elevation", _fake_request)
    elevate.spawn_with_guard(["--foo"])
    assert "--elevated-by-app" in captured["args"]
    assert "--foo" in captured["args"]


def test_spawn_with_guard_does_not_duplicate_marker(monkeypatch):
    captured = {}

    def _fake_request(args=None):
        captured["args"] = list(args or [])
        return elevate.ElevateResult(started=True)

    monkeypatch.setattr(elevate, "request_elevation", _fake_request)
    elevate.spawn_with_guard(["--elevated-by-app"])
    assert captured["args"].count("--elevated-by-app") == 1


# ---------------------------------------------------------------- autostart
def test_exe_path_none_in_source_mode():
    """源码态没有 exe，自启动不可用（避免把 python.exe 写进自启动项）。"""
    if getattr(sys, "frozen", False):
        pytest.skip("打包态下 exe_path 有值")
    assert autostart.exe_path() is None
    assert autostart.is_supported() is False


def test_task_names_are_stable():
    """计划任务名是契约：改名会导致旧任务变孤儿。"""
    assert autostart.TASK_NAME == "KaomojiAssistant"


def test_mode_values():
    assert autostart.MODE_TASK == "task"
    assert autostart.MODE_REGISTRY == "registry"


def test_set_autostart_disabled_clears_both(monkeypatch):
    """关闭自启动必须两种方式都清，否则会残留。"""
    calls = {"task": 0, "reg": None}
    monkeypatch.setattr(autostart, "disable_task_autostart",
                        lambda: (calls.__setitem__("task", calls["task"] + 1)
                                 or (True, "ok")))
    monkeypatch.setattr(autostart, "_set_registry",
                        lambda v: calls.__setitem__("reg", v) or True)

    ok, _msg = autostart.set_autostart(False)
    assert ok
    assert calls["task"] == 1
    assert calls["reg"] is False


def test_enable_task_first_clears_registry(monkeypatch):
    """启用计划任务前先清注册表，否则登录时会启动两次。"""
    calls = {}
    monkeypatch.setattr(autostart, "_set_registry",
                        lambda v: calls.__setitem__("reg", v) or True)
    monkeypatch.setattr(autostart, "enable_task_autostart",
                        lambda: (True, "ok"))

    ok, _msg = autostart.set_autostart(True, use_task=True)
    assert ok
    assert calls["reg"] is False, "启用计划任务前应先清掉注册表项"


def test_enable_registry_first_clears_task(monkeypatch):
    """反过来也一样：用注册表方式时若已有计划任务，要先删掉。"""
    calls = {"task_deleted": 0}
    monkeypatch.setattr(autostart, "task_exists", lambda: True)
    monkeypatch.setattr(autostart, "disable_task_autostart",
                        lambda: (calls.__setitem__("task_deleted",
                                                   calls["task_deleted"] + 1)
                                 or (True, "ok")))
    monkeypatch.setattr(autostart, "is_supported", lambda: True)
    monkeypatch.setattr(autostart, "_set_registry", lambda v: True)

    ok, _msg = autostart.set_autostart(True, use_task=False)
    assert ok
    assert calls["task_deleted"] == 1


def test_enable_task_without_exe_reports_clearly(monkeypatch):
    """源码态启用计划任务要给出明确原因，而不是静默失败。"""
    if getattr(sys, "frozen", False):
        pytest.skip("打包态")
    ok, msg = autostart.enable_task_autostart()
    assert ok is False
    assert "exe" in msg or "打包" in msg


# ---------------------------------------------------------------- uia_elevation
def test_uia_status_includes_elevate_fields():
    """status 字典新增两个字段，调用方依赖它们决定是否退出旧进程。"""
    from core import uia_elevation

    st = uia_elevation.ensure_uiaccess(
        {"use_uia_elevation": False}, lambda *a: None)
    assert "elevate_requested" in st
    assert "relaunched" in st
    assert "needs_admin" in st


def test_uia_disabled_reports_disabled():
    from core import uia_elevation

    st = uia_elevation.ensure_uiaccess({}, None)
    assert st.get("granted") is False
    assert st.get("elevate_requested") is False
    assert "未启用" in st.get("message", "")


@pytest.mark.skipif(sys.platform != "win32", reason="仅 Windows")
def test_uia_without_elevate_never_requests_uac():
    """`allow_elevate=False`（启动期）绝不能弹 UAC。

    否则每次开机都会弹窗——这正是"开机自启自动提权"要避免的事。
    """
    from core import uia_elevation

    if uia_elevation.is_user_admin():
        pytest.skip("当前已是管理员，不会走到 needs_admin 分支")

    st = uia_elevation.ensure_uiaccess(
        {"use_uia_elevation": True}, None, allow_elevate=False)
    assert st.get("needs_admin") is True
    assert st.get("elevate_requested") is False
    assert "管理员" in st.get("message", "")


def test_main_handles_elevated_marker():
    """main.py 必须识别 `--elevated-by-app` 并标记，否则会无限弹 UAC。"""
    main_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "main.py")
    with open(main_path, encoding="utf-8") as f:
        src = f.read()
    assert "--elevated-by-app" in src
    assert "mark_guarded" in src
