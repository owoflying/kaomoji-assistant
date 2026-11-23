"""自绘悬浮提示，替代 Qt 原生 QToolTip。

## 为什么必须自绘

原生 `QToolTip` 在 Windows 上有两个治不好的毛病：

1. **QSS 的 `QToolTip {{}}` 规则经常不生效**——它是原生弹出窗口，
   Qt 绘制时优先取 QPalette，QSS 规则被跳过。浅色主题下会变成黑底。

2. **系统阴影变成黑块**（最致命）。实测枚举窗口能看到：

       Qt6111QWindowToolTipDropShadowSaveBits   423x33
       SysShadow                                428x38

   `SysShadow` 是 Windows 给带 `CS_DROPSHADOW` 风格的窗口画的系统阴影。
   Qt 的 tooltip 窗口默认带这个风格，同时又设了半透明背景，两边打架，
   阴影就变成**一条纯黑块**露在提示框外面。这不是 Qt 画的，QSS 管不到，
   `Qt.NoDropShadowWindowHint` 实测也去不掉。

所以干脆自己画：一个无边框、无阴影、纯 Qt 绘制的顶层小窗口。
颜色完全由主题决定，不会再有黑块。

## 用法

和原生完全一致，业务代码不用改：

    widget.setToolTip("说明文字")

安装：

    install_tooltips(app, theme_provider)

`theme_provider` 是个返回当前 `Theme` 的回调——主题切换后新弹出的
提示自动用新配色，不需要重新安装。
"""
from PySide6.QtCore import QEvent, QObject, QPoint, Qt, QTimer
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import QWidget

#: 显示延迟与自动消失时间（毫秒）
SHOW_DELAY = 550
HIDE_AFTER = 12000

#: 内边距与圆角
_PAD_X = 10
_PAD_Y = 6
_RADIUS = 6

#: 与鼠标指针的间距
_OFFSET = QPoint(12, 20)


class ToolTipBubble(QWidget):
    """自绘的提示气泡。

    ## 为什么不用 QLabel

    需要同时控制：无边框、无系统阴影、圆角背景、细边框、按主题上色。
    `QLabel` + 样式表能做，但 Windows 会给带样式的顶层窗口画 SysShadow
    ——那正是要躲开的东西。纯 `QWidget` + `paintEvent` 自己画，
    配合 `WindowDoesNotAcceptFocus`，系统不会给它加阴影。
    """

    def __init__(self, theme_provider):
        super().__init__(
            None,
            Qt.ToolTip
            | Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            # 关键：不接受焦点、不激活、不请求系统阴影。
            # 少了 NoDropShadowWindowHint 就会出现那条黑块。
            | Qt.WindowDoesNotAcceptFocus
            | Qt.NoDropShadowWindowHint,
        )
        self._provider = theme_provider
        self._text = ""
        # 不激活、不抢焦点：提示出现时不该影响用户正在做的事
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setFocusPolicy(Qt.NoFocus)

    def theme(self):
        """取当前主题（provider 可以返回 Theme 对象或名字）。"""
        try:
            t = self._provider() if self._provider else None
        except Exception:
            t = None
        if t is None:
            from ui.win11_theme import Theme

            return Theme("light")
        if isinstance(t, str):
            from ui.win11_theme import Theme

            return Theme(t)
        return t

    def set_text(self, text):
        self._text = str(text or "")
        self._resize_to_content()
        self.update()

    def _lines(self):
        """按换行拆行；过长的手动折行。

        Qt 的 `QFontMetrics` 不含换行处理，这里自己拆：
        超过 360px 的部分在空格或任意位置断开，避免气泡长到屏幕外。
        """
        fm = QFontMetrics(self.font())
        max_w = 360
        out = []
        for raw in self._text.split("\n"):
            if not raw:
                out.append("")
                continue
            line = ""
            for ch in raw:
                if fm.horizontalAdvance(line + ch) > max_w and line:
                    out.append(line)
                    line = ch
                else:
                    line += ch
            out.append(line)
        return out or [""]

    def _resize_to_content(self):
        fm = QFontMetrics(self.font())
        lines = self._lines()
        w = max((fm.horizontalAdvance(s) for s in lines), default=0)
        line_h = fm.height()
        self.resize(
            max(24, w + _PAD_X * 2),
            max(18, line_h * len(lines) + _PAD_Y * 2),
        )

    def paintEvent(self, _ev):
        t = self.theme()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)

        rect = self.rect().adjusted(0, 0, -1, -1)
        # 背景：用主题 card 色，保证与界面同色系
        p.setPen(QPen(QColor(t.card_border), 1))
        p.setBrush(QColor(t.card))
        p.drawRoundedRect(rect, _RADIUS, _RADIUS)

        p.setPen(QColor(t.text))
        fm = QFontMetrics(self.font())
        y = _PAD_Y + fm.ascent()
        for line in self._lines():
            p.drawText(_PAD_X, y, line)
            y += fm.height()
        p.end()


