"""带平滑过渡的 QStackedWidget 替代：新页上浮 + 旧页截图淡出。

## 为什么不用 QGraphicsOpacityEffect

最初试过给页面控件直接挂 `QGraphicsOpacityEffect`，但那个效果每帧都要把
整页渲染到离屏缓冲再合成——大页面下极慢（实测切页卡 200ms+），
而且 opacity<1 会让页面透出下方的半透明窗体，露出桌面。

## 现在怎么做

    * 新页：从下方 rise 像素**上浮**就位（位移动画，很便宜）
    * 旧页：切换**之前**先 `grab()` 一张截图，切换后把截图当覆盖层
      叠在最上面淡出。截图只有一张位图，淡出全靠调它的不透明度，
      不涉及任何真控件重绘。

这样既有交叉过渡的观感，又不会拖慢或透出桌面。

## 动画期间不做的事

* 不调 `setStyleSheet`（整棵内容树重新 polish，实测 260ms）
* 不给真控件挂 graphics effect
"""
from PySide6.QtWidgets import QStackedWidget, QWidget
from PySide6.QtCore import (
    QAbstractAnimation, QEasingCurve, QPropertyAnimation, Qt,
)
from PySide6.QtGui import QColor, QPainter


class _FadeOverlay(QWidget):
    """盖在内容区上的淡化层：画截图或纯色，透明度靠属性动画驱动。"""

    def __init__(self, parent, pixmap=None):
        super().__init__(parent)
        self._pixmap = pixmap
        self._fill = None
        self._opacity = 1.0
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setGeometry(parent.rect())

    def set_fill(self, color):
        """用纯色代替截图（不抓旧页时用内容区表面色）。"""
        try:
            self._fill = QColor(color) if color is not None else None
        except Exception:
            self._fill = None

    def get_opacity(self):
        return self._opacity

    def set_opacity(self, v):
        self._opacity = max(0.0, min(1.0, float(v)))
        self.update()

    #: 供 QPropertyAnimation 绑定
    opacity = property(get_opacity, set_opacity)

    def paintEvent(self, _e):
        p = QPainter(self)
        if self._pixmap is not None and not self._pixmap.isNull():
            p.setRenderHint(QPainter.SmoothPixmapTransform, True)
            p.setOpacity(self._opacity)
            p.drawPixmap(0, 0, self._pixmap)
        elif self._fill is not None:
            c = QColor(self._fill)
            c.setAlphaF(self._opacity)
            p.fillRect(self.rect(), c)
        p.end()


class AnimatedStackedWidget(QStackedWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._busy = False
        #: 内容区表面背景色（None = 不画，靠样式表/父级）
        self._surface = None
        self._overlay = None

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
        # QStackedWidget 没有 viewport（那是 QScrollArea/QAbstractScrollArea 的），
        # 直接刷新自身即可。原来的 viewport() 调用会抛 AttributeError，
        # 但被上层 try 吃掉，表现为"表面色设置了却不生效"。
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
        """切换到指定 widget：新页**淡入**（不再抓旧页截图）。

        ## 前两版的问题

        **v1**：给新页做 `move()` 位移动画。
        新页是 `QStackedWidget` 的直接子控件、参与布局，布局每帧会把它
        改回原位 —— 动画和布局拉锯，画面抖动、跳帧。这就是
        "特别卡顿和诡异"的根源。

        **v2**：改用"旧页截图淡出"。
        截图要 `old.grab()`，几百个控件的页面一次 30~50ms
        **同步阻塞**在按键瞬间，体感是"按下去卡一下才动"。
        而且旧页截图 + 新页过渡是两套 16ms tick，节奏不同步。

        ## 现在：只让新页淡入

        用一个覆盖层盖住新页，从**不透明到透明**淡出——视觉上就是
        新页从旧内容的底片里浮现出来。不抓旧页截图，零阻塞。

        为什么这样看起来仍然是交叉过渡：切换瞬间屏幕上是新页，
        但被半透明的覆盖层遮住；覆盖层退去，新页逐渐清晰。
        人眼读到的就是"从上一页过渡过来"。

        ## `_busy` 的安全阀

        如果上一次动画的 `finished` 没触发（窗口被隐藏、异常退出等），
        `_busy` 会永久卡在 True，之后所有切页都被忽略——表现就是
        "侧边栏点了没反应"。这里加一个超时自检兜底。
        """
        # 安全阀：超过 1 秒还 busy，说明上一次动画没正常结束
        if self._busy:
            import time as _t

            if _t.time() - getattr(self, "_busy_since", 0) > 1.0:
                self._busy = False
                self._cleanup_overlay()
            else:
                return

        if widget is None or widget is self.currentWidget():
            return

        geo = widget.geometry()
        if geo.width() <= 0 or geo.height() <= 0 or not self.isVisible():
            self.setCurrentWidget(widget)
            return

        # 开关：关掉就静态切换，不留任何过渡
        try:
            from core.anim_settings import enabled as _anim_on

            if not _anim_on("page_switch"):
                self.setCurrentWidget(widget)
                return
        except Exception:
            pass

        import time as _t

        self._busy = True
        self._busy_since = _t.time()
        self.setCurrentWidget(widget)
        widget.setGraphicsEffect(None)
        widget.move(geo.x(), geo.y())

        try:
            overlay = _FadeOverlay(self, None)
            overlay.set_fill(self._surface or QColor(255, 255, 255))
            overlay.show()
            overlay.raise_()
            fade = QPropertyAnimation(overlay, b"opacity", self)
            fade.setDuration(duration)
            fade.setStartValue(1.0)
            fade.setEndValue(0.0)
            fade.setEasingCurve(QEasingCurve.OutCubic)

            def _done():
                self._busy = False
                self._cleanup_overlay()

            fade.finished.connect(_done)
            self._overlay = overlay
            fade.start(QAbstractAnimation.DeleteWhenStopped)
            return
        except Exception:
            pass

        self._busy = False

    def _cleanup_overlay(self):
        """确保覆盖层被彻底移除（不能留下游离窗口）。"""
        ov = getattr(self, "_overlay", None)
        self._overlay = None
        if ov is None:
            return
        try:
            ov.hide()
            ov.setParent(None)
            ov.deleteLater()
        except Exception:
            pass

    def hideEvent(self, e):
        super().hideEvent(e)
        # 隐藏时清掉覆盖层：否则它可能作为游离窗口残留，
        # 并在下一次显示时挡住内容
        self._busy = False
        self._cleanup_overlay()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        # 覆盖层跟着内容区大小走
        if self._overlay is not None:
            try:
                self._overlay.setGeometry(self.rect())
            except Exception:
                pass
