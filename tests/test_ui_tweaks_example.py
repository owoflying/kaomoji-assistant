"""正面示例插件 `ui_tweaks_example` 的契约测试。

与 `test_home_greeting_plugin.py`（邪修反面教材）一一对照：

| 事项 | 本插件（正规） | 邪修版 |
|------|--------------|--------|
| 定位 | `api.ui` 稳定 id | register_page + window() 爬树 |
| 清理 | **不用写** | destroyed/标志位/轮询，试错 7 轮 |
| 宿主重构 | id 契约保证不碎 | 静默失效 |

本文件同时验证两个关键时序特性：
  1. **延迟重放**：插件在 `setup()` 里改界面时元素表还没登记，修改被排队，
     等窗口建好后统一生效；
  2. **完整回滚**：卸载后每一处修改都精确还原。
"""
import os

import pytest

from core.kaomoji_data import KaomojiData
from core.plugins import PluginManager, STATE_LOADED
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
        "use_test_features": True, "plugins_enabled": {},
    }
    cfg.update(over)
    return cfg


@pytest.fixture
def live(tmp_path, quiet_log):
    """**完全按真实装配顺序**建运行时：先加载插件、再建窗口。

    这正是暴露"setup 阶段界面还没就绪"的路径——用 offscreen 手搓窗口
    会跳过这个顺序，测不出真问题。
    """
    from PySide6.QtWidgets import QApplication

    from core.ui_registry import UiRegistry
    from ui.unified_window import UnifiedSettingsWindow

    app = QApplication.instance() or QApplication([])

    cfg = _config(plugins_enabled={PLUGIN_ID: True})
    registry = UiRegistry()
    pm = PluginManager(
        plugins_dir=PLUGINS_DIR, config=cfg, save_config=lambda c: None,
        log=quiet_log, settings_dir=str(tmp_path / "ps"),
        theme_provider=lambda: cfg["theme"],
        ui_hooks=build_ui_hooks(theme_provider=lambda: cfg["theme"]),
        ui_registry=registry)
    pm.discover()
    # ① 先加载插件（setup 执行时界面还不存在）
    assert pm.load(PLUGIN_ID) is True

    # ② 再建窗口（构造过程中登记元素 + mark_ready 重放排队操作）
    d = str(tmp_path / "data")
    os.makedirs(d, exist_ok=True)
    window = UnifiedSettingsWindow(
        KaomojiData(), cfg, UserState(path=os.path.join(d, "s.json")),
        UserKaomoji(path=os.path.join(d, "u.json")),
        UserTriggers(path=os.path.join(d, "t.json")),
        save_config=lambda c: None, open_log=lambda: None,
        dev_refs={}, plugins=pm, ui_registry=registry)
    app.processEvents()

    class Live:
        pass

    obj = Live()
    obj.window = window
    obj.plugins = pm
    obj.registry = registry
    obj.app = app
    obj.home = window.home_page
    try:
        yield obj
    finally:
        pm._ui_hub.rollback_all()
        window.hide()
        window.deleteLater()


# ---------------------------------------------------------------- 时序
def test_setup_phase_changes_are_replayed(live):
    """插件在 setup() 里改的界面，必须在窗口建好后真正生效。

    回归用例：装配顺序是「先加载插件、后建窗口」，插件 setup 时元素表为空。
    若不排队重放，这些修改会全部静默失败——插件作者只会看到"改了没生效"。
    """
    greeting = live.registry.resolve("home.greeting")
    # setup 里设的样式（若没重放，这里是空串）
    assert "border" in greeting.styleSheet()
    # setup 里隐藏的提示卡片
    assert live.registry.resolve("home.tip").isHidden() is True
    # setup 里改的快捷入口文字
    assert live.registry.resolve("home.actions.search").text() == "找颜文字"
    # setup 里插入的卡片
    assert live.home.greeting_card.layout().count() >= 3


def test_registry_ready_flag(live):
    assert live.registry.ready is True


def test_startup_changes_add_greeting_prefix(live):
    """需要读原值的改动在 startup 阶段完成（此时界面已就绪）。"""
    live.plugins.emit("startup")
    live.app.processEvents()
    title = live.registry.resolve("home.greeting.title").text()
    assert "插件向你问好" in title


def test_startup_is_idempotent(live):
    """重复触发 startup 不应把后缀叠两次。"""
    live.plugins.emit("startup")
    live.plugins.emit("startup")
    live.app.processEvents()
    title = live.registry.resolve("home.greeting.title").text()
    assert title.count("插件向你问好") == 1


# ---------------------------------------------------------------- 回滚
def test_unload_restores_everything(live):
    """卸载后所有修改精确还原——这是本插件"零清理代码"能成立的前提。"""
    live.plugins.emit("startup")
    live.app.processEvents()

    greeting = live.registry.resolve("home.greeting")
    tip = live.registry.resolve("home.tip")
    search = live.registry.resolve("home.actions.search")
    title = live.registry.resolve("home.greeting.title")

    styled = greeting.styleSheet()
    assert styled                      # 确认已被改过
    assert tip.isHidden() is True
    assert search.text() == "找颜文字"
    cards_before = live.home.greeting_card.layout().count()

    live.plugins.unload(PLUGIN_ID)
    live.app.processEvents()

    assert greeting.styleSheet() == "", "样式未还原"
    assert tip.isHidden() is False, "隐藏的卡片未恢复显示"
    assert search.text() == "搜索颜文字", "快捷入口文字未还原"
    assert "插件向你问好" not in title.text(), "问候语未还原"
    assert live.home.greeting_card.layout().count() == cards_before - 1, \
        "插入的卡片未移除"
    assert live.plugins._ui_hub.pending(PLUGIN_ID) == 0


def _plugin_code_only():
    """读插件源码，去掉模块 docstring 与注释——只看真正执行的代码。

    文件头的对照表里会提到 `window()` / `home_page`（作为"邪修版才用的东西"），
    直接扫全文会误判。
    """
    import ast

    src_path = os.path.join(PLUGINS_DIR, PLUGIN_ID, "main.py")
    with open(src_path, encoding="utf-8") as f:
        src = f.read()
    tree = ast.parse(src)
    # 丢掉模块级 docstring
    body = tree.body
    if body and isinstance(body[0], ast.Expr) and isinstance(
            getattr(body[0], "value", None), ast.Constant) and isinstance(
            body[0].value.value, str):
        body = body[1:]
    return ast.unparse(ast.Module(body=body, type_ignores=[])), src


def test_no_cleanup_code_needed(live):
    """插件自身不含任何清理代码（对照邪修版的 7 轮折腾）。"""
    code, _src = _plugin_code_only()
    for forbidden in ("destroyed", "setParent", "_manager._",
                      ".window()", "home_page"):
        assert forbidden not in code, \
            "正规写法不应出现 %r（那是邪修版才需要的东西）" % forbidden


def test_plugin_uses_only_public_api(live):
    """确认用的是 api.ui 而不是爬对象树。"""
    code, _src = _plugin_code_only()
    assert "api.ui" in code
    assert "ui_ids" in code


def test_plugin_state_loaded(live):
    assert live.plugins.plugins[PLUGIN_ID]["state"] == STATE_LOADED
