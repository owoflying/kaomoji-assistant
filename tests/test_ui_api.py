"""`api.ui` 通用界面修改 API 的契约测试。

这是本次新增能力的核心保障：**插件对界面的任何修改都必须能被完整回滚**。
每条用例都遵循同一模式：

    记录原状态 -> 施加修改 -> 断言生效 -> 卸载插件 -> 断言原样还原

覆盖五类能力：属性修改、结构修改、渲染替换、信号绑定、跨插件修改。
"""
import os

import pytest

from core.kaomoji_data import KaomojiData
from core.plugin_ui import PluginUi
from core.plugins import PluginManager
from core.ui_registry import UiRegistry
from core.user_kaomoji import UserKaomoji
from core.user_state import UserState
from core.user_triggers import UserTriggers
from ui.plugin_ui_hooks import build_ui_hooks


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
def env(qapp, tmp_path, quiet_log):
    """建出「主页 + registry + manager」，返回便于断言的命名空间对象。"""
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

    d = str(tmp_path / "data")
    os.makedirs(d, exist_ok=True)
    window = UnifiedSettingsWindow(
        KaomojiData(), cfg, UserState(path=os.path.join(d, "s.json")),
        UserKaomoji(path=os.path.join(d, "u.json")),
        UserTriggers(path=os.path.join(d, "t.json")),
        save_config=lambda c: None, open_log=lambda: None,
        dev_refs={}, plugins=pm, ui_registry=registry)
    qapp.processEvents()

    class Env:
        pass

    e = Env()
    e.window = window
    e.plugins = pm
    e.registry = registry
    e.qapp = qapp
    e.ui = PluginUi(pm, "test_plugin")
    e.home = window.home_page
    e.title = registry.resolve("home.greeting.title")
    try:
        yield e
    finally:
        pm._ui_hub.rollback_all()
        window.hide()
        window.deleteLater()


# ---------------------------------------------------------------- 登记
def test_registry_registers_builtin_ids(env):
    ids = env.registry.ids()
    for expected in ("home.greeting", "home.greeting.title", "home.welcome",
                     "home.tip", "shell.window", "shell.sidebar"):
        assert expected in ids, "缺少登记 id: %s" % expected


def test_registry_resolves_by_id_and_path(env):
    by_id = env.registry.resolve("home.greeting.title")
    assert by_id is env.title
    # 路径也能定位（类型+序号）
    path = env.registry.path_of(env.title)
    assert path is not None
    assert env.registry.resolve(path) is env.title


def test_registry_unknown_target_is_none(env):
    assert env.registry.resolve("nope.nope") is None
    assert env.registry.resolve("home/NoSuchType[0]") is None
    assert env.registry.resolve("") is None


def test_walk_covers_tree(env):
    paths = [p for p, _w in env.registry.walk()]
    assert any("GreetingCard" in p for p in paths)


# ---------------------------------------------------------------- 属性修改
def test_set_text_and_rollback(env):
    original = env.title.text()
    assert env.ui.set_text("home.greeting.title", "插件改的") is True
    assert env.title.text() == "插件改的"

    env.plugins._ui_hub.rollback("test_plugin")
    assert env.title.text() == original


def test_set_text_missing_target_returns_false(env):
    assert env.ui.set_text("不存在的.id", "x") is False


def test_set_style_and_rollback(env):
    original = env.title.styleSheet()
    assert env.ui.set_style("home.greeting.title", "color: red;") is True
    assert "color: red;" in env.title.styleSheet()

    env.plugins._ui_hub.rollback("test_plugin")
    assert env.title.styleSheet() == original


def test_set_style_append_preserves_original(env):
    env.title.setStyleSheet("font-weight: bold;")
    env.ui.set_style("home.greeting.title", "color: red;")
    # 追加模式必须保留原样式（否则会切断宿主 QSS 级联）
    assert "font-weight: bold;" in env.title.styleSheet()
    assert "color: red;" in env.title.styleSheet()


def test_set_visible_and_rollback(env):
    tip = env.registry.resolve("home.tip")
    assert tip.isHidden() is False
    assert env.ui.set_visible("home.tip", False) is True
    assert tip.isHidden() is True

    env.plugins._ui_hub.rollback("test_plugin")
    assert tip.isHidden() is False


def test_set_enabled_and_rollback(env):
    w = env.registry.resolve("home.actions.search")
    assert w.isEnabled() is True
    env.ui.set_enabled("home.actions.search", False)
    assert w.isEnabled() is False

    env.plugins._ui_hub.rollback("test_plugin")
    assert w.isEnabled() is True


def test_set_property_and_rollback(env):
    w = env.registry.resolve("home.stats")
    before = w.property("pluginTag")
    env.ui.set_property("home.stats", "pluginTag", "on")
    assert w.property("pluginTag") == "on"

    env.plugins._ui_hub.rollback("test_plugin")
    assert w.property("pluginTag") == before


