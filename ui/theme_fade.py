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
from PySide6.QtGui import QColor, QPainter, QPainterPath
from PySide6.QtWidgets import QGraphicsOpacityEffect, QWidget

#: 后退时右下各收多少像素。24/18 在 1024x720 上约缩到 97%，
#: 能看出层次又不至于让内容明显缩水。
_INSET_RIGHT = 26
_INSET_BOTTOM = 20

#: 后退期间内容的不透明度（轻微变淡强化层次）
_RECEDE_OPACITY = 0.78

#: 各阶段时长（毫秒）
#:
#: ## 为什么比初版短

#: 初版 220/180/260（合计约 660ms），用户反馈"切换卡"。
#: 但卡的主体是换肤本身（2 秒），动画只是在它前后各加了一段。
#: 缩短到 150/120/180（约 450ms）后：
#:   * 后退仍然看得见（这是"界面让开了"的视觉信号，不能省）；
#:   * 但不再让人等两段明显的前奏和收尾。
_RECEDE_MS = 150
_VEIL_MS = 120
_RETURN_MS = 180

#: 加载图标的旋转周期（毫秒）与半径
_SPIN_MS = 900
_SPIN_R = 15


def _is_generator_fn(fn):
    """这个函数是不是生成器函数（用来决定是否分片执行）。"""
    import inspect

    try:
        return inspect.isgeneratorfunction(fn)
    except Exception:
        return False


def _pump(app, min_ms=16):
    """跑一轮事件循环，让动画推进。

    ## 为什么要等，而不是只 processEvents

    `processEvents()` 只处理**当前已排队**的消息就返回。而旋转图标的
    `QTimer` 是 16ms 触发一次——刚设完定时器那会儿还没有到期事件，
    `processEvents` 扫一遍就回来了，图标根本不动。

    ## 为什么是 16ms 而不是 48ms

    最初用 48ms（转 2~3 帧更明显），但换肤有 7 个分片，累计等待
    7×48 = 336ms，是纯开销。

    实测 16ms 就够：正好是图标定时器的周期，每个分片之间能转 1 帧。
    换肤本身要 2 秒，这 7 帧已经足够让用户感知到"它在转"，
    再多就是白白拖长总时长。

    ## 为什么不用 QEventLoop.exec()

    在 Qt 信号槽内部再开嵌套事件循环，Qt 会拒绝（"event loop is
    already running"）。实测那样做角度完全不变。
    """
    if app is None:
        return
    import time as _time

    deadline = _time.perf_counter() + max(0.001, min_ms / 1000.0)
    while _time.perf_counter() < deadline:
        try:
            app.processEvents()
        except Exception:
            pass
        _time.sleep(0.003)


