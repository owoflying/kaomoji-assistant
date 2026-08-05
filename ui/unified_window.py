"""统一主窗口：左侧导航 + 右侧内容区，Win11 Settings 风格。

整合 主页 / 颜文字库 / 我的颜文字 / 快捷短语 / 搜索 / 设置 / 关于，
替代原先独立的 settings/custom/trigger/search 对话框。

视觉上采用 WinUI 3 风格：
  * 无边框窗口（FramelessWindowHint）+ 圆角边框，调用 DWM 系统亚克力（acrylic）
    材质，并叠加一层细微的竖向渐变；
  * 自定义标题栏（应用名 + 关闭按钮），通过 WM_NCHITTEST 实现原生拖拽与八向缩放；
  * 导航与快捷入口全部使用 Segoe Fluent Icons 官方图标，不再使用 emoji。
"""
import ctypes
from ctypes.wintypes import MSG

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, QLabel, QStackedWidget,
    QSizePolicy, QFrame, QPushButton,
)
from PySide6.QtCore import Qt, Signal, QPoint, QRect, QRectF, QEvent
from PySide6.QtGui import (
    QFont, QColor, QPainter, QPainterPath, QPen, QLinearGradient,
    QRegion, QTransform,
)

from ui.win_style import (
    apply_backdrop, apply_dark_mode, _has_dwm,
    DWMSBT_TRANSIENTWINDOW, DWMSBT_NONE,
)
from ui.win11_theme import Theme
from ui.fluent_icons import icon_label, recolor
from ui.animated_stack import AnimatedStackedWidget
from ui.pages import (
    HomePage, LibraryPage, CustomKaomojiPage, TriggerPage,
    SearchPage, SettingsPage, AboutPage,
)

# WM_NCHITTEST 命中测试返回值
WM_NCHITTEST = 0x0084
HTCLIENT, HTCAPTION = 1, 2
HTLEFT, HTRIGHT, HTTOP = 10, 11, 12
HTTOPLEFT, HTTOPRIGHT = 13, 14
HTBOTTOM, HTBOTTOMLEFT, HTBOTTOMRIGHT = 15, 16, 17


def _parse_color(spec):
    """把主题里的 rgba()/hex 颜色字符串安全地转成 QColor。"""
    c = QColor(spec)
    if c.isValid():
        return c
    import re
    m = re.match(r"rgba?\((\d+),\s*(\d+),\s*(\d+)(?:,\s*([\d.]+))?\)", spec)
    if m:
        r, g, b = int(m.group(1)), int(m.group(2)), int(m.group(3))
        a = float(m.group(4)) if m.group(4) else 1.0
        return QColor(r, g, b, int(a * 255) if a <= 1.0 else int(a))
    return QColor(0, 0, 0)


