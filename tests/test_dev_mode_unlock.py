"""开发者模式解锁流程的交互测试。

## 为什么要专门测这个

"连点版本号 8 次解锁开发者模式"是个藏在角落的交互，用户反馈"手感差"时
很难定位到底差在哪。实测发现三个具体问题，本文件逐个钉住：

1. **点击热区太小** —— QLabel 默认尺寸贴文字，实测高度只有 13px，
   作为"要点 8 次"的目标太细。现撑到 24px（触控最小点击目标）。
2. **按住拖动会重复计数** —— 原先用 MouseButtonPress 判定，按住拖开
   也被算作点击。改用 MouseButtonRelease + 释放位置判定。
3. **第 8 次点击后主线程卡 ~710ms** —— DevPage 构建（构造 74ms +
   set_theme 174ms + addWidget 76ms）一次性做完。现拆成三帧，
   最大单帧降到 ~200ms。
"""
import time

import pytest

from core.kaomoji_data import KaomojiData
from core.user_kaomoji import UserKaomoji
from core.user_state import UserState
from core.user_triggers import UserTriggers

_CLICK_TARGET = 8


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
def window(qapp, tmp_path):
    from ui.unified_window import UnifiedSettingsWindow

    d = str(tmp_path / "data")
    import os
    os.makedirs(d, exist_ok=True)
    w = UnifiedSettingsWindow(
        KaomojiData(), _config(), UserState(path=os.path.join(d, "s.json")),
        UserKaomoji(path=os.path.join(d, "u.json")),
        UserTriggers(path=os.path.join(d, "t.json")),
        save_config=lambda c: None, open_log=lambda: None, plugins=None)
    w.show()
    w._set_page("about")
    qapp.processEvents()
    try:
        yield w
    finally:
        w.hide()
        w.deleteLater()


# ---------------------------------------------------------------- 热区
def test_version_label_has_usable_click_target(window):
    """版本号的点击热区不能只是一行文字的高度（原 13px）。"""
    label = window.about_page._ver_label
    assert label.height() >= 24, \
        "点击热区过小（%d px），连点 8 次很难瞄准" % label.height()
    assert label.minimumHeight() >= 24


def test_version_label_has_tooltip(window):
    """应给用户一点提示，否则没人知道这里能点。"""
    assert window.about_page._ver_label.toolTip()


# ---------------------------------------------------------------- 计数
def test_click_counts_down_and_unlocks(window, qapp):
    """连点 8 次后应解锁，中途提示剩余次数。"""
    ab = window.about_page
    for i in range(_CLICK_TARGET - 1):
        ab._on_version_clicked()
        assert "再点击" in ab._dev_hint.text()
    assert window.config.get("developer_mode") is not True

    ab._on_version_clicked()          # 第 8 次
    assert window.config.get("developer_mode") is True
    assert ab._dev_badge.isVisible() is True


def test_click_timeout_resets_counter(window, qapp):
    """超过 6 秒没点够就重来，避免误触累积。"""
    ab = window.about_page
    ab._on_version_clicked()
    ab._on_version_clicked()
    assert ab._click_count == 2
    ab._reset_clicks()
    assert ab._click_count == 0


# ---------------------------------------------------------------- 不卡顿
def test_dev_page_build_is_split_across_frames(window, qapp):
    """DevPage 构建必须**分帧**完成，不能一次性做完阻塞主线程。

    ⚠ 为什么不用"耗时 < N ms"来断言：实测同一份代码在 standalone 脚本里
    单帧 105~180ms，在全量测试（CPU 更忙）里能到 460ms。**绝对时间阈值
    在不同负载下必然误报**，这种测试只会变成噪音。

    改为断言**结构性事实**：构建过程分三步（构造 → 套主题 → 挂载），
    每步之间投递 `singleShot(0)`。用"挂载是否发生在后续帧"来验证拆分
    确实存在——一旦有人合并回一个函数，这里立刻失败。

    时序（实测）：
        点击第 8 次         → enable_dev_tab() 只加导航项 + 投递 singleShot
        第 1 次 processEvents → finish_enable_dev_tab（构造 DevPage）
        第 2 次 processEvents → _finish_dev_tab_theme（套主题）
        第 3 次 processEvents → _finish_dev_tab_mount（挂载 + 切页）
    """
    ab = window.about_page
    for _ in range(_CLICK_TARGET - 1):
        ab._on_version_clicked()
    ab._on_version_clicked()          # 第 8 次，只投递任务，不同步构建

    # 点击当帧：导航项已加入（即时反馈），但页面尚未构建
    assert "developer" not in window._nav.pages, \
        "第 8 次点击不应同步完成整个构建流程"

    # 第 1 帧：构造出 DevPage 对象，但还没挂进内容栈
    qapp.processEvents()
    assert getattr(window, "dev_page", None) is not None, \
        "第 1 帧后应已构造 DevPage"
    assert "developer" not in window._nav.pages, \
        "构造与挂载必须在不同帧 —— 合并成一步会阻塞主线程"

    # 后续帧：完成套主题与挂载
    for _ in range(4):
        qapp.processEvents()
    assert "developer" in window._nav.pages


