"""「我的颜文字」管理对话框：分组 + 条目（增删改、分组、标签）。

从托盘菜单 / 设置入口打开，非模态（show 而非 exec），打开时主程序会暂停
自动弹出监听与全局热键，关闭后恢复。所有变更经 UserKaomoji 落盘（去抖）。
"""
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QComboBox,
    QListWidget, QListWidgetItem, QLineEdit, QDialogButtonBox, QFrame, QMessageBox,
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont


class _ItemEditDialog(QDialog):
    """新增 / 编辑单条颜文字的小窗。"""

    def __init__(self, groups, text="", group="默认", tags="", parent=None):
        super().__init__(parent)
        self.setWindowTitle("编辑颜文字" if text else "新增颜文字")
        self.setMinimumWidth(380)
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(10)

        root.addWidget(QLabel("颜文字"))
        self.text_edit = QLineEdit(text)
        self.text_edit.setPlaceholderText("例如 (｡•̀ᴗ-)✧")
        self.text_edit.setFont(QFont("Segoe UI Symbol", 13))
        root.addWidget(self.text_edit)

        grp_row = QHBoxLayout()
        grp_row.addWidget(QLabel("分组"))
        self.group_combo = QComboBox()
        self.group_combo.setEditable(True)
        self.group_combo.addItems(groups)
        if group:
            idx = self.group_combo.findText(group)
            self.group_combo.setCurrentIndex(idx if idx >= 0 else 0)
        grp_row.addWidget(self.group_combo, 1)
        root.addLayout(grp_row)

        root.addWidget(QLabel("标签（逗号分隔，用于情绪关联与搜索）"))
        self.tags_edit = QLineEdit(tags)
        self.tags_edit.setPlaceholderText("例如 开心, 加油")
        root.addWidget(self.tags_edit)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        root.addWidget(btns)

    def data(self):
        return (
            self.text_edit.text().strip(),
            self.group_combo.currentText().strip() or "默认",
            self.tags_edit.text().strip(),
        )


class CustomKaomojiDialog(QDialog):
    def __init__(self, user_kao, parent=None):
        super().__init__(parent)
        self.user_kao = user_kao
        self.setWindowTitle("我的颜文字")
        self.setMinimumWidth(440)
        self.setMinimumHeight(420)
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(10)

        # 分组选择 + 分组管理
        grp_row = QHBoxLayout()
        grp_row.addWidget(QLabel("分组"))
        self.group_combo = QComboBox()
        self.group_combo.setMinimumWidth(140)
        self.group_combo.currentTextChanged.connect(self._refresh_list)
        grp_row.addWidget(self.group_combo, 1)
        self.new_group_btn = QPushButton("新建分组")
        self.new_group_btn.clicked.connect(self._new_group)
        self.del_group_btn = QPushButton("删除分组")
        self.del_group_btn.clicked.connect(self._del_group)
        grp_row.addWidget(self.new_group_btn)
        grp_row.addWidget(self.del_group_btn)
        root.addLayout(grp_row)

        # 条目列表
        self.list_w = QListWidget()
        self.list_w.itemDoubleClicked.connect(self._edit_item)
        root.addWidget(self.list_w, 1)

        # 操作按钮
        op_row = QHBoxLayout()
        self.add_btn = QPushButton("新增")
        self.edit_btn = QPushButton("编辑")
        self.del_btn = QPushButton("删除")
        self.add_btn.clicked.connect(self._add_item)
        self.edit_btn.clicked.connect(self._edit_item)
        self.del_btn.clicked.connect(self._del_item)
        op_row.addWidget(self.add_btn)
        op_row.addWidget(self.edit_btn)
        op_row.addWidget(self.del_btn)
        op_row.addStretch(1)
        root.addLayout(op_row)

        self._refresh_groups()

    # ---------- 刷新 ----------
    def _refresh_groups(self):
        self.group_combo.blockSignals(True)
        cur = self.group_combo.currentText()
        self.group_combo.clear()
        self.group_combo.addItems(self.user_kao.get_groups())
        if cur and self.group_combo.findText(cur) >= 0:
            self.group_combo.setCurrentText(cur)
        self.group_combo.blockSignals(False)
        self._refresh_list()

    def _refresh_list(self):
        group = self.group_combo.currentText()
        self.list_w.clear()
        for text in self.user_kao.items_for_group(group):
            tags = self.user_kao.tags_of(text)
            item = QListWidgetItem(text if not tags else "%s    [%s]" % (text, ", ".join(tags)))
            item.setData(Qt.UserRole, text)
            item.setFont(QFont("Segoe UI Symbol", 13))
            self.list_w.addItem(item)

    # ---------- 分组操作 ----------
    def _new_group(self):
        name, ok = _text_input(self, "新建分组", "分组名称：")
        if ok and name:
            if self.user_kao.add_group(name):
                self._refresh_groups()
                self.group_combo.setCurrentText(name)

    def _del_group(self):
        name = self.group_combo.currentText()
        if name == "默认":
            QMessageBox.information(self, "提示", "「默认」分组不可删除。")
            return
        if self.user_kao.remove_group(name):
            self._refresh_groups()

    # ---------- 条目操作 ----------
    def _add_item(self):
        dlg = _ItemEditDialog(self.user_kao.get_groups(),
                              group=self.group_combo.currentText())
        if dlg.exec() == QDialog.Accepted:
            text, group, tags = dlg.data()
            if text:
                self.user_kao.add_item(text, group, tags)
                self._refresh_groups()

    def _edit_item(self, *args):
        row = self.list_w.currentRow()
        if row < 0:
            return
        old = self.list_w.item(row).data(Qt.UserRole)
        tags = ", ".join(self.user_kao.tags_of(old))
        group = self.group_combo.currentText()
        dlg = _ItemEditDialog(self.user_kao.get_groups(), text=old,
                              group=group, tags=tags)
        if dlg.exec() == QDialog.Accepted:
            text, group, tags = dlg.data()
            if text:
                self.user_kao.update_item(old, text, group, tags)
                self._refresh_groups()

    def _del_item(self):
        row = self.list_w.currentRow()
        if row < 0:
            return
        text = self.list_w.item(row).data(Qt.UserRole)
        self.user_kao.remove_item(text)
        self._refresh_groups()


def _text_input(parent, title, label):
    """极简单行输入对话框，返回 (text, ok)。"""
    dlg = QDialog(parent)
    dlg.setWindowTitle(title)
    dlg.setMinimumWidth(300)
    root = QVBoxLayout(dlg)
    root.setContentsMargins(16, 14, 16, 14)
    root.setSpacing(10)
    root.addWidget(QLabel(label))
    edit = QLineEdit()
    root.addWidget(edit)
    btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
    btns.accepted.connect(dlg.accept)
    btns.rejected.connect(dlg.reject)
    root.addWidget(btns)
    ok = dlg.exec() == QDialog.Accepted
    return edit.text().strip(), ok
