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
from PySide6.QtWidgets import QGraphicsOpacityEffect, QStackedWidget, QWidget
from PySide6.QtCore import (
    QAbstractAnimation, QPropertyAnimation, Qt,
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

    def slide_to(self, widget, duration=None, rise=8):
        """切换页面：**让新页自身淡入**，不加任何覆盖层。

        ## 三版的教训

        **v1**：给新页 `move()` 位移动画。
        新页参与布局，布局每帧把它改回原位——动画和布局拉锯，
        画面抖动、跳帧。

        **v2**：旧页截图淡出。
        `old.grab()` 一次 30~50ms 同步阻塞在按键瞬间；两套 16ms tick
        节奏不同步。

        **v3**：纯色覆盖层淡出。**这是用户说"闪一下白屏"的那一版**。
        做法是先铺一层不透明的表面色盖住新页，再让它消失——
        用户看到的当然是"先白一下，然后页面突然出现"，而不是淡入。
        而且每次切页都要 new 一个全屏覆盖控件 + 跑一套动画，更卡。

        ## 现在：直接给新页加透明度效果

            eff.setOpacity(0) → 1，180ms OutCubic

        实测整页加 `QGraphicsOpacityEffect` 只多 8ms（94 → 102ms），
        完全可接受——之前"opacity 很贵"的结论是在窗口半透明 + 材质
        叠加的老架构上测的，现在前提已经变了。

        动画结束立刻摘掉 effect，避免它长期挂在页面上拖慢滚动。
        """
        # 安全阀：上一次动画没正常结束（窗口隐藏/异常）时不要永久卡住
        if self._busy:
            import time as _t

            if _t.time() - getattr(self, "_busy_since", 0) > 1.0:
                self._busy = False
            else:
                return

        if widget is None or widget is self.currentWidget():
            return

        # 开关
        try:
            from core.anim_settings import enabled as _anim_on

            anim_on = _anim_on("page_switch")
        except Exception:
            anim_on = True

        geo = widget.geometry()
        if (not anim_on or geo.width() <= 0 or geo.height() <= 0
                or not self.isVisible()):
            # 关掉动画 / 窗口不可见 / 尺寸无效 → 直接切
            widget.setGraphicsEffect(None)
            self.setCurrentWidget(widget)
            return

        import time as _t

        self._busy = True
        self._busy_since = _t.time()
        self.setCurrentWidget(widget)

        try:
            # ⚠ effect 必须挂在 widget 上并且持有引用。
            # 只写 widget.setGraphicsEffect(eff) 时，Python 侧的 eff 一旦
            # 被回收，C++ 对象随之销毁，属性的 owner 就没了——
            # QPropertyAnimation 会报
            #   "you're trying to animate a non-existing property opacity"
            # 所以这里同时把它挂到 self 上保活。
            eff = QGraphicsOpacityEffect(widget)
            eff.setOpacity(0.0)
            widget.setGraphicsEffect(eff)
            self._page_effect = eff
            anim = QPropertyAnimation(eff, b"opacity", self)
            # 时长/曲线走统一规范（core/anim_tuning.py）。
            # 原来写的是 180ms + OutCubic —— OutCubic 前 30% 就走完 66%，
            # 末段拖尾被读成"卡住"。
            from core.anim_tuning import apply as _tune

            _tune(anim, "page")
            anim.setStartValue(0.0)
            anim.setEndValue(1.0)

            def _done():
                # 必须摘掉：effect 留着会让该页每次重绘都多一次离屏合成
                try:
                    widget.setGraphicsEffect(None)
                except Exception:
                    pass
                self._page_effect = None
                self._busy = False

            anim.finished.connect(_done)
            self._page_anim = anim        # 防止被 GC
            anim.start(QAbstractAnimation.DeleteWhenStopped)
        except Exception:
            widget.setGraphicsEffect(None)
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
