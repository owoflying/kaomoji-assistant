"""首次使用引导：教用户怎么用、帮做几项基本配置、顺带介绍亮点。

## 定位（重要）

这不是"免责声明"，而是**新手引导**。三件事：

    1. 告诉用户这软件怎么用（热键、候选条、选字）
    2. 帮他把最基本的两项配置定下来（热键、主题）
    3. 介绍值得一试的能力（插件、UIA 提权、自动更新）

**风险提示不在这里**。UIA 提权和插件各自在"第一次启用时"单独提示
（见 `ui/risk_dialog.py`）——谁用谁看，比在欢迎页一次性堆所有免责条款
更符合用户的阅读习惯。

## 语言风格

说人话：写"能干什么"而不是"不会留下什么副作用"；
写"按 Ctrl+Shift+K 叫出来"而不是"通过全局快捷键唤起候选面板"。

## 只在真正第一次出现

用独立标记文件判定（`welcome_shown.txt`），与版本号无关。
点「开始使用」才算完成；关掉窗口的用户下次还会看到。
"""
import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QComboBox, QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
)

from ui.win11_theme import Theme

#: 特色介绍（图标 + 标题 + 一句话）
HIGHLIGHTS = (
    ("⌨", "随手叫出来",
     "任何程序里按 Ctrl+Shift+K，候选条就贴在光标旁边出现。"),
    ("💬", "猜你想说什么",
     "打出「开心」「好累」这类词，会自动推荐对应表情。"),
    ("🧩", "可以装插件",
     "在「插件」页能加新表情库、改上屏文字，甚至改界面。"),
    ("🔄", "自己保持更新",
     "新版本会在后台下好，关掉程序时顺手装上。"),
)

#: 可选热键（label, 配置值）
HOTKEY_PRESETS = (
    ("Ctrl+Shift+K（默认）", "<ctrl>+<shift>+k"),
    ("Ctrl+Alt+K", "<ctrl>+<alt>+k"),
    ("Ctrl+Shift+空格", "<ctrl>+<shift>+space"),
    ("Alt+`", "<alt>+`"),
)

#: 应用内固定用深/浅色
THEME_PRESETS = (
    ("跟随系统", "system"),
    ("浅色", "light"),
    ("深色", "dark"),
)


