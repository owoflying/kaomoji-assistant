"""我的颜文字页：分组 + 条目管理（增删改、标签、拖拽排序、导入导出备份）。"""
import json
from datetime import datetime

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QListWidget, QListWidgetItem, QLineEdit, QDialogButtonBox, QDialog,
    QMessageBox, QFrame, QFileDialog,
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont

from ui.fluent_combobox import FluentComboBox
from ui.win11_theme import kaomoji_font


class _KaoList(QListWidget):
    """支持拖拽重排的列表；拖放结束时通过 on_drop(dragged_text, target_text) 回调。"""

    def __init__(self, on_drop, parent=None):
        super().__init__(parent)
        self._on_drop = on_drop
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDragDropMode(QListWidget.InternalMove)
        self.setSelectionMode(QListWidget.SingleSelection)
        self.setDefaultDropAction(Qt.MoveAction)

    def dropEvent(self, event):
        tgt = self.itemAt(event.pos())
        src = self.currentItem()
        if src is not None and tgt is not None and src != tgt:
            self._on_drop(src.data(Qt.UserRole), tgt.data(Qt.UserRole))
        event.accept()
        # 不直接调用 super().dropEvent：底层数据已在 _on_drop 里重排，
        # 随后 _refresh_list 按新顺序重建列表，避免 Qt 内部移动与数据重排双重处理。


class _ItemEditDialog(QDialog):
    def __init__(self, groups, text="", group="默认", tags="", theme=None, parent=None):
        super().__init__(parent)
        self._theme = theme
        self.setWindowTitle("编辑颜文字" if text else "新增颜文字")
        self.setMinimumWidth(380)
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(10)

        root.addWidget(QLabel("颜文字"))
        self.text_edit = QLineEdit(text)
        self.text_edit.setPlaceholderText("例如 (｡•̀ᴗ-)✧")
        self.text_edit.setFont(kaomoji_font(14))
        root.addWidget(self.text_edit)

        grp_row = QHBoxLayout()
        grp_row.addWidget(QLabel("分组"))
        self.group_combo = FluentComboBox(self._theme)
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


