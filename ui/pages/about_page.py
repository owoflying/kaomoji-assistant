"""关于页：应用信息、开源链接、致谢。"""
from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel, QFrame, QPushButton, QHBoxLayout
from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QFont, QDesktopServices


class AboutPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._init_ui()

    def _init_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(36, 28, 36, 28)
        root.setSpacing(22)

        title = QLabel("关于")
        title.setObjectName("PageTitle")
        root.addWidget(title)

        card = QFrame()
        card.setObjectName("Card")
        croot = QVBoxLayout(card)
        croot.setContentsMargins(24, 22, 24, 22)
        croot.setSpacing(14)

        name = QLabel("颜文字输入辅助器")
        name.setFont(QFont("Segoe UI Variable", 20, QFont.Weight.Bold))
        croot.addWidget(name)

        ver = QLabel("版本 1.0.0")
        ver.setObjectName("BodyText")
        croot.addWidget(ver)

        desc = QLabel("一款 Windows 11 风格的颜文字输入辅助工具。\n"
                      "支持全局热键唤起候选条、情绪识别自动弹出、快捷短语、自定义颜文字与搜索。")
        desc.setObjectName("BodyText")
        desc.setWordWrap(True)
        croot.addWidget(desc)

        link_row = QHBoxLayout()
        gh = QPushButton("GitHub 仓库")
        gh.clicked.connect(lambda: QDesktopServices.openUrl(QUrl("https://github.com/owoflying/kaomoji-assistant")))
        link_row.addWidget(gh)
        link_row.addStretch(1)
        croot.addLayout(link_row)

        croot.addSpacing(8)
        thanks = QLabel("技术栈：Python · PySide6 · pynput · Windows DWM")
        thanks.setObjectName("Caption")
        croot.addWidget(thanks)

        root.addWidget(card)
        root.addStretch(1)
