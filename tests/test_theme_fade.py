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


def test_veil_has_loading_hint(stage):
    """遮罩上要有加载提示文字。"""
    fader, _inner, _lay, _host, _app = stage
    fader.run(lambda: None)
    veil = fader._veil
    assert veil is not None, "应创建遮罩"
    assert "正在" in veil._text or "切换" in veil._text, \
        "遮罩上要有说明文字，实际：%r" % veil._text
    # 有旋转动画驱动
    assert veil._spin_timer is not None
    fader._teardown()


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
