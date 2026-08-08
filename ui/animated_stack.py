"""带平滑过渡的 QStackedWidget 替代：真实「交叉淡入淡出 + 轻微上浮」。

切换时：
  * 新页置为当前页并做「上浮 + 淡入」（透明度 0→1，从下方 8px 升到原位）；
  * 旧页保持在最上层并做「淡出」（透明度 1→0），与下层新页同时渐变，
    形成真正的 crossfade —— 旧页文字会随透明度平滑消失，不会再“残留”一下；
  * 全程不把控件移到窗口外，故不会越界到侧边栏/窗口之外，无视觉错位。
动画时长与缓动曲线可调，默认 230ms、OutCubic，明显可感知且顺滑。
"""
from PySide6.QtWidgets import QStackedWidget, QGraphicsOpacityEffect
from PySide6.QtCore import QAbstractAnimation, QPropertyAnimation, QEasingCurve, QPoint


class AnimatedStackedWidget(QStackedWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._busy = False

    def slide_to(self, widget, duration=230, rise=8):
        """切换到指定 widget，带「旧页淡出 + 新页上浮就位」过渡。

        关键修复：新页始终保持不透明（仅做位置上浮），旧页置于最上层淡出。
        这样过渡期间永远是「不透明的新页」衬在淡出的旧页之下，绝不会透出
        窗口背后的桌面——此前新页也做 0→1 透明度淡入，二者同时半透时，
        会透过 WA_TranslucentBackground 的亚克力窗体直接露出桌面。
        """
        if widget is None or widget is self.currentWidget() or self._busy:
            return

        cur = self.currentWidget()
        self.setCurrentWidget(widget)

        # 窗口未显示时 geometry 为无效/零尺寸，此时运行动画会导致后续布局压缩错乱，
        # 直接切页更安全。
        geo = widget.geometry()
        if geo.width() <= 0 or geo.height() <= 0:
            return

        self._busy = True
        widget.setGraphicsEffect(None)  # 新页：保持不透明，不做透明度淡入

        # 新页：从下方 rise 像素处上浮就位（仅位移动画）
        start_pos = QPoint(geo.x(), geo.y() + rise)
        widget.move(start_pos)

        # 旧页：置于新页之上并淡出，露出下方不透明的新页，形成正确交叉过渡
        eff_old = None
        if cur is not None and cur is not widget:
            cur.setGeometry(geo)
            cur.show()
            cur.raise_()
            cur.setGraphicsEffect(None)
            eff_old = QGraphicsOpacityEffect(cur)
            eff_old.setOpacity(1.0)
            cur.setGraphicsEffect(eff_old)

        anim_new_pos = QPropertyAnimation(widget, b"pos", self)
        anim_new_pos.setDuration(duration)
        anim_new_pos.setStartValue(start_pos)
        anim_new_pos.setEndValue(QPoint(geo.x(), geo.y()))

        curve = QEasingCurve.OutCubic
        anim_new_pos.setEasingCurve(curve)

        anims = [anim_new_pos]

        if eff_old is not None:
            anim_old_op = QPropertyAnimation(eff_old, b"opacity", self)
            anim_old_op.setDuration(duration)
            anim_old_op.setStartValue(1.0)
            anim_old_op.setEndValue(0.0)
            anim_old_op.setEasingCurve(curve)
            anims.append(anim_old_op)

        def _finished():
            if cur is not None and cur is not widget:
                cur.setGraphicsEffect(None)
                cur.hide()
            widget.setGraphicsEffect(None)
            widget.move(geo.x(), geo.y())
            self._busy = False

        anim_new_pos.finished.connect(_finished)
        # 先放置到起始位置再启动，避免首帧闪现
        widget.move(start_pos)
        for a in anims:
            a.start(QAbstractAnimation.DeleteWhenStopped)
