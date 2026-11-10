"""测试：关闭开发者模式 / 测试模式**不影响插件**（插件已毕业为正式功能）。

## 语义变迁

**过去（插件在测试门控下）**：关闭开发者模式必须卸载插件，否则
数据源/转换/事件回调继续生效、`api.ui` 的界面修改也不回滚——
表现为"关掉开发者模式后，插件插的东西还留在界面上"。

**现在（插件已毕业）**：插件由自己的启用开关（`plugins_enabled`）控制，
与开发者模式/测试模式**无关**。关闭开发者模式时：
  * 插件保持 `loaded`；
  * 它的界面修改保留（不回滚）；
  * 「插件」导航页仍在。

本文件验证新语义，并确认"用户主动停用插件"这条路径**仍然**会正确卸载并回滚
（功能没丢，只是不再由关闭测试模式间接触发）。

## 覆盖

1. `_disable_dev_tab()`（开发者页点「关闭开发者模式」）
2. `DevPage._on_toggle_test()`（开发者页直接关「测试功能」）
3. `set_enabled(pid, False)`（用户主动停用——仍应回滚）
"""
import os

import pytest

from core.kaomoji_data import KaomojiData
from core.plugins import PluginManager
from core.ui_registry import UiRegistry
from core.user_kaomoji import UserKaomoji
from core.user_state import UserState
from core.user_triggers import UserTriggers
from ui.plugin_ui_hooks import build_ui_hooks


PLUGIN_ID = "ui_tweaks_example"
PLUGINS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "plugins")


def _config(**over):
    cfg = {
        "theme": "light", "panel_alpha": 1.0, "opacity": 0.98,
        "hotkey": "<ctrl>+<shift>+k", "input_method": "direct",
        "max_recent": 30, "page_size": 3, "auto_popup": True,
        "autostart": False, "auto_hide_on_blur": True,
        "developer_mode": True, "use_uia_elevation": False,
        "use_test_features": True, "plugins_enabled": {PLUGIN_ID: True},
    }
    cfg.update(over)
    return cfg


@pytest.fixture
def live(tmp_path, qapp, quiet_log):
    """按真实装配顺序建运行时：先加载插件、再建窗口。"""
    from ui.unified_window import UnifiedSettingsWindow

    cfg = _config()
    registry = UiRegistry()
    pm = PluginManager(
        plugins_dir=PLUGINS_DIR, config=cfg, save_config=lambda c: None,
        log=quiet_log, settings_dir=str(tmp_path / "ps"),
        theme_provider=lambda: cfg["theme"],
        ui_hooks=build_ui_hooks(theme_provider=lambda: cfg["theme"]),
        ui_registry=registry)
    pm.discover()
    assert pm.load(PLUGIN_ID) is True

    d = str(tmp_path / "data")
    os.makedirs(d, exist_ok=True)
    window = UnifiedSettingsWindow(
        KaomojiData(), cfg, UserState(path=os.path.join(d, "s.json")),
        UserKaomoji(path=os.path.join(d, "u.json")),
        UserTriggers(path=os.path.join(d, "t.json")),
        save_config=lambda c: None, open_log=lambda: None,
        dev_refs={}, plugins=pm, ui_registry=registry)
    qapp.processEvents()
    # startup 阶段会基于原值改问候语
    pm.emit("startup")
    qapp.processEvents()

    class Live:
        pass

    obj = Live()
    obj.window = window
    obj.plugins = pm
    obj.registry = registry
    obj.app = qapp
    obj.config = cfg
    try:
        yield obj
    finally:
        pm._ui_hub.rollback_all()
        window.hide()
        window.deleteLater()


def _assert_modified(live):
    """确认插件确实改了界面（避免"本来就没改"导致假通过）。"""
    assert live.registry.resolve("home.greeting").styleSheet() != ""
    assert live.registry.resolve("home.tip").isHidden() is True
    assert live.registry.resolve("home.actions.search").text() == "找颜文字"
    assert live.plugins._ui_hub.pending(PLUGIN_ID) > 0


