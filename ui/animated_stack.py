"""带平滑过渡的 QStackedWidget 替代：解决原「瞬间切换 + 从 0 淡入」造成的闪烁/跳变。

切换时：
  * 新页置顶并做「上浮 + 淡入」（从下方 10px 上浮到原位，透明度 0→1）；
  * 旧页保持在底层可见，避免中途露出纯色底造成的闪烁；
  * 全程不把控件移到窗口外，故不会越界到侧边栏/窗口之外，无视觉错位。
动画时长与缓动曲线可调，默认 260ms、OutCubic，明显可感知且顺滑。
"""
from PySide6.QtWidgets import QStackedWidget, QGraphicsOpacityEffect
from PySide6.QtCore import QAbstractAnimation, QPropertyAnimation, QEasingCurve, QPoint


class AnimatedStackedWidget(QStackedWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._busy = False

    def slide_to(self, widget, duration=260, rise=10):
        """切换到指定 widget，带「上浮 + 淡入」过渡；无动画时直接 setCurrentWidget。"""
        if widget is None:
            return
        cur = self.currentWidget()
        if widget is cur or self._busy:
            return
        self._busy = True

        # 让目标页成为当前页（置顶显示）并锁定到正常位置
        self.setCurrentWidget(widget)
        target_geo = widget.geometry()
        widget.setGraphicsEffect(None)

        # 旧页保持在底层可见，弥补切换瞬间的背景空洞（防闪烁）
        if cur is not None and cur is not widget:
            cur.setGeometry(target_geo)
            cur.show()
            cur.lower()  # 置于新页之下

        # 新页：从下方 rise 像素处上浮 + 淡入
        eff = QGraphicsOpacityEffect(widget)
        eff.setOpacity(0.0)
        widget.setGraphicsEffect(eff)
        start_pos = QPoint(target_geo.x(), target_geo.y() + rise)

        anim_op = QPropertyAnimation(eff, b"opacity", self)
        anim_op.setDuration(duration)
        anim_op.setStartValue(0.0)
        anim_op.setEndValue(1.0)

        anim_pos = QPropertyAnimation(widget, b"pos", self)
        anim_pos.setDuration(duration)
        anim_pos.setStartValue(start_pos)
        anim_pos.setEndValue(QPoint(target_geo.x(), target_geo.y()))

        curve = QEasingCurve.OutCubic
        anim_op.setEasingCurve(curve)
        anim_pos.setEasingCurve(curve)

        def _finished():
            if cur is not None and cur is not widget:
                cur.hide()
                cur.setGraphicsEffect(None)
            widget.setGraphicsEffect(None)
            self._busy = False

        anim_op.finished.connect(_finished)
        # 先放置到起始位置再启动，避免首帧闪现
        widget.move(start_pos)
        anim_op.start(QAbstractAnimation.DeleteWhenStopped)
        anim_pos.start(QAbstractAnimation.DeleteWhenStopped)