class _NavItem(QWidget):
    """单个导航项：左侧高亮指示条 + 图标 + 文字 + 悬停/选中背景。"""

    clicked = Signal()

    def __init__(self, icon_name, text, theme, parent=None):
        super().__init__(parent)
        self.theme = theme
        self._selected = False
        self.setFixedHeight(40)
        self.setCursor(Qt.PointingHandCursor)
        self._icon_name = icon_name
        root = QHBoxLayout(self)
        root.setContentsMargins(10, 0, 12, 0)
        root.setSpacing(10)

        self.indicator = QLabel()
        self.indicator.setFixedWidth(3)
        self.indicator.setFixedHeight(18)
        self.indicator.setStyleSheet("background:transparent;border-radius:2px;")

        self.ico = icon_label(icon_name, 16, theme.text_secondary)
        self.ico.setFixedWidth(26)

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
        recolor(self.ico, t.text if self._selected else t.text_secondary)
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
        ("home",     "主页",     "home"),
        ("library",  "颜文字库", "library"),
        ("edit",     "我的颜文字", "custom"),
        ("flash",    "快捷短语", "triggers"),
        ("search",   "搜索",     "search"),
        ("settings", "设置",     "settings"),
        ("info",     "关于",     "about"),
    ]

    # 圆角 / 留白 / 标题栏尺寸
    _RADIUS = 12
    _PAD = 12          # 窗口边缘到面板内容的距离（也是阴影与缩放热区宽度）
    _TITLEBAR_H = 40

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
        self._acrylic_on = bool(config.get("acrylic", True)) and _has_dwm
        self._shadow_color = _parse_color(self.theme.shadow_color)

        self._init_window()
        self._init_ui()
        self._apply_theme()

    # ---------- 窗口与材质 ----------
    def _init_window(self):
        # 无边框 + 透明背景（亚克力由 DWM 提供）
        self.setWindowFlags(self.windowFlags() | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setWindowTitle("颜文字输入辅助器")
        self.setMinimumSize(900, 640)
        self.resize(1024, 720)
        self._update_mask()

    def _update_mask(self):
        """把内容区裁剪到圆角矩形内，使子控件四角随窗口一起圆角化。"""
        P = self._PAD
        r = self._RADIUS
        w, h = self.width(), self.height()
        if w <= 0 or h <= 0 or getattr(self, "central", None) is None:
            return
        rect = QRect(P, P, w - 2 * P, h - 2 * P)
        pp = QPainterPath()
        pp.addRoundedRect(QRectF(rect), r, r)
        poly = pp.toFillPolygon(QTransform())
        self.central.setMask(QRegion(poly.toPolygon()))

    def _init_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        self.central = central
        root = QVBoxLayout(central)
        root.setContentsMargins(self._PAD, self._PAD, self._PAD, self._PAD)
        root.setSpacing(0)

        # ---------- 自定义标题栏 ----------
        self.titlebar = QWidget()
        self.titlebar.setFixedHeight(self._TITLEBAR_H)
        troot = QHBoxLayout(self.titlebar)
        troot.setContentsMargins(8, 0, 8, 0)
        troot.setSpacing(8)

        brand = QLabel("颜文字助手")
        brand.setObjectName("BrandTitle")
        brand.setFont(QFont("Segoe UI Variable", 13, QFont.Weight.Medium))
        troot.addWidget(brand)
        troot.addStretch(1)

        # 关闭按钮（Segoe Fluent Icons 叉号）
        self._close_btn = QPushButton()
        self._close_btn.setObjectName("TitleClose")
        self._close_btn.setFixedSize(34, 30)
        self._close_btn.setCursor(Qt.PointingHandCursor)
        self._close_ico = icon_label("close", 14, self.theme.text)
        cb_layout = QHBoxLayout(self._close_btn)
        cb_layout.setContentsMargins(0, 0, 0, 0)
        cb_layout.addStretch(1)
        cb_layout.addWidget(self._close_ico)
        cb_layout.addStretch(1)
        self._close_btn.clicked.connect(self.close)
        troot.addWidget(self._close_btn)
        root.addWidget(self.titlebar)

        # ---------- 主体：左导航 + 右内容 ----------
        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)

        self.sidebar = QWidget()
        self.sidebar.setFixedWidth(248)
        sroot = QVBoxLayout(self.sidebar)
        sroot.setContentsMargins(12, 14, 12, 14)
        sroot.setSpacing(6)

        self._nav_container = QWidget()
        nvbox = QVBoxLayout(self._nav_container)
        nvbox.setContentsMargins(0, 0, 0, 0)
        nvbox.setSpacing(4)
        for icon_name, text, key in self._NAV:
            item = _NavItem(icon_name, text, self.theme)
            item.clicked.connect(lambda k=key: self._set_page(k))
            nvbox.addWidget(item)
            self._nav_items.append((key, item))
        nvbox.addStretch(1)
        sroot.addWidget(self._nav_container, 1)

        body.addWidget(self.sidebar)

        # 1px 分隔线：左侧亚克力侧栏 与 右侧内容区表面 之间，增强层次
        divider = QFrame()
        divider.setObjectName("Divider")
        divider.setFixedWidth(1)
        body.addWidget(divider)

        # 右侧内容区（带平滑过渡的栈式容器）
        self.content = AnimatedStackedWidget()
        self.content.setObjectName("ContentArea")
        body.addWidget(self.content, 1)

        root.addLayout(body)

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
            # 真实交叉淡入淡出 + 轻微上浮，旧页淡出、新页淡入，无文字残留
            self.content.slide_to(self._pages[key], 230, rise=10)
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
        # 内容区表面较侧栏略实，形成 Win11 设置式层次（半透明以透出亚克力）
        self.content.setStyleSheet("background:%s;border:none;" % t.content_surface)
        recolor(self._close_ico, t.text)
        # 导航项刷新主题
        for _, item in self._nav_items:
            item.update_theme(t)
        # 子页中的自定义控件（搜索框、开关、主页图标）跟随主题
        self.search_page.set_theme(t)
        self.settings_page.set_theme(t)
        self.home_page.set_theme(t)
        self._update_backdrop()
        self.update()

    def apply_config(self, config):
        """外部配置变更后刷新窗口主题与内部页。"""
        self.config = config
        self.theme = Theme(config.get("theme", "light"))
        self._acrylic_on = bool(config.get("acrylic", True)) and _has_dwm
        self._shadow_color = _parse_color(self.theme.shadow_color)
        self._apply_theme()
        self.settings_page.config = config
        self.settings_page.refresh_from_config()
        self.settings_page.set_theme(self.theme)
        self.search_page.set_theme(self.theme)
        self.home_page.set_theme(self.theme)
        self._update_backdrop()

    def _on_settings_applied(self, new_cfg):
        self.config_applied.emit(new_cfg)

    def showEvent(self, e):
        super().showEvent(e)
        self._update_mask()
        self._update_backdrop()
        self.home_page.refresh_stats()

    def closeEvent(self, e):
        self.finished.emit()
        super().closeEvent(e)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._update_mask()

    def _update_backdrop(self):
        hwnd = int(self.winId())
        if self._acrylic_on:
            apply_backdrop(hwnd, DWMSBT_TRANSIENTWINDOW)
        else:
            apply_backdrop(hwnd, DWMSBT_NONE)
        apply_dark_mode(hwnd, self.theme.dark)

    def _close_btn_rect_window(self):
        """关闭按钮在窗口坐标系中的矩形（用于 WM_NCHITTEST 排除点击）。"""
        top_left = self._close_btn.mapTo(self, QPoint(0, 0))
        return QRect(top_left, self._close_btn.size())

    def nativeEvent(self, eventType, message):
        """通过 WM_NCHITTEST 实现：标题栏原生拖拽、窗口八向缩放。"""
        if eventType != "windows_generic_MSG":
            return super().nativeEvent(eventType, message)
        msg = MSG.from_address(int(message))
        if msg.message != WM_NCHITTEST:
            return super().nativeEvent(eventType, message)

        x = ctypes.c_short(msg.lParam & 0xFFFF).value
        y = ctypes.c_short((msg.lParam >> 16) & 0xFFFF).value
        g = self.frameGeometry()
        lx = x - g.x()
        ly = y - g.y()
        w, h = self.width(), self.height()
        P = self._PAD
        R = P  # 缩放热区覆盖整圈留白
        TH = self._TITLEBAR_H

        if lx <= R and ly <= R:
            return True, HTTOPLEFT
        if lx >= w - R and ly <= R:
            return True, HTTOPRIGHT
        if lx <= R and ly >= h - R:
            return True, HTBOTTOMLEFT
        if lx >= w - R and ly >= h - R:
            return True, HTBOTTOMRIGHT
        if lx <= R:
            return True, HTLEFT
        if lx >= w - R:
            return True, HTRIGHT
        if ly >= h - R:
            return True, HTBOTTOM
        if ly <= R:
            return True, HTTOP
        # 标题栏区域：拖动移动（关闭按钮除外，保证可点击）
        if P <= ly <= P + TH:
            if self._close_btn_rect_window().contains(lx, ly):
                return True, HTCLIENT
            return True, HTCAPTION
        return True, HTCLIENT

    def paintEvent(self, e):
        """自绘：亚克力基底（由 DWM 提供模糊）+ 细微竖向渐变 + 1px 圆角边框 + 柔和外阴影。"""
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        P = self._PAD
        r = self._RADIUS
        panel = QRect(P, P, self.width() - 2 * P, self.height() - 2 * P)
        t = self.theme

        # 1) 柔和外阴影（多层圆角矩形，落在窗口矩形内侧留白内）
        base = self._shadow_color.alpha()
        for d in range(P, 0, -1):
            a = max(1, int(base * (1 - (d - 1) / P) * 0.35))
            col = QColor(self._shadow_color.red(), self._shadow_color.green(),
                         self._shadow_color.blue(), a)
            rr = r + d * 0.5
            pp = QPainterPath()
            pp.addRoundedRect(QRectF(panel.adjusted(-d, -d, d, d)), rr, rr)
            p.fillPath(pp, col)

        # 2) 面板基底：半透明 tint（亚克力关闭时退化为不透明纯色）
        pp = QPainterPath()
        pp.addRoundedRect(QRectF(panel), r, r)
        tint = _parse_color(t.window_tint if self._acrylic_on else t.bg)
        p.fillPath(pp, tint)

        # 3) 细微竖向渐变叠加（顶部略亮、底部略暗）
        grad = QLinearGradient(0, panel.top(), 0, panel.bottom())
        grad.setColorAt(0, _parse_color(t.window_grad_top))
        grad.setColorAt(1, _parse_color(t.window_grad_bottom))
        p.fillPath(pp, grad)

        # 4) 1px 圆角边框
        pen = QPen(_parse_color(t.window_border))
        pen.setWidth(1)
        p.strokePath(pp, pen)

        super().paintEvent(e)
