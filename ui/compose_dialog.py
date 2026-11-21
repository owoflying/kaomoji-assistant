"""颜文字组合器对话框：用「积木」拼出一个颜文字。

## 为什么做成对话框而不是页面

组合是**一次性动作**：拼好、确认、存进"我的颜文字"，之后就用存的
那一条。做成独立页面会平白多一个导航项，还得管它的主题广播。

## 交互

五个下拉框 + 一个实时预览：

    左括号  眼睛  嘴  右眼  右括号  装饰
      (      ◕    ‿    ◕     )      ♡

右眼默认跟随左眼镜像（`•̀` ↔ `•́`），可以单独改。
底部有「随机」和「反解」（把预览框里的文字拆回积木）。
"""

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QVBoxLayout,
)

from core import kaomoji_compose as KC
from ui.fluent_combobox import FluentComboBox


def _kaomoji_font(size=20):
    try:
        from ui.win11_theme import kaomoji_font
        return kaomoji_font(size)
    except Exception:
        f = QFont()
        f.setPixelSize(size)
        return f


class ComposeDialog(QDialog):
    """积木式颜文字组合器。"""

    def __init__(self, theme=None, initial="", parent=None):
        super().__init__(parent)
        self._theme = theme
        self._quiet = False          # 程序化改动期间屏蔽联动

        self.setWindowTitle("组合颜文字")
        self.setMinimumWidth(440)
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 16)
        root.setSpacing(12)

        # ---------- 预览 ----------
        self.preview = QLineEdit()
        self.preview.setFont(_kaomoji_font(22))
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setMinimumHeight(46)
        root.addWidget(self.preview)

        # ---------- 积木行 ----------
        self._combos = {}
        row = QHBoxLayout()
        row.setSpacing(6)
        for key, label, items in (
            ("left", "左括", KC.LEFT),
            ("eye", "眼睛", KC.EYES),
            ("mouth", "嘴", KC.MOUTHS),
            ("right", "右括", KC.RIGHT),
            ("decor", "装饰", KC.DECOR),
        ):
            col = QVBoxLayout()
            col.setSpacing(4)
            cap = QLabel(label)
            cap.setAlignment(Qt.AlignCenter)
            col.addWidget(cap)
            cb = FluentComboBox(theme)
            # 空装饰项显示成「无」，否则下拉里是一个空白行
            cb.addItems([x if x else "（无）" for x in items])
            cb.currentIndexChanged.connect(self._on_part_changed)
            cb.setMinimumWidth(64)
            col.addWidget(cb)
            row.addLayout(col)
            self._combos[key] = cb
        root.addLayout(row)

        # ---------- 右眼是否镜像 ----------
        self.mirror_note = QLabel()
        self.mirror_note.setAlignment(Qt.AlignCenter)
        root.addWidget(self.mirror_note)

        # ---------- 按钮 ----------
        tools = QHBoxLayout()
        rand_btn = QPushButton("随机一个")
        rand_btn.clicked.connect(self._randomize)
        tools.addWidget(rand_btn)

        unpack_btn = QPushButton("从预览反解")
        unpack_btn.setToolTip("把预览框里手打的颜文字拆回积木")
        unpack_btn.clicked.connect(self._unpack)
        tools.addWidget(unpack_btn)
        tools.addStretch(1)
        root.addLayout(tools)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.button(QDialogButtonBox.Ok).setText("就用它")
        btns.button(QDialogButtonBox.Cancel).setText("取消")
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        root.addWidget(btns)

        # ---------- 初始值 ----------
        self._quiet = True
        if initial:
            self.preview.setText(initial)
            self._unpack(silent=True)
        else:
            self._apply_defaults()
        self._quiet = False
        self._sync()

    # ------------------------------------------------------------ 内部
    def _apply_defaults(self):
        for key, val in (("left", "("), ("eye", "◕"), ("mouth", "‿"),
                         ("right", ")"), ("decor", "")):
            cb = self._combos[key]
            idx = cb.findText(val if val else "（无）")
            if idx >= 0:
                cb.setCurrentIndex(idx)

    def _part(self, key):
        txt = self._combos[key].currentText()
        return "" if txt == "（无）" else txt

    def _on_part_changed(self, *_):
        if self._quiet:
            return
        self._sync()

    def _sync(self):
        """按当前积木刷新预览与镜像说明。"""
        if self._quiet:
            return
        eye = self._part("eye")
        mirror_eye = KC.mirror_eye(eye)
        text = KC.compose(
            left=self._part("left"), eye=eye, mouth=self._part("mouth"),
            right=self._part("right"), decor=self._part("decor"))
        self.preview.setText(text)
        if mirror_eye != eye:
            self.mirror_note.setText(
                "右眼自动用了镜像：%s → %s" % (eye, mirror_eye))
        else:
            self.mirror_note.setText("")

    def _randomize(self):
        parts = KC.random_parts()
        self._quiet = True
        try:
            for key in ("left", "eye", "mouth", "right", "decor"):
                cb = self._combos[key]
                val = parts[key] or "（无）"
                idx = cb.findText(val)
                if idx >= 0:
                    cb.setCurrentIndex(idx)
        finally:
            self._quiet = False
        self._sync()

    def _unpack(self, silent=False):
        """把预览里的文字拆回积木；拆不了就提示。"""
        text = self.preview.text().strip()
        parts = KC.parts_of(text)
        if not parts:
            if not silent:
                self.mirror_note.setText(
                    "这个颜文字结构不常规，拆不回积木——可以直接手改预览框")
            return
        self._quiet = True
        try:
            for key in ("left", "eye", "mouth", "right", "decor"):
                cb = self._combos[key]
                val = parts.get(key) or "（无）"
                idx = cb.findText(val)
                if idx >= 0:
                    cb.setCurrentIndex(idx)
        finally:
            self._quiet = False
        self._sync()

    # ------------------------------------------------------------ 对外
    def text(self):
        return self.preview.text().strip()
