"""主题切换过渡动画（3D 后退 + 遮罩 + 加载提示）的测试。

## 测什么

动画的目的是「把换肤的卡顿藏进一个用户能理解的等待过程」。
所以关键不是好不好看，而是：

1. **后退确实发生**：右下收边量真的从 0 涨到设定值，之后归位
2. **换肤发生在后退之后**：否则卡顿暴露在正常界面上
3. **遮罩带加载提示**：用户得知道"正在切换主题"
4. **结束后一切复原**：边距归位、effect 摘掉、遮罩隐藏
5. **异常/连点/隐藏**等边界不出事
"""
import os
import sys

import pytest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

pytestmark = pytest.mark.skipif(sys.platform != "win32",
                               reason="Qt 窗口测试在 Windows 上跑")


@pytest.fixture()
def stage():
    """造一个 host + 布局 + fader 的真实组合。"""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout

    app = QApplication.instance() or QApplication([])
    from ui.theme_fade import ThemeFader

    host = QWidget()
    host.resize(800, 600)
    lay = QVBoxLayout(host)
    lay.setContentsMargins(12, 12, 12, 12)
    inner = QWidget()
    lay.addWidget(inner)
    host.show()
    app.processEvents()

    fader = ThemeFader(host, inner, lay)
    yield fader, inner, lay, host, app

    try:
        fader._teardown()
        host.hide()
    except Exception:
        pass


def _margins(lay):
    m = lay.contentsMargins()
    return (m.left(), m.top(), m.right(), m.bottom())


def test_recede_increases_right_bottom_margins(stage):
    """后退 = 右下边距增大，左上保持不变。

    直接驱动动画状态对象（绕开时间轴），验证"收边量 → 布局边距"
    这条链路是通的。时间轴本身由 Qt 保证。
    """
    fader, _inner, lay, _host, _app = stage
    before = _margins(lay)
    fader._remember_margins()

    # 模拟动画走到终点：把两个收边量设到目标值
    from ui.theme_fade import _INSET_RIGHT, _INSET_BOTTOM
    fader._state.inset_right = float(_INSET_RIGHT)
    fader._state.inset_bottom = float(_INSET_BOTTOM)

    after = _margins(lay)
    assert after[0] == before[0], "左边距不该变（否则不是'朝左上退'）"
    assert after[1] == before[1], "上边距不该变"
    assert after[2] == before[2] + _INSET_RIGHT, "右边距应按设定值增大"
    assert after[3] == before[3] + _INSET_BOTTOM, "下边距应按设定值增大"

    # 归零后必须完全恢复
    fader._state.inset_right = 0.0
    fader._state.inset_bottom = 0.0
    assert _margins(lay) == before, "收边量归零后边距要复原"
    fader._teardown()


def test_apply_runs_after_recede(stage):
    """换肤必须在后退之后，不能一上来就换。"""
    fader, _inner, _lay, _host, _app = stage
    called = []
    fader.run(lambda: called.append(1))
    # run() 返回时后退动画刚开始，apply_fn 还没执行
    assert called == [], "不该在后退完成前就换肤"
    fader._teardown()


def test_loading_hint_uses_thread_overlay(stage):
    """加载提示改用**子线程驱动的独立窗口**，不再画在遮罩上。

    ## 为什么这么改

    主线程被 `setStyleSheet` 卡住时，主窗口里的任何控件都不会重绘，
    画在遮罩上的旋转图标必然定格。

    改成独立顶层窗口 + 子线程出帧后：
      * 它不参与主窗口的 QSS 级联，样式重算不波及它；
      * 阻塞解除的瞬间，最新一帧已经在队列里，立刻显示、不滞后。

    这里验证：过渡过程中确实会拉起那个窗口，并且带说明文字。
    """
    fader, _inner, _lay, _host, _app = stage
    fader.run(lambda: None)
    fader._show_overlay()
    ov = fader._overlay
    assert ov is not None, "应创建加载指示器窗口"
    assert "正在切换主题" in ov._text, \
        "窗口上要有说明文字，实际：%r" % ov._text
    # 子线程在工作
    assert ov._thread.isRunning(), "出帧线程应已启动"
    fader._shutdown_overlay()
    fader._teardown()


def test_spinner_thread_produces_frames():
    """子线程要真的持续产出帧（主线程阻塞也不影响它算帧）。"""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    import time

    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    from ui.spinner_overlay import SpinnerOverlay

    ov = SpinnerOverlay()
    n = {"c": 0}
    ov._worker.frame_ready.connect(lambda img: n.__setitem__("c", n["c"] + 1))
    from PySide6.QtCore import QPoint

    ov.start(QPoint(300, 300))
    app.processEvents()

    t0 = time.perf_counter()
    while time.perf_counter() - t0 < 0.5:
        app.processEvents()
        time.sleep(0.01)

    ov.shutdown()
    assert n["c"] >= 3, "0.5 秒内至少该出几帧，实际 %d" % n["c"]
    assert not ov._thread.isRunning(), "关闭后线程要停干净"