def test_unlock_completes_within_reasonable_time(window, qapp):
    """解锁流程整体（含三帧构建）应在合理时间内完成。

    这里给的是**宽松**上限（3 秒），只防"卡死/死循环"这类灾难性问题，
    不作为性能基准——性能由上面的结构断言守护。
    """
    ab = window.about_page
    for _ in range(_CLICK_TARGET - 1):
        ab._on_version_clicked()

    t0 = time.perf_counter()
    ab._on_version_clicked()
    for _ in range(6):
        qapp.processEvents()
    elapsed = (time.perf_counter() - t0) * 1000

    assert "developer" in window._nav.pages
    assert elapsed < 3000, "解锁耗时 %.0f ms，可能卡死" % elapsed


def test_dev_tab_mounts_after_a_few_frames(window, qapp):
    """分帧不能把页面弄丢——多跑几轮事件循环必须挂上。"""
    window._nav.enable_dev_tab()
    for _ in range(6):
        qapp.processEvents()
    assert "developer" in window._nav.pages
    assert window.dev_page is not None


# ---------------------------------------------------------------- 事件语义
def test_mouse_press_does_not_count(window, qapp):
    """按下不该计数——只有释放才算"点了一下"。

    这是个真实手感问题：原先用 MouseButtonPress 判定，按住后拖开、
    或按住久一点松手都会被算成多次点击。
    """
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent

    ab = window.about_page
    label = ab._ver_label
    assert ab._click_count == 0

    press = QMouseEvent(QEvent.MouseButtonPress, QPointF(5, 5),
                        QPointF(5, 5), Qt.LeftButton, Qt.LeftButton,
                        Qt.NoModifier)
    ab.eventFilter(label, press)
    assert ab._click_count == 0, "按下不该计数"

    release = QMouseEvent(QEvent.MouseButtonRelease, QPointF(5, 5),
                          QPointF(5, 5), Qt.LeftButton, Qt.NoButton,
                          Qt.NoModifier)
    ab.eventFilter(label, release)
    assert ab._click_count == 1, "释放应计数一次"


def test_release_outside_label_does_not_count(window, qapp):
    """按下后拖出标签再松手 = 取消，不算点击。"""
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent

    ab = window.about_page
    label = ab._ver_label
    far = QPointF(label.width() + 100, label.height() + 100)
    release = QMouseEvent(QEvent.MouseButtonRelease, far, far,
                          Qt.LeftButton, Qt.NoButton, Qt.NoModifier)
    ab.eventFilter(label, release)
    assert ab._click_count == 0


# ---------------------------------------------------------------- 反馈
def test_rapid_clicks_all_count(window, qapp):
    """连续快速点击必须**每次都计数**，不能"点一下停一下"。

    ⚠ 这是真实用户反馈的 bug。根因：`_flash_version` 每次点击都调
    `setStyleSheet`，会让标签发生 `StyleChange + FontChange×2 + PaletteChange
    + Paint` 一整套重算，**打断 Qt 的鼠标事件序列**，后续 press/release
    收不到 —— 表现为必须停顿一下才能再点。

    现在点击时不触碰样式/字体/几何（只 `update()` 安排重绘）。

    本用例用真实鼠标事件（QTest.mouseClick）而非直接调 `_on_version_clicked`，
    因为要验证的正是"事件能不能到达"。
    """
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    ab = window.about_page
    label = ab._ver_label

    for i in range(_CLICK_TARGET):
        QTest.mouseClick(label, Qt.LeftButton)
        qapp.processEvents()
        if i < _CLICK_TARGET - 1:
            assert ab._click_count == i + 1, (
                "第 %d 次连点后计数是 %d（期望 %d）—— 连点失效了"
                % (i + 1, ab._click_count, i + 1)
            )

    assert window.config.get("developer_mode") is True, \
        "连点 8 次后应解锁开发者模式"


def test_clicking_does_not_trigger_style_recalc(window, qapp):
    """点击时不得触发 StyleChange / FontChange（那会打断鼠标事件链）。"""
    from PySide6.QtCore import QObject, Qt
    from PySide6.QtTest import QTest

    ab = window.about_page
    label = ab._ver_label

    seen = []

    class _Spy(QObject):
        def eventFilter(self, obj, ev):
            if obj is label:
                seen.append(ev.type().name)
            return False

    spy = _Spy()
    label.installEventFilter(spy)
    try:
        QTest.mouseClick(label, Qt.LeftButton)
        qapp.processEvents()
        qapp.processEvents()
    finally:
        label.removeEventFilter(spy)

    assert "StyleChange" not in seen, \
        "点击触发了 StyleChange —— 会打断鼠标事件序列（连点失灵）"
    assert "FontChange" not in seen, \
        "点击触发了 FontChange —— 会打断鼠标事件序列（连点失灵）"
