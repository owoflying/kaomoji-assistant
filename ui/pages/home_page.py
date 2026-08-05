"""主页：仪表盘，展示快速统计与常用入口卡片。"""
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame, QPushButton
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QCursor

from ui.win11_theme import Theme
from ui.fluent_icons import icon_label, recolor


class HomePage(QWidget):
    nav_request = Signal(str)

    def __init__(self, config, state, user_kao, triggers, data, parent=None):
        super().__init__(parent)
        self.config = config
        self.state = state
        self.user_kao = user_kao
        self.triggers = triggers
        self.data = data
        self.theme = Theme(config.get("theme", "light"))
        self._action_icons = []
        self._init_ui()

    def _init_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(36, 28, 36, 28)
        root.setSpacing(24)

        title = QLabel("主页")
        title.setObjectName("PageTitle")
        root.addWidget(title)

        # 欢迎/状态卡片
        welcome = self._card()
        wroot = QVBoxLayout(welcome)
        wroot.setContentsMargins(20, 18, 20, 18)
        wroot.setSpacing(6)
        hello = QLabel("颜文字输入辅助器")
        hello.setObjectName("HomeTitle")
        sub = QLabel("按全局热键唤起候选条，打字时自动识别情绪推荐颜文字。")
        sub.setObjectName("BodyText")
        sub.setWordWrap(True)
        wroot.addWidget(hello)
        wroot.addWidget(sub)
        root.addWidget(welcome)

        # 统计行
        stats_row = QHBoxLayout()
        stats_row.setSpacing(16)
        self.stat_recent, self.stat_recent_val = self._stat_card("最近使用", str(len(self.state.recent)))
        self.stat_custom, self.stat_custom_val = self._stat_card("我的颜文字", str(len(self.user_kao.get_all())))
        self.stat_trigger, self.stat_trigger_val = self._stat_card("快捷短语", str(len(self.triggers.get_all())))
        self.stat_lib, self.stat_lib_val = self._stat_card("颜文字库", str(len(self.data.get_items())))
        stats_row.addWidget(self.stat_recent)
        stats_row.addWidget(self.stat_custom)
        stats_row.addWidget(self.stat_trigger)
        stats_row.addWidget(self.stat_lib)
        root.addLayout(stats_row)

        # 快捷入口
        root.addWidget(self._section_title("快捷入口"))
        grid = QHBoxLayout()
        grid.setSpacing(16)
        grid.addWidget(self._action_card("search", "搜索颜文字", "search"))
        grid.addWidget(self._action_card("edit", "我的颜文字", "custom"))
        grid.addWidget(self._action_card("flash", "快捷短语", "triggers"))
        grid.addWidget(self._action_card("settings", "设置", "settings"))
        root.addLayout(grid)

        # 提示卡片
        tip = self._card()
        troot = QVBoxLayout(tip)
        troot.setContentsMargins(16, 14, 16, 14)
        troot.setSpacing(4)
        t1 = QLabel("使用提示")
        t1.setObjectName("CardTitle")
        t2 = QLabel("• 托盘图标右键可唤起面板或打开本窗口\n"
                    "• 候选条打开时按 1-9 选择，回车上屏，Esc 关闭\n"
                    "• 自定义颜文字可设置分组与标签，用于情绪推荐和搜索")
        t2.setObjectName("BodyText")
        t2.setWordWrap(True)
        troot.addWidget(t1)
        troot.addWidget(t2)
        root.addWidget(tip)

        root.addStretch(1)

    def _card(self):
        card = QFrame()
        card.setObjectName("Card")
        return card

    def _section_title(self, text):
        lb = QLabel(text)
        lb.setObjectName("SectionTitle")
        return lb

    def _stat_card(self, label, value):
        card = self._card()
        root = QVBoxLayout(card)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(4)
        val = QLabel(value)
        val.setObjectName("StatValue")
        lb = QLabel(label)
        lb.setObjectName("BodyText")
        root.addWidget(val)
        root.addWidget(lb)
        return card, val

    def _action_card(self, icon_name, text, target):
        card = self._card()
        card.setCursor(QCursor(Qt.PointingHandCursor))
        root = QVBoxLayout(card)
        root.setContentsMargins(16, 18, 16, 18)
        root.setSpacing(6)
        ico = icon_label(icon_name, 22, self.theme.accent)
        ico.setFixedHeight(28)
        ico.setAlignment(Qt.AlignCenter)
        self._action_icons.append(ico)
        lb = QLabel(text)
        lb.setAlignment(Qt.AlignCenter)
        lb.setObjectName("CardTitle")
        root.addWidget(ico)
        root.addWidget(lb)
        card.mouseReleaseEvent = lambda *_: self.nav_request.emit(target)
        return card

    def set_theme(self, theme_obj):
        """主题切换时更新快捷入口图标颜色（主页图标用强调色）。"""
        self.theme = theme_obj
        for ico in self._action_icons:
            recolor(ico, theme_obj.accent)

    def refresh_stats(self):
        self.stat_recent_val.setText(str(len(self.state.recent)))
        self.stat_custom_val.setText(str(len(self.user_kao.get_all())))
        self.stat_trigger_val.setText(str(len(self.triggers.get_all())))
        self.stat_lib_val.setText(str(len(self.data.get_items())))
