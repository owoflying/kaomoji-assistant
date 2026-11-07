"""UI 层离屏单测：统一主窗口的拆分边界与主题/配置链路。

**为什么要跑这些**：`py_compile` 只查语法、`import` 只执行模块顶层，二者都不会
执行类方法体。若某个符号在方法里用到却被误删，只有**真正构造窗口并调用方法**
才会抛 `NameError`（历史上踩过）。所以这里一律真建窗口、真调 `apply_config`。

⚠ 全部用例都只在临时目录里读写，绝不触碰项目根的 config.json。
"""
import os

import pytest

from core.kaomoji_data import KaomojiData
from core.user_kaomoji import UserKaomoji
from core.user_state import UserState
from core.user_triggers import UserTriggers


def _config(**over):
    cfg = {
        "theme": "light", "panel_alpha": 1.0, "opacity": 0.98,
        "hotkey": "<ctrl>+<shift>+k", "input_method": "direct",
        "max_recent": 30, "page_size": 3, "auto_popup": True,
        "autostart": False, "auto_hide_on_blur": True,
        "developer_mode": False, "use_uia_elevation": False,
        "use_test_features": False, "plugins_enabled": {},
    }
    cfg.update(over)
    return cfg


@pytest.fixture
def stores(isolated_data):
    os.makedirs(isolated_data, exist_ok=True)
    return {
        "data": KaomojiData(),
        "user_kao": UserKaomoji(path=os.path.join(isolated_data, "uk.json")),
        "triggers": UserTriggers(path=os.path.join(isolated_data, "tr.json")),
        "state": UserState(path=os.path.join(isolated_data, "st.json")),
    }


@pytest.fixture
def window(qapp, stores):
    from ui.unified_window import UnifiedSettingsWindow
    w = UnifiedSettingsWindow(
        stores["data"], _config(), stores["state"], stores["user_kao"],
        stores["triggers"], save_config=lambda c: None, open_log=lambda: None,
        dev_refs={}, plugins=None)
    yield w
    w.deleteLater()


# ---------------------------------------------------------------- 组装
def test_window_builds_all_builtin_pages(window):
    pages = window._nav.pages
    for key in ("home", "library", "custom", "triggers", "search",
                "settings", "about"):
        assert key in pages, "缺少页面 %s" % key
    assert pages["home"] is window.home_page
    assert pages["settings"] is window.settings_page


def test_nav_order_matches_nav_items(window):
    from ui.unified_nav import NavController
    assert [k for k, _ in window._nav.nav_items] == \
        [k for k, _, _ in NavController.NAV_ITEMS]


def test_shell_geometry_constants(window):
    assert window._RADIUS == 12
    assert window._PAD == 12
    assert window._TITLEBAR_H == 40
    assert window.minimumWidth() == 900


def test_title_button_rects_after_show(window, qapp):
    """标题按钮矩形必须互不重叠且落在窗口内，否则 WM_NCHITTEST 吞点击。"""
    window.show()
    qapp.processEvents()
    rects = window._title_btn_rects()
    assert len(rects) == 3
    assert all(r.width() > 0 and r.height() > 0 for r in rects)
    assert window.rect().contains(rects[-1])
    window.hide()


# ---------------------------------------------------------------- 页面切换
def test_set_page_switches_content(window):
    window._set_page("library")
    assert window.content.currentWidget() is window.library_page
    window._set_page("search")
    assert window.content.currentWidget() is window.search_page


def test_set_page_unknown_key_is_noop(window):
    window._set_page("library")
    before = window.content.currentWidget()
    window._set_page("不存在的页")
    assert window.content.currentWidget() is before


def test_page_shown_hook_refreshes_stats(window):
    """切到主页要刷新统计（否则仪表盘数字停在旧值）。"""
    calls = []
    window.home_page.refresh_stats = lambda: calls.append(1)
    window._set_page("home", animate=False)
    assert calls


# ---------------------------------------------------------------- 主题链路
def test_apply_config_switches_theme(window):
    window.apply_config(_config(theme="dark"))
    assert window.theme.name == "dark"
    assert window.theme.dark is True


def test_apply_config_system_resolves(window):
    window.apply_config(_config(theme="system"))
    assert window.theme.name in ("light", "dark")


def test_panel_base_follows_panel_alpha(window):
    """不透明度语义：值越大越实；亚克力开时保底 0.6，关时保底 0.4。"""
    from ui.unified_window import _rgba_parts

    window.apply_config(_config(panel_alpha=1.0))
    hi = _rgba_parts(window._adjusted_panel_base())[3]
    window.apply_config(_config(panel_alpha=0.3))
    lo = _rgba_parts(window._adjusted_panel_base())[3]
    assert hi > lo, "panel_alpha 越大面板应越不透明"
    assert hi <= 1.0
    assert lo >= 0.4


def test_apply_config_is_idempotent(window):
    """同样配置重复应用不应重置当前页面（历史 bug：改不透明度跳回第一页）。"""
    window._set_page("search")
    window.apply_config(_config(theme="light"))
    window.apply_config(_config(theme="light"))
    assert window.content.currentWidget() is window.search_page


def test_pages_receive_theme(window):
    """主题广播不得硬编码页面列表：所有实现 set_theme 的页都要收到。"""
    seen = []
    for key, page in window._nav.pages.items():
        if hasattr(page, "set_theme"):
            page.set_theme = (lambda k: lambda t: seen.append(k))(key)
    window._apply_theme_to_pages(window.theme)
    assert len(seen) == len([p for p in window._nav.pages.values()
                             if hasattr(p, "set_theme")])


# ---------------------------------------------------------------- 门控
def test_developer_tab_absent_by_default(window):
    assert "developer" not in window._nav.pages
    assert "dev_page" not in vars(window)


def test_developer_tab_can_be_added_and_removed(window, qapp):
    if not window.config.get("developer_mode"):
        window._nav.enable_dev_tab()
        qapp.processEvents()
    assert "developer" in window._nav.pages
    assert window._developer_mode is True
    window._disable_dev_tab()
    qapp.processEvents()
    assert "developer" not in window._nav.pages
    assert window.dev_page is None
    # 关闭开发者模式必须连带关闭测试模式（否则测试功能逃逸到正式环境）
    assert window.config["use_test_features"] is False


def test_plugin_tab_absent_without_plugins(window):
    assert "plugin" not in window._nav.pages


# ---------------------------------------------------------------- 颜色解析
@pytest.mark.parametrize("spec,expected_alpha", [
    ("rgba(255,255,255,0.88)", 0.88),
    ("rgba(30,30,30,1)", 1.0),
    ("#ff0000", 1.0),
])
def test_rgba_parts_parses_theme_tokens(spec, expected_alpha):
    """Qt6 的 QColor 不认浮点 alpha，必须走自己的解析（否则整窗发黑）。"""
    from ui.unified_window import _rgba_parts
    _r, _g, _b, a = _rgba_parts(spec)
    assert abs(a - expected_alpha) < 0.01


def test_theme_parse_color_handles_float_alpha():
    from ui.win11_theme import parse_color
    c = parse_color("rgba(255,255,255,0.88)")
    assert c.alpha() > 200, "浮点 alpha 必须被正确换算成 0-255"