class WelcomeDialog(QDialog):
    """首次使用引导（三步合一，不强制勾选任何东西）。"""

    def __init__(self, theme_name="light", version="", config=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("欢迎使用")
        self.setWindowFlags(
            self.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)
        self._config = dict(config or {})
        t = Theme(theme_name)
        self.setStyleSheet(t.style_sheet())
        self.setMinimumWidth(600)

        #: 用户在引导页里选的配置（点「开始使用」后由调用方合并进主配置）
        self.chosen = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(30, 26, 30, 22)
        root.setSpacing(16)

        # ---------- 标题 ----------
        title = QLabel("欢迎使用颜文字输入辅助器")
        title.setFont(QFont("Microsoft YaHei UI", 17, QFont.Weight.Bold))
        root.addWidget(title)

        sub = QLabel("一款常驻托盘的小工具：随手打颜文字，不用记符号。")
        sub.setObjectName("BodyText")
        sub.setWordWrap(True)
        root.addWidget(sub)

        # ---------- 特色 ----------
        root.addWidget(self._highlight_block())

        # ---------- 基本配置 ----------
        root.addWidget(self._setup_block())

        # ---------- 按钮 ----------
        row = QHBoxLayout()
        row.setSpacing(10)
        self._skip = QPushButton("跳过")
        self._skip.clicked.connect(self.reject)
        row.addWidget(self._skip)
        row.addStretch(1)

        start = QPushButton("开始使用")
        start.setObjectName("AccentButton")
        start.setMinimumHeight(36)
        start.setMinimumWidth(130)
        start.clicked.connect(self.accept)
        row.addWidget(start)
        root.addLayout(row)

    def _highlight_block(self):
        box = QWidget()
        lay = QVBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        for icon, name, desc in HIGHLIGHTS:
            line = QHBoxLayout()
            line.setSpacing(10)
            ico = QLabel(icon)
            ico.setFixedWidth(22)
            ico.setAlignment(Qt.AlignTop | Qt.AlignHCenter)
            line.addWidget(ico)

            text = QVBoxLayout()
            text.setSpacing(1)
            n = QLabel(name)
            n.setObjectName("BodyText")
            text.addWidget(n)
            d = QLabel(desc)
            d.setObjectName("Caption")
            d.setWordWrap(True)
            text.addWidget(d)
            line.addLayout(text, 1)
            lay.addLayout(line)
        return box

    def _setup_block(self):
        """两项最基本的配置：热键 + 主题。"""
        from ui.pages._scaffold import card, card_layout

        box = card()
        lay = card_layout(box, margins=(16, 14, 16, 14), spacing=10)

        head = QLabel("先定两件事")
        head.setObjectName("CardTitle")
        lay.addWidget(head)

        # 热键
        r1 = QHBoxLayout()
        r1.setSpacing(10)
        l1 = QLabel("唤起热键")
        l1.setObjectName("BodyText")
        l1.setFixedWidth(72)
        r1.addWidget(l1)
        self._hotkey_combo = QComboBox()
        for label, value in HOTKEY_PRESETS:
            self._hotkey_combo.addItem(label, value)
        self._hotkey_combo.setMinimumWidth(220)
        r1.addWidget(self._hotkey_combo)
        r1.addStretch(1)
        lay.addLayout(r1)

        # 主题
        r2 = QHBoxLayout()
        r2.setSpacing(10)
        l2 = QLabel("外观")
        l2.setObjectName("BodyText")
        l2.setFixedWidth(72)
        r2.addWidget(l2)
        self._theme_combo = QComboBox()
        for label, value in THEME_PRESETS:
            self._theme_combo.addItem(label, value)
        self._theme_combo.setMinimumWidth(220)
        r2.addWidget(self._theme_combo)
        r2.addStretch(1)
        lay.addLayout(r2)

        # 回填当前值
        cur_hotkey = str(self._config.get("hotkey", ""))
        for i in range(self._hotkey_combo.count()):
            if self._hotkey_combo.itemData(i) == cur_hotkey:
                self._hotkey_combo.setCurrentIndex(i)
                break
        cur_theme = str(self._config.get("theme", "system"))
        for i in range(self._theme_combo.count()):
            if self._theme_combo.itemData(i) == cur_theme:
                self._theme_combo.setCurrentIndex(i)
                break

        tip = QLabel("之后随时能在「设置」里改。")
        tip.setObjectName("Caption")
        lay.addWidget(tip)
        return box

    def accept(self):
        """收集用户的选择，交给调用方合并进配置。"""
        try:
            hotkey = self._hotkey_combo.currentData()
            theme = self._theme_combo.currentData()
            if hotkey and hotkey != self._config.get("hotkey"):
                self.chosen["hotkey"] = hotkey
            if theme and theme != self._config.get("theme"):
                self.chosen["theme"] = theme
        except Exception:
            pass
        super().accept()

    # ---------- 入口 ----------
    @staticmethod
    def maybe_show(theme_name="light", config=None, force=False):
        """按标记决定是否弹欢迎页。

        :return: 用户选择的配置 dict（未改动或跳过时为空 dict）；
                 没弹（已弹过）返回 None。
        """
        from core import version as ver

        if not ver.should_show_welcome(force=force):
            return None

        dlg = WelcomeDialog(theme_name=theme_name,
                            version=ver.get_app_version(),
                            config=config)
        accepted = dlg.exec() == QDialog.Accepted
        os.environ.pop("KAOMOJI_FORCE_WELCOME", None)
        if accepted:
            ver.mark_welcome_shown()
            return dict(dlg.chosen)
        # 跳过：不标记，下次启动还会看到引导
        return {}
