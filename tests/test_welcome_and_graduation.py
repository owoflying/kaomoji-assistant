"""首次使用欢迎界面与「功能毕业」的测试。

## 两部分

### 1. 欢迎界面（ui/welcome_dialog.py）

首次使用弹一次，含风险告知 + 必须勾选确认。要点：

    * **判定与版本号无关**：用独立标记文件（welcome_shown.txt），
      弹过一次就永不再弹。这与"每个版本首次启动弹更新说明"刻意区分；
    * **未勾选不能继续**：风险告知不是走过场；
    * **点「退出」不算完成**：用户没真正开始使用，下次启动还该看到。

### 2. 功能毕业

UIA 提权、插件系统、三种输入方式都已从测试功能转为正式功能：
    * 关闭测试模式不再归位/回退它们；
    * 插件不再随开发者模式卸载；
    * 「插件」页常驻。
"""
import pytest


# ======================================================================
# 首次运行判定
# ======================================================================
@pytest.fixture
def appdata(tmp_path, monkeypatch):
    """把 APPDATA 指到临时目录，避免污染真实用户数据。"""
    from core import runtime
    monkeypatch.setattr(runtime, "app_data_dir", lambda: str(tmp_path))
    return tmp_path


def test_first_run_shows_welcome(appdata):
    from core import version as ver

    assert ver.has_shown_welcome() is False
    assert ver.should_show_welcome() is True


def test_welcome_shown_only_once(appdata):
    from core import version as ver

    ver.mark_welcome_shown()
    assert ver.has_shown_welcome() is True
    assert ver.should_show_welcome() is False, "弹过一次就不该再弹"


def test_welcome_independent_of_version(appdata):
    """欢迎页判定与版本号解耦：换版本也不会再弹。"""
    from core import version as ver

    ver.mark_welcome_shown()
    ver.save_seen_version("v2.0")     # 模拟升级
    assert ver.should_show_welcome() is False


def test_welcome_force_flag(appdata, monkeypatch):
    """开发者模式「模拟首启」可强制弹。"""
    from core import version as ver

    ver.mark_welcome_shown()
    assert ver.should_show_welcome(force=True) is True

    monkeypatch.setenv("KAOMOJI_FORCE_WELCOME", "1")
    assert ver.should_show_welcome() is True


def test_welcome_may_show_uses_flag_file(appdata):
    """maybe_show 在已弹过时直接返回 False，不再构造弹窗。"""
    from core import version as ver

    ver.mark_welcome_shown()

    from ui.welcome_dialog import WelcomeDialog
    # 已弹过：不应该走到 exec()（否则会阻塞）。返回 False 即证明提前退出。
    assert WelcomeDialog.maybe_show(theme_name="light") is False


# ======================================================================
# 欢迎界面组件
# ======================================================================
def _make_welcome(qapp):
    from ui.fluent_icons import ensure_icon_font
    ensure_icon_font()
    from ui.welcome_dialog import WelcomeDialog
    return WelcomeDialog(theme_name="light", version="v1.3(abc)")


def test_welcome_requires_checkbox(qapp):
    """未勾选「我已了解」时不能点「开始使用」。"""
    dlg = _make_welcome(qapp)
    try:
        assert dlg._start.isEnabled() is False
        dlg._agree.setChecked(True)
        assert dlg._start.isEnabled() is True
        dlg._agree.setChecked(False)
        assert dlg._start.isEnabled() is False, "取消勾选后又该禁用"
    finally:
        dlg.deleteLater()


def test_welcome_lists_risks(qapp):
    """风险说明必须覆盖两项正式功能的真实风险。"""
    from ui.welcome_dialog import RISK_ITEMS

    text = " ".join(n + d for n, d in RISK_ITEMS)
    assert "管理员" in text, "应说明 UIA 提权需要管理员权限"
    assert "插件" in text, "应说明插件风险"
    assert "权限" in text


def test_welcome_accept_sets_ok(qapp):
    dlg = _make_welcome(qapp)
    try:
        assert dlg.result_ok() is False
        dlg.accept()
        assert dlg.result_ok() is True
    finally:
        dlg.deleteLater()


# ======================================================================
# 功能毕业
# ======================================================================
def test_test_feature_defaults_is_empty():
    """当前没有未毕业的测试功能。"""
    from core import config_store

    assert config_store.TEST_FEATURE_DEFAULTS == {}


def test_uia_not_gated_by_test_mode():
    from core import config_store

    cfg = dict(config_store.DEFAULT_CONFIG,
               use_test_features=False, use_uia_elevation=True)
    config_store.enforce_test_feature_gate(cfg)
    assert cfg["use_uia_elevation"] is True


def test_all_input_methods_graduated():
    from core import config_store

    assert config_store.ALWAYS_AVAILABLE_INPUT_METHODS == frozenset(
        {"direct", "clipboard", "type"})
    cfg = dict(config_store.DEFAULT_CONFIG,
               use_test_features=False, input_method="type")
    config_store.enforce_test_feature_gate(cfg)
    assert cfg["input_method"] == "type"
