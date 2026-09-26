"""主题化无边框弹窗：供插件（与将来其它模块）复用的一致观感容器。

背景：主窗口是 WA_TranslucentBackground + 自绘圆角阴影的无边框窗口，
若插件直接 new 一个原生 QDialog，会与整体 Win11 观感割裂（方角、系统主题框、
不跟随应用深浅色）。本模块提供统一的自绘容器，插件只需提供一个内容 QWidget，
由主程序负责标题栏、拖拽、阴影、圆角遮蔽与主题注入。

两个入口：
    show_plugin_dialog(body, title, ...)  -> PluginDialog   自定义内容
    show_message(...)  /  ask(...)                          轻量消息框（同步返回结果）

模态约定：QWidget 没有 QDialog.exec()，这里用 QEventLoop 模拟
（`setWindowModality(Qt.ApplicationModal)` + 本地事件循环 + finished 信号退出），
关闭时会 emit finished(result)，返回值语义与 QDialog 一致（1=确定，0=取消）。
"""
from PySide6.QtCore import Qt, QRect, QRectF, QEventLoop, Signal
from PySide6.QtGui import (
    QColor, QPainter, QPainterPath, QPen, QLinearGradient, QRegion, QTransform,
)
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
)

from ui.win11_theme import Theme, parse_color
from ui.win_style import apply_backdrop, apply_dark_mode, DWMSBT_TRANSIENTWINDOW
from ui.fluent_icons import icon_label

# 与 LogViewer / 主窗口保持一致的视觉参数
_RADIUS = 12
_PAD = 12
_TITLEBAR_H = 40


