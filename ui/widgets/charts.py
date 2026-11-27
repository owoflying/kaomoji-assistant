"""轻量图表控件：条形图 / 折线图 / 热力条。

## 为什么自己画

项目没有任何图表库（matplotlib 会让打包体积增加几十 MB），
而这里要画的只有三种很简单的图。用 `QPainter` 直接画几十行就够，
也天然跟随主题（颜色从 `Theme` 取，不写死）。

## 共同约定

* 所有控件都在 `set_data()` 时接收**已算好的数值**，不碰业务数据
* 颜色一律从 `theme.accent` / `theme.text_*` 取，深色浅色自动适配
* 数据为空时画一句"暂无数据"，不画空坐标系
"""
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget


def _tokens(theme):
    """从主题取一组颜色（缺失时给安全默认值）。"""
    def _c(name, default):
        try:
            v = getattr(theme, name, None)
            if isinstance(v, str) and v:
                return QColor(v) if v.startswith("#") else default
        except Exception:
            pass
        return default

    return {
        "accent": _c("accent", QColor(0, 103, 192)),
        "text": _c("text", QColor(32, 32, 32)),
        "secondary": _c("text_secondary", QColor(96, 96, 96)),
        "tertiary": _c("text_tertiary", QColor(140, 140, 140)),
        "border": _c("card_border", QColor(224, 224, 224)),
    }


class BarChart(QWidget):
    """横向条形图：每行「标签 + 条 + 数值」。

    :param rows: `[(label, value), ...]`
    """

    def __init__(self, parent=None, theme=None, label_width=110,
                 min_height=28, bar_color=None):
        super().__init__(parent)
        self._rows = []
        self._theme = theme
        self._label_width = int(label_width)
        self._row_h = int(min_height)
        self._bar_color = bar_color
        self.setMinimumHeight(self._row_h)

    def set_theme(self, theme):
        self._theme = theme
        self.update()

    def set_data(self, rows):
        self._rows = list(rows or [])
        self.setMinimumHeight(max(self._row_h,
                                  self._row_h * max(1, len(self._rows))))
        self.updateGeometry()
        self.update()

    def sizeHint(self):
        from PySide6.QtCore import QSize

        return QSize(400, self._row_h * max(1, len(self._rows)))

    def paintEvent(self, _e):
        if not self._rows:
            return
        t = _tokens(self._theme)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)

        vmax = max((v for _l, v in self._rows), default=0) or 1
        area_x = self._label_width
        area_w = max(20, self.width() - area_x - 54)
        base = self._bar_color or t["accent"]

        fb = QFont(self.font())
        p.setFont(fb)
        for i, (label, value) in enumerate(self._rows):
            y = i * self._row_h
            cy = y + self._row_h // 2

            # 标签（超长省略）
            p.setPen(QPen(t["secondary"]))
            metrics = p.fontMetrics()
            txt = metrics.elidedText(str(label), Qt.ElideRight,
                                     area_x - 12)
            p.drawText(QRectF(0, y, area_x - 8, self._row_h),
                       Qt.AlignRight | Qt.AlignVCenter, txt)

            # 背景槽
            slot = QRectF(area_x, cy - 6, area_w, 12)
            track = QColor(t["border"])
            track.setAlpha(90)
            p.setPen(Qt.NoPen)
            p.setBrush(track)
            p.drawRoundedRect(slot, 6, 6)

            # 条
            w = area_w * (float(value) / float(vmax)) if vmax else 0
            if w > 0.5:
                bar = QRectF(area_x, cy - 6, max(3.0, w), 12)
                c = QColor(base)
                p.setBrush(c)
                p.drawRoundedRect(bar, 6, 6)

            # 数值
            p.setPen(QPen(t["tertiary"]))
            p.drawText(QRectF(area_x + area_w + 6, y, 48, self._row_h),
                       Qt.AlignLeft | Qt.AlignVCenter, str(value))
        p.end()


