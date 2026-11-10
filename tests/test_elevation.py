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

    ⚠ 源码态会更早被拦在"不支持"（见 is_supported），
    所以这个用例只在打包态才真正走到 needs_admin 分支。
    """
    from core import uia_elevation

    if not elevate.is_supported():
        pytest.skip("源码态会先被形态检查拦下，走不到管理员判定")
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


# ---------------------------------------------------------------- 源码态禁用
def test_source_mode_is_not_supported():
    """源码态（python main.py）必须判定为"不支持 UIA 提权"。

    原因：提权重启依赖 `sys.executable` 指向"本程序"，而源码态它是
    python.exe —— 用 runas 启动它只会打开一个空解释器，既不是本程序，
    也走不通 UIAccess 流程。
    """
    if getattr(sys, "frozen", False):
        pytest.skip("当前是打包态")
    if sys.platform != "win32":
        pytest.skip("仅 Windows")
    assert elevate.is_supported() is False
    assert "源码" in elevate.unsupported_reason()


def test_source_mode_request_is_refused():
    """源码态发起提权必须直接拒绝，不能真的去启动 python.exe。"""
    if getattr(sys, "frozen", False) or sys.platform != "win32":
        pytest.skip("仅源码态 Windows")
    r = elevate.request_elevation([])
    assert r.started is False
    assert r.error_code == -3
    assert "源码" in r.message


def test_source_mode_uia_never_attempts():
    """源码态即使 allow_elevate=True 也不应尝试任何提权动作。"""
    from core import uia_elevation

    if getattr(sys, "frozen", False) or sys.platform != "win32":
        pytest.skip("仅源码态 Windows")

    st = uia_elevation.ensure_uiaccess(
        {"use_uia_elevation": True}, None, allow_elevate=True)
    assert st["attempted"] is False
    assert st["elevate_requested"] is False
    assert st["granted"] is False
    assert st["needs_admin"] is False, "源码态不该报'需要管理员'，应报'不支持'"
    assert "源码" in st["message"]


def test_unsupported_reason_non_windows(monkeypatch):
    """非 Windows 平台给出各自的说明，不要混用源码态文案。"""
    monkeypatch.setattr(elevate.sys, "platform", "linux")
    assert elevate.is_supported() is False
    assert "Windows" in elevate.unsupported_reason()


# ---------------------------------------------------------------- 设置页
def test_settings_does_not_clobber_config_in_source_mode(qapp, tmp_path):
    """源码态改其它设置，不得把配置里的 use_uia_elevation=true 抹成 false。

    真实场景：用户在打包态开了 UIA 提权，临时用源码跑一次改了主题——
    如果这里把开关恒 False 写回配置，那个 true 就丢了。
    """
    import os

    from core.kaomoji_data import KaomojiData
    from core.user_kaomoji import UserKaomoji
    from core.user_state import UserState
    from core.user_triggers import UserTriggers
    from ui.unified_window import UnifiedSettingsWindow

    if getattr(sys, "frozen", False) or sys.platform != "win32":
        pytest.skip("仅源码态 Windows")

    cfg = {
        "theme": "light", "panel_alpha": 1.0, "opacity": 0.98,
        "hotkey": "<ctrl>+<shift>+k", "input_method": "direct",
        "max_recent": 30, "page_size": 3, "auto_popup": True,
        "autostart": False, "auto_hide_on_blur": True,
        "developer_mode": True, "use_uia_elevation": True,
        "use_test_features": True, "plugins_enabled": {},
    }
    d = str(tmp_path / "d")
    os.makedirs(d, exist_ok=True)
    w = UnifiedSettingsWindow(
        KaomojiData(), cfg, UserState(path=os.path.join(d, "s.json")),
        UserKaomoji(path=os.path.join(d, "u.json")),
        UserTriggers(path=os.path.join(d, "t.json")),
        save_config=lambda c: None, open_log=lambda: None, plugins=None)
    try:
        sp = w.settings_page
        # 开关应禁用且显示为关
        assert sp.uia_elevation_check.isEnabled() is False
        assert sp.uia_elevation_check.isChecked() is False

        # 改一个无关设置，配置里的 true 必须保留
        sp.auto_check.setChecked(False)
        assert sp.config.get("use_uia_elevation") is True, \
            "源码态改其它设置把 use_uia_elevation 抹掉了"
    finally:
        w.hide()
        w.deleteLater()
