"""玻璃质感的控件助手：按钮 / 输入框。

## 为什么普通 QSS 做不出玻璃感

QSS 只能画**纯色/渐变背景**，画不出这两样：

1. **背后那块背景**（真正的 `backdrop-filter` 语义）
2. **釉面高光**（顶部一条亮、往下迅速衰减）

卡片的玻璃效果之所以成立，是靠 `MaterialCard` 自绘：从窗口合成图里
裁出自己背后那块 + 叠底色 + 叠高光。控件也应该走同一条路。

## 做法

`GlassFrameMixin` 提供 `_paint_glass(surface)`：

    1. 从窗口的合成背景里裁出控件背后那块（复用 backdrop_for）
    2. 叠半透明底色
    3. 叠顶部釉面高光
    4. 圆角裁切 + 1px 边框

控件自己决定何时调用（按下/悬停时换不同强度的底色）。
"""
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import (
    QColor, QImage, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap,
)


def request_window_backdrop(widget):
    """取 `widget` 背后的合成背景：`(QPixmap, offset)` 或 None。"""
    try:
        w = widget.window()
        fn = getattr(w, "backdrop_for", None)
        if fn is None:
            # 独立窗口（日志/候选条/插件弹窗）走 composer 的合成图
            comp = getattr(w, "_material_composer", None)
            if comp is None or not comp.enabled():
                return None
            from PySide6.QtCore import QPoint

            pad = getattr(widget, "_PAD", 12)
            panel_w = w.width() - 2 * pad
            panel_h = w.height() - 2 * pad
            if panel_w <= 0 or panel_h <= 0:
                return None
            pm = comp.compose(widget_size(w, panel_w, panel_h))
            if pm is None or pm.isNull():
                return None
            tl = widget.mapTo(w, QPoint(0, 0))
            rel = tl - QPoint(pad, pad)
            rect = widget.rect().translated(rel).intersected(pm.rect())
            if rect.isEmpty():
                return None
            return (pm.copy(rect), tl - rect.topLeft())
        return fn(widget)
    except Exception:
        return None


def widget_size(w, pw, ph):
    """构造面板尺寸（避免在模块顶层 import QtCore 的 QSize 造成循环）。"""
    from PySide6.QtCore import QSize

    return QSize(pw, ph)


def paint_glass(surface, widget, tint, radius=4, sheen=1.0,
                border=None, cached=None):
    """在 `surface` 上画玻璃底，返回可用于缓存的 QPixmap。

    :param surface: 目标 QWidget（用其 rect 与设备像素比）
    :param tint: 半透明底色 QColor
    :param radius: 圆角
    :param sheen: 釉面高光强度倍数（0 = 无高光）
    :param border: 1px 边框色（QColor 或 None）
    :param cached: 之前渲染好的 QPixmap（形状/颜色没变时直接复用）
    :return: 渲染后的 QPixmap（供调用方缓存）
    """
    w = max(1, widget.width())
    h = max(1, widget.height())
    canvas = QImage(w, h, QImage.Format_ARGB32_Premultiplied)
    canvas.fill(Qt.transparent)
    p = QPainter(canvas)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setRenderHint(QPainter.SmoothPixmapTransform, True)

    # 1. 背后那块真实背景
    got = request_window_backdrop(widget)
    if got is not None:
        bg, offset = got
        if bg is not None and not bg.isNull():
            p.drawPixmap(-offset.x(), -offset.y(), bg)

    # 2. 半透明底色
    p.fillRect(0, 0, w, h, tint)

    # 3. 釉面高光：顶部亮、往下快速衰减，让平面色块有"湿润反光"
    if sheen > 0:
        dark = bool(getattr(widget.window(), "theme", None)
                    and widget.window().theme.dark)
        g = QLinearGradient(0, 0, 0, h)
        if dark:
            g.setColorAt(0.0, QColor(255, 255, 255, int(20 * sheen)))
            g.setColorAt(0.45, QColor(255, 255, 255, int(8 * sheen)))
            g.setColorAt(1.0, QColor(255, 255, 255, 0))
        else:
            g.setColorAt(0.0, QColor(255, 255, 255, int(96 * sheen)))
            g.setColorAt(0.45, QColor(255, 255, 255, int(34 * sheen)))
            g.setColorAt(1.0, QColor(255, 255, 255, 0))
        p.fillRect(0, 0, w, h, g)

    p.end()

    # 4. 圆角裁切 + 边框
    out = QImage(w, h, QImage.Format_ARGB32_Premultiplied)
    out.fill(Qt.transparent)
    q = QPainter(out)
    q.setRenderHint(QPainter.Antialiasing, True)
    clip = QPainterPath()
    clip.addRoundedRect(QRectF(0, 0, w, h), radius, radius)
    q.setClipPath(clip)
    q.drawImage(0, 0, canvas)
    q.setClipping(False)
    if border is not None:
        pen = QPen(border)
        pen.setWidthF(1.0)
        q.setPen(pen)
        q.setBrush(Qt.NoBrush)
        q.drawRoundedRect(QRectF(0.5, 0.5, w - 1, h - 1), radius, radius)
    q.end()
    return QPixmap.fromImage(out)
