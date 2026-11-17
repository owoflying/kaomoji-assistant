"""子线程驱动的加载指示器窗口。

## 为什么单独做一个窗口

换肤期间主线程会被 `setStyleSheet` 卡住 0.3~1 秒。主窗口里的任何
控件在这期间都不会重绘，旋转图标看起来就是定格的。

把指示器做成**独立顶层窗口**之后，它有自己的窗口句柄和绘制表面。
虽然绘制仍要主线程驱动，但好处是：
  * 它不参与主窗口的 QSS 级联，样式重算不波及它；
  * 主窗口的缩放/遮罩动画不会带着它一起变形；
  * 定位、圆角、阴影都可以独立控制。

## 子线程负责什么

**画帧**。子线程用 `QImage` + `QPainter` 把旋转进度画成位图，
通过信号把 `QImage` 发给主线程；主线程只做
`QPixmap.fromImage()` + `setPixmap()`。

## ⚠ 必须说清楚的限制

主线程完全阻塞时（`setStyleSheet` 期间），它**收不到子线程发来的帧**，
所以画面仍然会停住。子线程的价值在于：

  * 阻塞解除的瞬间，最新一帧已经在队列里等着，立刻就能显示——
    不会出现"恢复后还要等下一帧才动"的滞后；
  * 阻塞期间子线程继续算帧，恢复后是连续的，不是跳变。

真正让冻结变短的，是减少控件数量（见 `_existing_page` 的说明）。
"""
from PySide6.QtCore import (
    QObject, QPoint, QSize, Qt, QThread, QTimer, Signal,
)
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QWidget

#: 每帧覆盖的角度
_FRAME_DEG = 9.0
#: 帧间隔（毫秒）。40ms ≈ 25fps，对加载指示器足够且省 CPU。
_FRAME_MS = 40
#: 图标的逻辑尺寸（像素）
ICON_PX = 40


class _SpinnerWorker(QObject):
    """在子线程里把旋转进度画成 QImage。

    ⚠ 定时器必须在**子线程里**创建和启动（通过 `started` 信号触发），
    不能从主线程直接调 `start()`——那会让 QTimer 归属主线程，
    而后又被 `stop()` 跨线程操作，Qt 会报
    `Timers cannot be stopped from another thread`。

    这里的做法：主线程只发信号，真正的 `_init_timer` 在子线程执行。
    """

    frame_ready = Signal(QImage)

    def __init__(self):
        super().__init__()
        self._angle = 0.0
        self._color = QColor(0, 120, 212)
        self._timer = None

    def set_color(self, color):
        self._color = QColor(color)

    def _init_timer(self):
        """**在子线程里**创建并启动定时器。"""
        if self._timer is None:
            self._timer = QTimer(self)
            self._timer.setInterval(_FRAME_MS)
            self._timer.timeout.connect(self._tick)
        self._timer.start()
        self._tick()          # 立刻出一帧，避免第一帧前有空白期

    def _stop_timer(self):
        """**在子线程里**停止定时器。"""
        if self._timer is not None:
            self._timer.stop()

    def _tick(self):
        self._angle = (self._angle + _FRAME_DEG) % 360.0
        img = QImage(ICON_PX, ICON_PX, QImage.Format_ARGB32)
        img.fill(Qt.transparent)
        p = QPainter(img)
        try:
            p.setRenderHint(QPainter.Antialiasing)
            pen = QPen(self._color)
            pen.setWidth(3)
            pen.setCapStyle(Qt.RoundCap)
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            margin = 4
            rect = img.rect().adjusted(margin, margin, -margin, -margin)
            # Qt 角度单位 1/16 度，逆时针为正；这里顺时针转
            p.drawArc(rect, int(-self._angle * 16), int(280 * 16))
        finally:
            p.end()
        self.frame_ready.emit(img)


