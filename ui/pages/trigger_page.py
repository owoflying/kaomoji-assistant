"""快捷短语页：触发词 -> 输出管理。"""
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QListWidget,
    QListWidgetItem, QLineEdit, QDialogButtonBox, QDialog, QFrame,
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont


class _TriggerEditDialog(QDialog):
    def __init__(self, trigger="", output="", parent=None):
        super().__init__(parent)
        self.setWindowTitle("编辑快捷短语" if trigger else "新增快捷短语")
        self.setMinimumWidth(380)
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(10)

        root.addWidget(QLabel("触发词（打字时输入这个词即弹出）"))
        self.trigger_edit = QLineEdit(trigger)
        self.trigger_edit.setPlaceholderText("例如 kk")
        root.addWidget(self.trigger_edit)

        root.addWidget(QLabel("输出（弹出的内容）"))
        self.output_edit = QLineEdit(output)
        self.output_edit.setPlaceholderText("例如 (๑•̀ㅂ•́)و✧")
        self.output_edit.setFont(QFont("Segoe UI Symbol", 13))
        root.addWidget(self.output_edit)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        root.addWidget(btns)

    def data(self):
        return self.trigger_edit.text().strip(), self.output_edit.text().strip()


class TriggerPage(QWidget):
    def __init__(self, triggers, parent=None):
        super().__init__(parent)
        self.triggers = triggers
        self._init_ui()
        self._refresh()

    def _init_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(36, 28, 36, 28)
        root.setSpacing(22)

        title = QLabel("快捷短语")
        title.setObjectName("PageTitle")
        root.addWidget(title)

        caption = QLabel("打字时输入「触发词」，候选条会弹出对应的「输出」。")
        caption.setObjectName("BodyText")
        root.addWidget(caption)

        card = QFrame()
        card.setObjectName("Card")
        croot = QVBoxLayout(card)
        croot.setContentsMargins(12, 12, 12, 12)
        croot.setSpacing(8)

        self.list_w = QListWidget()
        self.list_w.itemDoubleClicked.connect(self._edit)
        croot.addWidget(self.list_w, 1)

        op_row = QHBoxLayout()
        self.add_btn = QPushButton("新增")
        self.edit_btn = QPushButton("编辑")
        self.del_btn = QPushButton("删除")
        self.add_btn.clicked.connect(self._add)
        self.edit_btn.clicked.connect(self._edit)
        self.del_btn.clicked.connect(self._del)
        op_row.addWidget(self.add_btn)
        op_row.addWidget(self.edit_btn)
        op_row.addWidget(self.del_btn)
        op_row.addStretch(1)
        croot.addLayout(op_row)

        root.addWidget(card, 1)

    def _refresh(self):
        self.list_w.clear()
        for it in self.triggers.get_all():
            item = QListWidgetItem("%s  →  %s" % (it["trigger"], it["output"]))
            item.setData(Qt.UserRole, it["trigger"])
            item.setFont(QFont("Segoe UI Symbol", 12))
            self.list_w.addItem(item)

    def _add(self):
        dlg = _TriggerEditDialog()
        if dlg.exec() == QDialog.Accepted:
            trig, out = dlg.data()
            if trig and out:
                self.triggers.add(trig, out)
                self._refresh()

    def _edit(self, *args):
        row = self.list_w.currentRow()
        if row < 0:
            return
        trig = self.list_w.item(row).data(Qt.UserRole)
        out = ""
        for it in self.triggers.get_all():
            if it["trigger"] == trig:
                out = it["output"]
                break
        dlg = _TriggerEditDialog(trig, out)
        if dlg.exec() == QDialog.Accepted:
            new_trig, new_out = dlg.data()
            if new_trig and new_out:
                if new_trig != trig:
                    self.triggers.remove(trig)
                self.triggers.add(new_trig, new_out)
                self._refresh()

    def _del(self):
        row = self.list_w.currentRow()
        if row < 0:
            return
        trig = self.list_w.item(row).data(Qt.UserRole)
        self.triggers.remove(trig)
        self._refresh()
