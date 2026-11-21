"""从剪贴板提取颜文字并一键收藏。

## 使用流程

    1. 在聊天记录里**选中**那段含颜文字的文字（或整条消息）
    2. 按 Ctrl+C（或右键复制）
    3. 按热键（默认 Ctrl+Alt+K）唤起本窗口
    4. 打勾想要的几条 → 一键加入「我的颜文字」

## 为什么用剪贴板而不是读窗口

试过用 UIA 遍历窗口 UI 树。结论是不该这么做：

* 微信/QQ 大量使用自绘控件，UIA 树经常是空的，读不到；
* 即使能读，还要处理"读哪个窗口""什么时候读""权限够不够"；
* 遍历整棵树很慢，而且每次都读全量聊天记录也不合适。

剪贴板方案则是**用户已经明确表达了"我要这段"**——他手动选中了、
手动复制了。我们只负责从中挑出颜文字，责任边界干净，
而且兼容一切软件（浏览器、微信、Slack、终端…）。

## 一个必须处理的边界

用户可能复制的就是普通文字（没有颜文字）。这时要**明确告诉他
"没找到"**，而不是弹一个空窗口让人困惑。
"""

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QHBoxLayout, QLabel,
    QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from core import kaomoji_extract as KX


def _kaomoji_font(size=18):
    try:
        from ui.win11_theme import kaomoji_font
        return kaomoji_font(size)
    except Exception:
        f = QFont()
        f.setPixelSize(size)
        return f


class ExtractDialog(QDialog):
    """展示从剪贴板提取到的颜文字，勾选后收藏。"""

    def __init__(self, items, theme=None, parent=None, group="收藏",
                 preexisting=None):
        super().__init__(parent)
        self._theme = theme
        self._group = group
        self._checks = []
        self._preexisting = set(preexisting or ())

        self.setWindowTitle("从剪贴板提取颜文字")
        self.setMinimumWidth(420)
        self.setMinimumHeight(300)

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 16)
        root.setSpacing(10)

        tip = QLabel("在聊天记录里选中文字 → 复制 → 这里挑要收藏的")
        tip.setObjectName("Caption")
        tip.setWordWrap(True)
        root.addWidget(tip)

        # ---------- 列表 ----------
        area = QScrollArea()
        area.setWidgetResizable(True)
        holder = QWidget()
        v = QVBoxLayout(holder)
        v.setContentsMargins(4, 4, 4, 4)
        v.setSpacing(6)

        for text in items:
            row = QHBoxLayout()
            cb = QCheckBox()
            cb.setChecked(True)
            row.addWidget(cb)

            lbl = QLabel(text)
            lbl.setFont(_kaomoji_font(18))
            lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
            row.addWidget(lbl, 1)

            if text in self._preexisting:
                # 已经在库里了：默认不勾，并标注出来，
                # 免得用户重复收藏同一批
                cb.setChecked(False)
                mark = QLabel("已在库中")
                mark.setObjectName("Caption")
                row.addWidget(mark)

            row.addStretch(0)
            wrapper = QWidget()
            wrapper.setLayout(row)
            v.addWidget(wrapper)
            self._checks.append((cb, text))

        v.addStretch(1)
        area.setWidget(holder)
        root.addWidget(area, 1)

        # ---------- 底部 ----------
        btns = QHBoxLayout()
        all_btn = QPushButton("全选")
        all_btn.clicked.connect(lambda: self._set_all(True))
        none_btn = QPushButton("全不选")
        none_btn.clicked.connect(lambda: self._set_all(False))
        btns.addWidget(all_btn)
        btns.addWidget(none_btn)
        btns.addStretch(1)
        root.addLayout(btns)

        bb = QDialogButtonBox()
        ok = bb.addButton("收藏选中的", QDialogButtonBox.AcceptRole)
        bb.addButton("取消", QDialogButtonBox.RejectRole)
        ok.setDefault(True)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        root.addWidget(bb)

        try:
            if theme is not None:
                self.setStyleSheet(theme.style_sheet())
        except Exception:
            pass

    def _set_all(self, on):
        for cb, text in self._checks:
            if on and text in self._preexisting:
                continue        # 已在库中的不因"全选"而被勾上
            cb.setChecked(on)

    def selected(self):
        """用户勾选的颜文字列表。"""
        return [text for cb, text in self._checks if cb.isChecked()]


def read_clipboard_items(valid_items=None, max_items=20):
    """读剪贴板 → 提取颜文字。

    :param valid_items: 当前库里的条目集合；传入后会用来标注"已在库中"
    :return: (items, raw_text)；剪贴板为空时返回 ([], "")

    这个函数不做任何 UI 操作，方便单独测试。
    """
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        return [], ""
    try:
        raw = app.clipboard().text() or ""
    except Exception:
        return [], ""
    items = KX.extract(raw, max_items=max_items)
    return items, raw
