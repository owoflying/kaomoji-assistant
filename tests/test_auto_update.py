"""自动更新测试：版本比较、压缩包解析、替换脚本、流程调度。

## 覆盖范围

真实替换流程已在开发期用模拟 onedir 目录完整验证过（改名 → 搬移 → 启动 →
清理，以及失败回滚）。本文件覆盖**可离线断言**的部分：

    * 版本比较语义（含预发布、无法解析的降级）
    * onedir 压缩包结构识别（标准层级 / 无外层目录 / 无 exe）
    * 替换脚本生成（路径、改名、回滚分支）
    * 解压（含 zip slip 防护）
    * 更新流程的触发条件（间隔、开关、形态）
    * 源码态与不可写目录的降级

⚠ 不真的替换文件、不真的重启进程——那些行为由 `_probe_update` 式的
模拟验证保证，放在单元测试里会污染环境。
"""
import os
import sys
import time
import zipfile

import pytest

from core import updater, updater_install
from core.version import compare_versions, is_newer_version, normalize_tag, parse_version


# ---------------------------------------------------------------- 版本比较
@pytest.mark.parametrize("a,b,expected", [
    ("1.3", "1.2", 1),
    ("v1.2", "v1.3", -1),
    ("1.2", "1.2", 0),
    ("1.2.1", "1.2", 1),
    ("v2.0.0", "v1.9.9", 1),
    ("1.2.0", "1.2.0.0", 0),
    # 正式版 > 预发布版
    ("1.2.0", "1.2.0-beta", 1),
    ("1.2.0-beta", "1.2.0", -1),
    ("1.2.0-rc1", "1.2.0-rc2", 0),   # 后缀不比较，只看数字
    # 无法解析
    ("abc", "1.2", None),
    ("", "1.2", None),
    ("unknown", "unknown", None),
])
def test_compare_versions(a, b, expected):
    assert compare_versions(a, b) == expected


def test_parse_version_fills_missing_segments():
    assert parse_version("v1.2.3") == ((1, 2, 3, 0), False)
    assert parse_version("1") == ((1, 0, 0, 0), False)
    assert parse_version("1.2.3-beta") == ((1, 2, 3, 0), True)


def test_is_newer_version_refuses_unparsable():
    """开发构建的 commit 哈希绝不能当作版本号比较，否则会误触发下载。"""
    assert is_newer_version("1.3", "1.2") is True
    assert is_newer_version("1.2", "1.2") is False
    assert is_newer_version("1.3", "abc1234") is False
    assert is_newer_version("abc", "1.2") is False
    assert is_newer_version("1.3", "") is False


def test_normalize_tag():
    assert normalize_tag("v1.2.0") == "1.2.0"
    assert normalize_tag("V2.0") == "2.0"
    assert normalize_tag("1.0") == "1.0"
    assert normalize_tag("") == ""


# ---------------------------------------------------------------- 压缩包结构
def _make_zip(path, entries):
    with zipfile.ZipFile(path, "w") as z:
        for name, data in entries.items():
            z.writestr(name, data)
    return path


def test_detect_layout_standard_onedir(tmp_path):
    """标准 onedir：外层目录 + exe + _internal。"""
    p = _make_zip(str(tmp_path / "a.zip"), {
        "KaomojiAssistant/KaomojiAssistant.exe": "exe",
        "KaomojiAssistant/_internal/x.dll": "dll",
    })
    layout = updater_install.detect_layout(p, "KaomojiAssistant.exe")
    assert layout["ok"]
    assert layout["root"] == "KaomojiAssistant/"
    assert layout["exe"] == "KaomojiAssistant/KaomojiAssistant.exe"


def test_detect_layout_flat_archive(tmp_path):
    """压缩包直接是内容（无外层目录）也要能识别。"""
    p = _make_zip(str(tmp_path / "b.zip"), {
        "KaomojiAssistant.exe": "exe",
        "_internal/x.dll": "dll",
    })
    layout = updater_install.detect_layout(p, "KaomojiAssistant.exe")
    assert layout["ok"]
    assert layout["root"] == ""


