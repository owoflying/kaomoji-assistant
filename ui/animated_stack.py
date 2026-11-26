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
    QAbstractAnimation, QEasingCurve, QPoint, QPropertyAnimation, Qt,
)
from PySide6.QtGui import QColor, QPainter


class _FadeOverlay(QWidget):
    """盖在内容区上的淡化层：只画一张截图，透明度靠属性动画驱动。"""

    def __init__(self, parent, pixmap):
        super().__init__(parent)
        self._pixmap = pixmap
        self._opacity = 1.0
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setGeometry(parent.rect())

    def get_opacity(self):
        return self._opacity

    def set_opacity(self, v):
        self._opacity = max(0.0, min(1.0, float(v)))
        self.update()

    #: 供 QPropertyAnimation 绑定
    opacity = property(get_opacity, set_opacity)

    def paintEvent(self, _e):
        if self._pixmap is None or self._pixmap.isNull():
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)
        p.setOpacity(self._opacity)
        p.drawPixmap(0, 0, self._pixmap)
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

    def slide_to(self, widget, duration=240, rise=10):
        """切换到指定 widget：旧页截图淡出 + 新页上浮就位。

        :param duration: 过渡时长（ms）。240ms 是"能感知到但不拖沓"的长度
        :param rise: 新页起始时比最终位置低多少像素

        ## 步骤

            1. 抓旧页截图（在切换之前，否则抓不到）
            2. setCurrentWidget 切到新页
            3. 新页从下方 rise 处上浮到原位
            4. 旧页截图作为覆盖层淡出

        截图淡出是**位图**操作，不涉及真控件重绘，所以比
        QGraphicsOpacityEffect 便宜几个数量级。
        """
        if widget is None or widget is self.currentWidget() or self._busy:
            return

        old = self.currentWidget()

        # 窗口未显示时 geometry 为无效/零尺寸，运行动画会导致布局压缩错乱
        geo = widget.geometry()
        if geo.width() <= 0 or geo.height() <= 0 or not self.isVisible():
            self.setCurrentWidget(widget)
            return

        # ---- 1. 抓旧页截图（必须在切换前）----
        snapshot = None
        try:
            if old is not None:
                snapshot = old.grab()
        except Exception:
            snapshot = None

        self._busy = True
        self.setCurrentWidget(widget)
        widget.setGraphicsEffect(None)

        # ---- 2. 新页上浮 ----
        end_pos = QPoint(geo.x(), geo.y())
        start_pos = QPoint(geo.x(), geo.y() + rise)
        widget.move(start_pos)

        anim = QPropertyAnimation(widget, b"pos", self)
        anim.setDuration(duration)
        anim.setStartValue(start_pos)
        anim.setEndValue(end_pos)
        anim.setEasingCurve(QEasingCurve.OutCubic)

        # ---- 3. 旧页截图淡出 ----
        overlay = None
        fade = None
        if snapshot is not None and not snapshot.isNull():
            try:
                overlay = _FadeOverlay(self, snapshot)
                overlay.show()
                overlay.raise_()
                fade = QPropertyAnimation(overlay, b"opacity", self)
                fade.setDuration(duration)
                fade.setStartValue(1.0)
                fade.setEndValue(0.0)
                # 淡出用 OutQuad：开头掉得快些，尾巴收得柔和，
                # 避免和新页上浮"同时结束"显得仓促
                fade.setEasingCurve(QEasingCurve.OutQuad)
                fade.start(QAbstractAnimation.DeleteWhenStopped)
                self._overlay = overlay
            except Exception:
                overlay = None

        def _cleanup():
            widget.move(end_pos)
            self._busy = False
            if overlay is not None:
                try:
                    overlay.hide()
                    overlay.setParent(None)
                    overlay.deleteLater()
                except Exception:
                    pass
            self._overlay = None

        anim.finished.connect(_cleanup)
        widget.move(start_pos)
        anim.start(QAbstractAnimation.DeleteWhenStopped)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        # 覆盖层跟着内容区大小走
        if self._overlay is not None:
            try:
                self._overlay.setGeometry(self.rect())
            except Exception:
                pass
