"""运行日志查看器：查看 / 复制 / 保存 应用运行日志。

日志由 main.py 的全局 LOG_BUFFER 收集（Qt 运行时消息 + 未捕获异常），
本窗口负责把它呈现出来，并提供「复制全部」「保存为文件」功能。

视觉风格与统一主窗口一致：无边框 + 圆角 + DWM 亚克力 + 自绘阴影/边框。
"""
import os

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPlainTextEdit, QPushButton,
    QLabel, QFileDialog, QCheckBox,
)
from PySide6.QtCore import Qt, QTimer, QRect, QRectF, QPoint
from PySide6.QtGui import (
    QGuiApplication, QFont, QFontDatabase, QColor, QPainter, QPainterPath,
    QPen, QLinearGradient, QRegion, QTransform,
)

from ui.win11_theme import Theme, parse_color, resolve_theme_name
from ui.fluent_icons import icon_label, recolor


_PLACEHOLDER = "（暂无日志）"

# 窗口阴影/圆角参数（与统一窗口保持一致的风格）
_RADIUS = 12
_PAD = 12
_TITLEBAR_H = 40


class LogViewer(QWidget):
    """只读日志窗口：实时显示 LOG_BUFFER，支持复制与保存。"""

    def __init__(self, buffer, parent=None, theme_name="light"):
        super().__init__(parent)
        self._buffer = buffer
        self._shown = 0           # 已渲染到文本框的行数
        self._drag_pos = None
        # 配置里可能是 "system"，统一解析成实际 light/dark，避免主题名未解析
        # 时落到默认分支（表现为窗口配色与主体不一致）。
        self._theme = Theme(resolve_theme_name(theme_name))
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        self._init_window()
        self._init_ui()
        self._refresh()

        # 每秒自动拉取新日志（用户若滚上去看历史则不强制跟到底）
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._refresh)
        self._timer.start()

    # ---------- 窗口材质 ----------
    def _init_window(self):
        self.setWindowTitle("运行日志")
        self.setWindowFlags(Qt.Window | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setMinimumSize(720, 480)
        self.resize(880, 560)
        self._update_mask()

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

    # ---------- 初始化 ----------
    def _init_ui(self):
        t = self._theme
        root = QVBoxLayout(self)
        root.setContentsMargins(_PAD, _PAD, _PAD, _PAD)
        root.setSpacing(0)

        # 自定义标题栏
        titlebar = QWidget()
        titlebar.setFixedHeight(_TITLEBAR_H)
        troot = QHBoxLayout(titlebar)
        troot.setContentsMargins(12, 0, 8, 0)
        troot.setSpacing(8)

        self._icon = icon_label("info", 18, t.accent)
        troot.addWidget(self._icon)
        title = QLabel("运行日志")
        title.setObjectName("PageTitle")
        troot.addWidget(title)
        troot.addStretch(1)
        self._count = QLabel("")
        self._count.setObjectName("Caption")
        troot.addWidget(self._count)
        close_btn = QPushButton("\u2715")
        close_btn.setObjectName("TitleButton")
        close_btn.setFixedSize(32, 32)
        close_btn.clicked.connect(self.close)
        troot.addWidget(close_btn)
        root.addWidget(titlebar)

        # 内容区
        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(16, 10, 16, 16)
        body_layout.setSpacing(12)

        sub = QLabel("应用运行期间产生的消息与异常（最多保留最近 2000 条）")
        sub.setObjectName("BodyText")
        body_layout.addWidget(sub)

        # 日志文本框（等宽字体，便于对齐时间戳）
        self._text = QPlainTextEdit()
        self._text.setReadOnly(True)
        self._text.setLineWrapMode(QPlainTextEdit.NoWrap)
        mono_name = "Consolas" if "Consolas" in QFontDatabase.families() else "Courier New"
        self._text.setFont(QFont(mono_name, 12))
        body_layout.addWidget(self._text, 1)

        # 底部工具条
        bar = QHBoxLayout()
        bar.setSpacing(8)
        self._auto = QCheckBox("自动刷新")
        self._auto.setChecked(True)
        self._auto.stateChanged.connect(self._on_auto_changed)
        bar.addWidget(self._auto)
        bar.addStretch(1)

        btn_refresh = QPushButton("刷新")
        btn_refresh.setObjectName("AccentButton")
        btn_refresh.clicked.connect(self._refresh)
        btn_copy = QPushButton("复制")
        btn_copy.clicked.connect(self._copy)
        btn_save = QPushButton("保存")
        btn_save.clicked.connect(self._save)
        btn_close = QPushButton("关闭")
        btn_close.clicked.connect(self.close)

        for b in (btn_refresh, btn_copy, btn_save, btn_close):
            bar.addWidget(b)
        body_layout.addLayout(bar)
        root.addWidget(body, 1)

        self._apply_theme()

    def _apply_theme(self):
        """把当前主题完整应用到窗口与子控件（构造与换肤共用）。"""
        t = self._theme
        # 应用完整主题 QSS，确保按钮/复选框/标签样式一致
        self.setStyleSheet(t.style_sheet())
        self._text.setStyleSheet(
            "QPlainTextEdit{background:%s;border:1px solid %s;border-radius:8px;"
            "padding:10px 12px;color:%s;}"
            % (t.card, t.card_border, t.text)
        )
        recolor(self._icon, t.accent)

    def set_theme(self, theme_name):
        """换肤：主题名（可含 system）解析后重刷窗口，避免开着窗口改主题不跟随。"""
        name = resolve_theme_name(theme_name)
        if self._theme.name == name:
            return
        self._theme = Theme(name)
        self._apply_theme()
        self.update()

    # ---------- 行为 ----------
    def _on_auto_changed(self, state):
        if state == Qt.Checked:
            self._timer.start()
        else:
            self._timer.stop()

    def _refresh(self):
        n = len(self._buffer)
        if n == 0:
            if self._shown == 0:
                self._text.setPlainText(_PLACEHOLDER)
                self._count.setText("0 条记录")
            return
        if n == self._shown:
            return

        sb = self._text.verticalScrollBar()
        at_bottom = sb.value() >= sb.maximum() - 4

        if self._shown == 0:
            self._text.setPlainText("\n".join(self._buffer))
        else:
            self._text.appendPlainText("\n".join(self._buffer[self._shown:n]))

        self._shown = n
        self._count.setText("%d 条记录" % n)
        if at_bottom:
            sb.setValue(sb.maximum())

    def _copy(self):
        if not self._buffer:
            self._count.setText("没有可复制的日志")
            return
        clip = QGuiApplication.clipboard()
        if clip is not None:
            clip.setText("\n".join(self._buffer))
        self._count.setText("已复制 %d 条记录" % len(self._buffer))

    def _save(self):
        if not self._buffer:
            self._count.setText("没有可保存的日志")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "保存日志", "kaomoji-assistant-log.txt",
            "文本文件 (*.txt);;所有文件 (*.*)",
        )
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write("\n".join(self._buffer) + "\n")
            self._count.setText("已保存到 %s" % os.path.basename(path))
        except Exception as e:  # pragma: no cover - 防御性
            self._count.setText("保存失败：%r" % (e,))

    # ---------- 自绘：亚克力基底 + 圆角 + 阴影 + 边框 ----------
    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        t = self._theme
        panel = QRect(_PAD, _PAD, self.width() - 2 * _PAD, self.height() - 2 * _PAD)

        # 柔和外阴影
        # 注意：主题里的 rgba(r,g,b,浮点a) 字符串在 Qt6 下 QColor() 解析失败会
        # 静默变成「不透明黑色」，窗口会整个发黑（表现为「日志窗口永远是深色」）。
        # 必须走 parse_color() 正则兜底解析，不可直接 QColor(spec)。
        sc = parse_color(t.shadow_color)
        base = sc.alpha()
        for d in range(_PAD, 0, -1):
            a = max(1, int(base * (1 - (d - 1) / _PAD) * 0.35))
            col = QColor(sc.red(), sc.green(), sc.blue(), a)
            rr = _RADIUS + d * 0.5
            sp = QPainterPath()
            sp.addRoundedRect(QRectF(panel.adjusted(-d, -d, d, d)), rr, rr)
            p.fillPath(sp, col)

        # 面板基底
        pp = QPainterPath()
        pp.addRoundedRect(QRectF(panel), _RADIUS, _RADIUS)
        # 面板基底：window_tint 固有 alpha 仅 0.85/0.88，且本窗口不带 DWM 亚克力，
        # 半透会直接透出桌面（浅色主题下被暗色桌面压暗）→ 与主窗口/插件弹窗一致
        # 取等效实色。
        tint = parse_color(t.window_tint)
        tint.setAlpha(255)
        p.fillPath(pp, tint)

        # 细微竖向渐变
        grad = QLinearGradient(0, panel.top(), 0, panel.bottom())
        grad.setColorAt(0, parse_color(t.window_grad_top))
        grad.setColorAt(1, parse_color(t.window_grad_bottom))
        p.fillPath(pp, grad)

        # 1px 圆角边框
        pen = QPen(parse_color(t.window_border))
        pen.setWidth(1)
        p.strokePath(pp, pen)

        super().paintEvent(e)

    # ---------- 无边框窗口拖动 ----------
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


def show_log_viewer(buffer, parent=None, theme_name="light"):
    """弹出日志查看器（非模态）。"""
    dlg = LogViewer(buffer, parent, theme_name)
    dlg.show()
    dlg.raise_()
    dlg.activateWindow()
    return dlg