def _assert_rolled_back(live):
    """确认所有界面修改都已还原。"""
    assert live.registry.resolve("home.greeting").styleSheet() == "", \
        "问候卡片样式未回滚"
    assert live.registry.resolve("home.tip").isHidden() is False, \
        "被隐藏的提示卡片未恢复"
    assert live.registry.resolve("home.actions.search").text() == "搜索颜文字", \
        "快捷入口文字未还原"
    assert "插件向你问好" not in live.registry.resolve(
        "home.greeting.title").text(), "问候语未还原"
    assert live.plugins._ui_hub.pending(PLUGIN_ID) == 0, "仍有未回滚的修改"


# ---------------------------------------------------------------- 路径 1
def test_disable_dev_tab_keeps_plugins_loaded(live):
    """关闭开发者模式**不应**影响插件（插件已是正式功能）。

    历史：插件曾在测试门控下，关闭开发者模式必须卸载它，否则它通过 api.ui
    做的界面修改不会回滚。插件毕业为正式功能后，这个约束消失——
    插件由自己的启用开关控制，与开发者/测试模式无关。
    """
    _assert_modified(live)
    assert live.plugins.plugins[PLUGIN_ID]["state"] == "loaded"

    live.window._disable_dev_tab()
    live.app.processEvents()

    assert live.config["use_test_features"] is False
    assert live.plugins.plugins[PLUGIN_ID]["state"] == "loaded", \
        "插件已毕业，不该因关闭开发者模式而被卸载"


def test_disable_dev_tab_keeps_plugin_modifications(live):
    """插件做的界面修改也应保留（不再被回滚）。"""
    cards_before = live.window.home_page.greeting_card.layout().count()
    assert cards_before >= 3          # 内置 2 项 + 插件插入 1 项

    live.window._disable_dev_tab()
    live.app.processEvents()

    assert live.window.home_page.greeting_card.layout().count() == cards_before, \
        "插件插入的卡片不该被移除"
    _assert_modified(live)


def test_disable_dev_tab_keeps_plugin_nav(live):
    """「插件」导航页应保留（正式功能）。"""
    assert "plugin" in live.window._nav.pages
    live.window._disable_dev_tab()
    live.app.processEvents()
    assert "plugin" in live.window._nav.pages, \
        "插件页是正式功能，不该随开发者模式消失"


# ---------------------------------------------------------------- 路径 2
def test_dev_page_toggle_test_features_keeps_plugins(live):
    """开发者页关「测试功能」：同样不影响插件。"""
    _assert_modified(live)

    live.window.dev_page._on_toggle_test()
    live.app.processEvents()

    assert live.config["use_test_features"] is False
    assert live.plugins.plugins[PLUGIN_ID]["state"] == "loaded"
    _assert_modified(live)


# ---------------------------------------------------------------- 仍可显式卸载
def test_plugin_can_still_be_disabled_explicitly(live):
    """插件仍可被用户主动停用——只是不再由"关闭测试模式"间接触发。"""
    _assert_modified(live)

    live.plugins.set_enabled(PLUGIN_ID, False)
    live.app.processEvents()

    assert live.plugins.plugins[PLUGIN_ID]["state"] != "loaded"
    _assert_rolled_back(live)


# ---------------------------------------------------------------- 不误伤
def test_unload_all_is_idempotent(live):
    """重复调用卸载不应抛异常（保留为工具方法）。"""
    live.window._unload_all_plugins()
    live.window._unload_all_plugins()
    live.app.processEvents()
    assert live.plugins._ui_hub.pending(PLUGIN_ID) == 0


def test_unload_all_without_plugins(tmp_path):
    """没有 PluginManager 时（纯 UI 场景）不能崩。"""
    from PySide6.QtWidgets import QApplication

    from ui.unified_window import UnifiedSettingsWindow

    app = QApplication.instance() or QApplication([])
    d = str(tmp_path / "d")
    os.makedirs(d, exist_ok=True)
    w = UnifiedSettingsWindow(
        KaomojiData(), _config(plugins_enabled={}),
        UserState(path=os.path.join(d, "s.json")),
        UserKaomoji(path=os.path.join(d, "u.json")),
        UserTriggers(path=os.path.join(d, "t.json")),
        save_config=lambda c: None, open_log=lambda: None, plugins=None)
    try:
        assert w._unload_all_plugins() == 0
    finally:
        w.hide()
        w.deleteLater()
        app.processEvents()
