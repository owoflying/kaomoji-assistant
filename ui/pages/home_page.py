"""主页：仪表盘，展示快速统计与常用入口卡片。"""
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import QHBoxLayout, QLabel, QWidget

from ui.fluent_icons import icon_label, recolor
from ui.pages._scaffold import (
    body_text, card, card_layout, card_title, page_layout, page_title,
    section_title,
)
from ui.win11_theme import Theme, resolve_theme_name


class HomePage(QWidget):
    nav_request = Signal(str)

    def __init__(self, config, state, user_kao, triggers, data, parent=None):
        super().__init__(parent)
        self.config = config
        self.state = state
        self.user_kao = user_kao
        self.triggers = triggers
        self.data = data
        self.theme = Theme(resolve_theme_name(config.get("theme", "light")))
        self._action_icons = []
        self._init_ui()

    def _init_ui(self):
        root = page_layout(self, spacing=24)
        root.addWidget(page_title("主页"))

        # 欢迎/状态卡片
        welcome = card()
        wroot = card_layout(welcome, margins=(20, 18, 20, 18), spacing=6)
        hello = QLabel("颜文字输入辅助器")
        hello.setObjectName("HomeTitle")
        sub = body_text("按全局热键唤起候选条，打字时自动识别情绪推荐颜文字。")
        sub.setWordWrap(True)
        wroot.addWidget(hello)
        wroot.addWidget(sub)
        root.addWidget(welcome)

        # 统计行
        stats_row = QHBoxLayout()
        stats_row.setSpacing(16)
        self.stat_recent, self.stat_recent_val = self._stat_card(
            "最近使用", str(len(self.state.recent)))
        self.stat_custom, self.stat_custom_val = self._stat_card(
            "我的颜文字", str(len(self.user_kao.get_all())))
        self.stat_trigger, self.stat_trigger_val = self._stat_card(
            "快捷短语", str(len(self.triggers.get_all())))
        self.stat_lib, self.stat_lib_val = self._stat_card(
            "颜文字库", str(len(self.data.get_items())))
        for w in (self.stat_recent, self.stat_custom,
                  self.stat_trigger, self.stat_lib):
            stats_row.addWidget(w)
        root.addLayout(stats_row)

        # 快捷入口
        root.addWidget(section_title("快捷入口"))
        grid = QHBoxLayout()
        grid.setSpacing(16)
        for icon_name, text, target in (
            ("search", "搜索颜文字", "search"),
            ("edit", "我的颜文字", "custom"),
            ("flash", "快捷短语", "triggers"),
            ("settings", "设置", "settings"),
        ):
            grid.addWidget(self._action_card(icon_name, text, target))
        root.addLayout(grid)

        # 提示卡片
        tip = card()
        troot = card_layout(tip, spacing=4)
        troot.addWidget(card_title("使用提示"))
        t2 = body_text("• 托盘图标右键可唤起面板或打开本窗口\n"
                       "• 候选条打开时按 1-9 选择，回车上屏，Esc 关闭\n"
                       "• 自定义颜文字可设置分组与标签，用于情绪推荐和搜索")
        t2.setWordWrap(True)
        troot.addWidget(t2)
        root.addWidget(tip)

        root.addStretch(1)

    def _stat_card(self, label, value):
        wrap = card()
        root = card_layout(wrap, spacing=4)
        val = QLabel(value)
        val.setObjectName("StatValue")
        root.addWidget(val)
        root.addWidget(body_text(label))
        return wrap, val

    def _action_card(self, icon_name, text, target):
        wrap = card()
        wrap.setCursor(QCursor(Qt.PointingHandCursor))
        root = card_layout(wrap, margins=(16, 18, 16, 18), spacing=6)
        ico = icon_label(icon_name, 22, self.theme.accent)
        ico.setFixedHeight(28)
        ico.setAlignment(Qt.AlignCenter)
        self._action_icons.append(ico)
        lb = card_title(text)
        lb.setAlignment(Qt.AlignCenter)
        root.addWidget(ico)
        root.addWidget(lb)
        # 整卡可点：QFrame 没有 clicked 信号，直接接管鼠标释放事件
        wrap.mouseReleaseEvent = lambda *_: self.nav_request.emit(target)
        return wrap

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
