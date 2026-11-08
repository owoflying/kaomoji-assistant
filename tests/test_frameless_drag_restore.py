"""无边框窗口「最大化后拖拽还原」的回归测试。

## Bug 现场

最大化后按住标题栏往下拖，窗口不会恢复成普通大小——拖不动。

**根因**：原生窗口由 `DefWindowProc` 处理 `WM_NCLBUTTONDOWN + HTCAPTION` 时
会自动"还原并拖动"，但无边框窗口在 `WM_NCHITTEST` 里自己返回 `HTCAPTION`，
等于接管了标题栏，系统就不再替我们做这件事。

**修复**：在 `nativeEvent` 里拦截 `WM_NCLBUTTONDOWN`，最大化时手动
还原（清 `WS_MAXIMIZE` → `showNormal` → 恢复上次普通几何 → 摆到鼠标下方），
再发 `SC_MOVE` 进入系统拖动循环。

## 测试策略

`_restore_from_maximized_drag(..., enter_drag_loop=False)` 不进入拖动循环
（那个 `SendMessageW` 会阻塞到用户松手，自动化测试会挂住）。
我们用这个开关验证"还原"这一步的全部逻辑。

真实拖动手感只能在真机验证——本文件覆盖可自动化的部分。
"""
import ctypes
from ctypes.wintypes import MSG

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
def window(qapp, tmp_path):
    import os

    from ui.unified_window import UnifiedSettingsWindow

    d = str(tmp_path / "data")
    os.makedirs(d, exist_ok=True)
    w = UnifiedSettingsWindow(
        KaomojiData(), _config(), UserState(path=os.path.join(d, "s.json")),
        UserKaomoji(path=os.path.join(d, "u.json")),
        UserTriggers(path=os.path.join(d, "t.json")),
        save_config=lambda c: None, open_log=lambda: None, plugins=None)
    w.resize(1024, 720)
    w.show()
    qapp.processEvents()
    try:
        yield w
    finally:
        w.hide()
        w.deleteLater()


def _lparam(x, y):
    """把屏幕坐标打包成 WM_NCLBUTTONDOWN 的 lParam 格式。"""
    return ((y & 0xFFFF) << 16) | (x & 0xFFFF)


def test_wm_nclbuttondown_constant_exists():
    """常量必须真实存在且值正确（ctypes 常量凭记忆写会静默失效）。"""
    from ui.frameless_shell import WM_NCLBUTTONDOWN

    assert WM_NCLBUTTONDOWN == 0x00A1     # WinUser.h 中的真实值


def test_restore_from_maximized_returns_to_normal_size(window, qapp):
    """最大化状态下拖动 → 窗口恢复到上次的普通尺寸。"""
    normal = window.geometry()
    window._toggle_maximize()
    qapp.processEvents()
    assert window.isMaximized()

    window._restore_from_maximized_drag(_lparam(700, 20), enter_drag_loop=False)
    qapp.processEvents()

    assert window.isMaximized() is False, "拖动后应退出最大化"
    g = window.geometry()
    assert g.width() == normal.width() and g.height() == normal.height(), \
        "还原尺寸应等于上次的普通几何，实际 %dx%d（期望 %dx%d）" % (
            g.width(), g.height(), normal.width(), normal.height())


def test_restore_keeps_titlebar_on_screen(window, qapp):
    """还原后标题栏必须完整可见（不能跑到屏幕上方）。"""
    window._toggle_maximize()
    qapp.processEvents()

    avail = window.screen().availableGeometry()
    # 最大化时鼠标通常贴在屏幕最顶端
    window._restore_from_maximized_drag(_lparam(700, 2), enter_drag_loop=False)
    qapp.processEvents()

    g = window.geometry()
    assert g.y() >= avail.y(), \
        "窗口顶部跑到工作区外（y=%d < %d），标题栏会被吃掉" % (g.y(), avail.y())


def test_restore_works_at_different_horizontal_positions(window, qapp):
    """从屏幕左右两侧拖动都要能正确还原（横向比例映射）。

    ⚠ 不拿"初始 geometry"当基准：offscreen 平台的工作区尺寸与真实屏幕不同，
    窗口最小尺寸（900）可能比期望还大，直接比较会误判。这里比较的是
    **每一次还原后尺寸是否一致**（三次结果应相同），以及顶部不越界。
    """
    avail = window.screen().availableGeometry()
    sizes = []

    for x in (avail.x() + 5, avail.x() + avail.width() // 2,
              avail.x() + avail.width() - 5):
        if not window.isMaximized():
            window.showMaximized()
            qapp.processEvents()
        window._restore_from_maximized_drag(_lparam(x, 5), enter_drag_loop=False)
        qapp.processEvents()

        assert window.isMaximized() is False
        g = window.geometry()
        assert g.y() >= avail.y(), "x=%d 位置还原后顶部越界（y=%d）" % (x, g.y())
        sizes.append((g.width(), g.height()))

        window.showNormal()
        qapp.processEvents()

    assert len(set(sizes)) == 1, \
        "不同横向位置还原出的尺寸不一致：%s（应全部相同）" % sizes


def test_restore_falls_back_when_no_normal_geometry(window, qapp):
    """没有记录普通几何时（如启动即最大化）要用兜底尺寸，不能还原成 0 尺寸。"""
    window._normal_geometry = None
    window.showMaximized()
    qapp.processEvents()
    assert window.isMaximized()

    window._restore_from_maximized_drag(_lparam(700, 20), enter_drag_loop=False)
    qapp.processEvents()

    g = window.geometry()
    assert window.isMaximized() is False
    assert g.width() > 200 and g.height() > 200, \
        "兜底尺寸异常：%dx%d" % (g.width(), g.height())


