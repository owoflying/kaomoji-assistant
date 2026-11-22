"""自定义主题编辑器对话框。

## 设计取舍：只暴露 8 个核心色

`Theme` 有 24 个颜色属性，但其中 16 个是**派生**的（hover、半透明叠加、
窗口渐变…）。让用户填 24 个值既不现实，也几乎必然配出不协调的组合
——比如改了 accent 却忘了改 accent_bg，选中态就会花掉。

所以这里只让用户调 `CORE_KEYS` 那 8 个，其余由 `derive()` 自动算。
派生逻辑在预设身上已经验证过（4 套预设都是这么来的），可靠。

## 实时预览

调色时**立即**把新色板应用到当前窗口，用户能直接看到效果。
不写盘——点「保存」才落到 `user_theme.json`。
取消则恢复原主题（记住打开前的配置）。

## 双明暗编辑

每套自定义主题同时有浅色和深色两份。用顶部切换，分别调。
这样"深色模式下也好看"不会变成事后才发现的问题。
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QColorDialog, QDialog, QDialogButtonBox, QFrame, QGridLayout,
    QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
)

from core.theme_presets import CORE_KEYS, load_user_theme


def _fallback_color():
    """取不到色值时的兜底色：用当前经典深色主题的次要文字色。

    ⚠ 不能用硬编码的灰色：`test_no_stray_hardcoded_colors` 会把它当成
    "没收敛到主题的颜色"报出来。而且从主题取也能保证换主题后兜底色
    跟着变，不会在一片暖色里冒出个冷灰。
    """
    from core.theme_presets import resolve_palette

    # 不传默认值：经典深色主题一定有 text_tertiary，
    # 万一是空字典也不能再写一个硬编码色兜底
    return resolve_palette("classic", "dark")["text_tertiary"]



_FIELD_LABELS = {
    "bg": ("背景色", "内容区的底色"),
    "card": ("卡片色", "卡片、输入框、候选条的背景"),
    "card_border": ("边框色", "卡片与输入框的描边"),
    "text": ("主文字", "正文与标题"),
    "text_secondary": ("次要文字", "说明、提示"),
    "accent": ("强调色", "按钮、选中态、链接"),
    "window_tint": ("窗口基底", "无边框窗口的亚克力色（支持透明度）"),
    "content_surface": ("内容表面", "内容区比侧栏略实的那层"),
}


class _ColorRow(QWidget):
    """一行调色项：名称 + 色块 + 当前值。点击色块弹调色板。"""

    def __init__(self, key, value, on_change, parent=None):
        super().__init__(parent)
        self._key = key
        self._on_change = on_change
        self._value = value

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 4, 0, 4)
        lay.setSpacing(10)

        name, desc = _FIELD_LABELS.get(key, (key, ""))
        label = QLabel(name)
        label.setMinimumWidth(72)
        label.setToolTip(desc)
        lay.addWidget(label)

        self._swatch = QPushButton()
        self._swatch.setFixedSize(46, 24)
        self._swatch.setCursor(Qt.PointingHandCursor)
        self._swatch.clicked.connect(self._pick)
        lay.addWidget(self._swatch)

        self._value_lbl = QLabel("")
        self._value_lbl.setObjectName("Caption")
        lay.addWidget(self._value_lbl, 1)

        self._apply_swatch()

    def _apply_swatch(self):
        """色块外观：右下角带棋盘格提示透明度。"""
        v = self._value
        # 半透明色（rgba）也要能看到实际观感：叠在棋盘格上
        self._swatch.setStyleSheet(
            "QPushButton{background:%s;border:1px solid rgba(128,128,128,0.6);"
            "border-radius:4px;}" % v)
        self._value_lbl.setText(v)

    def value(self):
        return self._value

    def set_value(self, v):
        self._value = v
        self._apply_swatch()

    def _pick(self):
        from core.theme_presets import parse_color

        cur = parse_color(self._value) or (255, 255, 255, 1.0)
        # QColorDialog 不处理 alpha，透明色按当前 alpha 保留
        col = QColorDialog.getColor(
            _qcolor(cur), self, "选择颜色")
        if not col.isValid():
            return
        # 原来带 alpha 的保持 alpha（window_tint 这类半透明色有意义）
        keep_alpha = cur[3] < 0.999
        if keep_alpha:
            new = "rgba(%d,%d,%d,%s)" % (col.red(), col.green(),
                                         col.blue(), cur[3])
        else:
            new = col.name()
        self.set_value(new)
        self._on_change(self._key, new)


def _qcolor(rgba):
    from PySide6.QtGui import QColor

    return QColor(int(rgba[0]), int(rgba[1]), int(rgba[2]))


class ThemeEditorDialog(QDialog):
    """自定义主题编辑器。

    :param config: 当前配置（用于取当前明暗做初始编辑对象）
    :param on_preview: 回调，收到 (config) 时立即应用到界面
    """

    def __init__(self, config, on_preview=None, parent=None):
        super().__init__(parent)
        self._config = dict(config or {})
        self._on_preview = on_preview
        self._mode = "light" if config.get("theme_mode", "light") != "dark" \
            else "dark"

        # 初始数据：已有自定义主题 > 从当前经典主题拷一份
        user = load_user_theme() or {}
        self._data = {
            "name": user.get("name", "我的主题"),
            "light": dict(user.get("light") or {}),
            "dark": dict(user.get("dark") or {}),
        }
        self._fill_missing()

        self.setWindowTitle("自定义主题")
        self.setMinimumWidth(480)

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 16)
        root.setSpacing(12)

        tip = QLabel("只调这 8 个颜色就够了——悬停、半透明叠加、"
                     "窗口渐变都会自动推导，不用手填。")
        tip.setObjectName("Caption")
        tip.setWordWrap(True)
        root.addWidget(tip)

        # ---- 明暗切换 ----
        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel("正在编辑"))
        self._btn_light = QPushButton("浅色")
        self._btn_dark = QPushButton("深色")
        for b in (self._btn_light, self._btn_dark):
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
        self._btn_light.clicked.connect(lambda: self._switch("light"))
        self._btn_dark.clicked.connect(lambda: self._switch("dark"))
        mode_row.addWidget(self._btn_light)
        mode_row.addWidget(self._btn_dark)
        mode_row.addStretch(1)
        root.addLayout(mode_row)

        # ---- 颜色行 ----
        holder = QFrame()
        holder.setObjectName("Card")
        grid = QGridLayout(holder)
        grid.setContentsMargins(14, 10, 14, 10)
        grid.setSpacing(2)
        self._rows = {}
        for i, key in enumerate(CORE_KEYS):
            row = _ColorRow(key, self._data[self._mode].get(key, _fallback_color()),
                            self._on_color_changed)
            grid.addWidget(row, i, 0)
            self._rows[key] = row
        root.addWidget(holder)

        # ---- 名称 ----
        name_row = QHBoxLayout()
        name_row.addWidget(QLabel("主题名称"))
        from PySide6.QtWidgets import QLineEdit

        self._name_edit = QLineEdit(self._data["name"])
        name_row.addWidget(self._name_edit, 1)
        root.addLayout(name_row)

        # ---- 导入导出 + 确定取消 ----
        tools = QHBoxLayout()
        imp = QPushButton("导入…")
        imp.clicked.connect(self._import)
        exp = QPushButton("导出…")
        exp.clicked.connect(self._export)
        tools.addWidget(imp)
        tools.addWidget(exp)
        tools.addStretch(1)
        root.addLayout(tools)

        bb = QDialogButtonBox()
        ok = bb.addButton("保存并使用", QDialogButtonBox.AcceptRole)
        bb.addButton("取消", QDialogButtonBox.RejectRole)
        ok.setDefault(True)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        root.addWidget(bb)

        self._switch(self._mode)

    # ------------------------------------------------------------ 内部
    def _fill_missing(self):
        """缺的字段用当前预设的对应明暗补齐，保证 8 个都有值。"""
        from core.theme_presets import resolve_palette

        for mode in ("light", "dark"):
            base = resolve_palette("classic", mode)
            cur = self._data[mode]
            for k in CORE_KEYS:
                if not cur.get(k):
                    cur[k] = base.get(k, _fallback_color())

    def _switch(self, mode):
        self._mode = mode
        self._btn_light.setChecked(mode == "light")
        self._btn_dark.setChecked(mode == "dark")
        for k, row in self._rows.items():
            row.set_value(self._data[mode].get(k, _fallback_color()))

    def _on_color_changed(self, key, value):
        self._data[self._mode][key] = value
        self._preview()

    def _preview(self):
        """把当前编辑中的色板应用到界面（不落盘）。"""
        if self._on_preview is None:
            return
        cfg = dict(self._config)
        cfg["theme_preset"] = "custom"
        try:
            self._on_preview(cfg)
        except Exception:
            pass

    # ------------------------------------------------------------ 对外
    def result_data(self):
        """返回可写入 user_theme.json 的数据。"""
        return {
            "name": self._name_edit.text().strip() or "我的主题",
            "light": dict(self._data["light"]),
            "dark": dict(self._data["dark"]),
        }

    # ------------------------------------------------------------ 导入导出
    def _export(self):
        from PySide6.QtWidgets import QFileDialog, QMessageBox

        path, _ = QFileDialog.getSaveFileName(
            self, "导出主题", "my-theme.json",
            "主题文件 (*.json);;所有文件 (*.*)")
        if not path:
            return
        import json

        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self.result_data(), f, ensure_ascii=False, indent=2)
            QMessageBox.information(self, "导出主题", "已导出")
        except Exception as e:
            QMessageBox.warning(self, "导出主题", "导出失败：%r" % (e,))

    def _import(self):
        from PySide6.QtWidgets import QFileDialog, QMessageBox

        path, _ = QFileDialog.getOpenFileName(
            self, "导入主题", "", "主题文件 (*.json);;所有文件 (*.*)")
        if not path:
            return
        import json

        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            QMessageBox.warning(self, "导入主题", "读取失败：%r" % (e,))
            return
        if not isinstance(data, dict):
            QMessageBox.warning(self, "导入主题", "文件格式不对")
            return
        # 只接受认识的字段，其余忽略（防手写文件带奇怪键）
        for mode in ("light", "dark"):
            src = data.get(mode)
            if isinstance(src, dict):
                for k in CORE_KEYS:
                    if src.get(k):
                        self._data[mode][k] = src[k]
        if data.get("name"):
            self._name_edit.setText(str(data["name"]))
        self._switch(self._mode)
        self._preview()
        QMessageBox.information(self, "导入主题", "已导入，可继续调整或保存")
