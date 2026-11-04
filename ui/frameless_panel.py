"""无边框窗口的共用绘制：柔和外阴影 + 面板基底 + 竖向渐变 + 1px 圆角边框。

为什么单独抽出来：主窗口 / 日志窗口 / 插件弹窗 / 候选条此前各抄了一份几乎相同
的 paintEvent，阴影衰减系数、渐变顺序、边框宽度全靠手抄保持一致——改一处漏三处
就会让四个窗口的观感逐渐分叉。这里只保留"怎么画"，各窗口仍负责"画多大、用什么
颜色"，视觉参数（圆角、留白、配色）继续留在各自模块。

颜色入参同时接受 `QColor` 与主题里的 `rgba(r,g,b,浮点a)` 字符串，内部统一走
`win11_theme.parse_color()`。Qt6 的 `QColor("rgba(...,0.88)")` 是无效值并静默
回退成黑色，直接构造会让整个窗口发黑——这是本项目踩过两次的坑。
"""
from PySide6.QtCore import QRectF
from PySide6.QtGui import QColor, QLinearGradient, QPainterPath, QPen

from ui.win11_theme import parse_color


def _as_color(spec, fallback=None):
    """把主题色字符串 / QColor 统一成 QColor；None 时返回 fallback。"""
    if isinstance(spec, QColor):
        return spec
    if not spec:
        return fallback
    return parse_color(spec)


def paint_shadow(painter, panel, shadow_color, pad, radius,
                 spread=0.35, growth=0.5):
    """在 panel 外侧逐层绘制由外向内减淡的柔光阴影（全部落在窗口矩形内）。

    阴影画在窗口内部留白里（而不是窗口外），因为分层窗口用
    UpdateLayeredWindow 贴图，超出窗口矩形绘制会报错。
    """
    if pad <= 0:
        return
    sc = _as_color(shadow_color, QColor(0, 0, 0, 40))
    base = sc.alpha()
    if base <= 0:
        return
    for d in range(pad, 0, -1):
        a = max(1, int(base * (1 - (d - 1) / pad) * spread))
        path = QPainterPath()
        rr = radius + d * growth
        path.addRoundedRect(QRectF(panel.adjusted(-d, -d, d, d)), rr, rr)
        painter.fillPath(path, QColor(sc.red(), sc.green(), sc.blue(), a))


def paint_surface(painter, panel, radius, tint=None, grad_top=None,
                  grad_bottom=None, border=None):
    """绘制圆角面板本体（基底色 -> 竖向渐变 -> 1px 边框），返回该面板路径。"""
    path = QPainterPath()
    path.addRoundedRect(QRectF(panel), radius, radius)
    if tint is not None:
        painter.fillPath(path, _as_color(tint))
    if grad_top is not None and grad_bottom is not None:
        grad = QLinearGradient(0, panel.top(), 0, panel.bottom())
        grad.setColorAt(0, _as_color(grad_top))
        grad.setColorAt(1, _as_color(grad_bottom))
        painter.fillPath(path, grad)
    if border is not None:
        pen = QPen(_as_color(border))
        pen.setWidth(1)
        painter.strokePath(path, pen)
    return path


def paint_frameless_panel(painter, panel, *, radius, tint=None,
                          grad_top=None, grad_bottom=None, border=None,
                          shadow=None, shadow_pad=0, shadow_spread=0.35,
                          shadow_growth=0.5):
    """一次性画出「外阴影 + 面板」；最大化态传 shadow=None / border=None 即可。"""
    if shadow is not None:
        paint_shadow(painter, panel, shadow, shadow_pad, radius,
                     spread=shadow_spread, growth=shadow_growth)
    return paint_surface(painter, panel, radius, tint, grad_top, grad_bottom,
                         border)
