"""主题切换的过渡动画：界面 3D 后退 + 遮罩 + 加载提示 + 归位。

## 效果

切换主题时：

    ① 后退（220ms）   整个内容区向右下"收"（左上的原点固定），
                      视觉上就是朝左上方后退的透视感；同时微微变淡。
    ② 遮罩浮现(180ms) 一层半透明遮罩盖住界面，中间浮现：
                        ⏳ 一个旋转的加载图标
                        「正在切换主题…」
    ③ 换肤（1~2.5s）  所有耗时工作在这里完成，卡顿被遮罩盖住。
    ④ 归位（260ms）   遮罩与提示淡出，内容退回来——此时已是新主题。

## 为什么用「右下收边」表现后退

内容装在布局里。直接改子控件的 `geometry` 会被布局管理器抢回，
但改**布局自己的 `setContentsMargins()`** 一定生效：
把右、下边距一起增大，左上保持不动，内容就是朝左上角收缩。
配合轻微变淡，就有了纵深方向退后的感觉。

## 为什么必须给用户一个"正在处理"的说明

换肤是同步阻塞的：`setStyleSheet` 要让 Qt 重算几百个控件的样式，
实测 1~2.5 秒，期间事件循环完全不转，**加载动画也转不动**。

既然无法让界面保持响应，就必须让用户理解"它在忙、这是正常的"。
旋转图标 + 明确的文字承担这个职责——用户看到说明就不会以为程序挂了。

⚠ 因为事件循环被阻塞，那个旋转图标实际上只在换肤**前后**会转，
换肤期间是定格的。这是无法回避的限制，但"有个图标停在那里 +
写着正在切换主题"远比"界面卡住不动"更好接受。

## 三个实现要点

### 1. QPropertyAnimation 只认 Qt 的 Property

用 Python 的 `property()` 做动画目标会报
`you're trying to animate a non-existing property`。
必须用 `PySide6.QtCore.Property` 声明。

### 2. 效果只在动画期间存在

`QGraphicsOpacityEffect` 会让控件走离屏渲染路径，常驻会拖慢
日常绘制。进入动画时挂上，结束立刻摘掉。

### 3. 遮罩与提示都是叠层控件

它们是 `self._widget` 的子控件、绝对定位盖在最上层
（`raise_()`）。这样能独立做透明度动画，不会和内容的
缩放/变淡互相干扰。
"""

from PySide6.QtCore import (
    Property, QEasingCurve, QObject, QParallelAnimationGroup,
    QPropertyAnimation, QRectF, Qt, QTimer,
)
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QGraphicsOpacityEffect, QWidget

#: 后退时右下各收多少像素。24/18 在 1024x720 上约缩到 97%，
#: 能看出层次又不至于让内容明显缩水。
_INSET_RIGHT = 26
_INSET_BOTTOM = 20

#: 后退期间内容的不透明度（轻微变淡强化层次）
_RECEDE_OPACITY = 0.78

#: 各阶段时长（毫秒）
_RECEDE_MS = 220
_VEIL_MS = 180
_RETURN_MS = 260

#: 加载图标的旋转周期（毫秒）与半径
_SPIN_MS = 900
_SPIN_R = 15


class _AnimState(QObject):
    """承载两个可动画的数值：右下收边量。

    ⚠ 必须用 Qt 的 `Property`（不是 Python 的 `property`），
    否则 QPropertyAnimation 会报 "non-existing property"。
    """

    changed = None  # 占位，避免 pylint 抱怨（实际不用信号）

    def __init__(self, parent, on_change):
        super().__init__(parent)
        self._r = 0.0
        self._b = 0.0
        self._on_change = on_change

    def _get_r(self):
        return self._r

    def _set_r(self, v):
        self._r = float(v)
        self._on_change(self._r, self._b)

    def _get_b(self):
        return self._b

    def _set_b(self, v):
        self._b = float(v)
        self._on_change(self._r, self._b)

    inset_right = Property(float, _get_r, _set_r)
    inset_bottom = Property(float, _get_b, _set_b)


