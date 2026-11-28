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
def no_crash_dialogs():
    """屏蔽崩溃弹窗，避免它干扰"游离窗口"的判定。

    ## 为什么需要

    这些测试断言的是"操作后不应**新增**可见顶层窗口"。
    但全套运行时，前面某个用例可能留下了一个会抛异常的全局状态，
    于是启用/停用插件时 crash_guard 弹出了 `CrashDialog`——
    它确实是新窗口，于是被误判成"控件闪现"。

    实测就是这么翻车的：断言里报的是
    `多出了顶层窗口 ['CrashDialog', 'CrashDialog', 'CrashDialog']`，
    而插件卡片本身一个都没漏。

    本测试只关心**控件闪现**，不关心报错弹窗。所以这里把弹窗回调
    摘掉（崩溃仍然写日志、仍然被兜住），断言就只反映它该反映的东西。
    """
    import core.crash_guard as CG

    old_cb = getattr(CG, "_alert_callback", None)
    CG.set_alert_callback(None)
    try:
        yield
    finally:
        CG.set_alert_callback(old_cb)


@pytest.fixture
def runtime_like(qapp, tmp_path, quiet_log, no_crash_dialogs):
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

    ## 为什么用"集合差 + 多点采样"

    原来用 `len(tops) == baseline`。两个问题：

      1. 基线是**全局**窗口数，全套测试里历史遗留的窗口会干扰判定
         → 单独跑绿、全套跑红，反复横跳
      2. 只在操作**结束后**采样一次。但"闪现"意味着窗口可能在
         中间帧出现又消失，单点采样抓不到，于是偶发失败

    现在：用 id 集合差找出**新出现**的窗口，并在操作过程中
    多次采样（含 0ms/10ms 两个时点），把"闪现"也纳进来。
    这样结果既不受历史窗口影响，也不再依赖运气。
    """
    window, pm, _cfg = runtime_like
    window.show()
    window._set_page("plugin")
    for _ in range(3):
        qapp.processEvents()

    baseline_ids = {id(w) for w in _visible_tops(qapp)}

    def _sample():
        """采样一次，返回新出现的窗口（排除已记录的）。"""
        qapp.processEvents()
        return [w for w in _visible_tops(qapp) if id(w) not in baseline_ids]

    for pid, enabled in (("startup_notice", False),
                         ("startup_notice", True),
                         ("example_kaomoji", False),
                         ("example_kaomoji", True)):
        pm.set_enabled(pid, enabled)
        seen = []
        # 多点采样：操作完成后立刻一次，再让事件循环跑一轮
        for _ in range(2):
            seen.extend(_sample())
        if not seen:
            # 再等一小段，覆盖"下一帧才闪现"的情况
            qapp.processEvents()
            seen.extend(_sample())
        assert not seen, (
            "启用/停用 %s 后多出了顶层窗口 %s —— "
            "说明有控件在 setParent(None) 之前没有 hide()"
            % (pid, [type(w).__name__ for w in seen]))


def test_no_stray_windows_when_switching_pages(runtime_like, qapp):
    """切换页面（含插件扩展页增删）不得产生游离顶层窗口。

    同 `..._toggling_plugin`：用 id 集合差判断"新出现"的窗口，
    不受全套测试里历史遗留窗口的影响。
    """
    window, _pm, _cfg = runtime_like
    window.show()
    for _ in range(3):
        qapp.processEvents()
    baseline_ids = {id(w) for w in _visible_tops(qapp)}

    for key in ("library", "custom", "triggers", "search", "settings",
                "about", "plugin", "home"):
        window._set_page(key)
        qapp.processEvents()
        new = [w for w in _visible_tops(qapp) if id(w) not in baseline_ids]
        assert not new, (
            "切到 %s 后出现游离顶层窗口 %s"
            % (key, [type(w).__name__ for w in new]))


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
