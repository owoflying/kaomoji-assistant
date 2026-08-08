"""首次启动 / 版本更新弹窗（欢迎更新 v1.2(bdb300a) ...）。

仅在正式发布构建（core.version.is_release_build() 为真）且本次版本与上次已见版本
不同时，由 main.py 在启动后弹出一次。弹窗内容（版本号 + 更新说明）均来自构建期
烘焙的 core._build_version，不依赖运行时 git。
"""
import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QDialog, QLabel, QPushButton, QSpacerItem, QSizePolicy, QVBoxLayout,
)

from ui.win11_theme import Theme


class UpdateDialog(QDialog):
    def __init__(self, version, notes="", theme_name="light", parent=None):
        super().__init__(parent)
        self.setWindowTitle("更新提示")
        self.setWindowFlags(
            self.windowFlags() | Qt.WindowType.WindowStaysOnTopHint
        )
        t = Theme(theme_name)
        self.setStyleSheet(t.style_sheet())
        self.setMinimumWidth(440)
        self.setFixedHeight(280)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 20)
        layout.setSpacing(14)

        title = QLabel("欢迎更新 %s" % version)
        title.setObjectName("SectionTitle")  # 16px / 600 / t.text
        title.setFont(QFont("Segoe UI Variable", 18, QFont.Weight.Bold))
        layout.addWidget(title)

        if notes and notes.strip():
            body = QLabel(notes.strip())
            body.setObjectName("BodyText")
            body.setWordWrap(True)
            body.setAlignment(Qt.AlignmentFlag.AlignLeft)
            layout.addWidget(body)
        else:
            hint = QLabel("颜文字输入辅助器已更新到最新版本，感谢你的使用。")
            hint.setObjectName("BodyText")
            hint.setWordWrap(True)
            layout.addWidget(hint)

        layout.addSpacerItem(
            QSpacerItem(0, 0, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding)
        )

        btn = QPushButton("开始使用")
        btn.setObjectName("AccentButton")
        btn.setMinimumHeight(36)
        btn.clicked.connect(self.accept)
        layout.addWidget(btn, alignment=Qt.AlignmentFlag.AlignRight)

    @staticmethod
    def maybe_show(parent=None, theme_name="light"):
        """按版本决定是否需要弹窗；需要则模态弹出并标记已见。"""
        from core import version as ver
        if not ver.should_show_update_popup():
            return False
        cur = ver.get_app_version()
        dlg = UpdateDialog(
            cur, ver.get_build_notes(), theme_name=theme_name, parent=parent
        )
        dlg.exec()
        ver.save_seen_version(cur)
        # 清掉「强制弹窗」标志，避免本进程内（或异常情况下）被重复触发
        os.environ.pop("KAOMOJI_FORCE_UPDATE_POPUP", None)
        return True