# ---------------------------------------------------------------- 结构修改
def test_insert_and_rollback(env):
    from PySide6.QtWidgets import QLabel

    card = env.registry.resolve("home.greeting")
    before = card.layout().count()

    lbl = QLabel("插件插入")
    assert env.ui.insert("home.greeting", lbl, index=0) is True
    assert card.layout().count() == before + 1

    env.plugins._ui_hub.rollback("test_plugin")
    env.qapp.processEvents()
    assert card.layout().count() == before


def test_remove_and_rollback(env):
    tip = env.registry.resolve("home.tip")
    root = env.home.layout()
    index_before = root.indexOf(tip)
    assert index_before >= 0

    assert env.ui.remove("home.tip") is True
    assert root.indexOf(tip) == -1

    env.plugins._ui_hub.rollback("test_plugin")
    env.qapp.processEvents()
    assert root.indexOf(tip) == index_before, "移除的控件应插回原位置"


# ---------------------------------------------------------------- 渲染替换
def test_replace_render_swaps_container_contents(env):
    """replace_render 换的是「容器里的内容」，容器本身还在原位。"""
    from PySide6.QtWidgets import QLabel

    greeting = env.registry.resolve("home.greeting")
    root = env.home.layout()
    index_before = root.indexOf(greeting)

    marker = QLabel("插件接管了问候卡片内部")

    def factory(_parent):
        return marker

    assert env.ui.replace_render("home.greeting", factory) is True
    # 原内容（含嵌套布局）应被清空，只剩插件产物
    lay = greeting.layout()
    current_widgets = [lay.itemAt(i).widget() for i in range(lay.count())]
    assert current_widgets == [marker], "容器内容应只剩插件产物"
    assert root.indexOf(greeting) == index_before, "容器本身应仍在原位"

    env.plugins._ui_hub.rollback("test_plugin")
    env.qapp.processEvents()
    lay = greeting.layout()
    assert lay.count() >= 1
    assert marker not in [lay.itemAt(i).widget() for i in range(lay.count())], \
        "插件产物应被销毁"
    # 原问候标题回到容器里
    texts = []
    for i in range(lay.count()):
        w = lay.itemAt(i).widget()
        if w is not None and hasattr(w, "text"):
            texts.append(w.text())
    assert env.title in [lay.itemAt(i).widget() for i in range(lay.count())] or \
        any(t for t in texts), "回滚后原内容应恢复"


def test_swap_widget_replaces_whole_widget(env):
    """swap_widget 把整个控件换掉（位置也被接管），适合"整块重画"。"""
    from PySide6.QtWidgets import QLabel

    welcome = env.registry.resolve("home.welcome")
    root = env.home.layout()
    index_before = root.indexOf(welcome)

    assert env.ui.swap_widget(
        "home.welcome", lambda _p: QLabel("我换掉了整个欢迎卡片")) is True
    assert root.indexOf(welcome) == -1, "原控件应被移出布局"

    env.plugins._ui_hub.rollback("test_plugin")
    env.qapp.processEvents()
    assert root.indexOf(welcome) == index_before, "回滚后原控件应回到原位"


# ---------------------------------------------------------------- 信号绑定
def test_on_bind_and_auto_disconnect(env):
    calls = []
    w = env.registry.resolve("home.actions.search")
    assert env.ui.on("home.actions.search", "clicked", lambda: calls.append(1)) is True

    w.clicked.emit()
    assert calls == [1]

    env.plugins._ui_hub.rollback("test_plugin")
    w.clicked.emit()
    assert calls == [1], "回滚后连接应已断开"


# ---------------------------------------------------------------- 回滚整体
def test_multiple_changes_rollback_all(env):
    original = env.title.text()
    tip = env.registry.resolve("home.tip")

    env.ui.set_text("home.greeting.title", "A")
    env.ui.set_style("home.greeting.title", "color: blue;")
    env.ui.set_visible("home.tip", False)
    assert env.ui.pending() == 3

    n = env.plugins._ui_hub.rollback("test_plugin")
    assert n == 3
    assert env.ui.pending() == 0
    assert env.title.text() == original
    assert tip.isHidden() is False


def test_rollback_is_idempotent(env):
    env.ui.set_text("home.greeting.title", "X")
    first = env.plugins._ui_hub.rollback("test_plugin")
    second = env.plugins._ui_hub.rollback("test_plugin")
    assert first == 1
    assert second == 0


def test_unload_triggers_rollback(env):
    """插件卸载必须自动回滚——这是"停用后界面复原"的保证。"""
    original = env.title.text()
    env.ui.set_text("home.greeting.title", "临时改动")
    assert env.title.text() == "临时改动"

    # 直接调 detach（unload 内部会走它）
    env.plugins._detach("test_plugin")
    assert env.title.text() == original


# ---------------------------------------------------------------- 隔离
def test_mutators_are_isolated_per_plugin(env):
    other = PluginUi(env.plugins, "other_plugin")
    env.ui.set_text("home.greeting.title", "A插件")
    other.set_style("home.greeting.title", "color: green;")

    # 只回滚 A，B 的修改应保留
    env.plugins._ui_hub.rollback("test_plugin")
    assert "color: green;" in env.title.styleSheet()
    assert env.title.text() != "A插件"

    env.plugins._ui_hub.rollback("other_plugin")
    assert "color: green;" not in env.title.styleSheet()


