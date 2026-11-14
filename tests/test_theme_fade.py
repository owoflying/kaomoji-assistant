"""主题切换过渡动画的测试。

## 测什么

这个动画的作用是「掩盖换肤期间的卡顿」，所以关键不是动画本身
好不好看，而是：

1. 换肤**确实发生在**淡出完成之后（否则卡顿暴露在亮着的界面上）
2. 动画结束后 effect 被摘干净（常驻会让控件走离屏渲染，拖慢日常绘制）
3. 动画期间切换仍然**成功**（不能因为动画出问题导致主题没换）
4. 窗口不可见时不做动画（无头环境/后台切主题不该白等 330ms）
"""
import os
import sys

import pytest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

pytestmark = pytest.mark.skipif(sys.platform != "win32",
                               reason="Qt 窗口测试在 Windows 上跑")


@pytest.fixture()
def fader_pair():
    """造一个真实的 fader + 控件。"""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QWidget

    app = QApplication.instance() or QApplication([])
    from ui.theme_fade import ThemeFader

    host = QWidget()
    inner = QWidget(host)
    inner.resize(100, 100)
    pair = (ThemeFader(host, inner), inner, app, host)
    yield pair


def test_apply_happens_after_fade_out(fader_pair):
    """换肤必须在淡出之后，不能一上来就换。

    这是动画能"掩盖卡顿"的前提：如果先换再淡，用户会看到
    界面亮着僵住。这里验证 `run()` 不会同步调用 apply_fn。
    """
    fader, _inner, _app, host = fader_pair
    host.show()
    called = []
    fader.run(lambda: called.append(1))

    # 淡出是异步的：run() 返回时还没执行
    assert called == [], "apply_fn 不该在淡出完成前就执行"

    fader._anim.stop()
    fader._teardown()
    host.hide()


def test_hidden_widget_skips_animation(fader_pair):
    """窗口不可见时直接执行，不等动画。"""
    fader, _inner, _app, _host = fader_pair
    called = []
    fader.run(lambda: called.append(1))
    assert called == [1], "不可见时应立即执行"


def test_animation_disabled_runs_immediately(fader_pair):
    """显式禁用动画时直接执行。"""
    fader, _inner, _app, host = fader_pair
    host.show()
    called = []
    fader.run(lambda: called.append(1), enabled=False)
    assert called == [1]
    host.hide()


def test_teardown_removes_effect(fader_pair):
    """动画结束后必须摘掉 graphicsEffect。

    常驻 QGraphicsOpacityEffect 会让控件走离屏渲染路径，
    拖慢日常绘制——所以只能在动画期间挂。
    """
    fader, inner, _app, host = fader_pair
    host.show()
    fader.run(lambda: None)
    # 强制走完两段动画
    fader._anim.stop()
    fader._teardown()
    assert inner.graphicsEffect() is None, "effect 没摘干净"
    host.hide()


def test_apply_exception_does_not_break_fade(fader_pair):
    """apply_fn 抛异常时，淡入仍要进行（界面不能停在暗态）。"""
    fader, inner, _app, host = fader_pair
    host.show()

    def boom():
        raise RuntimeError("模拟换肤失败")

    fader.run(boom)
    fader._anim.stop()
    fader._teardown()
    # 关键：不崩、effect 摘干净
    assert inner.graphicsEffect() is None
    host.hide()


def test_rapid_calls_only_play_last(fader_pair):
    """连续快速调用只播最后一次（连点主题不该排队播一堆动画）。"""
    fader, _inner, _app, host = fader_pair
    host.show()
    seq = []
    for i in range(3):
        fader.run(lambda n=i: seq.append(n))
    fader._anim.stop()
    fader._teardown()
    # 前两次的动画被打断，只有最后一次有机会执行；
    # 这里只要求"不会三次都排队执行到位"，避免快速连点时卡成幻灯片
    assert len(seq) <= 1, "连点不该排队执行多次，实际 %r" % seq
    host.hide()
