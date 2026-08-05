"""颜文字搜索窗：输入关键词 / 标签，过滤「库 + 我的颜文字」，选中即上屏。

独立窗口（不走候选条的「永不抢焦点」约束，因为它本就是一次性的有意搜索交互）。
从托盘菜单「搜索颜文字」打开。回车上屏、Esc 关闭、上下键移动。
"""
import random

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLineEdit, QListWidget, QListWidgetItem,
    QLabel, QWidget,
)
from PySide6.QtCore import Qt, Signal, QEvent
from PySide6.QtGui import QFont, QColor, QGuiApplication

from core.kaomoji_data import KaomojiData
from core.user_kaomoji import UserKaomoji


class SearchDialog(QDialog):
    selected = Signal(str)

    _MAX = 200

    def __init__(self, data: KaomojiData, user_kao: UserKaomoji, theme="light", parent=None):
        super().__init__(parent)
        self.data = data
        self.user_kao = user_kao
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        dark = theme == "dark"
        self._bg = QColor(43, 43, 43) if dark else QColor(255, 255, 255)
        self._bg.setAlphaF(0.98)
        self._text = "#f0f0f0" if dark else "#1f1f1f"
        self._num = "#a0a0a6" if dark else "#8a8a8e"
        self.setMinimumWidth(420)
        self.setMinimumHeight(120)

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(8)

        self.query = QLineEdit()
        self.query.setPlaceholderText("搜索颜文字 / 标签（我的颜文字优先）")
        self.query.setFont(QFont("Segoe UI", 13))
        self.query.textChanged.connect(self._do_search)
        root.addWidget(self.query)

        self.results = QListWidget()
        self.results.setFont(QFont("Segoe UI Symbol", 13))
        self.results.itemDoubleClicked.connect(self._choose)
        root.addWidget(self.results, 1)

        self.hint = QLabel("↑↓ 选择 · 回车上屏 · Esc 关闭")
        self.hint.setStyleSheet("color:%s;font-size:11px;" % self._num)
        root.addWidget(self.hint)

        self._style()
        # 打开即聚焦搜索框
        self.results.setFocusProxy(self.query)

    def _style(self):
        self.setStyleSheet(
            "QWidget{color:%s;background:transparent;}"
            "QLineEdit{border:1px solid rgba(128,128,128,0.4);border-radius:8px;"
            "padding:6px 10px;background:rgba(127,127,127,0.12);}"
            "QListWidget{border:none;background:transparent;outline:none;}"
            "QListWidget::item{padding:4px 6px;border-radius:6px;}"
            "QListWidget::item:selected{background:rgba(0,103,192,0.18);}"
            % self._text
        )

    # ---------- 搜索 ----------
    def _build_pool(self, q):
        q = (q or "").strip().lower()
        if q:
            usr = self.user_kao.search(q)
            lib = self.data.search(q)
        else:
            usr = self.user_kao.get_all()
            lib = self.data.get_items()
        seen = set(usr)
        combined = list(usr) + [k for k in lib if k not in seen]
        return combined[: self._MAX]

    def _do_search(self, text=None):
        q = self.query.text()
        self.results.clear()
        for kao in self._build_pool(q):
            item = QListWidgetItem(kao)
            item.setData(Qt.UserRole, kao)
            self.results.addItem(item)
        if self.results.count():
            self.results.setCurrentRow(0)

    # ---------- 选择 ----------
    def _choose(self, *args):
        item = self.results.currentItem()
        if item is None:
            return
        text = item.data(Qt.UserRole)
        self.selected.emit(text)
        self.accept()

    def keyPressEvent(self, e):
        k = e.key()
        if k == Qt.Key_Return or k == Qt.Key_Enter:
            self._choose()
            return
        if k == Qt.Key_Escape:
            self.reject()
            return
        super().keyPressEvent(e)

    def showEvent(self, e):
        # 居中偏上，复刻候选条的位置感
        screen = QGuiApplication.primaryScreen()
        if screen is not None:
            sg = screen.availableGeometry()
            x = sg.x() + (sg.width() - self.width()) // 2
            y = sg.y() + int(sg.height() * 0.32)
            self.move(x, y)
        self.query.setFocus()
        self.query.selectAll()
        self._do_search()
        super().showEvent(e)

    def paintEvent(self, e):
        from PySide6.QtGui import QPainter, QPainterPath
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(0, 0, self.width(), self.height(), 12, 12)
        p.fillPath(path, self._bg)
        super().paintEvent(e)
