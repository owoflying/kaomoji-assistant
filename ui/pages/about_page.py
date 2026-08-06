"""关于页：应用信息、开源链接、致谢、开发者模式入口。

开发者模式解锁方式：在关于页连续点击「版本号」8 次。启用后：
- 关于页会显示「开发者模式」徽标与「查看运行日志」入口；
- 系统托盘菜单的「查看日志」项也随之可见。
日志功能因此完全被开发者模式门控，便于调试与诊断。
"""
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QFrame, QPushButton, QHBoxLayout,
)
from PySide6.QtCore import Qt, QUrl, QTimer, QEvent, Signal
from PySide6.QtGui import QFont, QDesktopServices


_CLICK_TARGET = 8          # 连续点击版本号多少次解锁开发者模式
_CLICK_TIMEOUT_MS = 2500    # 两次点击间隔超过该值则计数清零


class AboutPage(QWidget):
    developer_mode_enabled = Signal()  # 解锁开发者模式后实时通知主窗口添加标签

    def __init__(self, config=None, save_config=None, open_log=None, parent=None):
        super().__init__(parent)
        self.config = config or {}
        self._save_config = save_config
        self._open_log = open_log
        self._click_count = 0
        self._click_timer = QTimer(self)
        self._click_timer.setSingleShot(True)
        self._click_timer.setInterval(_CLICK_TIMEOUT_MS)
        self._click_timer.timeout.connect(self._reset_clicks)
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

        # 版本号：点击 8 次解锁开发者模式
        ver = QLabel("版本 1.0.0")
        ver.setObjectName("BodyText")
        ver.setCursor(Qt.PointingHandCursor)
        ver.installEventFilter(self)
        self._ver_label = ver
        croot.addWidget(ver)

        # 开发者模式徽标（启用前隐藏）
        self._dev_badge = QLabel("● 开发者模式")
        self._dev_badge.setObjectName("Caption")
        self._dev_badge.setStyleSheet("color:#0a84ff;font-weight:bold;")
        self._dev_badge.setVisible(False)
        croot.addWidget(self._dev_badge)

        # 点击版本号的反馈提示
        self._dev_hint = QLabel("")
        self._dev_hint.setObjectName("Caption")
        croot.addWidget(self._dev_hint)

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

        # 若已处于开发者模式，直接进入启用态
        if self.config.get("developer_mode", False):
            self._enter_developer_mode(announce=False)

    # ---------- 事件过滤：捕获版本号点击 ----------
    def eventFilter(self, obj, event):
        if obj is self._ver_label and event.type() == QEvent.MouseButtonPress \
                and event.button() == Qt.LeftButton:
            self._on_version_clicked()
            return True
        return super().eventFilter(obj, event)

    def _on_version_clicked(self):
        if self.config.get("developer_mode", False):
            self._dev_hint.setText("开发者模式已启用")
            return
        self._click_count += 1
        remaining = _CLICK_TARGET - self._click_count
        if remaining <= 0:
            self._enter_developer_mode(announce=True)
            return
        self._dev_hint.setText("再点击 %d 次解锁开发者模式" % remaining)
        self._click_timer.start()   # 重置超时

    def _reset_clicks(self):
        self._click_count = 0
        self._dev_hint.setText("")

    def _enter_developer_mode(self, announce):
        self.config["developer_mode"] = True
        if self._save_config:
            self._save_config(self.config)
        self._click_count = 0
        self._click_timer.stop()
        self._dev_badge.setVisible(True)
        self.developer_mode_enabled.emit()
        if announce:
            self._dev_hint.setText("已启用开发者模式，可在「开发者」标签查看运行日志与实时事件流")
        else:
            self._dev_hint.setText("开发者模式已启用")
