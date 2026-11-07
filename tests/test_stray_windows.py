"""无边框/嵌套控件生命周期回归：**控件脱离父级时不得闪现为顶层窗口**。

Qt 语义：`QWidget` 没有父级时就是**独立顶层窗口**。所以下面这种写法：

    old.removeWidget(w)      # 只移出布局，不隐藏
    w.setParent(None)        # ← 此刻 w 变成顶层窗口并显示出来
    w.deleteLater()          # 销毁动作排到下一轮事件循环

中间那一帧 w 会真实地显示在屏幕上。插件页重建卡片时一次清空 N 张卡片，
就是用户看到的"启用/停用一个插件，屏幕上闪过好几个小窗口"。

正确姿势：**hide() 必须在 setParent(None) 之前**。

本测试遍历真实操作路径，断言过程中可见顶层窗口数量不增加。
"""
import os

import pytest

from core.kaomoji_data import KaomojiData
from core.plugins import PluginManager
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
        "use_test_features": True,
        "plugins_enabled": {"startup_notice": True, "example_kaomoji": True},
    }
    cfg.update(over)
    return cfg


def _visible_tops(app):
    """当前可见的顶层窗口列表。"""
    return [w for w in app.topLevelWidgets() if w.isWindow() and w.isVisible()]


@pytest.fixture
def runtime_like(qapp, tmp_path, quiet_log):
    """建出一个带插件的主窗口（不启动监听/热键，只测 UI 生命周期）。"""
    from ui.unified_window import UnifiedSettingsWindow

    cfg = _config()
    pm = PluginManager(
        plugins_dir=PLUGINS_DIR, config=cfg, save_config=lambda c: None,
        log=quiet_log, settings_dir=str(tmp_path / "ps"),
        theme_provider=lambda: cfg["theme"],
        ui_hooks=build_ui_hooks(theme_provider=lambda: cfg["theme"]))
    pm.discover()
    pm.load_all()

    d = str(tmp_path / "data")
    os.makedirs(d, exist_ok=True)
    window = UnifiedSettingsWindow(
        KaomojiData(), cfg, UserState(path=os.path.join(d, "s.json")),
        UserKaomoji(path=os.path.join(d, "u.json")),
        UserTriggers(path=os.path.join(d, "t.json")),
        save_config=lambda c: None, open_log=lambda: None,
        dev_refs={}, plugins=pm)
    try:
        yield window, pm, cfg
    finally:
        window.hide()
        window.deleteLater()


def test_no_stray_windows_when_toggling_plugin(runtime_like, qapp):
    """启用/停用插件不得让任何控件变成可见顶层窗口。

    回归用例：`PluginPage.refresh()` 清空旧卡片时先 setParent(None) 后
    deleteLater，导致每张卡片都闪现为顶层窗口。
    """
    window, pm, _cfg = runtime_like
    window.show()
    window._set_page("plugin")
    for _ in range(3):
        qapp.processEvents()

    baseline = len(_visible_tops(qapp))

    for pid, enabled in (("startup_notice", False),
                         ("startup_notice", True),
                         ("example_kaomoji", False),
                         ("example_kaomoji", True)):
        pm.set_enabled(pid, enabled)
        for _ in range(2):
            qapp.processEvents()
        tops = _visible_tops(qapp)
        names = [type(w).__name__ for w in tops]
        assert len(tops) == baseline, (
            "启用/停用 %s 后多出了顶层窗口 %s —— "
            "说明有控件在 setParent(None) 之前没有 hide()" % (pid, names))


def test_no_stray_windows_when_switching_pages(runtime_like, qapp):
    """切换页面（含插件扩展页增删）不得产生游离顶层窗口。"""
    window, _pm, _cfg = runtime_like
    window.show()
    for _ in range(3):
        qapp.processEvents()
    baseline = len(_visible_tops(qapp))

    for key in ("library", "custom", "triggers", "search", "settings",
                "about", "plugin", "home"):
        window._set_page(key)
        qapp.processEvents()
        assert len(_visible_tops(qapp)) == baseline, \
            "切到 %s 后出现游离顶层窗口" % key


def test_no_stray_windows_when_picker_rebuilds(qapp, tmp_path):
    """候选条重建 chip 不得闪现顶层窗口。"""
    from ui.picker_window import PickerWindow

    data = KaomojiData()
    d = str(tmp_path / "d")
    os.makedirs(d, exist_ok=True)
    state = UserState(path=os.path.join(d, "s.json"))
    picker = PickerWindow(data, _config(), state,
                          UserKaomoji(path=os.path.join(d, "u.json")))
    try:
        picker.show()
        for _ in range(2):
            qapp.processEvents()
        baseline = len(_visible_tops(qapp))
        # 多次换页触发 _rebuild_chips
        for _ in range(3):
            picker.set_candidates(data.get_items())
            qapp.processEvents()
        assert len(_visible_tops(qapp)) == baseline, \
            "候选条重建时出现游离顶层窗口"
    finally:
        picker.hide()
        picker.deleteLater()