class CustomKaomojiPage(QWidget):
    def __init__(self, user_kao, parent=None):
        super().__init__(parent)
        self.user_kao = user_kao
        self._tag_filter = None
        self._init_ui()
        self._refresh_groups()

    def _init_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(36, 28, 36, 28)
        root.setSpacing(22)

        title = QLabel("我的颜文字")
        title.setObjectName("PageTitle")
        root.addWidget(title)

        # 迁移工具栏：导出 / 导入 / 备份
        tool_row = QHBoxLayout()
        self.export_btn = QPushButton("导出")
        self.import_btn = QPushButton("导入")
        self.backup_btn = QPushButton("备份")
        self.export_btn.clicked.connect(self._export)
        self.import_btn.clicked.connect(self._import)
        self.backup_btn.clicked.connect(self._backup)
        tool_row.addWidget(self.export_btn)
        tool_row.addWidget(self.import_btn)
        tool_row.addWidget(self.backup_btn)
        tool_row.addStretch(1)
        root.addLayout(tool_row)

        # 分组卡片
        grp_card = QFrame()
        grp_card.setObjectName("Card")
        grp_root = QHBoxLayout(grp_card)
        grp_root.setContentsMargins(16, 14, 16, 14)
        grp_root.setSpacing(12)
        grp_root.addWidget(QLabel("分组"))
        self.group_combo = FluentComboBox(None)
        self.group_combo.setMinimumWidth(160)
        self.group_combo.currentTextChanged.connect(self._refresh_list)
        grp_root.addWidget(self.group_combo, 1)
        self.new_group_btn = QPushButton("新建分组")
        self.new_group_btn.clicked.connect(self._new_group)
        self.del_group_btn = QPushButton("删除分组")
        self.del_group_btn.clicked.connect(self._del_group)
        grp_root.addWidget(self.new_group_btn)
        grp_root.addWidget(self.del_group_btn)
        grp_root.addSpacing(16)
        grp_root.addWidget(QLabel("标签"))
        self.tag_combo = FluentComboBox(None)
        self.tag_combo.setMinimumWidth(140)
        self.tag_combo.currentIndexChanged.connect(self._on_tag_filter)
        grp_root.addWidget(self.tag_combo)
        root.addWidget(grp_card)

        # 列表卡片
        list_card = QFrame()
        list_card.setObjectName("Card")
        list_root = QVBoxLayout(list_card)
        list_root.setContentsMargins(12, 12, 12, 12)
        list_root.setSpacing(8)
        self.list_w = _KaoList(on_drop=self._on_drop_reorder)
        self.list_w.itemDoubleClicked.connect(self._edit_item)
        list_root.addWidget(self.list_w, 1)
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
        list_root.addLayout(op_row)
        root.addWidget(list_card, 1)

    def _refresh_groups(self):
        self.group_combo.blockSignals(True)
        cur = self.group_combo.currentText()
        self.group_combo.clear()
        self.group_combo.addItems(self.user_kao.get_groups())
        if cur and self.group_combo.findText(cur) >= 0:
            self.group_combo.setCurrentText(cur)
        self.group_combo.blockSignals(False)
        self._refresh_tags()
        self._refresh_list()

    def _refresh_tags(self):
        self.tag_combo.blockSignals(True)
        cur = self.tag_combo.currentText()
        self.tag_combo.clear()
        self.tag_combo.addItem("（全部）", None)
        tags = set()
        for it in self.user_kao.items:
            for t in it.get("tags", []):
                tags.add(t)
        for t in sorted(tags):
            self.tag_combo.addItem(t, t)
        idx = self.tag_combo.findText(cur)
        if cur and idx >= 0:
            self.tag_combo.setCurrentIndex(idx)
        else:
            self.tag_combo.setCurrentIndex(0)
            self._tag_filter = None
        self.tag_combo.blockSignals(False)

    def _on_tag_filter(self, index):
        self._tag_filter = self.tag_combo.itemData(index)
        self._refresh_list()

    def _refresh_list(self):
        self.list_w.clear()
        if self._tag_filter:
            # 跨分组：列出带该标签的所有颜文字，并标注所属分组
            for it in self.user_kao.items:
                if self._tag_filter in it.get("tags", []):
                    text = it["text"]
                    label = "%s    （分组：%s）" % (text, it.get("group", "默认"))
                    item = QListWidgetItem(label)
                    item.setData(Qt.UserRole, text)
                    item.setFont(kaomoji_font(14))
                    self.list_w.addItem(item)
        else:
            group = self.group_combo.currentText()
            for text in self.user_kao.items_for_group(group):
                tags = self.user_kao.tags_of(text)
                item = QListWidgetItem(text if not tags else "%s    [%s]" % (text, ", ".join(tags)))
                item.setData(Qt.UserRole, text)
                item.setFont(kaomoji_font(14))
                self.list_w.addItem(item)

    def _on_drop_reorder(self, dragged, target):
        self.user_kao.move_item(dragged, target)
        self._refresh_list()

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

    # ---------- 迁移：导出 / 导入 / 备份 ----------
    def _export(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "导出颜文字库", "user_kaomoji.json", "JSON 文件 (*.json)")
        if not path:
            return
        payload = {
            "app": "kaomoji-assistant",
            "kind": "user_kaomoji",
            "version": 1,
            "exported_at": datetime.now().isoformat(timespec="seconds"),
            "data": self.user_kao.export_dict(),
        }
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            QMessageBox.information(self, "已导出", "已导出到：\n%s" % path)
        except Exception as e:
            QMessageBox.warning(self, "导出失败", str(e))

    def _import(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "导入颜文字库", "", "JSON 文件 (*.json)")
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                payload = json.load(f)
            data = payload.get("data") if isinstance(payload, dict) and "data" in payload else payload
            groups = data.get("groups") if isinstance(data, dict) else None
            items = data.get("items") if isinstance(data, dict) else None
            if not isinstance(items, list):
                QMessageBox.warning(self, "格式错误", "该文件不是有效的颜文字库备份。")
                return
            added, updated, skipped = self.user_kao.import_data(groups, items)
            self._refresh_groups()
            QMessageBox.information(
                self, "导入完成",
                "新增 %d 条，更新 %d 条，跳过 %d 条无效数据。" % (added, updated, skipped))
        except Exception as e:
            QMessageBox.warning(self, "导入失败", str(e))

    def _backup(self):
        dest = self.user_kao.backup()
        if dest:
            QMessageBox.information(self, "已备份", "已备份到：\n%s" % dest)
        else:
            QMessageBox.warning(self, "备份失败", "无法写入备份文件。")


def _text_input(parent, title, label):
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
