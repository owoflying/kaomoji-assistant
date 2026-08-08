"""WinUI 3 风格开关 ToggleSwitch：圆角轨道 + 滑动滑块，跟随主题强调色。

相比系统 QCheckBox，它更接近 Windows 11 设置里的开关观感：
  * 选中 -> 强调色实心轨道 + 白色滑块（靠右）；
  * 未选 -> 灰色轨道 + 白色滑块（靠左）；
  * 切换时用 QPropertyAnimation 平滑滑动（~160ms，OutCubic）；
  * 支持 setChecked/isChecked/toggled，可直接替换原 QCheckBox 调用处。
"""
from PySide6.QtWidgets import QAbstractButton
from PySide6.QtCore import Qt, QPropertyAnimation, QEasingCurve, Property, QSize
from PySide6.QtGui import QPainter, QColor, QPainterPath

from ui.win11_theme import Theme


class ToggleSwitch(QAbstractButton):
    """WinUI 3 风格开关；theme 接受主题名字符串或 Theme 实例。"""

    def __init__(self, theme="light", parent=None):
        super().__init__(parent)
        self._checked = False
        self._pos = 0.0  # 滑块位置 0..1
        self._theme = theme if isinstance(theme, Theme) else Theme(theme)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(44, 24)
        # 自定义绘制，必须禁用原生按钮背景/边框/焦点轮廓，否则 Windows 风格
        # 会在 hover/focus 时画出黑色/灰色背景条（旧 bug 回归）。
        self.setAttribute(Qt.WA_StyledBackground, False)
        self.setStyleSheet("background: transparent; border: none; outline: none;")
        self._anim = QPropertyAnimation(self, b"pos_f", self)
        self._anim.setDuration(160)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)

    # ---- 滑块位置属性（供动画驱动）----
    def get_pos(self):
        return self._pos

    def set_pos(self, v):
        self._pos = v
        self.update()

    pos_f = Property(float, get_pos, set_pos)

    # ---- 状态 ----
    def isChecked(self):
        return self._checked

    def setChecked(self, c):
        c = bool(c)
        if self._checked == c:
            return
        self._checked = c
        self._anim.stop()
        self._anim.setStartValue(self._pos)
        self._anim.setEndValue(1.0 if c else 0.0)
        self._anim.start()
        self.toggled.emit(c)

    def toggle(self):
        self.setChecked(not self._checked)

    def update_theme(self, theme_obj):
        self._theme = theme_obj
        self.update()

    def sizeHint(self):
        return QSize(44, 24)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.setChecked(not self._checked)
            e.accept()
            return
        super().mouseReleaseEvent(e)

    # ---- 绘制 ----
    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        if not self.isEnabled():
            p.setOpacity(0.45)
        t = self._theme
        w, h = self.width(), self.height()
        r = h / 2.0
        track = QPainterPath()
        track.addRoundedRect(0.0, 0.0, float(w), float(h), r, r)
        if self._checked:
            p.fillPath(track, QColor(t.accent))
        else:
            p.fillPath(track, QColor(t.toggle_off))

        thumb_d = h - 6
        margin = 3
        travel = w - thumb_d - margin * 2
        x = margin + travel * self._pos
        y = margin
        thumb = QPainterPath()
        thumb.addEllipse(x, y, thumb_d, thumb_d)
        p.fillPath(thumb, QColor("#ffffff"))
