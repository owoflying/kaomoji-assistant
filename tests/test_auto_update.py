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