def test_detect_layout_without_exe(tmp_path):
    p = _make_zip(str(tmp_path / "c.zip"), {"readme.txt": "hi"})
    layout = updater_install.detect_layout(p, "KaomojiAssistant.exe")
    assert layout["ok"] is False
    assert "可执行文件" in layout["reason"]


def test_detect_layout_broken_zip(tmp_path):
    p = tmp_path / "bad.zip"
    p.write_text("not a zip")
    layout = updater_install.detect_layout(str(p), "x.exe")
    assert layout["ok"] is False


# ---------------------------------------------------------------- 解压
def test_extract_strips_root_prefix(tmp_path):
    p = _make_zip(str(tmp_path / "d.zip"), {
        "App/App.exe": "exe",
        "App/_internal/y.dll": "dll",
        "App/sub/z.txt": "z",
    })
    dest = str(tmp_path / "out")
    n = updater_install.extract_to(p, dest, "App/")
    assert n == 3
    assert os.path.isfile(os.path.join(dest, "App.exe"))
    assert os.path.isfile(os.path.join(dest, "_internal", "y.dll"))
    assert os.path.isfile(os.path.join(dest, "sub", "z.txt"))


def test_extract_rejects_zip_slip(tmp_path):
    """压缩包里的 `..` 路径不能写到目标目录之外。"""
    p = _make_zip(str(tmp_path / "evil.zip"), {
        "App/App.exe": "exe",
        "App/../../../evil.txt": "bad",
    })
    dest = str(tmp_path / "out2")
    updater_install.extract_to(p, dest, "App/")
    outside = os.path.abspath(os.path.join(dest, "..", "..", "..", "evil.txt"))
    assert not os.path.exists(outside), "zip slip 没有被拦住"


def test_extract_empty_prefix_match_raises(tmp_path):
    p = _make_zip(str(tmp_path / "e.zip"), {"Other/x.exe": "e"})
    with pytest.raises(updater_install.InstallError):
        updater_install.extract_to(p, str(tmp_path / "out3"), "App/")


# ---------------------------------------------------------------- 替换脚本
def test_build_script_contains_key_steps():
    s = updater_install.build_script(
        r"C:\Apps\Kaomoji", "KaomojiAssistant.exe", r"C:\Temp\stage")
    assert "@echo off" in s
    assert r"set \"INS=C:\Apps\Kaomoji\"" in s.replace("\\\\", "\\") or \
        'set "INS=C:\\Apps\\Kaomoji"' in s
    # 等待旧进程退出
    assert "tasklist" in s and "goto wait" in s
    # 改名备份（运行中的 exe 不能删但能改名）
    assert ".old" in s
    # 搬移与启动
    assert "xcopy" in s
    assert "start \"\"" in s
    # 回滚分支
    assert ":rollback" in s


def test_build_script_rollback_restores_names():
    s = updater_install.build_script(
        r"C:\A", "app.exe", r"C:\S")
    # 回滚段里要把 .old 改回原名
    rollback = s.split(":rollback")[1]
    assert "app.exe.old" in rollback
    assert "_internal.old" in rollback


def test_build_script_checks_every_step():
    """脚本每一步都要校验，不能"搬完不管结果"。

    早期版本 `xcopy ... >nul 2>&1` 之后不检查 errorlevel，
    只有 exe 不存在才回滚。若 `_internal` 拷一半失败，
    会留下"新 exe + 半个旧运行库"的必崩组合。
    """
    s = updater_install.build_script(r"C:\A", "app.exe", r"C:\S")
    # 复制后必须检查 errorlevel
    assert "if errorlevel" in s
    # 必须校验关键文件存在
    assert 'if not exist "%INS%\\%EXE%"' in s
    assert 'if not exist "%INS%\\_internal"' in s
    # 必须有回滚与回滚失败两个分支
    assert ":rollback" in s
    assert ":rollback_failed" in s


