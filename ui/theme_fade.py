"""窗口整体的透明度动画：用于主题切换时的淡入淡出过渡。

## 为什么需要

切换主题时，Qt 会瞬间把所有控件重绘成新配色。那一帧在视觉上
是"啪"地跳变，看起来像卡了一下——其实不是卡，是**突变**。
给它一点过渡就自然得多。

## 一个必须承认的限制

换主题这一步是**同步阻塞**的：`setStyleSheet` 会让 Qt 重新计算
几百个控件的样式，实测 1~2.5 秒，期间事件循环完全不转，
任何动画都播不动。

所以动画能做的只有两件事：

    1. 换主题**之前**飞快地淡出（这 110ms 是流畅的）；
    2. 换主题**之后**柔和地淡入（这 220ms 也是流畅的）。

中间那段卡顿无法消除，只能让它发生在"看不清"的时候。

## 为什么淡到 0.12 而不是 0.5

最初淡到 0.5（"暗一下"），实测反而更怪：界面会卡在半透明的
灰暗状态一动不动一到两秒，像程序没响应。

淡到很低的值时，那段时间界面几乎是透明的，用户感知到的是
"柔和地隐了一下又回来"，比"卡住"好处理。

⚠ 不能真的淡到 0：那会让窗口完全消失、露出桌面，
在深色主题下像闪了一下白底。

## 为什么用 QGraphicsOpacityEffect 而不是 setWindowOpacity

* `setWindowOpacity` 动的是整个系统窗口（含边框阴影），在
  无边框+亚克力窗口上会和 DWM 的合成打架，出现闪烁；
* `QGraphicsOpacityEffect` 只影响控件树的绘制结果，窗口的
  圆角/阴影仍由自绘逻辑负责，不打架。

⚠ effect 只在动画期间挂上，播完立刻摘掉：`QGraphicsOpacityEffect`
会让控件走离屏渲染路径，常驻会拖慢日常绘制。
"""

from PySide6.QtCore import (
    QEasingCurve, QObject, QPropertyAnimation, QTimer,
)
from PySide6.QtWidgets import QGraphicsOpacityEffect

#: 淡出到的最低不透明度。见模块文档"为什么淡到 0.12"。
_DIM = 0.12
#: 淡出时长：用户刚点了切换，要尽快有反应，所以快。
_FADE_OUT_MS = 110
#: 淡入时长：收尾要柔和，可以慢一点。
_FADE_IN_MS = 220


class ThemeFader(QObject):
    """给一个控件做「淡出 → 换主题 → 淡入」的过渡。

    用法：

        fader = ThemeFader(self, self.central)
        fader.run(lambda: self._apply_theme())   # 换主题的动作放中间

    连续快速调用会取消上一次动画，只播最后一次——用户连点
    主题下拉框时不会排队播一堆动画。
    """

    def __init__(self, host, widget):
        super().__init__(host)
        self._widget = widget
        self._effect = None
        self._anim = None
        self._pending = None

    # ------------------------------------------------------------ 内部
    def _ensure_effect(self):
        if self._effect is None:
            self._effect = QGraphicsOpacityEffect(self._widget)
            self._effect.setOpacity(1.0)
        return self._effect

    def _teardown(self):
        """摘掉 effect，恢复正常绘制路径。"""
        if self._effect is not None:
            try:
                self._widget.setGraphicsEffect(None)
            except RuntimeError:
                pass
            self._effect = None
        self._anim = None

    def _stop_anim(self):
        if self._anim is not None:
            try:
                self._anim.stop()
            except RuntimeError:
                pass
            self._anim = None
        # 上一次动画被打断时 effect 可能还挂在控件上，恢复不透明度
        if self._effect is not None:
            try:
                self._effect.setOpacity(1.0)
            except RuntimeError:
                pass

    # ------------------------------------------------------------ 对外
    def run(self, apply_fn, enabled=True):
        """执行一次过渡：淡出 → apply_fn() → 淡入。"""
        if not enabled or not self._widget.isVisible():
            # 窗口不可见（后台切主题 / 无头测试）就没必要做动画
            apply_fn()
            return

        self._stop_anim()
        self._cancel_pending()

        self._widget.setGraphicsEffect(self._ensure_effect())

        fade_out = QPropertyAnimation(self._effect, b"opacity", self)
        fade_out.setDuration(_FADE_OUT_MS)
        fade_out.setStartValue(1.0)
        fade_out.setEndValue(_DIM)
        fade_out.setEasingCurve(QEasingCurve.InOutQuad)

        def _on_dims():
            # 主题在这一刻切换：跳变被藏在最暗处
            try:
                apply_fn()
            except Exception:
                import traceback
                traceback.print_exc()
            finally:
                self._start_fade_in()

        fade_out.finished.connect(_on_dims)
        self._anim = fade_out
        fade_out.start()

    def _start_fade_in(self):
        effect = self._effect
        if effect is None:
            return
        fade_in = QPropertyAnimation(effect, b"opacity", self)
        fade_in.setDuration(_FADE_IN_MS)
        fade_in.setStartValue(_DIM)
        fade_in.setEndValue(1.0)
        fade_in.setEasingCurve(QEasingCurve.OutCubic)
        fade_in.finished.connect(self._teardown)
        self._anim = fade_in
        fade_in.start()

    def _cancel_pending(self):
        if self._pending is not None:
            try:
                self._pending.stop()
            except RuntimeError:
                pass
            self._pending = None

    def schedule(self, apply_fn, delay_ms=0):
        """延迟执行过渡（避免在信号处理中间改控件树）。"""
        self._cancel_pending()
        timer = QTimer(self)
        timer.setSingleShot(True)

        def _fire():
            try:
                self.run(apply_fn)
            finally:
                if self._pending is timer:
                    self._pending = None

        timer.timeout.connect(_fire)
        self._pending = timer
        timer.start(delay_ms)