class SpinnerOverlay(QWidget):
    """独立的加载指示器窗口：圆角底卡片 + 旋转弧线 + 说明文字。

    定位在主窗口中央，作为无边框工具窗口浮在上面。
    """

    #: 跨线程通知子线程启动/停止定时器（见 _SpinnerWorker 的说明）
    _req_start = Signal()
    _req_stop = Signal()

    def __init__(self, parent=None):
        super().__init__(
            None,
            Qt.ToolTip | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint,
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)

        self._text = "正在切换主题…"
        self._dark = False
        self._pixmap = None

        # ---- 子线程：画帧 ----
        self._thread = QThread()
        self._worker = _SpinnerWorker()
        self._worker.moveToThread(self._thread)
        self._worker.frame_ready.connect(self._on_frame)
        # 跨线程排队调用：请求会投递到子线程的事件循环里执行，
        # 保证 QTimer 的创建/销毁都发生在它自己的线程
        self._req_start.connect(self._worker._init_timer)
        self._req_stop.connect(self._worker._stop_timer)
        self._thread.start()

        self._update_size()

    # ------------------------------------------------------------ 外观
    def set_dark(self, dark):
        self._dark = bool(dark)
        self._worker.set_color("#60cdff" if dark else "#0078d4")
        self.update()

    def set_text(self, text):
        self._text = str(text)
        self._update_size()
        self.update()

    def _update_size(self):
        w = max(200, len(self._text) * 16 + 60)
        self.setFixedSize(QSize(w, 110))

    # ------------------------------------------------------------ 生命周期
    def start(self, center_global: QPoint):
        """显示在指定全局坐标的中心，并启动子线程出帧。"""
        g = self.geometry()
        self.move(center_global.x() - g.width() // 2,
                  center_global.y() - g.height() // 2)
        self.show()
        self.raise_()
        self._req_start.emit()

    def stop(self):
        try:
            self._req_stop.emit()
        except RuntimeError:
            pass
        self.hide()

    def shutdown(self):
        """彻底停止子线程（窗口销毁时调用）。

        ⚠ 顺序很重要，否则会报
        `Timers cannot be stopped from another thread`：

            1. 用**排队信号**让子线程自己停掉定时器（QTimer 归属它）；
            2. 等它处理完；
            3. 再 quit 线程。

        直接从主线程 `thread.quit()` 而不管定时器，Qt 会在主线程里
        析构属于子线程的 QTimer，于是报上面那条警告。
        """
        self._req_stop.emit()          # 排队到子线程执行
        self.hide()
        if self._thread.isRunning():
            # 让子线程先把 stop 请求处理掉（此时它的事件循环还在跑），
            # 否则 quit() 会先掐掉循环、定时器没人停
            from PySide6.QtCore import QCoreApplication

            QCoreApplication.processEvents()
            self._thread.quit()
            if not self._thread.wait(2000):
                self._thread.terminate()
                self._thread.wait(500)

    def closeEvent(self, e):
        """关闭窗口时也要把线程收干净。"""
        self.shutdown()
        super().closeEvent(e)

    # ------------------------------------------------------------ 绘制
    def _on_frame(self, img: QImage):
        # 主线程里把 QImage 转 QPixmap（QPixmap 只能在 GUI 线程用）
        self._pixmap = QPixmap.fromImage(img)
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()

        # 圆角底卡片
        bg = QColor(250, 250, 252, 235) if not self._dark \
            else QColor(40, 40, 44, 235)
        p.setBrush(bg)
        p.setPen(QColor(0, 0, 0, 30))
        p.drawRoundedRect(0, 0, w - 1, h - 1, 12, 12)

        # 旋转图标
        if self._pixmap is not None:
            p.drawPixmap((w - ICON_PX) // 2, 16, self._pixmap)

        # 说明文字
        f = QFont()
        f.setPixelSize(14)
        p.setFont(f)
        p.setPen(QColor(40, 40, 45) if not self._dark
                 else QColor(235, 235, 240))
        p.drawText(0, 62, w, h - 62,
                   Qt.AlignHCenter | Qt.AlignTop, self._text)


def parent_center_global(widget):
    """给一个控件算它中心的全局坐标（用于把浮层摆到它中间）。"""
    try:
        r = widget.rect()
        return widget.mapToGlobal(QPoint(r.width() // 2, r.height() // 2))
    except Exception:
        from PySide6.QtGui import QGuiApplication

        scr = QGuiApplication.primaryScreen()
        if scr is None:
            return QPoint(0, 0)
        return scr.availableGeometry().center()
