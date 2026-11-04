"""颜文字库页：按分类浏览内置颜文字，点击即可上屏。"""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton, QWidget,
)

from ui.fluent_combobox import FluentComboBox
from ui.pages._scaffold import (
    card, card_layout, page_layout, page_title,
)
from ui.win11_theme import kaomoji_font

_ALL_CATEGORIES = "全部"


class LibraryPage(QWidget):
    selected = Signal(str)

    def __init__(self, data, theme=None, parent=None):
        super().__init__(parent)
        self.data = data
        self._theme = theme
        self._init_ui()
        self._refresh_list()

    def _init_ui(self):
        root = page_layout(self)
        root.addWidget(page_title("颜文字库"))

        # 分类选择卡片
        cat_card = card()
        cat_root = card_layout(cat_card, horizontal=True)
        cat_root.addWidget(QLabel("分类"))
        self.cat_combo = FluentComboBox(self._theme)
        self.cat_combo.setMinimumWidth(180)
        self._fill_categories()
        self.cat_combo.currentTextChanged.connect(self._refresh_list)
        cat_root.addWidget(self.cat_combo, 1)
        root.addWidget(cat_card)

        # 列表卡片
        list_card = card()
        list_root = card_layout(list_card, margins=(12, 12, 12, 12), spacing=8)
        self.list_w = QListWidget()
        self.list_w.setFont(kaomoji_font(14))
        self.list_w.itemDoubleClicked.connect(self._choose)
        list_root.addWidget(self.list_w, 1)

        op_row = QHBoxLayout()
        self.copy_btn = QPushButton("选中上屏")
        self.copy_btn.clicked.connect(self._choose)
        op_row.addWidget(self.copy_btn)
        op_row.addStretch(1)
        self.count_label = QLabel("")
        self.count_label.setObjectName("Caption")
        op_row.addWidget(self.count_label)
        list_root.addLayout(op_row)

        root.addWidget(list_card, 1)

    def _fill_categories(self):
        """用当前数据重建分类项（首项固定为「全部」）。"""
        self.cat_combo.clear()
        self.cat_combo.addItem(_ALL_CATEGORIES)
        self.cat_combo.addItems(self.data.get_category_names())

    def set_theme(self, theme):
        self._theme = theme
        if getattr(self, "cat_combo", None) is not None:
            self.cat_combo.set_theme(theme)

    def reload_categories(self):
        """重建分类下拉（插件启用/停用后分类集合可能变化），尽量保留当前选择。"""
        if getattr(self, "cat_combo", None) is None:
            return
        current = self.cat_combo.currentText()
        self.cat_combo.blockSignals(True)
        self._fill_categories()
        idx = self.cat_combo.findText(current)
        self.cat_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.cat_combo.blockSignals(False)
        self._refresh_list()

    def _refresh_list(self):
        cat = self.cat_combo.currentText()
        items = self.data.get_items(None if cat == _ALL_CATEGORIES else cat)
        self.list_w.clear()
        for kao in items:
            item = QListWidgetItem(kao)
            item.setData(Qt.UserRole, kao)
            self.list_w.addItem(item)
        self.count_label.setText("共 %d 条" % len(items))

    def _choose(self):
        item = self.list_w.currentItem()
        if item is None:
            return
        self.selected.emit(item.data(Qt.UserRole))