class ToolTipManager(QObject):
    """应用级事件过滤器：接管所有原生 tooltip 显示。"""

    def __init__(self, app, theme_provider):
        super().__init__(app)
        self._app = app
        self._bubble = ToolTipBubble(theme_provider)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._maybe_show)
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self.hide)
        self._pending = None

    # ------------------------------------------------------------ 对外
    def hide(self):
        self._timer.stop()
        self._hide_timer.stop()
        self._bubble.hide()

    # ------------------------------------------------------------ 内部
    def eventFilter(self, obj, ev):
        try:
            etype = ev.type()
            if etype == QEvent.ToolTip:
                # 吃掉原生 tooltip：返回 True 表示"已处理"，Qt 不再弹自己的
                self._on_tooltip(obj, ev)
                return True
            if etype in (QEvent.Leave, QEvent.MouseButtonPress):
                self.hide()
            elif etype in (QEvent.KeyPress, QEvent.Wheel):
                self.hide()
            elif etype == QEvent.Hide:
                self.hide()
        except Exception:
            pass
        return False

    def _on_tooltip(self, obj, ev):
        """原生 tooltip 触发时被调用。

        `obj` 就是被悬停的控件；文本从它的 `toolTip()` 取。
        """
        try:
            text = obj.toolTip() if obj is not None else ""
        except Exception:
            text = ""
        if not text:
            self.hide()
            return
        # 有提示文本才进入延迟显示（对齐系统默认的 550ms，避免一闪而过）
        self._pending = (obj, text, ev.globalPos() if hasattr(ev, "globalPos")
                         else None)
        self._bubble.hide()
        self._timer.start(SHOW_DELAY)

    def _maybe_show(self):
        if not self._pending:
            return
        obj, text, pos = self._pending
        # 控件可能已经被销毁（窗口关了、页面重建了）
        #
        # ⚠ 只判"对象还在"，**不要**判 `isVisible()`。
        # 实测：按钮在有 ToolTip 事件时 `isVisible()` 可能仍返回 False
        # （父窗口刚 show、控件还没完成布局映射），于是气泡永远不显示
        # ——用户看到的就是"悬停什么都没有"。
        # 真正该防的是"C++ 对象已销毁"，用 shiboken 的 isValid 更准。
        try:
            from shiboken6 import isValid as _valid

            if obj is not None and not _valid(obj):
                return
        except Exception:
            pass
        self._bubble.set_text(text)
        if pos is None:
            try:
                pos = obj.mapToGlobal(QPoint(0, obj.height()))
            except Exception:
                return
        self._place(pos)
        self._bubble.show()
        self._hide_timer.start(HIDE_AFTER)

    def _place(self, pos):
        """定位气泡：默认在指针右下方，超出屏幕就翻到另一侧。"""
        screen = self._app.primaryScreen()
        try:
            screen = self._app.screenAt(pos) or screen
        except Exception:
            pass
        x = pos.x() + _OFFSET.x()
        y = pos.y() + _OFFSET.y()
        if screen is not None:
            geo = screen.availableGeometry()
            w, h = self._bubble.width(), self._bubble.height()
            if x + w > geo.right():
                x = max(geo.left(), pos.x() - w - _OFFSET.x())
            if y + h > geo.bottom():
                y = max(geo.top(), pos.y() - h - 8)
        self._bubble.move(int(x), int(y))


def install_tooltips(app, theme_provider):
    """装上自绘 tooltip（幂等：重复调用不会装多份）。

    :param app: QApplication
    :param theme_provider: 返回当前 Theme 的回调
    :return: ToolTipManager 实例（也挂在 app 上，防止被 GC）
    """
    existing = getattr(app, "_kao_tooltip_manager", None)
    if existing is not None:
        return existing
    mgr = ToolTipManager(app, theme_provider)
    app.installEventFilter(mgr)
    app._kao_tooltip_manager = mgr
    return mgr