def test_no_registry_degrades_gracefully(qapp, tmp_path, quiet_log):
    """未注入 registry 时（纯 core 场景）api.ui 应安全降级而非崩溃。"""
    pm = PluginManager(
        plugins_dir=PLUGINS_DIR, config=_config(), log=quiet_log,
        settings_dir=str(tmp_path / "ps"))
    ui = PluginUi(pm, "orphan")
    assert ui.find("home.greeting") is None
    assert ui.ids() == []
    assert ui.set_text("home.greeting.title", "x") is False
    assert ui.rollback() == 0


def test_hub_rollback_all(env):
    env.ui.set_text("home.greeting.title", "A")
    PluginUi(env.plugins, "p2").set_style("home.greeting.title", "color: red;")
    assert env.plugins._ui_hub.pending() == 2
    assert env.plugins._ui_hub.rollback_all() == 2
    assert env.plugins._ui_hub.pending() == 0


# ---------------------------------------------------------------- 崩溃回滚
def test_crash_in_event_callback_rolls_back(env):
    """插件事件回调抛异常时，它改过的界面必须自动复原。

    这正是"崩溃时自动回滚"的核心场景：插件改了一半界面然后崩溃，
    不还原就会留下半残界面，且用户无法通过停用插件修复（插件看起来仍是启用）。
    """
    original = env.title.text()

    def boom():
        # 先改界面，再崩溃
        env.ui.set_text("home.greeting.title", "改到一半就崩了")
        raise RuntimeError("插件内部错误")

    env.plugins._add_listener("test_plugin", "startup", boom)
    assert env.title.text() == original      # 尚未执行

    env.plugins.emit("startup")              # 触发回调 → 崩溃

    assert env.title.text() == original, "回调崩溃后应自动回滚界面修改"
    assert env.plugins._ui_hub.pending("test_plugin") == 0


def test_crash_in_transform_rolls_back(env):
    """转换函数崩溃同样要回滚。"""
    original = env.title.text()

    def bad_transform(text, ctx):
        env.ui.set_style("home.greeting.title", "color: red;")
        raise ValueError("transform 炸了")

    env.plugins._add_transform("test_plugin", bad_transform, "bad", 10)
    env.plugins.run_transforms("x", {})

    assert "color: red;" not in env.title.styleSheet(), "转换崩溃后样式应回滚"
    assert env.title.text() == original


def test_crash_does_not_disable_plugin(env):
    """一次回调崩溃不该让整个插件下线——只回滚界面修改。"""
    env.plugins.plugins["test_plugin"] = {
        "id": "test_plugin", "dir": "", "manifest": {"name": "t"},
        "state": "loaded", "error": None, "error_kind": None,
        "module": None, "api": None,
    }
    env.plugins._add_listener("test_plugin", "startup", lambda: 1 / 0)
    env.plugins.emit("startup")
    assert env.plugins.plugins["test_plugin"]["state"] == "loaded"


def test_setup_crash_rolls_back(tmp_path, qapp, quiet_log):
    """插件在 setup() 里改界面后崩溃：加载失败且界面复原。"""
    from ui.unified_window import UnifiedSettingsWindow

    pdir = tmp_path / "plugins" / "crashy"
    pdir.mkdir(parents=True)
    (pdir / "plugin.json").write_text(
        '{"name":"crashy","version":"1","entry":"main.py","api_version":2}',
        encoding="utf-8")
    (pdir / "main.py").write_text(
        "def setup(api):\n"
        "    api.ui.set_text('home.greeting.title', '崩前改动')\n"
        "    raise RuntimeError('setup 炸了')\n",
        encoding="utf-8")

    cfg = _config()
    registry = UiRegistry()
    pm = PluginManager(
        plugins_dir=str(tmp_path / "plugins"), config=cfg,
        save_config=lambda c: None, log=quiet_log,
        settings_dir=str(tmp_path / "ps"),
        ui_hooks=build_ui_hooks(theme_provider=lambda: "light"),
        ui_registry=registry)
    pm.discover()

    d = str(tmp_path / "data")
    os.makedirs(d, exist_ok=True)
    window = UnifiedSettingsWindow(
        KaomojiData(), cfg, UserState(path=os.path.join(d, "s.json")),
        UserKaomoji(path=os.path.join(d, "u.json")),
        UserTriggers(path=os.path.join(d, "t.json")),
        save_config=lambda c: None, open_log=lambda: None,
        dev_refs={}, plugins=pm, ui_registry=registry)
    try:
        original = registry.resolve("home.greeting.title").text()
        assert pm.load("crashy") is False
        assert pm.plugins["crashy"]["state"] == "error"
        assert registry.resolve("home.greeting.title").text() == original, \
            "setup 崩溃后界面应复原"
    finally:
        window.hide()
        window.deleteLater()
