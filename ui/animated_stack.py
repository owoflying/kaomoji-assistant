"""带平滑过渡的 QStackedWidget 替代：仅「新页轻微上浮就位」，无透明度淡入。

设计要点（修复「切换透出桌面 / 卡顿」两项问题）：
  * 切换时**不**对任何页面施加 QGraphicsOpacityEffect —— 该效果每帧需把整页
    渲染到离屏缓冲再合成，大页面下极慢 → 卡顿；且 opacity<1 会让页面透出下方
    半透明窗体 → 露出桌面。
  * 旧页在 setCurrentWidget 时即被隐藏，仅「新页」从下方 rise 像素上浮就位，
    全程页面不透明度恒为 1，绝不透出背后画面；顶部几像素留白是内容区亚克力
    背景（与常态一致，非硬露桌面）。
  * 动画期间**不**调用 setStyleSheet（避免整棵内容树样式重算导致的卡顿）。
"""
from PySide6.QtWidgets import QStackedWidget
from PySide6.QtCore import QAbstractAnimation, QPropertyAnimation, QEasingCurve, QPoint
from PySide6.QtGui import QColor, QPainter


class AnimatedStackedWidget(QStackedWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._busy = False
        #: 内容区表面背景色（None = 不画，靠样式表/父级）
        self._surface = None

    # ---------- 背景自绘 ----------
    def set_surface(self, color):
        """设置内容区表面背景色并重绘。

        **为什么不走 QSS**：`setStyleSheet` 会让整棵子树重新 polish ——
        内容区有 7 个页面、几百个控件，一次实测 **260ms**。
        而拖动「不透明度」滑块需要逐档更新这个颜色，用 QSS 每档卡 260ms。

        改成自绘后，更新一次是「设个 QColor + update()」，亚毫秒级；
        子页本来就有 `SettingsBody` 之类的透明规则，背景透上来正好。
        """
        c = QColor(color) if color is not None else None
        if c is not None and not c.isValid():
            return
        self._surface = c
        self.viewport().update()
        self.update()

    def surface(self):
        return self._surface

    def paintEvent(self, e):
        # 先画自己的背景，再让 QStackedWidget 画子页
        s = self._surface
        if s is not None and s.isValid():
            p = QPainter(self)
            p.fillRect(self.rect(), s)
            p.end()
        super().paintEvent(e)

    def slide_to(self, widget, duration=200, rise=8):
        """切换到指定 widget，仅让新页做「上浮就位」过渡（无透明度变化）。

        旧页在 setCurrentWidget 时随之隐藏，新页从下方 rise 像素处上浮到原位。
        全程页面不透明度恒为 1，不会透出窗口背后画面；也不重算样式表，无卡顿。
        """
        if widget is None or widget is self.currentWidget() or self._busy:
            return

        self.setCurrentWidget(widget)  # 旧页随之隐藏

        # 窗口未显示时 geometry 为无效/零尺寸，此时运行动画会导致后续布局压缩错乱，
        # 直接切页更安全。
        geo = widget.geometry()
        if geo.width() <= 0 or geo.height() <= 0:
            return

        self._busy = True
        widget.setGraphicsEffect(None)  # 确保无残留透明度效果

        # 新页：从下方 rise 像素处上浮就位（仅位移动画，不透明度恒为 1）
        start_pos = QPoint(geo.x(), geo.y() + rise)
        widget.move(start_pos)

        anim = QPropertyAnimation(widget, b"pos", self)
        anim.setDuration(duration)
        anim.setStartValue(start_pos)
        anim.setEndValue(QPoint(geo.x(), geo.y()))
        anim.setEasingCurve(QEasingCurve.OutCubic)

        def _finished():
            widget.move(geo.x(), geo.y())
            self._busy = False

        anim.finished.connect(_finished)
        # 先放置到起始位置再启动，避免首帧闪现
        widget.move(start_pos)
        anim.start(QAbstractAnimation.DeleteWhenStopped)
