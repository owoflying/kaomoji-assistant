"""主页：仪表盘，展示问候、快速统计与常用入口卡片。

主页同时是**插件卡片的宿主**：插件通过 `api.register_home_card()` 注册的卡片
会插进「问候卡片」与「欢迎卡片」之间的插槽（见 `plugin_cards_slot`）。
"""
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from ui.fluent_icons import icon_label, recolor
from ui.pages._scaffold import (
    body_text, card, card_layout, card_title, page_layout, page_title,
    section_title,
)
from ui.pages.greeting_card import GreetingCard
from ui.win11_theme import Theme, resolve_theme_name


class ActionCard(QFrame):
    """快捷入口卡片：整卡可点，并发出 `clicked` 信号。

    原先用「给实例塞一个 mouseReleaseEvent」实现点击，好处是省事，坏处是
    **没有 clicked 信号**——插件通过 `api.ui.on(..., "clicked", ...)` 绑不上。
    这里改成正经的子类，信号才是可绑定的契约。

    另外对外暴露 `text()` / `setText()`：卡片自身没有文字（文字在内嵌 QLabel 上），
    但插件用 `api.ui.set_text("home.actions.search", ...)` 时理应能改到它显示的文字。
    """

    clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Card")      # 复用宿主 QSS 的卡片样式
        self.setCursor(QCursor(Qt.PointingHandCursor))
        self._label = None

    def bind_label(self, label):
        """登记卡片内用于显示文字的那个 QLabel。"""
        self._label = label

    def text(self):
        """卡片显示的文字（供 api.ui.text_of / set_text 使用）。"""
        return self._label.text() if self._label is not None else ""

    def setText(self, value):
        if self._label is not None:
            self._label.setText(str(value))

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self.rect().contains(e.position().toPoint()):
            self.clicked.emit()
        super().mouseReleaseEvent(e)


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

        # 时间问候（内置功能，随主页刷新）
        self.greeting_card = GreetingCard(self)
        root.addWidget(self.greeting_card)

        # 插件卡片插槽：api.register_home_card() 注册的卡片挂在这里。
        # 用独立容器而非直接塞进 root，是为了让插件的增删不影响主页自身布局。
        self.plugin_cards_slot = QWidget(self)
        self.plugin_cards_slot.setObjectName("PluginCardsSlot")
        self._plugin_cards_layout = QVBoxLayout(self.plugin_cards_slot)
        self._plugin_cards_layout.setContentsMargins(0, 0, 0, 0)
        self._plugin_cards_layout.setSpacing(16)
        self.plugin_cards_slot.setVisible(False)   # 没有插件卡片时完全不占位
        root.addWidget(self.plugin_cards_slot)

        # 欢迎/状态卡片
        welcome = card()
        wroot = card_layout(welcome, margins=(20, 18, 20, 18), spacing=6)
        hello = QLabel("颜文字输入辅助器")
        hello.setObjectName("HomeTitle")
        sub = body_text("按全局热键唤起候选条，打字时自动识别情绪推荐颜文字。")
        sub.setWordWrap(True)
        wroot.addWidget(hello)
        wroot.addWidget(sub)
        self.welcome_card = welcome
        self.welcome_title = hello
        self.welcome_sub = sub
        root.addWidget(welcome)

        # 统计行
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
        # 统计行是 QHBoxLayout，没有承载 widget；包一层容器以便按 id 寻址/被插件替换
        self.stats_row_widget = QWidget(self)
        self.stats_row_widget.setObjectName("StatsRow")
        self.stats_row_widget.setLayout(stats_row)
        root.addWidget(self.stats_row_widget)

        # 快捷入口
        root.addWidget(section_title("快捷入口"))
        grid = QHBoxLayout()
        grid.setSpacing(16)
        #: target -> 卡片控件（供 ui_ids 登记为 home.actions.<target>）
        self.action_cards = {}
        for icon_name, text, target in (
            ("search", "搜索颜文字", "search"),
            ("edit", "我的颜文字", "custom"),
            ("flash", "快捷短语", "triggers"),
            ("settings", "设置", "settings"),
        ):
            w = self._action_card(icon_name, text, target)
            self.action_cards[target] = w
            grid.addWidget(w)
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
        self.tip_card = tip
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
        wrap = ActionCard()
        root = card_layout(wrap, margins=(16, 18, 16, 18), spacing=6)
        ico = icon_label(icon_name, 22, self.theme.accent)
        ico.setFixedHeight(28)
        ico.setAlignment(Qt.AlignCenter)
        self._action_icons.append(ico)
        lb = card_title(text)
        lb.setAlignment(Qt.AlignCenter)
        wrap.bind_label(lb)
        root.addWidget(ico)
        root.addWidget(lb)
        # 整卡可点：ActionCard 自带 clicked 信号，插件也能绑定
        wrap.clicked.connect(lambda t=target: self.nav_request.emit(t))
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
        # 问候语跟着时间走：用户可能挂了很久才切回主页
        card = getattr(self, "greeting_card", None)
        if card is not None:
            card.refresh()

    # ------------------------------------------------------------------
    # 插件卡片插槽（由 UnifiedSettingsWindow 驱动，见 _sync_home_cards）
    # ------------------------------------------------------------------
    def plugin_cards(self):
        """当前挂载的插件卡片 widget 列表（按插入顺序）。"""
        lay = getattr(self, "_plugin_cards_layout", None)
        if lay is None:
            return []
        out = []
        for i in range(lay.count()):
            w = lay.itemAt(i).widget()
            if w is not None:
                out.append(w)
        return out

    def add_plugin_card(self, widget):
        """把一张插件卡片追加到插槽末尾并显示插槽。"""
        lay = getattr(self, "_plugin_cards_layout", None)
        if lay is None or widget is None:
            return
        lay.addWidget(widget)
        self.plugin_cards_slot.setVisible(True)

    def clear_plugin_cards(self):
        """移除并销毁所有插件卡片（插件停用/卸载/注册表变动时调用）。

        ⚠ hide() 必须在 setParent(None) 之前：无父控件会变成独立顶层窗口，
        在 deleteLater 执行前会闪一下（同 PluginPage.refresh 的坑）。
        """
        lay = getattr(self, "_plugin_cards_layout", None)
        if lay is None:
            return
        while lay.count():
            item = lay.takeAt(0)
            w = item.widget() if item is not None else None
            if w is None:
                continue
            try:
                w.hide()
                w.setParent(None)
                w.deleteLater()
            except RuntimeError:
                pass
        self.plugin_cards_slot.setVisible(False)