class PluginDialog(QWidget):
    """自定义标题栏 + 内容区 + 自绘（阴影/圆角/渐变/边框）的无边框弹窗。

    body 由调用方（插件）提供，本容器只负责把它摆进统一外壳。
    """

    finished = Signal(int)

    def __init__(self, body=None, title="", theme=None, parent=None,
                 width=0, height=0):
        super().__init__(parent)
        self._theme = theme if isinstance(theme, Theme) else Theme(theme or "light")
        self._drag_pos = None
        self._result = 0
        self.setWindowTitle(title or "插件")
        self.setWindowFlags(Qt.Window | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        if width and height:
            self.resize(int(width), int(height))
        elif width:
            self.resize(int(width), 420)
        else:
            self.resize(560, 420)
        self.setMinimumSize(360, 240)
        self._init_ui(body, title)
        # 圆角遮罩必须在有尺寸之后设置
        self._update_mask()

    # ---------- UI ----------
    def _init_ui(self, body, title):
        t = self._theme
        root = QVBoxLayout(self)
        root.setContentsMargins(_PAD, _PAD, _PAD, _PAD)
        root.setSpacing(0)

        # 标题栏
        bar = QWidget()
        bar.setFixedHeight(_TITLEBAR_H)
        broot = QHBoxLayout(bar)
        broot.setContentsMargins(12, 0, 8, 0)
        broot.setSpacing(8)
        broot.addWidget(icon_label("plugin", 18, t.accent))
        ttl = QLabel(title or "插件")
        ttl.setObjectName("PageTitle")
        broot.addWidget(ttl)
        broot.addStretch(1)
        self._close_btn = QPushButton("\u2715")
        self._close_btn.setObjectName("TitleButton")
        self._close_btn.setFixedSize(32, 32)
        self._close_btn.setCursor(Qt.PointingHandCursor)
        self._close_btn.clicked.connect(self.reject)
        broot.addWidget(self._close_btn)
        root.addWidget(bar)

        # 内容区
        content = QWidget()
        cl = QVBoxLayout(content)
        cl.setContentsMargins(16, 10, 16, 16)
        cl.setSpacing(12)
        if body is not None:
            body.setParent(content)
            cl.addWidget(body, 1)
        root.addWidget(content, 1)
        self._content_layout = cl

        # 完整主题 QSS 兜住外壳内的原生控件样式
        self.setStyleSheet(t.style_sheet())

    # ---------- 外壳内的布局（供消息框追加按钮行） ----------
    def content_layout(self):
        return self._content_layout

    def set_result(self, value):
        self._result = int(value)

    def result(self):
        return self._result

    def accept(self):
        self.set_result(1)
        self.close()

    def reject(self):
        self.set_result(0)
        self.close()

    # ---------- 模态 exec（QWidget 版） ----------
    def exec(self):
        """同步显示并返回结果（1=确定 / 0=取消）。"""
        self.setWindowModality(Qt.ApplicationModal)
        self.setAttribute(Qt.WA_ShowModal, True)
        self.show()
        self.raise_()
        self.activateWindow()
        loop = QEventLoop()
        self.finished.connect(loop.quit)
        try:
            loop.exec()
        finally:
            try:
                self.finished.disconnect(loop.quit)
            except (RuntimeError, TypeError):
                pass
            self.setAttribute(Qt.WA_ShowModal, False)
        return self._result

    # ---------- 圆角遮罩 / 自绘 / 拖拽 ----------
    def showEvent(self, e):
        super().showEvent(e)
        # 与主窗口/候选条一致：DWM 亚克力磨砂 + 标题栏深浅色跟随主题。
        # 无 DWM（Win10 以下/其他平台）时 apply_backdrop 为 no-op，靠自绘不透明面板兜底。
        try:
            hwnd = int(self.winId())
            apply_backdrop(hwnd, DWMSBT_TRANSIENTWINDOW)
            apply_dark_mode(hwnd, self._theme.dark)
        except Exception:
            pass

    def _update_mask(self):
        w, h = self.width(), self.height()
        if w <= 0 or h <= 0:
            return
        rect = QRect(_PAD, _PAD, w - 2 * _PAD, h - 2 * _PAD)
        pp = QPainterPath()
        pp.addRoundedRect(QRectF(rect), _RADIUS, _RADIUS)
        poly = pp.toFillPolygon(QTransform())
        self.setMask(QRegion(poly.toPolygon()))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._update_mask()

    def closeEvent(self, e):
        self.finished.emit(self._result)
        super().closeEvent(e)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        t = self._theme
        panel = QRect(_PAD, _PAD, self.width() - 2 * _PAD, self.height() - 2 * _PAD)

        sc = parse_color(t.shadow_color)
        base = sc.alpha()
        for d in range(_PAD, 0, -1):
            a = max(1, int(base * (1 - (d - 1) / _PAD) * 0.35))
            col = QColor(sc.red(), sc.green(), sc.blue(), a)
            rr = _RADIUS + d * 0.5
            sp = QPainterPath()
            sp.addRoundedRect(QRectF(panel.adjusted(-d, -d, d, d)), rr, rr)
            p.fillPath(sp, col)

        pp = QPainterPath()
        pp.addRoundedRect(QRectF(panel), _RADIUS, _RADIUS)
        # 面板基底强制不透明：window_tint 的固有 alpha 只有 0.85/0.88，直接用会
        # 比主窗口（_adjusted_panel_base 默认映射到 1.0）明显更透，深色桌面上
        # 甚至发黑。弹窗没有 panel_alpha 配置，取主窗口默认（100%）的等效实色。
        tint = parse_color(t.window_tint)
        tint.setAlpha(255)
        p.fillPath(pp, tint)

        grad = QLinearGradient(0, panel.top(), 0, panel.bottom())
        grad.setColorAt(0, parse_color(t.window_grad_top))
        grad.setColorAt(1, parse_color(t.window_grad_bottom))
        p.fillPath(pp, grad)

        pen = QPen(parse_color(t.window_border))
        pen.setWidth(1)
        p.strokePath(pp, pen)

        super().paintEvent(e)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and e.pos().y() < _TITLEBAR_H + _PAD:
            self._drag_pos = e.globalPos() - self.frameGeometry().topLeft()
            e.accept()
        else:
            super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if e.buttons() == Qt.LeftButton and self._drag_pos is not None:
            self.move(e.globalPos() - self._drag_pos)
            e.accept()
        else:
            super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        self._drag_pos = None
        super().mouseReleaseEvent(e)


def show_plugin_dialog(body=None, title="", theme=None, parent=None,
                       width=0, height=0, modal=False):
    """弹出自定义内容弹窗；modal=True 时同步阻塞并返回结果码。"""
    dlg = PluginDialog(body, title, theme, parent, width, height)
    if modal:
        dlg.exec()
    else:
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()
    return dlg


def show_message(text, title="提示", theme=None, parent=None,
                 ok_text="确定", cancel_text=None):
    """轻量消息框（自绘外壳，观感与统一窗口一致）。

    传入 cancel_text 时返回 True/False，否则恒返回 True。
    """
    t = theme if isinstance(theme, Theme) else Theme(theme or "light")
    body = QWidget()
    lay = QVBoxLayout(body)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(14)

    lbl = QLabel(text or "")
    lbl.setWordWrap(True)
    lbl.setObjectName("BodyText")
    lay.addWidget(lbl, 1)

    row = QHBoxLayout()
    row.addStretch(1)
    holder = {}

    def _finish(value):
        holder["dlg"].set_result(value)
        holder["dlg"].close()

    if cancel_text:
        btn_no = QPushButton(cancel_text)
        btn_no.setCursor(Qt.PointingHandCursor)
        btn_no.clicked.connect(lambda: _finish(0))
        row.addWidget(btn_no)
    btn_ok = QPushButton(ok_text or "确定")
    btn_ok.setObjectName("AccentButton")
    btn_ok.setCursor(Qt.PointingHandCursor)
    btn_ok.clicked.connect(lambda: _finish(1))
    row.addWidget(btn_ok)
    lay.addLayout(row)

    dlg = PluginDialog(body, title, t, parent, width=460, height=0)
    holder["dlg"] = dlg
    return bool(dlg.exec())


def ask(text, title="确认", theme=None, parent=None,
        yes_text="确定", no_text="取消"):
    """是/否 询问框；返回 bool。"""
    return show_message(text, title, theme, parent,
                        ok_text=yes_text, cancel_text=no_text)