def test_fallback_size_respects_minimum(window, qapp):
    """兜底尺寸不得低于窗口最小尺寸。

    回归用例：原先兜底取"工作区 × 0.75"，若这比 minimumHeight 还小，
    窗口会被最小尺寸撑开，还原结果与预期不符（实测 728×0.75≈546 被撑成 640）。
    """
    window._normal_geometry = None
    window.showMaximized()
    qapp.processEvents()

    window._restore_from_maximized_drag(_lparam(700, 20), enter_drag_loop=False)
    qapp.processEvents()

    g = window.geometry()
    assert g.width() >= window.minimumWidth()
    assert g.height() >= window.minimumHeight()


def test_normal_geometry_recorded_on_resize(window, qapp):
    """普通态下改尺寸必须记录为还原基准。

    回归用例：原先只在 `_toggle_maximize()` 里记录几何。若用户走
    "双击标题栏 / 系统菜单 / showMaximized()" 等其它路径进入最大化，
    `_normal_geometry` 仍是 None，只能靠兜底尺寸 → 还原结果不对。
    """
    window.showNormal()
    qapp.processEvents()
    window.resize(1000, 664)
    qapp.processEvents()

    assert window._normal_geometry is not None, "普通态 resize 后应记录几何"
    assert window._normal_geometry.width() == window.width()
    assert window._normal_geometry.height() == window.height()


def test_restore_works_via_direct_showmaximized(window, qapp):
    """不经过 `_toggle_maximize()` 直接最大化，也要能还原到正确尺寸。"""
    window.showNormal()
    qapp.processEvents()
    window.resize(1000, 664)
    qapp.processEvents()
    expected = window.geometry()

    window.showMaximized()          # 模拟双击标题栏 / 系统菜单
    qapp.processEvents()
    assert window.isMaximized()

    window._restore_from_maximized_drag(_lparam(700, 20), enter_drag_loop=False)
    qapp.processEvents()

    g = window.geometry()
    assert window.isMaximized() is False
    assert g.width() == expected.width() and g.height() == expected.height(), \
        "直接最大化后还原尺寸不对：%dx%d（期望 %dx%d）" % (
            g.width(), g.height(), expected.width(), expected.height())


def test_chrome_state_applied_before_geometry(window, qapp):
    """还原时必须先把窗口外观切回普通态，再设置几何。

    反过来的话，setGeometry 发生时根布局边距仍是最大化态的 0，
    窗口内容按错误边距重算，尺寸会被压小（实测 720 被压成 640）。
    """
    window.showNormal()
    qapp.processEvents()
    window.resize(1000, 664)
    qapp.processEvents()
    expected = window.geometry()

    window.showMaximized()
    qapp.processEvents()
    window._restore_from_maximized_drag(_lparam(700, 20), enter_drag_loop=False)
    qapp.processEvents()

    # 边距应恢复为普通态的 _PAD（非 0）
    m = window._root_layout.contentsMargins()
    assert m.left() == window._PAD, "还原后根布局边距未恢复（仍为最大化态的 0）"
    assert window.geometry().height() == expected.height()


def test_nclbuttondown_only_restores_when_maximized(window, qapp):
    """`WM_NCLBUTTONDOWN` 的还原分支**只在最大化时**触发。

    普通态拖标题栏应交给系统正常移动窗口，不能被我们的还原逻辑劫持
    （否则普通态一拖窗口就跳位置）。
    """
    from ui.frameless_shell import FramelessShell

    calls = []
    orig = FramelessShell._restore_from_maximized_drag

    def _spy(self, lparam, enter_drag_loop=True):
        calls.append(lparam)

    FramelessShell._restore_from_maximized_drag = _spy
    try:
        # ---- 普通态：不应调用 ----
        assert window.isMaximized() is False
        before = window.geometry()
        _send_nclbuttondown(window, 700, 20)
        qapp.processEvents()
        assert calls == [], "普通态不该触发还原逻辑"
        assert window.geometry() == before, "普通态窗口几何不该被改动"

        # ---- 最大化态：应调用一次 ----
        window.showMaximized()
        qapp.processEvents()
        assert window.isMaximized() is True
        _send_nclbuttondown(window, 700, 20)
        assert len(calls) == 1, "最大化态应触发还原逻辑"
    finally:
        FramelessShell._restore_from_maximized_drag = orig
        window.showNormal()
        qapp.processEvents()


def _send_nclbuttondown(window, x, y):
    """构造一条真实的 WM_NCLBUTTONDOWN 消息，喂给窗口的 nativeEvent。

    直接调 `nativeEvent` 而不是 `SendMessageW`：后者会同步走完整消息处理，
    在测试里容易挂住。这里验证的是"分支是否被走到"。

    ⚠ 伪造的 MSG 地址不能传给 `QWidget.nativeEvent` 基类（它会去解读
    真实的 Qt 消息结构，对伪造指针报 ValueError）。所以临时把基类调用
    换成 no-op —— 我们只关心自己的分支逻辑。
    """
    from ui.frameless_shell import HTCAPTION, WM_NCLBUTTONDOWN
    from PySide6.QtWidgets import QWidget

    msg = MSG()
    msg.hwnd = int(window.winId())
    msg.message = WM_NCLBUTTONDOWN
    msg.wParam = HTCAPTION
    msg.lParam = _lparam(x, y)

    orig = QWidget.nativeEvent

    def _noop(self, *a, **k):
        return False, 0

    QWidget.nativeEvent = _noop
    try:
        # eventType 必须是 bytes（QByteArray 兼容）
        window.nativeEvent(b"windows_generic_MSG", int(ctypes.addressof(msg)))
    finally:
        QWidget.nativeEvent = orig
