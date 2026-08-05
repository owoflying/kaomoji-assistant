"""颜文字库页：按分类浏览内置颜文字，点击即可复制到剪贴板或上屏。"""
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
    QComboBox, QPushButton, QFrame,
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont


class LibraryPage(QWidget):
    selected = Signal(str)

    def __init__(self, data, parent=None):
        super().__init__(parent)
        self.data = data
        self._init_ui()
        self._refresh_list()

    def _init_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(36, 28, 36, 28)
        root.setSpacing(22)

        title = QLabel("颜文字库")
        title.setObjectName("PageTitle")
        root.addWidget(title)

        # 分类选择卡片
        cat_card = QFrame()
        cat_card.setObjectName("Card")
        cat_root = QHBoxLayout(cat_card)
        cat_root.setContentsMargins(16, 14, 16, 14)
        cat_root.setSpacing(12)
        cat_root.addWidget(QLabel("分类"))
        self.cat_combo = QComboBox()
        self.cat_combo.setMinimumWidth(180)
        self.cat_combo.addItem("全部")
        self.cat_combo.addItems(self.data.get_category_names())
        self.cat_combo.currentTextChanged.connect(self._refresh_list)
        cat_root.addWidget(self.cat_combo, 1)
        root.addWidget(cat_card)

        # 列表卡片
        list_card = QFrame()
        list_card.setObjectName("Card")
        list_root = QVBoxLayout(list_card)
        list_root.setContentsMargins(12, 12, 12, 12)
        list_root.setSpacing(8)
        self.list_w = QListWidget()
        self.list_w.setFont(QFont("Segoe UI Symbol", 13))
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

    def _refresh_list(self):
        cat = self.cat_combo.currentText()
        items = self.data.get_items(None if cat == "全部" else cat)
        self.list_w.clear()
        for kao in items:
            item = QListWidgetItem(kao)
            item.setData(Qt.UserRole, kao)
            self.list_w.addItem(item)
        self.count_label.setText(f"共 {len(items)} 条")

    def _choose(self):
        item = self.list_w.currentItem()
        if item is None:
            return
        text = item.data(Qt.UserRole)
        self.selected.emit(text)
