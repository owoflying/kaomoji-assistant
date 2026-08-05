"""运行日志查看器：查看 / 复制 / 保存 应用运行日志。

日志由 main.py 的全局 LOG_BUFFER 收集（Qt 运行时消息 + 未捕获异常），
本对话框只负责把它呈现出来，并提供「复制全部」「保存为文件」功能。
"""
import os

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPlainTextEdit, QPushButton,
    QLabel, QFileDialog, QCheckBox,
)
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QGuiApplication, QFont, QFontDatabase

from ui.win11_theme import Theme
from ui.fluent_icons import icon_label


_PLACEHOLDER = "（暂无日志）"


class LogViewer(QDialog):
    """只读日志对话框：实时显示 LOG_BUFFER，支持复制与保存。"""

    def __init__(self, buffer, parent=None, theme_name="light"):
        super().__init__(parent)
        self._buffer = buffer
        self._shown = 0           # 已渲染到文本框的行数
        self.setWindowTitle("运行日志")
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self.setMinimumSize(720, 480)
        self.resize(880, 560)
        self._init_ui(theme_name)
        self._refresh()

        # 每秒自动拉取新日志（用户若滚上去看历史则不强制跟到底）
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._refresh)
        self._timer.start()

    # ---------- 初始化 ----------
    def _init_ui(self, theme_name):
        t = Theme(theme_name)
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(12)

        # 标题行
        head = QHBoxLayout()
        head.setSpacing(10)
        head.addWidget(icon_label("info", 18, t.accent))
        title = QLabel("运行日志")
        title.setObjectName("PageTitle")
        head.addWidget(title)
        head.addStretch(1)
        self._count = QLabel("")
        self._count.setObjectName("Caption")
        head.addWidget(self._count)
        root.addLayout(head)

        sub = QLabel("应用运行期间产生的消息与异常（最多保留最近 2000 条）")
        sub.setObjectName("BodyText")
        root.addWidget(sub)
        root.addSpacing(4)

        # 日志文本框（等宽字体，便于对齐时间戳）
        self._text = QPlainTextEdit()
        self._text.setReadOnly(True)
        self._text.setLineWrapMode(QPlainTextEdit.NoWrap)
        mono_name = "Consolas" if "Consolas" in QFontDatabase.families() else "Courier New"
        self._text.setFont(QFont(mono_name, 12))
        self._text.setStyleSheet(
            "QPlainTextEdit{background:%s;border:1px solid %s;border-radius:8px;"
            "padding:10px 12px;color:%s;}"
            % (("#2a2a2a" if t.dark else "#ffffff"), t.card_border, t.text)
        )
        root.addWidget(self._text, 1)

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
        root.addLayout(bar)

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


def show_log_viewer(buffer, parent=None, theme_name="light"):
    """弹出日志查看器（非模态）。"""
    dlg = LogViewer(buffer, parent, theme_name)
    dlg.show()
    dlg.raise_()
    dlg.activateWindow()
    return dlg
