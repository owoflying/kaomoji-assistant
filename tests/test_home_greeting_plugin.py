"""`plugins/home_greeting` 的契约测试。

这个插件是**反面教材**：它用「非正常手段」修改宿主自带界面（沿 Qt 父子链爬到
`UnifiedSettingsWindow` 再改主页布局），而官方 API 并无此能力。

测试目的有二：
  1. 确认这条路**确实走得通**（否则文档里的说法就是空话）；
  2. 钉住它的**副作用边界**——插进去的东西必须能被干净收回，
     不能留下孤儿控件或临时锚点页污染侧边栏。

⚠ 任何一条失败都说明"爬对象"手法与宿主结构已经错位：
要么主程序重构了主页/导航结构（插件需跟着改），要么清理逻辑失效（会漏控件）。
"""
import os
import re

import pytest

from core.kaomoji_data import KaomojiData
from core.plugins import PluginManager, STATE_LOADED
from core.user_kaomoji import UserKaomoji
from core.user_state import UserState
from core.user_triggers import UserTriggers
from ui.plugin_ui_hooks import build_ui_hooks


PLUGIN_ID = "home_greeting"
ANCHOR_KEY = "plugin:%s:anchor" % PLUGIN_ID


def _config(**over):
    cfg = {
        "theme": "light", "panel_alpha": 1.0, "opacity": 0.98,
        "hotkey": "<ctrl>+<shift>+k", "input_method": "direct",
        "max_recent": 30, "page_size": 3, "auto_popup": True,
        "autostart": False, "auto_hide_on_blur": True,
        "developer_mode": False, "use_uia_elevation": False,
        "use_test_features": True, "plugins_enabled": {},
    }
    cfg.update(over)
    return cfg


@pytest.fixture
def greeting_setup(tmp_path, qapp, quiet_log):
    """加载 home_greeting 插件并建出主窗口，返回 (window, manager, logs)。"""
    from ui.unified_window import UnifiedSettingsWindow

    cfg = _config(plugins_enabled={PLUGIN_ID: True})
    pm = PluginManager(
        plugins_dir=os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "plugins"),
        config=cfg, save_config=lambda c: None, log=quiet_log,
        settings_dir=str(tmp_path / "plugin_settings"),
        theme_provider=lambda: cfg["theme"],
        ui_hooks=build_ui_hooks(theme_provider=lambda: cfg["theme"]))
    pm.discover()
    assert pm.load(PLUGIN_ID) is True
    assert pm.plugins[PLUGIN_ID]["state"] == STATE_LOADED

    data_dir = str(tmp_path / "data")
    os.makedirs(data_dir, exist_ok=True)
    window = UnifiedSettingsWindow(
        KaomojiData(), cfg, UserState(path=os.path.join(data_dir, "s.json")),
        UserKaomoji(path=os.path.join(data_dir, "u.json")),
        UserTriggers(path=os.path.join(data_dir, "t.json")),
        save_config=lambda c: None, open_log=lambda: None,
        dev_refs={}, plugins=pm)
    qapp.processEvents()
    yield window, pm, quiet_log
    window.deleteLater()


def _home_cards(window):
    """主页根布局里所有 objectName == Card 的控件。"""
    lay = window.home_page.layout()
    out = []
    for i in range(lay.count()):
        w = lay.itemAt(i).widget()
        if w is not None and w.objectName() == "Card":
            out.append(w)
    return out


def _greeting_card(window):
    """识别**插件插入**的那张卡片。

    ⚠ 主程序现已内置同名功能的问候卡片（`ui.pages.greeting_card.GreetingCard`），
    所以不能只按"有 _title/_emoji 属性"来认——两者特征一样。这里用类名区分：
    插件的是 `_GreetingCard`，内置的是 `GreetingCard`。
    """
    for w in _home_cards(window):
        if type(w).__name__ == "_GreetingCard":
            return w
    return None


# ---------------------------------------------------------------- 核心断言
def test_greeting_card_inserted_into_home(greeting_setup):
    """插件确实把卡片插进了主页布局（自带页面被外部修改）。"""
    window, _pm, _log = greeting_setup
    card = _greeting_card(window)
    assert card is not None, "问候卡片未出现——宿主主页结构可能已变，插件需跟进"


def test_greeting_card_positioned_under_page_title(greeting_setup):
    """插在 PageTitle 之后，而不是页脚或随机位置。"""
    window, _pm, _log = greeting_setup
    lay = window.home_page.layout()
    card = _greeting_card(window)
    assert card is not None
    index = lay.indexOf(card)
    title_index = None
    for i in range(lay.count()):
        w = lay.itemAt(i).widget()
        if w is not None and w.objectName() == "PageTitle":
            title_index = i
            break
    assert title_index is not None
    assert index == title_index + 1


def test_greeting_text_matches_time_period(greeting_setup):
    """问候语与当前时段一致（上午/中午/下午/晚上/凌晨）。"""
    window, _pm, _log = greeting_setup
    card = _greeting_card(window)
    assert card is not None
    import datetime
    hour = datetime.datetime.now().hour
    expected = ("凌晨" if hour < 5 else
                "上午" if hour < 11 else
                "中午" if hour < 13 else
                "下午" if hour < 18 else "晚上")
    assert card._title.text() == "%s好" % expected


def test_card_reuses_host_object_names_for_theming(greeting_setup):
    """卡片复用宿主 objectName 以继承 QSS：自身 styleSheet 必须为空。"""
    window, _pm, _log = greeting_setup
    card = _greeting_card(window)
    assert card is not None
    assert card.objectName() == "Card"
    assert card.styleSheet() == "", "卡片不应自带样式表，否则会切断宿主级联"


