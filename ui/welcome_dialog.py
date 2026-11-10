"""首次使用欢迎界面：介绍核心用法 + 风险告知与勾选确认。

## 为什么需要它

UIA 提权与插件系统已从"测试功能"**毕业为正式功能**。这两项都有真实风险：

  * **UIA 提权**：需要管理员权限，会弹 UAC 并以管理员身份重启程序；
    进程获得 UIAccess 后能与屏幕键盘等系统级窗口互相覆盖。
  * **插件**：插件是**以与主程序相同的权限**运行的 Python 代码，
    能读写文件、访问网络、修改界面（包括其他插件的界面）。

正式功能意味着默认可见可开，但用户**有权在第一次就被告知**。所以这里
把风险写清楚，并要求勾选"我已了解"才能继续。

## 为什么只在首次弹一次

用独立的标记文件（`welcome_shown.txt`）而非版本号判定：
欢迎 + 风险告知属于**用户第一次接触软件**时的一次性仪式，
不应该每个版本都弹（那是 `ui/update_dialog.py` 的职责）。

## 视觉

与 `UpdateDialog` 保持一致：主题化 QSS、AccentButton、模态。
额外加一块带警示色的风险说明，以及一个必须勾选的确认框。
"""
import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QDialog, QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
)

from ui.fluent_checkbox import FluentCheckBox
from ui.win11_theme import Theme

#: 风险说明分条列出（每条一段，UI 里逐条渲染）
RISK_ITEMS = (
    ("UIA 提权需要管理员权限",
     "开启后程序会弹出 Windows 的 UAC 确认框，同意后会以管理员身份重启。"
     "提权期间程序拥有管理员权限，可与屏幕键盘等系统级窗口互相覆盖；"
     "退出程序后恢复普通权限。"),
    ("插件以与主程序相同的权限运行",
     "插件是本机上的 Python 代码，能读写文件、访问网络、修改界面"
     "（包括修改其他插件的界面）。请只安装你信任来源的插件。"),
    ("所有界面修改都可回滚",
     "插件对界面的改动由主程序记录，插件停用/卸载/崩溃时会自动还原，"
     "不会留下改坏的界面。"),
)


class WelcomeDialog(QDialog):
    """首次使用欢迎页（模态，需勾选确认才能进入）。"""

    def __init__(self, theme_name="light", version="", parent=None):
        super().__init__(parent)
        self.setWindowTitle("欢迎使用")
        self.setWindowFlags(
            self.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)
        t = Theme(theme_name)
        self.setStyleSheet(t.style_sheet())
        self.setMinimumWidth(560)
        self._ok = False

        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 20)
        root.setSpacing(14)

        # ---------- 标题 ----------
        title = QLabel("欢迎使用颜文字输入辅助器")
        title.setObjectName("SectionTitle")
        title.setFont(QFont("Segoe UI Variable", 18, QFont.Weight.Bold))
        root.addWidget(title)

        sub = QLabel(
            "按全局热键（默认 Ctrl+Shift+K）唤起候选条，"
            "或在任意程序里打字时自动推荐颜文字。")
        sub.setObjectName("BodyText")
        sub.setWordWrap(True)
        root.addWidget(sub)
        if version:
            ver = QLabel("版本 %s" % version)
            ver.setObjectName("Caption")
            root.addWidget(ver)

        # ---------- 风险说明 ----------
        root.addWidget(self._risk_block())

        # ---------- 确认勾选 ----------
        self._agree = FluentCheckBox(
            "我已阅读并了解上述风险", t, self)
        self._agree.stateChanged.connect(self._on_agree_changed)
        root.addWidget(self._agree)

        # ---------- 按钮 ----------
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)
        self._cancel = QPushButton("退出")
        self._cancel.clicked.connect(self.reject)
        btn_row.addWidget(self._cancel)
        btn_row.addStretch(1)

        self._start = QPushButton("开始使用")
        self._start.setObjectName("AccentButton")
        self._start.setMinimumHeight(36)
        self._start.setMinimumWidth(120)
        self._start.setEnabled(False)      # 未勾选不可点
        self._start.clicked.connect(self.accept)
        btn_row.addWidget(self._start)
        root.addLayout(btn_row)

    def _risk_block(self):
        """风险说明卡片：标题 + 逐条要点 + 警示色。"""
        box = QFrame()
        box.setObjectName("Card")
        lay = QVBoxLayout(box)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(8)

        head = QLabel("使用前须知")
        head.setObjectName("CardTitle")
        lay.addWidget(head)

        for name, detail in RISK_ITEMS:
            item = QWidget()
            il = QVBoxLayout(item)
            il.setContentsMargins(0, 0, 0, 0)
            il.setSpacing(2)

            n = QLabel("•  " + name)
            n.setObjectName("BodyText")
            n.setWordWrap(True)
            il.addWidget(n)

            d = QLabel("      " + detail)
            d.setObjectName("Caption")
            d.setWordWrap(True)
            il.addWidget(d)

            lay.addWidget(item)
        return box

    def _on_agree_changed(self, *_a):
        self._start.setEnabled(bool(self._agree.isChecked()))

    def result_ok(self):
        return self._ok

    def accept(self):
        self._ok = True
        super().accept()

    # ---------- 入口 ----------
    @staticmethod
    def maybe_show(theme_name="light", parent=None, force=False):
        """按标记决定是否弹欢迎页；弹过则标记，返回是否弹了。"""
        from core import version as ver

        if not ver.should_show_welcome(force=force):
            return False

        dlg = WelcomeDialog(theme_name=theme_name,
                            version=ver.get_app_version(), parent=parent)
        accepted = dlg.exec() == QDialog.Accepted
        if accepted:
            # 只有真正点了「开始使用」才算完成 —— 点「退出」的用户
            # 下次启动应该还能看到欢迎页（他并没有真正开始使用）。
            ver.mark_welcome_shown()
        os.environ.pop("KAOMOJI_FORCE_WELCOME", None)
        return accepted
