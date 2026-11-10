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
    """maybe_show 在已弹过时返回 None（不构造弹窗、不阻塞）。"""
    from core import version as ver

    ver.mark_welcome_shown()

    from ui.welcome_dialog import WelcomeDialog
    assert WelcomeDialog.maybe_show(theme_name="light") is None


# ======================================================================
# 欢迎界面组件（新手引导：介绍 + 配置，不再是风险告知书）
# ======================================================================
def _make_welcome(qapp, config=None):
    from ui.fluent_icons import ensure_icon_font
    ensure_icon_font()
    from ui.welcome_dialog import WelcomeDialog
    return WelcomeDialog(
        theme_name="light", version="v1.3(abc)",
        config=config or {"hotkey": "<ctrl>+<shift>+k", "theme": "system"})


def test_welcome_offers_basic_setup(qapp):
    """引导页要让用户当场定下热键与主题。"""
    dlg = _make_welcome(qapp)
    try:
        assert dlg._hotkey_combo.count() > 0
        assert dlg._theme_combo.count() > 0
        # 回填当前值
        assert dlg._hotkey_combo.currentData() == "<ctrl>+<shift>+k"
        assert dlg._theme_combo.currentData() == "system"
    finally:
        dlg.deleteLater()


def test_welcome_collects_changes(qapp):
    """用户改了热键/主题，accept() 后应收集到 chosen 里。"""
    dlg = _make_welcome(qapp)
    try:
        dlg._hotkey_combo.setCurrentIndex(1)
        dlg._theme_combo.setCurrentIndex(2)
        dlg.accept()
        assert "hotkey" in dlg.chosen
        assert "theme" in dlg.chosen
    finally:
        dlg.deleteLater()


def test_welcome_collects_nothing_when_unchanged(qapp):
    """没改动时 chosen 为空——不产生无意义的配置写入。"""
    dlg = _make_welcome(qapp)
    try:
        dlg.accept()
        assert dlg.chosen == {}
    finally:
        dlg.deleteLater()


def test_welcome_has_highlights(qapp):
    """有宣传/介绍内容（不是一屏免责声明）。"""
    from ui.welcome_dialog import HIGHLIGHTS

    assert len(HIGHLIGHTS) >= 3
    text = " ".join(n + d for _i, n, d in HIGHLIGHTS)
    assert "插件" in text or "更新" in text


# ======================================================================
# 风险提示：挪到各功能的首次入口
# ======================================================================
def test_risk_dialog_flags_are_separate(appdata):
    """插件与 UIA 各自独立标记，互不影响。"""
    from ui import risk_dialog

    assert risk_dialog._seen(risk_dialog._PLUGIN_FLAG) is False
    assert risk_dialog._seen(risk_dialog._UIA_FLAG) is False

    risk_dialog._mark(risk_dialog._PLUGIN_FLAG)
    assert risk_dialog._seen(risk_dialog._PLUGIN_FLAG) is True
    assert risk_dialog._seen(risk_dialog._UIA_FLAG) is False, \
        "标记插件风险不该顺带标记 UIA"


def test_risk_texts_are_plain(appdata):
    """文案说人话：讲清楚会发生什么，不用套话。"""
    from ui import risk_dialog

    for text in (risk_dialog.PLUGIN_TEXT, risk_dialog.UIA_TEXT):
        assert "不会留下" not in text
        assert "副作用" not in text

    assert "管理员" in risk_dialog.UIA_TEXT
    assert "权限" in risk_dialog.PLUGIN_TEXT


# ======================================================================
# 开发者页的「弹窗预览」
# ======================================================================
@pytest.fixture
def dev_page(monkeypatch, tmp_path):
    """建一个轻量的开发者页（不跑完整 AppRuntime 装配）。"""
    monkeypatch.setenv("KAOMOJI_SKIP_WELCOME", "1")
    from core import runtime as rt_mod
    monkeypatch.setattr(rt_mod, "app_data_dir", lambda: str(tmp_path))

    from core.config_store import load_config
    from ui.dev import DevPage

    page = DevPage({}, load_config(), None, "light")
    try:
        yield page
    finally:
        page.deleteLater()


def test_dev_page_has_dialog_preview_card(dev_page):
    """开发者页要有"重看一次性弹窗"的入口。

    欢迎引导、插件风险、UIA 风险都只弹一次，用过就看不到。
    改文案时只能清 APPDATA 才能复现——这个卡片就是为此准备的。
    """
    from PySide6.QtWidgets import QPushButton

    texts = {b.text() for b in dev_page.findChildren(QPushButton)}
    assert "首次使用引导" in texts
    assert "插件风险提示" in texts
    assert "UIA 风险提示" in texts


def test_preview_does_not_mark_seen(dev_page, monkeypatch):
    """预览不能写入"已看过"标记，否则日常使用时那两个弹窗就再也不会出现了。"""
    from core import version as ver
    from ui import risk_dialog

    # 打桩避免真的弹模态框
    monkeypatch.setattr(risk_dialog, "_ask", lambda *a, **k: True)

    before = (ver.has_shown_welcome(),
              risk_dialog._seen(risk_dialog._PLUGIN_FLAG),
              risk_dialog._seen(risk_dialog._UIA_FLAG))

    dev_page._preview_plugin_risk()
    dev_page._preview_uia_risk()

    after = (ver.has_shown_welcome(),
             risk_dialog._seen(risk_dialog._PLUGIN_FLAG),
             risk_dialog._seen(risk_dialog._UIA_FLAG))
    assert before == after, "预览不应改变任何「已看过」状态"


def test_dialog_state_label_reflects_marks(dev_page):
    """状态行要如实反映三个弹窗是否已看过。"""
    from core import version as ver

    dev_page._refresh_dialog_state()
    text = dev_page._dialog_state_lbl.text()
    assert "首次使用引导" in text and "插件风险提示" in text

    ver.mark_welcome_shown()
    dev_page._refresh_dialog_state()
    assert "首次使用引导 已看过" in dev_page._dialog_state_lbl.text()


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
