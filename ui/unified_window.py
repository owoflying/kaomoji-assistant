"""统一主窗口：左侧导航 + 右侧内容区，Win11 Settings 风格。

整合 主页 / 颜文字库 / 我的颜文字 / 快捷短语 / 搜索 / 设置 / 关于，
替代原先独立的 settings/custom/trigger/search 对话框。
"""
import ctypes

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, QLabel, QStackedWidget,
    QSizePolicy, QFrame,
)
from PySide6.QtCore import Qt, Signal, QPropertyAnimation, QEasingCurve, QPoint, QSize, QRect
from PySide6.QtGui import QFont, QColor, QPainter, QPainterPath

from ui.win_style import apply_backdrop, apply_dark_mode, DWMSBT_MAINWINDOW
from ui.win11_theme import Theme
from ui.animated_stack import AnimatedStackedWidget
from ui.pages import (
    HomePage, LibraryPage, CustomKaomojiPage, TriggerPage,
    SearchPage, SettingsPage, AboutPage,
)


class _NavItem(QWidget):
    """单个导航项：左侧高亮指示条 + 图标 + 文字 + 悬停/选中背景。"""

    clicked = Signal()

    def __init__(self, icon, text, theme, parent=None):
        super().__init__(parent)
        self.theme = theme
        self._selected = False
        self.setFixedHeight(40)
        self.setCursor(Qt.PointingHandCursor)
        root = QHBoxLayout(self)
        root.setContentsMargins(10, 0, 12, 0)
        root.setSpacing(10)

        self.indicator = QLabel()
        self.indicator.setFixedWidth(3)
        self.indicator.setFixedHeight(18)
        self.indicator.setStyleSheet("background:transparent;border-radius:2px;")

        self.ico = QLabel(icon)
        self.ico.setFont(QFont("Segoe UI", 15))
        self.ico.setFixedWidth(26)
        self.ico.setAlignment(Qt.AlignCenter)
        self.txt = QLabel(text)
        self.txt.setFont(QFont("Segoe UI Variable", 13))
        root.addWidget(self.indicator)
        root.addWidget(self.ico)
        root.addWidget(self.txt, 1)
        root.addStretch(0)
        self._apply_style()

    def set_selected(self, selected):
        self._selected = selected
        self._apply_style()

    def _apply_style(self):
        t = self.theme
        bg = t.nav_selected if self._selected else "transparent"
        # 选中态悬停时保持选中底色，避免悬停把选中项“洗白”
        hover = t.nav_selected if self._selected else t.nav_hover
        color = t.text if self._selected else t.text_secondary
        self.setStyleSheet(
            "_NavItem{background:%s;border-radius:6px;}"
            "_NavItem:hover{background:%s;}"
            "QLabel{background:transparent;color:%s;}" % (bg, hover, color)
        )
        self.indicator.setStyleSheet(
            "background:%s;border-radius:2px;" % (t.accent if self._selected else "transparent")
        )

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(e)

    def update_theme(self, theme):
        self.theme = theme
        self._apply_style()