def test_build_script_rollback_order_is_safe():
    """回滚必须"先把旧的改回来，再删新的"。

    反过来一旦删新的失败，旧目录还在 .old 里，程序彻底打不开。

    正确顺序是三步（不是两步）：
        1. 把新的 `_internal` **重命名**为 `_internal.new`
           （腾开位置，但不能立刻删——删失败就卡住了）
        2. 把 `_internal.old` 改回 `_internal`
        3. 才删除 `_internal.new`

    只断言"恢复在删除之前"是不够的（重命名也算提到 new），
    要断言三者的先后关系。
    """
    s = updater_install.build_script(r"C:\A", "app.exe", r"C:\S")
    rb = s.split(":rollback")[1].split(":rollback_failed")[0]

    rename_new = rb.find('ren "%INS%\\_internal" "_internal.new"')
    restore_old = rb.find('ren "%INS%\\_internal.old" "_internal"')
    delete_new = rb.find('rd /S /Q "%INS%\\_internal.new"')

    assert rename_new >= 0, "缺少把新目录移开的步骤"
    assert restore_old >= 0, "缺少恢复旧目录的步骤"
    assert delete_new >= 0, "缺少清理新目录的步骤"

    # 顺序：移开新的 -> 恢复旧的 -> 删掉移开的
    assert rename_new < restore_old < delete_new, \
        "顺序应为「移开新的 → 恢复旧的 → 清理」"


def test_build_script_logs_result():
    """脚本要往日志里写明确的结果标记，供 Python 侧判断成败。

    早期方案是让脚本写单独的 `update_failed.txt`，但实测在长脚本里
    那一句写不出文件（同处日志却能写）。改成"脚本只写日志、
    Python 解析日志"后少了一类失败模式。
    """
    s = updater_install.build_script(r"C:\A", "app.exe", r"C:\S")
    assert "update_log.txt" in s, "脚本要写日志"
    # 三种结果标记都要有，且是 ASCII（中文在 chcp 65001 下不稳定）
    assert "update done" in s, "成功标记"
    assert "rollback ok" in s, "回滚成功标记"
    assert "ROLLBACK FAILED" in s, "回滚失败标记"


def test_build_script_no_process_detection_after_start():
    """`start` 之后**不能**再查进程判断成功与否。

    实测踩坑：`timeout /t` 在没有控制台的环境立即返回，
    刚 start 就查必然查不到 → 正常更新被误判失败并回滚，
    用户看到"更新完成但版本没变"。

    现在改为"文件替换成功即成功"，由下次启动比对版本号兜底。
    """
    s = updater_install.build_script(r"C:\A", "app.exe", r"C:\S")
    after_start = s.split('start ""')[1] if 'start ""' in s else ""
    # start 之后不应该再有 tasklist 检查
    tail = after_start.split("goto cleanup")[0]
    assert "tasklist" not in tail, "start 之后不该再查进程"


def test_failure_detected_from_log(tmp_path, monkeypatch):
    """从日志尾部判断更新结果：回滚成功 → 提示失败；成功 → 不提示。"""
    log = tmp_path / "update_log.txt"
    monkeypatch.setattr(updater_install, "log_path", lambda: str(log))

    # 1) 没有日志 → 不提示
    assert updater_install.read_failure_note() == ""

    # 2) 回滚成功 → 提示"已恢复为原版本"
    log.write_text(
        "[x] old process gone\n[x] copy failed, rolling back\n"
        "[x] rollback ok\n", encoding="utf-8")
    got = updater_install.read_failure_note()
    assert "自动恢复" in got or "恢复" in got, got
    assert str(log) in got, "应附上日志路径"

    # 3) 回滚也失败 → 提示更严重
    log.write_text("[x] ROLLBACK FAILED\n", encoding="utf-8")
    got2 = updater_install.read_failure_note()
    assert ".old" in got2, "回滚失败要告诉用户手工怎么处理"

    # 4) 更新成功 → 不提示
    log.write_text("[x] copy verified\n[x] update done\n", encoding="utf-8")
    assert updater_install.read_failure_note() == "", "成功不该报失败"