class _Veil(QWidget):
    """盖在内容上的遮罩 + 居中加载提示。

    自绘内容：半透明底色（带圆角）+ 旋转弧线 + 一行说明文字。
    透明度由自己的 `opacity` 属性控制，供动画驱动。
    """

    def __init__(self, parent):
        super().__init__(parent)
        self._opacity = 0.0
        self._angle = 0.0
        self._dark = False
        self._text = "正在切换主题…"
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        # 旋转动画：只在遮罩可见时跑
        self._spin_timer = QTimer(self)
        self._spin_timer.setInterval(16)     # ~60fps
        self._spin_timer.timeout.connect(self._on_spin)

    # ---------------- 属性 ----------------
    def _get_opacity(self):
        return self._opacity

    def _set_opacity(self, v):
        self._opacity = max(0.0, min(1.0, float(v)))
        self.update()

    opacity = Property(float, _get_opacity, _set_opacity)

    def set_theme_dark(self, dark):
        self._dark = bool(dark)
        self.update()

    def start_spin(self):
        if not self._spin_timer.isActive():
            self._spin_timer.start()

    def stop_spin(self):
        self._spin_timer.stop()

    def _on_spin(self):
        self._angle = (self._angle + 360.0 * 16 / _SPIN_MS) % 360.0
        self.update()

    # ---------------- 绘制 ----------------
    def paintEvent(self, _e):
        if self._opacity <= 0.001:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)

        # 底色：浅色主题用白、深色主题用深灰，都半透明
        base = QColor(250, 250, 252) if not self._dark else QColor(32, 32, 36)
        base.setAlpha(int((170 if not self._dark else 150) * self._opacity))
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()), 10, 10)
        p.fillPath(path, base)

        # 居中：图标 + 文字
        cx = self.width() / 2.0
        cy = self.height() / 2.0
        alpha = self._opacity

        accent = QColor(0, 120, 212) if not self._dark else QColor(96, 205, 255)
        accent.setAlpha(int(255 * alpha))

        # 旋转弧线（缺口圆环）
        pen = QPen(accent)
        pen.setWidth(3)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        ring = QRectF(cx - _SPIN_R, cy - 44 - _SPIN_R,
                      _SPIN_R * 2, _SPIN_R * 2)
        # Qt 角度单位是 1/16 度，逆时针为正
        p.drawArc(ring, int(-self._angle * 16), int(270 * 16))

        # 文字
        f = QFont()
        f.setPixelSize(14)
        f.setWeight(QFont.Weight.Medium)
        p.setFont(f)
        txt_col = QColor(40, 40, 45) if not self._dark else QColor(235, 235, 240)
        txt_col.setAlpha(int(255 * alpha))
        p.setPen(txt_col)
        text_rect = QRectF(0, cy - 6, self.width(), 28)
        p.drawText(text_rect, Qt.AlignHCenter | Qt.AlignTop, self._text)