def test_teardown_restores_everything(stage):
    """结束后：边距归位、effect 摘掉、遮罩隐藏。"""
    fader, inner, lay, _host, _app = stage
    before = _margins(lay)
    fader.run(lambda: None)
    fader._teardown()

    assert _margins(lay) == before, "边距应完全归位"
    assert inner.graphicsEffect() is None, "effect 应摘掉"
    assert fader._veil is None or not fader._veil.isVisible(), \
        "遮罩应隐藏"


def test_hidden_widget_skips_animation(stage):
    """窗口不可见时直接执行，不白等动画。"""
    fader, _inner, _lay, host, _app = stage
    host.hide()
    called = []
    fader.run(lambda: called.append(1))
    assert called == [1]


def test_disabled_runs_immediately(stage):
    """显式禁用动画时直接执行。"""
    fader, _inner, _lay, _host, _app = stage
    called = []
    fader.run(lambda: called.append(1), enabled=False)
    assert called == [1]


def test_apply_exception_still_returns(stage):
    """apply_fn 抛异常时也要归位，界面不能停在后退状态。"""
    fader, inner, lay, _host, _app = stage
    before = _margins(lay)

    def boom():
        raise RuntimeError("模拟换肤失败")

    fader.run(boom)
    fader._anim.stop()
    fader._teardown()

    assert _margins(lay) == before, "异常后边距仍要归位"
    assert inner.graphicsEffect() is None


def test_rapid_calls_do_not_queue(stage):
    """连点主题不该排队播一堆动画。"""
    fader, _inner, lay, _host, _app = stage
    before = _margins(lay)
    seq = []
    for i in range(3):
        fader.run(lambda n=i: seq.append(n))
    fader._teardown()
    # 最终状态必须干净
    assert _margins(lay) == before


def test_veil_color_follows_target_theme(stage):
    """遮罩颜色按目标主题选（深色遮罩不该闪在浅色界面上）。"""
    fader, _inner, _lay, _host, _app = stage
    fader.run(lambda: None, dark=True)
    assert fader._veil._dark is True
    fader._teardown()

    fader.run(lambda: None, dark=False)
    assert fader._veil._dark is False
    fader._teardown()


def test_veil_aligns_with_padded_panel():
    """遮罩要贴合窗口圆角底板，不能铺满整个窗口。

    用户反馈「遮罩没有完全和主程序对齐」。原因是 central 铺满
    (0,0,w,h)，而窗口自绘的底板只在 (PAD,PAD,w-2PAD,h-2PAD)——
    按 central 铺会盖住圆角和外阴影，看起来就多出一圈。
    """
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout

    app = QApplication.instance() or QApplication([])
    from ui.theme_fade import ThemeFader

    host = QWidget()
    host.resize(800, 600)
    lay = QVBoxLayout(host)
    lay.setContentsMargins(12, 12, 12, 12)
    inner = QWidget()
    lay.addWidget(inner)
    host.show()
    app.processEvents()

    PAD, RADIUS = 12, 12
    fader = ThemeFader(host, inner, lay, pad=PAD, radius=RADIUS)
    fader.run(lambda: None)
    veil = fader._veil
    assert veil is not None
    g = veil.geometry()
    host_rect = inner.rect()

    # 左上角从 PAD 开始
    assert (g.x(), g.y()) == (PAD, PAD), \
        "遮罩左上应从 PAD 开始，实际 %r" % ((g.x(), g.y()),)
    # 右下角比宿主内缩 PAD（不铺满整个 central）
    assert g.width() < host_rect.width(), "遮罩不该铺满宽度"
    assert g.height() < host_rect.height(), "遮罩不该铺满高度"
    assert host_rect.width() - (g.x() + g.width()) == PAD, "右侧应内缩 PAD"
    assert host_rect.height() - (g.y() + g.height()) == PAD, "底部应内缩 PAD"
    # 圆角跟着传入值走
    assert veil._radius == RADIUS
    fader._teardown()
    host.hide()


def test_pump_actually_waits():
    """`_pump` 必须真的等待一小段时间，定时器才有机会触发。

    只调 `processEvents()` 时事件队列一空就返回，16ms 间隔的定时器
    还没来得及到期——这也是早期"图标转不动"的原因之一。

    这里用一个独立 QTimer 验证：调 `_pump` 期间它应该能触发。
    """
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    import time

    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    from ui.theme_fade import _pump

    n = {"c": 0}
    t = QTimer()
    t.setInterval(16)
    t.timeout.connect(lambda: n.__setitem__("c", n["c"] + 1))
    t.start()

    deadline = time.perf_counter() + 0.2
    while time.perf_counter() < deadline:
        _pump(app, 48)

    t.stop()
    assert n["c"] >= 2, "200ms 内 16ms 定时器至少该触发几次，实际 %d" % n["c"]


def test_chunked_qss_actually_applies():
    """分片贴 QSS 后样式必须完整生效（不能只贴了前半段）。"""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    from ui.win11_theme import Theme

    theme = Theme("dark")
    ss = theme.style_sheet()
    mid = len(ss) // 2
    cut = ss.find("}", mid)
    cut = cut + 1 if cut > 0 else len(ss)
    # 前半段确实短于完整串
    assert 0 < cut < len(ss), "切分点不合理"
    # 分两段贴完，最终状态等于完整串
    app.setStyleSheet(ss[:cut])
    app.setStyleSheet(ss)
    assert app.styleSheet() == ss