def test_home_refresh_hook_wrapped(greeting_setup):
    """主页 refresh_stats 被非侵入地包了一层，用于刷新问候语。"""
    window, _pm, _log = greeting_setup
    assert getattr(window.home_page.refresh_stats, "_greeting_wrapped", False)
    # 原行为仍在（不会因为被包而丢失）
    window.home_page.refresh_stats()


# ---------------------------------------------------------------- 副作用边界
def test_anchor_page_removed_from_sidebar(greeting_setup):
    """临时锚点页必须自动撤销，不能污染侧边栏。"""
    window, pm, _log = greeting_setup
    assert ANCHOR_KEY not in pm.plugin_pages()
    nav_keys = [k for k, _ in window._nav.nav_items]
    assert ANCHOR_KEY not in nav_keys
    assert ANCHOR_KEY not in window._nav.pages


def test_card_survives_anchor_teardown(greeting_setup, qapp):
    """卡片必须在「主动撤销锚点页」之后仍然存在。

    回归用例：撤销锚点页会让宿主销毁该 widget，从而触发插件挂在 destroyed 上的
    清理逻辑。若插件不区分"自己拆踏板"与"被卸载"，清理会立刻把刚插好的卡片摘掉
    ——表现为「插件显示已启用，主页却什么都没有」。
    """
    window, pm, _log = greeting_setup
    # 多跑几轮事件循环，让撤销锚点页引发的连锁销毁全部落地
    for _ in range(3):
        qapp.processEvents()
    assert ANCHOR_KEY not in pm.plugin_pages(), "锚点页应已撤销"
    assert _greeting_card(window) is not None, \
        "撤销锚点页后卡片消失了——清理逻辑误把「拆踏板」当成「插件卸载」"


def test_card_survives_open_and_page_switch(qapp, tmp_path, quiet_log):
    """走真实交互路径：打开主面板 + 主页↔库页往返，卡片都得在。

    之前的用例只断言构造期状态，漏掉了"窗口 show 之后事件循环一跑就没了"这个
    真实故障。这里显式打开窗口并来回切页。
    """
    from ui.unified_window import UnifiedSettingsWindow

    cfg = _config(plugins_enabled={PLUGIN_ID: True})
    pm = PluginManager(
        plugins_dir=os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "plugins"),
        config=cfg, save_config=lambda c: None, log=quiet_log,
        settings_dir=str(tmp_path / "plugin_settings"),
        theme_provider=lambda: cfg["theme"],
        ui_hooks=build_ui_hooks(theme_provider=lambda: cfg["theme"]))
    pm.discover()
    assert pm.load(PLUGIN_ID) is True

    data_dir = str(tmp_path / "data")
    os.makedirs(data_dir, exist_ok=True)
    window = UnifiedSettingsWindow(
        KaomojiData(), cfg, UserState(path=os.path.join(data_dir, "s.json")),
        UserKaomoji(path=os.path.join(data_dir, "u.json")),
        UserTriggers(path=os.path.join(data_dir, "t.json")),
        save_config=lambda c: None, open_log=lambda: None,
        dev_refs={}, plugins=pm)
    try:
        window.show()
        for _ in range(3):
            qapp.processEvents()
        assert _greeting_card(window) is not None, "打开窗口后卡片消失"

        window._set_page("library")
        for _ in range(2):
            qapp.processEvents()
        window._set_page("home")
        for _ in range(2):
            qapp.processEvents()
        assert _greeting_card(window) is not None, "切页往返后卡片消失"
    finally:
        window.hide()
        window.deleteLater()


def test_unload_removes_card_and_restores_refresh(greeting_setup, qapp):
    """停用插件后：卡片被移除、refresh_stats 还原、导航干净。"""
    window, pm, _log = greeting_setup
    for _ in range(3):
        qapp.processEvents()
    home = window.home_page
    wrapped = home.refresh_stats
    assert _greeting_card(window) is not None

    pm.unload(PLUGIN_ID)
    qapp.processEvents()
    qapp.processEvents()

    assert _greeting_card(window) is None, "停用后卡片仍在——清理逻辑失效"
    assert home.refresh_stats is not wrapped, "refresh_stats 未被还原"
    assert ANCHOR_KEY not in [k for k, _ in window._nav.nav_items]


def test_reload_does_not_duplicate_card(greeting_setup, qapp):
    """重载后不能出现两张插件卡片。"""
    window, pm, _log = greeting_setup
    pm.reload(PLUGIN_ID)
    qapp.processEvents()
    qapp.processEvents()
    cards = [w for w in _home_cards(window) if type(w).__name__ == "_GreetingCard"]
    assert len(cards) <= 1, "重载后出现多张问候卡片"


# ---------------------------------------------------------------- 反向教材自证
def test_plugin_relies_on_private_host_attributes():
    """明示依赖了宿主私有结构——这是"非正常手段"的成本，不是意外。"""
    src_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "plugins", PLUGIN_ID, "main.py")
    with open(src_path, encoding="utf-8") as f:
        src = f.read()
    # 依赖的存在性：爬到宿主后直接用 home_page
    assert "home_page" in src
    # 明确使用私有属性（noqa 标记）
    assert re.search(r"#\s*noqa:\s*SLF001", src), \
        "触碰私有成员的代码必须标注，提醒后来者这是有意为之"