class ThemeFader(QObject):
    """主题切换的「后退 → 遮罩 → 换肤 → 归位」过渡。

    用法：

        fader = ThemeFader(self, host_widget, root_layout)
        fader.run(lambda: self._apply_theme(), dark=True)
    """

    def __init__(self, host, widget, layout=None):
        super().__init__(host)
        self._widget = widget
        self._layout = layout
        self._effect = None
        self._veil = None
        self._anim = None
        self._pending = None
        self._base_margins = None
        self._ins_r = 0.0
        self._ins_b = 0.0
        self._state = _AnimState(self, self._apply_inset)

    # ------------------------------------------------------------ 内部
    def _apply_inset(self, right, bottom):
        """把"收边量"换算成布局边距（左上保持基准值）。"""
        if self._layout is None or self._base_margins is None:
            return
        l, t, r0, b0 = self._base_margins
        self._layout.setContentsMargins(
            l, t, r0 + int(round(right)), b0 + int(round(bottom)))

    def _ensure_effect(self):
        if self._effect is None:
            self._effect = QGraphicsOpacityEffect(self._widget)
            self._effect.setOpacity(1.0)
        return self._effect

    def _ensure_veil(self, dark):
        if self._veil is None:
            self._veil = _Veil(self._widget)
        self._veil.set_theme_dark(dark)
        self._veil.setGeometry(self._widget.rect())
        self._veil.show()
        self._veil.raise_()
        return self._veil

    def _remember_margins(self):
        if self._layout is not None and self._base_margins is None:
            m = self._layout.contentsMargins()
            self._base_margins = (m.left(), m.top(), m.right(), m.bottom())

    def _teardown(self):
        """恢复常态：边距归位、藏遮罩、摘 effect。"""
        self._ins_r = 0.0
        self._ins_b = 0.0
        if self._layout is not None and self._base_margins is not None:
            l, t, r, b = self._base_margins
            self._layout.setContentsMargins(l, t, r, b)
        if self._veil is not None:
            try:
                self._veil.stop_spin()
                self._veil.opacity = 0.0
                self._veil.hide()
            except RuntimeError:
                self._veil = None
        if self._effect is not None:
            try:
                self._widget.setGraphicsEffect(None)
            except RuntimeError:
                pass
            self._effect = None
        self._anim = None

    def _stop_anim(self):
        if self._anim is not None:
            try:
                self._anim.stop()
            except RuntimeError:
                pass
            self._anim = None
        if self._effect is not None:
            try:
                self._effect.setOpacity(1.0)
            except RuntimeError:
                pass

    def _cancel_pending(self):
        if self._pending is not None:
            try:
                self._pending.stop()
            except RuntimeError:
                pass
            self._pending = None

    def _resize_veil(self):
        if self._veil is not None:
            self._veil.setGeometry(self._widget.rect())
            self._veil.raise_()

    # ------------------------------------------------------------ 对外
    def run(self, apply_fn, enabled=True, dark=False):
        """执行一轮过渡：后退 → 遮罩(+提示) → apply_fn() → 归位。"""
        if not enabled or not self._widget.isVisible():
            # 不可见（后台/无头）就不做动画，直接换，省掉无谓等待
            apply_fn()
            return

        self._stop_anim()
        self._cancel_pending()
        self._remember_margins()

        self._widget.setGraphicsEffect(self._ensure_effect())
        veil = self._ensure_veil(dark)
        self._resize_veil()

        # ---------- 阶段一：后退 + 遮罩浮现（并行） ----------
        group = QParallelAnimationGroup(self)

        # 1a. 内容向右下收（= 朝左上后退）
        self._ins_r = 0.0
        self._ins_b = 0.0
        if self._layout is not None and self._base_margins is not None:
            ar = QPropertyAnimation(self._state, b"inset_right", self)
            ar.setDuration(_RECEDE_MS)
            ar.setStartValue(0.0)
            ar.setEndValue(float(_INSET_RIGHT))
            ar.setEasingCurve(QEasingCurve.OutCubic)
            group.addAnimation(ar)

            ab = QPropertyAnimation(self._state, b"inset_bottom", self)
            ab.setDuration(_RECEDE_MS)
            ab.setStartValue(0.0)
            ab.setEndValue(float(_INSET_BOTTOM))
            ab.setEasingCurve(QEasingCurve.OutCubic)
            group.addAnimation(ab)

        # 1b. 内容微微变淡
        eff = self._effect
        fade = QPropertyAnimation(eff, b"opacity", self)
        fade.setDuration(_RECEDE_MS)
        fade.setStartValue(1.0)
        fade.setEndValue(_RECEDE_OPACITY)
        fade.setEasingCurve(QEasingCurve.OutCubic)
        group.addAnimation(fade)

        # 1c. 遮罩浮现（含加载提示）
        veil_anim = QPropertyAnimation(veil, b"opacity", self)
        veil_anim.setDuration(_VEIL_MS)
        veil_anim.setStartValue(0.0)
        veil_anim.setEndValue(1.0)
        veil_anim.setEasingCurve(QEasingCurve.OutQuad)
        group.addAnimation(veil_anim)

        def _on_receded():
            # 遮罩出来之后开始转图标（虽然换肤期间会卡住，但前后能转）
            try:
                veil.start_spin()
            except RuntimeError:
                pass
            # ---------- 阶段二：换肤（卡顿藏在遮罩下） ----------
            try:
                apply_fn()
            except Exception:
                import traceback
                traceback.print_exc()
            finally:
                self._start_return()

        group.finished.connect(_on_receded)
        self._anim = group
        group.start()

    def _start_return(self):
        """阶段三：遮罩散去 + 内容退回来。"""
        eff = self._effect
        if eff is None:
            self._teardown()
            return
        veil = self._veil
        group = QParallelAnimationGroup(self)

        fade = QPropertyAnimation(eff, b"opacity", self)
        fade.setDuration(_RETURN_MS)
        fade.setStartValue(_RECEDE_OPACITY)
        fade.setEndValue(1.0)
        fade.setEasingCurve(QEasingCurve.OutCubic)
        group.addAnimation(fade)

        if veil is not None:
            try:
                veil.stop_spin()
            except RuntimeError:
                pass
            va = QPropertyAnimation(veil, b"opacity", self)
            va.setDuration(_RETURN_MS)
            va.setStartValue(1.0)
            va.setEndValue(0.0)
            va.setEasingCurve(QEasingCurve.InQuad)
            group.addAnimation(va)

        if self._layout is not None and self._base_margins is not None:
            ar = QPropertyAnimation(self._state, b"inset_right", self)
            ar.setDuration(_RETURN_MS)
            ar.setStartValue(float(self._ins_r))
            ar.setEndValue(0.0)
            ar.setEasingCurve(QEasingCurve.OutCubic)
            group.addAnimation(ar)

            ab = QPropertyAnimation(self._state, b"inset_bottom", self)
            ab.setDuration(_RETURN_MS)
            ab.setStartValue(float(self._ins_b))
            ab.setEndValue(0.0)
            ab.setEasingCurve(QEasingCurve.OutCubic)
            group.addAnimation(ab)

        group.finished.connect(self._teardown)
        self._anim = group
        group.start()

    def schedule(self, apply_fn, delay_ms=0, dark=False):
        """延迟执行过渡（避免在信号处理中间改控件树）。"""
        self._cancel_pending()
        timer = QTimer(self)
        timer.setSingleShot(True)

        def _fire():
            try:
                self.run(apply_fn, dark=dark)
            finally:
                if self._pending is timer:
                    self._pending = None

        timer.timeout.connect(_fire)
        self._pending = timer
        timer.start(delay_ms)