def test_clear_failure_note_removes_legacy_flag(tmp_path, monkeypatch):
    """清除时要顺手删掉历史版本残留的标记文件。"""
    flag = tmp_path / "update_failed.txt"
    flag.write_text("UPDATE_FAILED", encoding="utf-8")
    monkeypatch.setattr(updater_install, "failure_note_path",
                        lambda: str(flag))

    updater_install.clear_failure_note()
    assert not flag.exists()


def test_build_script_requires_paths():
    with pytest.raises(updater_install.InstallError):
        updater_install.build_script("", "a.exe", "")


# ---------------------------------------------------------------- 形态判定
def test_can_self_update_false_in_source_mode():
    """源码态没有安装目录，不能自更新。"""
    if getattr(sys, "frozen", False):
        pytest.skip("打包态")
    assert updater_install.can_self_update() is False
    assert updater_install.install_dir() is None
    assert updater.is_supported() is False


def test_updater_unsupported_reason_in_source_mode():
    if getattr(sys, "frozen", False):
        pytest.skip("打包态")
    assert "源码" in updater.unsupported_reason()


def test_prepare_rejects_in_source_mode(tmp_path):
    """源码态 prepare 应明确报错，而不是解压到一半。"""
    if getattr(sys, "frozen", False):
        pytest.skip("打包态")
    p = _make_zip(str(tmp_path / "f.zip"), {"a.exe": "x"})
    with pytest.raises(updater_install.InstallError):
        updater_install.prepare(p)


# ---------------------------------------------------------------- 触发策略
def test_should_check_respects_switch(tmp_path, monkeypatch):
    """关掉 auto_update 后永不自动检查。"""
    from core import runtime
    monkeypatch.setattr(runtime, "app_data_dir", lambda: str(tmp_path))
    assert updater.should_check_now({"auto_update": False}) is False


def test_should_check_respects_interval(tmp_path, monkeypatch):
    """24 小时内不重复检查。"""
    from core import runtime
    monkeypatch.setattr(runtime, "app_data_dir", lambda: str(tmp_path))

    now = time.time()
    updater.mark_checked(now)
    assert updater.should_check_now({}, now=now + 60) is False
    assert updater.should_check_now(
        {}, now=now + updater.CHECK_INTERVAL_SEC + 1) is True


def test_last_check_time_zero_when_missing(tmp_path, monkeypatch):
    from core import runtime
    monkeypatch.setattr(runtime, "app_data_dir", lambda: str(tmp_path))
    assert updater.last_check_time() == 0.0


# ---------------------------------------------------------------- 待安装记录
def test_pending_update_result_success(tmp_path, monkeypatch):
    """pending 记录 + 当前版本已达目标 → 判定成功并清除记录。"""
    from core import runtime
    monkeypatch.setattr(runtime, "app_data_dir", lambda: str(tmp_path))

    updater_install.write_pending("1.3")
    state, ver = updater_install.check_pending_result("1.3")
    assert state == "ok"
    assert ver == "1.3"
    assert updater_install.read_pending() == ""


def test_pending_update_result_failed(tmp_path, monkeypatch):
    """版本没变 → 判定失败，保留记录供提示。"""
    from core import runtime
    monkeypatch.setattr(runtime, "app_data_dir", lambda: str(tmp_path))

    updater_install.write_pending("1.3")
    state, ver = updater_install.check_pending_result("1.2")
    assert state == "failed"
    assert ver == "1.3"
    updater_install.clear_pending()


def test_pending_update_no_record(tmp_path, monkeypatch):
    from core import runtime
    monkeypatch.setattr(runtime, "app_data_dir", lambda: str(tmp_path))
    updater_install.clear_pending()
    assert updater_install.check_pending_result("1.2") == ("none", "")