class Sparkline(QWidget):
    """折线图（无坐标轴），用于"最近 N 天"的趋势。

    :param values: `[int, ...]`，从旧到新
    """

    def __init__(self, parent=None, theme=None, height=64):
        super().__init__(parent)
        self._values = []
        self._labels = []
        self._theme = theme
        self.setMinimumHeight(int(height))

    def set_theme(self, theme):
        self._theme = theme
        self.update()

    def set_data(self, values, labels=None):
        self._values = [int(v) for v in (values or [])]
        self._labels = list(labels or [])
        self.update()

    def paintEvent(self, _e):
        t = _tokens(self._theme)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        w, h = self.width(), self.height()
        pad_l, pad_r, pad_t, pad_b = 8, 8, 8, 18

        if not self._values:
            p.setPen(QPen(t["tertiary"]))
            p.drawText(self.rect(), Qt.AlignCenter, "暂无数据")
            p.end()
            return

        vmax = max(self._values) or 1
        n = len(self._values)
        plot_w = max(1, w - pad_l - pad_r)
        plot_h = max(1, h - pad_t - pad_b)

        def _pt(i, v):
            x = pad_l + (plot_w * i / max(1, n - 1) if n > 1 else plot_w / 2)
            y = pad_t + plot_h * (1 - float(v) / float(vmax))
            return x, y

        pts = [_pt(i, v) for i, v in enumerate(self._values)]

        # 填充区域
        if len(pts) >= 2:
            path = QPainterPath()
            path.moveTo(pts[0][0], pad_t + plot_h)
            for x, y in pts:
                path.lineTo(x, y)
            path.lineTo(pts[-1][0], pad_t + plot_h)
            path.closeSubpath()
            fill = QColor(t["accent"])
            fill.setAlpha(46)
            p.setPen(Qt.NoPen)
            p.setBrush(fill)
            p.drawPath(path)

        # 折线
        pen = QPen(t["accent"])
        pen.setWidthF(2.0)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        if len(pts) == 1:
            p.drawEllipse(QRectF(pts[0][0] - 2.5, pts[0][1] - 2.5, 5, 5))
        else:
            path = QPainterPath()
            path.moveTo(pts[0][0], pts[0][1])
            for x, y in pts[1:]:
                path.lineTo(x, y)
            p.drawPath(path)

        # 首尾标签
        if self._labels:
            p.setPen(QPen(t["tertiary"]))
            first = str(self._labels[0])
            last = str(self._labels[-1])
            p.drawText(QRectF(pad_l, h - pad_b + 2, plot_w, pad_b),
                       Qt.AlignLeft | Qt.AlignVCenter, first)
            p.drawText(QRectF(pad_l, h - pad_b + 2, plot_w, pad_b),
                       Qt.AlignRight | Qt.AlignVCenter, last)
        p.end()


class HeatBar(QWidget):
    """均分的热力条：每段一个时段，透明度按占比。

    :param segments: `[(label, ratio 0~1, count), ...]`
    """

    def __init__(self, parent=None, theme=None, height=46):
        super().__init__(parent)
        self._segments = []
        self._theme = theme
        self.setMinimumHeight(int(height))

    def set_theme(self, theme):
        self._theme = theme
        self.update()

    def set_data(self, segments):
        self._segments = list(segments or [])
        self.update()

    def paintEvent(self, _e):
        if not self._segments:
            return
        t = _tokens(self._theme)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        w, h = self.width(), self.height()
        n = max(1, len(self._segments))
        gap = 4
        seg_w = max(6.0, (w - gap * (n - 1)) / n)
        bar_h = 14
        top = 4

        base = QColor(t["accent"])
        for i, (label, ratio, count) in enumerate(self._segments):
            x = i * (seg_w + gap)
            r = max(0.0, min(1.0, float(ratio)))
            # 占比为 0 时用极淡的槽色，靠"深浅"表达强弱
            c = QColor(base)
            c.setAlphaF(0.16 + 0.84 * r)
            p.setPen(Qt.NoPen)
            p.setBrush(c)
            p.drawRoundedRect(QRectF(x, top, seg_w, bar_h), 4, 4)

            p.setPen(QPen(t["secondary"] if r > 0.02 else t["tertiary"]))
            p.drawText(QRectF(x - 2, top + bar_h + 2, seg_w + 4, h - bar_h - 6),
                       Qt.AlignHCenter | Qt.AlignTop, str(label))
            del count
        p.end()