class UnifiedSettingsWindow(QMainWindow):
    """统一设置/管理窗口。"""

    output_selected = Signal(str)       # 搜索/库页选中颜文字 -> 注入
    config_applied = Signal(dict)       # 设置页应用配置
    finished = Signal()                 # 窗口关闭（类似 QDialog.finished）

    _NAV = [
        ("🏠", "主页", "home"),
        ("📚", "颜文字库", "library"),
        ("✏️", "我的颜文字", "custom"),
        ("⚡", "快捷短语", "triggers"),
        ("🔍", "搜索", "search"),
        ("⚙️", "设置", "settings"),
        ("ℹ️", "关于", "about"),
    ]

    def __init__(self, data, config, state, user_kao, triggers, parent=None):
        super().__init__(parent)
        self.data = data
        self.config = config
        self.state = state
        self.user_kao = user_kao
        self.triggers = triggers

        self.theme = Theme(config.get("theme", "light"))
        self._nav_items = []
        self._indicator = None
        self._pending_index = 0

        self.setWindowTitle("颜文字输入辅助器")
        self.setMinimumSize(900, 640)
        self.resize(1024, 720)
        self._init_ui()
        self._apply_theme()

    def _init_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # 左侧导航栏
        self.sidebar = QWidget()
        self.sidebar.setFixedWidth(260)
        sroot = QVBoxLayout(self.sidebar)
        sroot.setContentsMargins(12, 16, 12, 16)
        sroot.setSpacing(6)

        # 标题 / 搜索占位（与 Win11 Settings 保持一致）
        app_title = QLabel("颜文字助手")
        app_title.setFont(QFont("Segoe UI Variable", 14, QFont.Weight.Bold))
        app_title.setStyleSheet("color:%s;padding-left:6px;" % self.theme.text)
        sroot.addWidget(app_title)

        sroot.addSpacing(18)

        # 导航项
        self._nav_container = QWidget()
        nvbox = QVBoxLayout(self._nav_container)
        nvbox.setContentsMargins(0, 0, 0, 0)
        nvbox.setSpacing(4)
        for icon, text, key in self._NAV:
            item = _NavItem(icon, text, self.theme)
            item.clicked.connect(lambda k=key: self._set_page(k))
            nvbox.addWidget(item)
            self._nav_items.append((key, item))
        nvbox.addStretch(1)
        sroot.addWidget(self._nav_container, 1)

        root.addWidget(self.sidebar)

        # 1px 分隔线：左侧 mica 侧栏 与 右侧内容区表面 之间，增强层次
        divider = QFrame()
        divider.setObjectName("Divider")
        divider.setFixedWidth(1)
        root.addWidget(divider)

        # 右侧内容区（带平滑过渡的栈式容器）
        self.content = AnimatedStackedWidget()
        self.content.setObjectName("ContentArea")
        root.addWidget(self.content, 1)

        # 创建各页
        self.home_page = HomePage(self.config, self.state, self.user_kao, self.triggers, self.data)
        self.library_page = LibraryPage(self.data)
        self.custom_page = CustomKaomojiPage(self.user_kao)
        self.trigger_page = TriggerPage(self.triggers)
        self.search_page = SearchPage(self.data, self.user_kao, self.config.get("theme", "light"))
        self.settings_page = SettingsPage(self.config)
        self.about_page = AboutPage()

        self._pages = {
            "home": self.home_page,
            "library": self.library_page,
            "custom": self.custom_page,
            "triggers": self.trigger_page,
            "search": self.search_page,
            "settings": self.settings_page,
            "about": self.about_page,
        }
        for p in self._pages.values():
            self.content.addWidget(p)

        # 信号
        self.home_page.nav_request.connect(self._set_page)
        self.library_page.selected.connect(self.output_selected.emit)
        self.search_page.selected.connect(self.output_selected.emit)
        self.settings_page.config_applied.connect(self._on_settings_applied)

        self._set_page("home", animate=False)

    def _set_page(self, key, animate=True):
        target = None
        for i, (k, item) in enumerate(self._nav_items):
            selected = k == key
            item.set_selected(selected)
            if selected:
                target = i
        if target is None:
            return
        # 窗口未显示时没有有效 geometry，动画会导致布局错乱，强制走静态切换
        if animate and not self.isVisible():
            animate = False
        if animate:
            # 平滑「上浮 + 淡入」过渡，旧页留在底层防闪烁
            self.content.slide_to(self._pages[key], 260, rise=12)
        else:
            self.content.setCurrentWidget(self._pages[key])
        if key == "home":
            self.home_page.refresh_stats()
        if key == "search":
            self.search_page.focus_query()

    def _apply_theme(self):
        t = self.theme
        self.setStyleSheet(t.style_sheet())
        self.sidebar.setStyleSheet("background:transparent;")
        # 内容区表面较侧栏略亮/略深，形成 Win11 设置式层次
        self.content.setStyleSheet("background:%s;border:none;" % t.content_surface)
        # 导航项刷新主题
        for _, item in self._nav_items:
            item.update_theme(t)
        # 子页搜索框单独样式需刷新
        self.search_page._style()

    def apply_config(self, config):
        """外部配置变更后刷新窗口主题与内部页。"""
        self.config = config
        self.theme = Theme(config.get("theme", "light"))
        self._apply_theme()
        self.settings_page.config = config
        self.settings_page.refresh_from_config()
        self.search_page._style()
        self._update_backdrop()

    def _on_settings_applied(self, new_cfg):
        self.config_applied.emit(new_cfg)

    def showEvent(self, e):
        super().showEvent(e)
        self._update_backdrop()
        self.home_page.refresh_stats()

    def closeEvent(self, e):
        self.finished.emit()
        super().closeEvent(e)

    def _update_backdrop(self):
        hwnd = int(self.winId())
        apply_backdrop(hwnd, DWMSBT_MAINWINDOW)
        apply_dark_mode(hwnd, self.theme.dark)

    def paintEvent(self, e):
        """自绘内容区背景（Mica 由 DWM 处理，这里补一层纯色底防止透明异常）。"""
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(self.theme.bg))
        super().paintEvent(e)
