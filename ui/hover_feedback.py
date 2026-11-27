"""悬停/按下的**平滑过渡**——QSS 做不到，只能自绘。

## 为什么需要这个

Qt 的 QSS **不支持 `transition`**（实测贴上去直接报
"Unknown property transition"）。所以

    QPushButton       {{ background: <主题色 A>; }}
    QPushButton:hover {{ background: <主题色 B>; }}

的效果是**瞬间跳变**——鼠标一移上去颜色立刻就变，没有过渡。
这正是界面"不够丝滑"的主要来源：所有交互反馈都是硬切。

## 做法

给控件装一个悬停淡入层：

    HoverFader(button).install()

它在鼠标进入/离开时用 `QVariantAnimation` 把 0→1 平滑推进，
然后按这个进度画一层半透明高光。因为是**逐帧绘制**，所以有过渡。

## 与 QSS 的关系

QSS 的 `:hover` 规则会被保留但**效果被替代**：自绘层叠在上面，
视觉上就是平滑的。按下（pressed）同样处理。

## 性能

一轮动画 180ms，每帧只是 `fillRect` 一个圆角矩形——比 QSS 重新
polish 便宜得多（后者会重算整棵样式树）。
"""
from PySide6.QtCore import (
    QEvent, QObject, QRectF, Qt, QVariantAnimation, QEasingCurve,
)
from PySide6.QtGui import QColor, QPainter, QPainterPath
from PySide6.QtWidgets import QPushButton


class HoverFader(QObject):
    """给按钮加平滑的悬停/按下过渡。

    :param button: 目标按钮
    :param hover_alpha: 悬停时叠加的高光透明度（0~255）
    :param pressed_alpha: 按下时的透明度
    :param duration: 过渡时长（ms）

    ## 怎么画

    不拦截按钮自己的绘制，而是在按钮的 `paintEvent` 之后叠一层
    （通过事件过滤器 + `QPainter` 在按钮上追加绘制）。
    这样按钮原有的文字/图标/圆角都由 QSS 负责，我们只加"光"。
    """

    def __init__(self, button, hover_alpha=26, pressed_alpha=44,
                 duration=160, parent=None):
        super().__init__(parent or button)
        self._btn = button
        self._hover_alpha = hover_alpha
        self._pressed_alpha = pressed_alpha
        self._progress = 0.0
        self._pressed = False
        self._last_alpha = None

        self._anim = QVariantAnimation(self)
        # 统一规范：90ms + OutQuad（亮度渐变用太陡的曲线会像"闪一下"）
        try:
            from core.anim_tuning import DURATION, EASING

            self._anim.setDuration(DURATION["hover"])
            self._anim.setEasingCurve(EASING["hover"])
        except Exception:
            self._anim.setDuration(duration)
            self._anim.setEasingCurve(QEasingCurve.OutQuad)
        self._anim.valueChanged.connect(self._on_value)

        button.installEventFilter(self)
        # 让按钮自己在悬停时重绘（默认 QSS 会处理，但我们要逐帧画）
        button.setAttribute(Qt.WA_Hover, True)

    # ---------------------------------------------------------- 事件
    def eventFilter(self, obj, ev):
        if obj is not self._btn:
            return False
        t = ev.type()
        if t == QEvent.Enter:
            self._animate_to(1.0)
        elif t == QEvent.Leave:
            self._pressed = False
            self._animate_to(0.0)
        elif t == QEvent.MouseButtonPress:
            self._pressed = True
            self._repaint()
        elif t == QEvent.MouseButtonRelease:
            self._pressed = False
            self._repaint()
        elif t == QEvent.EnabledChange:
            if not self._btn.isEnabled():
                self._animate_to(0.0)
        return False

    def _animate_to(self, target):
        # 开关：关掉时直接跳到目标值（无过渡，但功能仍正常）
        try:
            from core.anim_settings import enabled as _anim_on

            if not _anim_on("hover"):
                self._anim.stop()
                self._progress = float(target)
                self._on_value(target)
                return
        except Exception:
            pass
        try:
            self._anim.stop()
            self._anim.setStartValue(float(self._progress))
            self._anim.setEndValue(float(target))
            self._anim.start()
        except Exception:
            self._progress = target
            self._repaint()

    def _on_value(self, v):
        try:
            self._progress = max(0.0, min(1.0, float(v)))
        except Exception:
            self._progress = 0.0
        self._repaint()

    def _repaint(self):
        """只在颜色真的变了时重绘，避免无谓刷新。"""
        alpha = self._current_alpha()
        if alpha == self._last_alpha:
            return
        self._last_alpha = alpha
        try:
            self._btn.update()
        except Exception:
            pass

    # ---------------------------------------------------------- 绘制
    def _current_alpha(self):
        if self._progress <= 0.001:
            return 0
        base = self._pressed_alpha if self._pressed else self._hover_alpha
        return int(base * self._progress)

    def paint_overlay(self, painter):
        """在按钮绘制之后调用，叠一层平滑出现的高光。"""
        alpha = self._current_alpha()
        if alpha <= 0:
            return
        btn = self._btn
        w, h = btn.width(), btn.height()
        if w <= 0 or h <= 0:
            return
        try:
            from ui.win11_theme import RADIUS_CONTROL

            radius = RADIUS_CONTROL
        except Exception:
            radius = 4
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        path = QPainterPath()
        path.addRoundedRect(QRectF(0, 0, w, h), radius, radius)
        # 深色主题用白色高光，浅色主题用极淡的黑——两者都能"提亮"自身
        dark = False
        try:
            t = getattr(btn.window(), "theme", None)
            dark = bool(t is not None and t.dark)
        except Exception:
            pass
        color = (QColor(255, 255, 255, alpha) if dark
                 else QColor(0, 0, 0, max(1, alpha // 3)))
        painter.fillPath(path, color)
        painter.restore()


def install_hover_faders(widget, alpha=26, duration=160):
    """给一个容器里所有 QPushButton 装上悬停过渡（返回装了几个）。

    ## 为什么要在容器里批量装

    项目里有 60+ 处 `QPushButton(...)`，逐个改造不现实。
    统一在页面构建完之后扫一遍更省事，新加的按钮也会被覆盖到
    （只要在构建后调用一次）。
    """
    count = 0
    try:
        for b in widget.findChildren(QPushButton):
            if getattr(b, "_hover_fader", None) is not None:
                continue
            fader = HoverFader(b, hover_alpha=alpha, duration=duration)
            b._hover_fader = fader
            _attach_paint_hook(b, fader)
            count += 1
    except Exception:
        pass
    return count


def _attach_paint_hook(button, fader):
    """让按钮在画完自己之后，再让 fader 叠一层高光。

    做法是把原有的 `paintEvent` 包一层——比子类化轻，也不会破坏
    按钮原有的 objectName / 样式表。
    """
    try:
        orig = button.paintEvent

        def _paint(ev, _orig=orig, _fader=fader):
            _orig(ev)
            try:
                p = QPainter(button)
                _fader.paint_overlay(p)
                p.end()
            except Exception:
                pass

        button.paintEvent = _paint
    except Exception:
        pass