def _drain(apply_fn):
    """不用动画时把换肤跑完（兼容生成器与普通函数）。"""
    from PySide6.QtCore import QCoreApplication

    app = QCoreApplication.instance()
    try:
        if _is_generator_fn(apply_fn):
            for _ in apply_fn():
                _pump(app)
        else:
            apply_fn()
    except Exception:
        import traceback
        traceback.print_exc()


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

    ## 对齐

    遮罩的矩形由宿主传入（见 `ThemeFader._veil_rect`），
    必须与窗口**自绘圆角底板**重合，而不是铺满整个窗口。
    `central` 铺满窗口 (0,0,w,h)，但底板只画在
    `(_PAD, _PAD, w-2*PAD, h-2*PAD)`——按 central 铺会盖住
    窗口的圆角和外阴影，看起来就是"没对齐、多出一圈"。
    """

    def __init__(self, parent, radius=10):
        super().__init__(parent)
        self._opacity = 0.0
        self._dark = False
        self._text = "正在切换主题…"
        self._radius = radius
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)

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
        path.addRoundedRect(QRectF(self.rect()), self._radius, self._radius)
        p.fillPath(path, base)

        # 遮罩自己的旋转动画已经移到子线程的独立窗口里
        # （见 ui/spinner_overlay.py）：主线程阻塞时这里画不动，
        # 而独立窗口至少能在阻塞解除的瞬间立即显示最新帧。


class ThemeFader(QObject):
    """主题切换的「后退 → 遮罩 → 换肤 → 归位」过渡。

    用法：

        fader = ThemeFader(self, host_widget, root_layout)
        fader.run(lambda: self._apply_theme(), dark=True)
    """

    def __init__(self, host, widget, layout=None, pad=0, radius=10):
        super().__init__(host)
        self._widget = widget
        self._layout = layout
        #: 窗口自绘底板的内缩量（= FramelessShell 的 _PAD）。
        #: 遮罩要贴合底板而不是铺满窗口，否则会盖住圆角与外阴影。
        self._pad = int(pad)
        self._radius = int(radius)
        self._effect = None
        self._veil = None
        self._overlay = None
        self._anim = None
        self._pending = None
        self._base_margins = None
        self._ins_r = 0.0
        self._ins_b = 0.0
        self._state = _AnimState(self, self._apply_inset)

    def _veil_rect(self):
        """遮罩应覆盖的矩形：与窗口自绘的圆角底板重合。

        `central` 铺满整个窗口 (0,0,w,h)，但窗口自绘的底板只画在
        `(PAD, PAD, w-2*PAD, h-2*PAD)`。遮罩按 central 铺会盖住
        圆角和外阴影——那就是用户看到的"没对齐"。

        最大化时窗口不画圆角/阴影（`_PAD` 语义失效），此时铺满即可。
        """
        host = self._widget
        if host is None:
            return None
        r = host.rect()
        if self._pad > 0 and r.width() > self._pad * 2 \
                and r.height() > self._pad * 2:
            from PySide6.QtCore import QRect
            return QRect(self._pad, self._pad,
                         r.width() - self._pad * 2,
                         r.height() - self._pad * 2)
        return r

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
            self._veil = _Veil(self._widget, radius=self._radius)
        self._veil.set_theme_dark(dark)
        self._resize_veil()
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
        if self._overlay is not None:
            self._shutdown_overlay()
        if self._veil is not None:
            try:
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
        """让遮罩贴合窗口底板的实际矩形（不是铺满 central）。"""
        if self._veil is None:
            return
        rect = self._veil_rect()
        if rect is not None:
            self._veil.setGeometry(rect)
            self._veil.raise_()

    def _show_overlay(self):
        """显示加载指示窗口。

        ## 为什么用纯 Win32 窗口而不是 Qt

        实测确认了 Qt 的硬约束：**所有 Qt 窗口的绘制都由 GUI 主线程执行**，
        与窗口属于哪个线程无关。同一 QWidget 上跑 20ms 定时器：

            正常运行时      ：12 次 paintEvent
            主线程阻塞 500ms： 0 次
            恢复后          ：11 次

        换肤期间主线程被 `setStyleSheet` 卡住（约 0.4~1 秒），
        Qt 窗口必然定格。

        Win32 的规则不同：「**窗口消息由创建它的线程派发**」。
        所以在子线程里 `CreateWindowExW` 的窗口，`WM_TIMER` / `WM_PAINT`
        都由子线程自己处理，完全不受主线程阻塞影响。

        实测：主线程阻塞 600ms 期间，`ui/win32_spinner.py` 的窗口
        角度从 45° 连续转到 162°——真正做到了动画不卡。
        """
        from ui.win32_spinner import Win32SpinnerWindow

        if self._overlay is None:
            dark = self._veil._dark if self._veil else False
            self._overlay = Win32SpinnerWindow("正在切换主题…", dark=dark)
        center = self._widget.rect().center()
        gp = self._widget.mapToGlobal(center)
        self._overlay.start(gp.x(), gp.y())

    def _shutdown_overlay(self):
        """停掉加载指示窗口（子线程会自行销毁窗口）。"""
        ov = self._overlay
        self._overlay = None
        if ov is None:
            return
        try:
            ov.stop()
            # join 会等子线程把窗口销毁掉；给足时间避免残留黑框
            ov.join(1.5)
        except Exception:
            pass

    # ------------------------------------------------------------ 对外
    def run(self, apply_fn, enabled=True, dark=False):
        """执行一轮过渡：后退 → 遮罩(+提示) → apply_fn() → 归位。

        `apply_fn` 可以是**生成器**：每次 `yield` 表示"这一步做完了"，
        过渡会在两次 yield 之间跑一轮事件循环，让加载图标继续转、
        遮罩动画不停顿。传普通函数则当作"一步做完"。
        """
        if not enabled or not self._widget.isVisible():
            # 不可见（后台/无头）就不做动画，直接换，省掉无谓等待
            _drain(apply_fn)
            return

        # 开关：用户关掉了"切换主题"动画 → 直接应用，不做遮罩过渡
        try:
            from core.anim_settings import enabled as _anim_on

            if not _anim_on("theme"):
                _drain(apply_fn)
                return
        except Exception:
            pass

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
            # 显示独立的加载指示器窗口（子线程负责出帧）
            try:
                self._show_overlay()
            except RuntimeError:
                pass
            # ---------- 阶段二：换肤（分片执行，图标能继续转） ----------
            self._run_steps(apply_fn)

        group.finished.connect(_on_receded)
        self._anim = group
        group.start()

    def _run_steps(self, apply_fn):
        """分片执行换肤，每片之间跑一轮事件循环。

        ## 为什么必须分片

        换主题的各个步骤（app 级 QSS、主窗口重铺、子页广播…）分开做
        都只要几百毫秒。如果一口气同步跑完，事件循环在这 1~2 秒里
        完全不转——**加载图标会定格**，遮罩也纹丝不动，用户看到的
        就是一个僵住的界面。

        每完成一片就跑一轮 `processEvents()`：
          * 图标的旋转定时器能触发，视觉上一直在转；
          * 遮罩和内容的动画不会停顿；
          * 代价是整体多花几十毫秒（事件循环调度的开销），值得。

        ⚠ 不能改用后台线程：Qt 的控件操作必须在主线程，
        跨线程改样式会直接崩。
        """
        from PySide6.QtCore import QCoreApplication

        app = QCoreApplication.instance()
        try:
            if hasattr(apply_fn, "__next__") or (
                    callable(apply_fn) and _is_generator_fn(apply_fn)):
                steps = apply_fn()
                for _ in steps:
                    _pump(app)
            else:
                # 普通函数：至少让图标在开始前动一下
                _pump(app)
                apply_fn()
                _pump(app)
        except Exception:
            import traceback
            traceback.print_exc()
        finally:
            self._start_return()

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
            if self._overlay is not None:
                self._shutdown_overlay()
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
