"""搜索页：输入关键词 / 标签，过滤「库 + 我的颜文字」，选中即上屏。

在统一窗口内作为一页使用；选中后发出 selected 信号，由主窗口决定是否隐藏并注入。
"""
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QLineEdit, QListWidget, QListWidgetItem,
    QLabel, QFrame,
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont

from core.kaomoji_data import KaomojiData
from core.user_kaomoji import UserKaomoji
from ui.win11_theme import kaomoji_font, Theme


class SearchPage(QWidget):
    selected = Signal(str)

    _MAX = 200

    def __init__(self, data: KaomojiData, user_kao: UserKaomoji, theme="light", parent=None):
        super().__init__(parent)
        self.data = data
        self.user_kao = user_kao
        self._init_ui(theme)
        self._do_search()

    def _init_ui(self, theme):
        dark = theme == "dark"
        self._theme = Theme(theme)
        self._text = "#f0f0f0" if dark else "#1f1f1f"
        self._num = "#a0a0a6" if dark else "#8a8a8e"

        root = QVBoxLayout(self)
        root.setContentsMargins(36, 28, 36, 28)
        root.setSpacing(22)

        title = QLabel("搜索颜文字")
        title.setObjectName("PageTitle")
        root.addWidget(title)

        card = QFrame()
        card.setObjectName("Card")
        croot = QVBoxLayout(card)
        croot.setContentsMargins(16, 16, 16, 16)
        croot.setSpacing(12)

        self.query = QLineEdit()
        self.query.setPlaceholderText("输入关键词 / 标签搜索（我的颜文字优先）")
        self.query.setFont(QFont("Segoe UI", 13))
        self.query.textChanged.connect(self._do_search)
        croot.addWidget(self.query)

        self.results = QListWidget()
        self.results.setFont(kaomoji_font(14))
        self.results.itemDoubleClicked.connect(self._choose)
        self.results.setFocusProxy(self.query)
        croot.addWidget(self.results, 1)

        self.hint = QLabel("↑↓ 选择 · 回车上屏 · Esc 清空")
        self.hint.setObjectName("Caption")
        croot.addWidget(self.hint)

        root.addWidget(card, 1)
        self._style()

    def _style(self):
        t = self._theme
        accent = t.accent
        self.setStyleSheet(
            "QLineEdit{border:1px solid rgba(128,128,128,0.4);border-radius:8px;"
            "padding:8px 12px;background:rgba(127,127,127,0.08);}"
            "QLineEdit:focus{border:1px solid %s;}"
            "QListWidget{border:none;background:transparent;outline:none;}"
            "QListWidget::item{padding:6px 8px;border-radius:6px;}"
            "QListWidget::item:selected{background:%s;}"
            % (accent, t.accent_bg)
        )

    def set_theme(self, theme_obj):
        self._theme = theme_obj
        self._style()

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

    def _do_search(self):
        q = self.query.text()
        self.results.clear()
        for kao in self._build_pool(q):
            item = QListWidgetItem(kao)
            item.setData(Qt.UserRole, kao)
            self.results.addItem(item)
        if self.results.count():
            self.results.setCurrentRow(0)

    def _choose(self):
        item = self.results.currentItem()
        if item is None:
            return
        text = item.data(Qt.UserRole)
        self.selected.emit(text)

    def keyPressEvent(self, e):
        k = e.key()
        if k == Qt.Key_Return or k == Qt.Key_Enter:
            self._choose()
            return
        if k == Qt.Key_Escape:
            self.query.clear()
            self.query.setFocus()
            return
        super().keyPressEvent(e)

    def focus_query(self):
        self.query.setFocus()
        self.query.selectAll()
