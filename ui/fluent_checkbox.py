"""Fluent 风格复选框：统一开发者模式 / 快捷短语等处的勾选控件外观与交互。

视觉规范（与 Windows 11 设置一致）：
- 未选中：空白方框（透明填充 + 细边框），仅显示方框轮廓。
- 选中：填充强调色方框 + 白色对勾，方框内包含一个勾选标记。
- 悬停：未选中时边框转为强调色，给出明确的可点击反馈。
- 随主题（Theme 对象或主题名字符串）自适应配色；主题切换时调用 set_theme() 重绘。

交互行为保持标准 QCheckBox 语义（点击/空格切换、stateChanged 信号正常），
仅重绘外观，不改变选中逻辑。
"""
from PySide6.QtWidgets import QCheckBox, QStyle, QStyleOptionButton
from PySide6.QtCore import Qt, QRect, QPoint
from PySide6.QtGui import QPainter, QPen, QColor

from ui.win11_theme import Theme


class FluentCheckBox(QCheckBox):
    def __init__(self, text="", theme=None, parent=None):
        super().__init__(text, parent)
        self._theme = theme
        self.setCursor(Qt.PointingHandCursor)

    def set_theme(self, theme):
        self._theme = theme
        self.update()

    def _resolve_theme(self):
        t = self._theme
        if isinstance(t, Theme):
            return t
        if isinstance(t, str):
            return Theme(t)
        return Theme("light")

    def _box_rect(self, opt):
        ir = self.style().subElementRect(QStyle.SE_CheckBoxIndicator, opt, self)
        size = 18
        x = ir.x()
        y = ir.y() + (ir.height() - size) // 2
        return QRect(x, y, size, size)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        t = self._resolve_theme()

        opt = QStyleOptionButton()
        self.initStyleOption(opt)
        checked = bool(opt.state & QStyle.State_On)
        hovered = bool(opt.state & QStyle.State_MouseOver)

        box = self._box_rect(opt)

        if checked:
            fill = QColor(t.accent)
            border = QColor(t.accent)
            pen_w = 1.5
        else:
            fill = QColor(0, 0, 0, 0)  # 透明 -> 空白方框（不填充）
            border = QColor(t.input_border)
            pen_w = 1
            if hovered:
                border = QColor(t.accent)

        pen = QPen(border)
        pen.setWidthF(pen_w)
        painter.setPen(pen)
        painter.setBrush(fill)
        painter.drawRoundedRect(box, 4, 4)

        if checked:
            # 白色对勾（按方框比例绘制，居中）
            cp = QPen(QColor("#ffffff"), 2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
            painter.setPen(cp)
            w, h = box.width(), box.height()
            x0, y0 = box.x(), box.y()
            pts = [
                QPoint(x0 + int(w * 0.24), y0 + int(h * 0.54)),
                QPoint(x0 + int(w * 0.44), y0 + int(h * 0.73)),
                QPoint(x0 + int(w * 0.76), y0 + int(h * 0.30)),
            ]
            painter.drawPolyline(pts)

        # 文本：沿用控件字体，颜色取主题文本色
        cr = self.style().subElementRect(QStyle.SE_CheckBoxContents, opt, self)
        painter.setPen(QColor(t.text))
        painter.drawText(cr, Qt.AlignLeft | Qt.AlignVCenter, self.text())
