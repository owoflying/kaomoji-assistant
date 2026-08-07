"""统一主窗口：左侧导航 + 右侧内容区，WinUI 3 Settings 风格。

整合 主页 / 颜文字库 / 我的颜文字 / 快捷短语 / 搜索 / 设置 / 关于，
替代原先独立的 settings/custom/trigger/search 对话框。

视觉上采用 WinUI 3 风格：
  * 无边框窗口（FramelessWindowHint）+ 圆角边框，调用 DWM 系统亚克力（acrylic）
    材质，并叠加一层细微的竖向渐变；
  * 自定义标题栏（应用名 + 关闭按钮），通过 WM_NCHITTEST 实现原生拖拽与八向缩放；
  * 导航与快捷入口全部使用内置的 Fluent System Icons 官方图标字体（随包分发，
    Win10 也能正常显示，不再依赖系统自带的 Segoe Fluent Icons）。
  * 最大化时自动隐藏圆角、阴影与留白，让面板贴合屏幕工作区（不含任务栏）。
"""
import ctypes
from ctypes.wintypes import MSG, POINT, RECT, DWORD

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, QLabel, QStackedWidget,
    QSizePolicy, QFrame, QPushButton,
)
from PySide6.QtCore import Qt, Signal, QPoint, QRect, QRectF, QEvent, QPropertyAnimation, QEasingCurve, QAbstractAnimation, QTimer
from PySide6.QtGui import (
    QFont, QColor, QPainter, QPainterPath, QPen, QLinearGradient,
    QRegion, QTransform,
)


# ---- 最大化贴合工作区所需的 Windows 结构（仅 Windows 平台用到） ----
class _MINMAXINFO(ctypes.Structure):
    _fields_ = [
        ("ptReserved", POINT),
        ("ptMaxSize", POINT),
        ("ptMaxPosition", POINT),
        ("ptMinTrackSize", POINT),
        ("ptMaxTrackSize", POINT),
    ]


class _MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", DWORD),
        ("rcMonitor", RECT),
        ("rcWork", RECT),
        ("dwFlags", DWORD),
    ]


def _ui_font(size, weight=QFont.Weight.Normal):
    """标题 / 导航文字字体：优先微软雅黑（Win10 自带、中文清晰），
    Segoe UI 作英文回退，整体比 Segoe UI Variable 更稳、更干净。"""
    f = QFont()
    f.setFamilies(["Microsoft YaHei UI", "Segoe UI", "Segoe UI Variable"])
    f.setPointSize(size)
    f.setWeight(weight)
    return f

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
from ui.dev import DevPage  # 开发者模式标签页（仅 developer_mode 时显示）

# WM 消息与 NCHITTEST 命中测试返回值
WM_NCHITTEST = 0x0084
WM_GETMINMAXINFO = 0x0024
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
        self.indicator.setFixedWidth(0)
        self.indicator.setFixedHeight(18)
        self.indicator.setStyleSheet("background:transparent;border-radius:2px;")

        self.ico = icon_label(icon_name, 16, theme.text_secondary)
        self.ico.setFixedWidth(26)

        self.txt = QLabel(text)
        self.txt.setFont(_ui_font(13))
        root.addWidget(self.indicator)
        root.addWidget(self.ico)
        root.addWidget(self.txt, 1)
        root.addStretch(0)
        self._apply_style()
        self._anim = None

    def _start_anim(self, target_width):
        # 停止并断开旧动画（若存在）。注意：旧动画一旦停止，无论是否仍在运行，
        # 都不能再持有底层 C++ 对象引用 —— 这里显式置 None 释放，下一行新建的
        # 动画才是 self._anim，避免对「已停止/已删除」对象调用 stop()。
        old = self._anim
        if old is not None:
            try:
                old.stop()
                old.valueChanged.disconnect()
            except Exception:
                pass
            self._anim = None

        anim = QPropertyAnimation(self.indicator, b"minimumWidth", self)
        anim.setDuration(180)
        anim.setEasingCurve(QEasingCurve.OutCubic)
        anim.setStartValue(self.indicator.width())
        anim.setEndValue(target_width)
        anim.valueChanged.connect(lambda v: self.indicator.setMaximumWidth(int(v)))

        def _finished():
            try:
                anim.valueChanged.disconnect()
            except Exception:
                pass
            if self._anim is anim:
                self._anim = None

        anim.finished.connect(_finished)
        self._anim = anim
        # 关键：用默认的 KeepWhenStopped，不要 DeleteWhenStopped。
        # 否则动画结束后底层 C++ 对象被自动删除，但 self._anim 仍指向它，
        # 下次切换页时对这个已删除对象调 stop() 会抛
        # "Internal C++ object already deleted"，导致打开非主页页面必崩。
        anim.start()

    def set_selected(self, selected):
        self._selected = selected
        self._apply_style()
        self._start_anim(3 if selected else 0)

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

    def __init__(self, data, config, state, user_kao, triggers,
                 save_config=None, open_log=None, dev_refs=None, parent=None):
        super().__init__(parent)
        self.data = data
        self.config = config
        self.state = state
        self.user_kao = user_kao
        self.triggers = triggers
        self.save_config = save_config
        self.open_log = open_log
        self.dev_refs = dev_refs or {}
        # 开发者模式权威状态：用独立标志位，不依赖 self.config["developer_mode"]，
        # 避免与设置页自动保存时的字典替换/刷新相互干扰。
        self._developer_mode = bool(config.get("developer_mode", False))

        self.theme = Theme(config.get("theme", "light"))
        self._nav_items = []
        self._indicator = None
        self._pending_index = 0
        self._acrylic_on = bool(config.get("acrylic", True)) and _has_dwm
        self._shadow_color = _parse_color(self.theme.shadow_color)
        self._theme_ss = ""          # 缓存当前主题的完整 QSS，供 _refresh_content_sheet 复用

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
        """把内容区裁剪到圆角矩形内，使子控件四角随窗口一起圆角化。

        最大化时取消遮罩（全矩形，方角），让面板贴合屏幕工作区。
        """
        if getattr(self, "central", None) is None:
            return
        w, h = self.width(), self.height()
        if w <= 0 or h <= 0:
            return
        if self.isMaximized():
            self.central.clearMask()
            return
        P = self._PAD
        r = self._RADIUS
        rect = QRect(P, P, w - 2 * P, h - 2 * P)
        pp = QPainterPath()
        pp.addRoundedRect(QRectF(rect), r, r)
        poly = pp.toFillPolygon(QTransform())
        self.central.setMask(QRegion(poly.toPolygon()))

    def _apply_window_chrome_state(self):
        """根据是否最大化调整留白：最大化时留白归零、面板贴合屏幕工作区。"""
        pad = 0 if self.isMaximized() else self._PAD
        if getattr(self, "_root_layout", None) is not None:
            self._root_layout.setContentsMargins(pad, pad, pad, pad)

    def _init_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        self.central = central
        root = QVBoxLayout(central)
        self._root_layout = root
        root.setContentsMargins(self._PAD, self._PAD, self._PAD, self._PAD)
        root.setSpacing(0)

        # ---------- 自定义标题栏 ----------
        self.titlebar = QWidget()
        self.titlebar.setFixedHeight(self._TITLEBAR_H)
        troot = QHBoxLayout(self.titlebar)
        troot.setContentsMargins(8, 0, 8, 0)
        troot.setSpacing(8)

        troot.addStretch(1)

        # 最小化、最大化/恢复、关闭按钮
        self._min_btn = QPushButton("\u2212")          # −
        self._min_btn.setObjectName("TitleButton")
        self._min_btn.setFixedSize(40, 30)
        self._min_btn.setCursor(Qt.PointingHandCursor)
        self._min_btn.clicked.connect(self.showMinimized)

        self._max_btn = QPushButton("\u25a1")          # □
        self._max_btn.setObjectName("TitleButton")
        self._max_btn.setFixedSize(40, 30)
        self._max_btn.setCursor(Qt.PointingHandCursor)
        self._max_btn.clicked.connect(self._toggle_maximize)

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
        troot.addWidget(self._min_btn)
        troot.addWidget(self._max_btn)
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
        # 开发者模式专属导航项：仅当已解锁才出现（插入到末尾的 stretch 之前）
        if self.config.get("developer_mode", False):
            self._add_dev_nav_item(nvbox)
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
        self.trigger_page = TriggerPage(self.triggers, self.theme)
        self.search_page = SearchPage(self.data, self.user_kao, self.config.get("theme", "light"))
        self.settings_page = SettingsPage(self.config)
        self.about_page = AboutPage(self.config, self.save_config, self.open_log)

        self._pages = {
            "home": self.home_page,
            "library": self.library_page,
            "custom": self.custom_page,
            "triggers": self.trigger_page,
            "search": self.search_page,
            "settings": self.settings_page,
            "about": self.about_page,
        }
        # 开发者模式标签页：仅当已解锁时创建并加入栈
        if self.config.get("developer_mode", False):
            self.dev_page = DevPage(
                self.dev_refs, self.config, self.save_config, self.theme.name)
            self.dev_page.developer_mode_disabled.connect(self._disable_dev_tab)
            self._pages["developer"] = self.dev_page
        for p in self._pages.values():
            self.content.addWidget(p)

        # 信号
        self.home_page.nav_request.connect(self._set_page)
        self.library_page.selected.connect(self.output_selected.emit)
        self.search_page.selected.connect(self.output_selected.emit)
        self.settings_page.config_applied.connect(self._on_settings_applied)
        # 关于页连点版本号解锁开发者模式后，实时添加「开发者」标签（无需重开窗口）
        self.about_page.developer_mode_enabled.connect(self._enable_dev_tab)

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
        # 进入/离开时管理开发者页后台计时器：离开即停止（含退出动画期间），
        # 避免淡出动画过程中后台仍刷新文本导致「文字跳动/闪烁」；进入时启动。
        leaving = self.content.currentWidget()
        entering = self._pages.get(key)
        if leaving is not entering:
            if isinstance(leaving, DevPage):
                leaving.set_active(False)
            if isinstance(entering, DevPage):
                entering.set_active(True)
        if animate:
            # 真实交叉淡入淡出 + 轻微上浮，旧页淡出、新页淡入，无文字残留
            self.content.slide_to(self._pages[key], 230, rise=10)
        else:
            self.content.setCurrentWidget(self._pages[key])
        if key == "home":
            self.home_page.refresh_stats()
        if key == "search":
            self.search_page.focus_query()

    # ---------- 开发者标签 ----------
    def _add_dev_nav_item(self, nav_layout):
        """在导航栏末尾（stretch 之前）插入「开发者」项。"""
        item = _NavItem("code", "开发者", self.theme)
        item.clicked.connect(lambda: self._set_page("developer"))
        # nav_layout 末尾是 addStretch(1)，插入到它之前，保持「开发者」在列表底部
        nav_layout.insertWidget(nav_layout.count() - 1, item)
        self._nav_items.append(("developer", item))

    def _enable_dev_tab(self):
        """关于页解锁开发者模式后实时添加「开发者」标签（无需重开窗口）。

        分两步：先把导航项同步加入侧边栏并立即刷新，给用户即时反馈
        （避免“首次进入侧边栏不立即出现标签页”）；重型 DevPage 构建放到下一事件循环，
        既避免第 8 次点击瞬间卡顿，又让侧边栏先完成布局、标签页随后淡入。
        """
        if "developer" in self._pages or getattr(self, "_dev_enabling", False):
            return
        if self._developer_mode:
            return
        self._dev_enabling = True
        # 1) 同步加入导航项并高亮，侧边栏立即出现「开发者」入口
        if not any(k == "developer" for k, _ in self._nav_items):
            self._add_dev_nav_item(self._nav_container.layout())
            self.sidebar.update()
        # 2) 重型构建延后，避免点击瞬间主线程阻塞
        QTimer.singleShot(0, self._finish_enable_dev_tab)

    def _finish_enable_dev_tab(self):
        self._dev_enabling = False
        if "developer" in self._pages:
            return
        self.dev_page = DevPage(
            self.dev_refs, self.config, self.save_config, self.theme.name)
        self.dev_page.developer_mode_disabled.connect(self._disable_dev_tab)
        self._pages["developer"] = self.dev_page
        self.content.addWidget(self.dev_page)
        self.dev_page.set_theme(self.theme)
        self._developer_mode = True
        self.config["developer_mode"] = True
        # 若当前停在关于页，自动跳到开发者页，让用户立刻看到入口
        self._set_page("developer")

    def _disable_dev_tab(self):
        """关闭开发者模式：置 config 标志为 false 并保存，实时移除标签页与导航项，跳回设置页。"""
        if not self._developer_mode:
            return
        self._developer_mode = False
        # 先暂停开发者页后台计时器，避免移除过程中仍触发刷新
        dev = self._pages.get("developer")
        if dev is not None:
            dev.set_active(False)
        self.config["developer_mode"] = False
        # 同步清空关于页的开发者模式状态，否则其仍记着 developer_mode=True，
        # 再次连点版本号会被短路、不再发出 developer_mode_enabled，导致无法二次进入。
        self.about_page.reset_developer_mode()
        # 移除导航项（保留其他项顺序）
        nav_layout = self._nav_container.layout()
        for i, (k, item) in enumerate(self._nav_items):
            if k == "developer":
                self._nav_items.pop(i)
                nav_layout.removeWidget(item)
                item.setParent(None)
                item.deleteLater()
                break
        nav_layout.update()
        self.sidebar.update()
        # 从栈与页面表中移除开发者页
        if "developer" in self._pages:
            self.content.removeWidget(self._pages["developer"])
            del self._pages["developer"]
        # 跳回设置页（保留与进入一致的淡入动画体验）
        self._set_page("settings")
        # 保存放到 UI 更新之后：即使落盘异常，也不能让页签继续显示在界面上。
        if self.save_config:
            try:
                self.save_config(self.config)
            except Exception:
                pass

    def _apply_theme(self):
        t = self.theme
        self.setStyleSheet(t.style_sheet())
        self._theme_ss = t.style_sheet()
        self.sidebar.setStyleSheet("background:transparent;")
        # 内容区表面较侧栏略实，形成 Win11 设置式层次（半透明以透出亚克力）。
        # 关键：content 必须应用「完整」主题 QSS，否则它的 setStyleSheet 会切断主窗口全局
        # QSS 的级联，导致其内部子页（设置/库/我的颜文字…）的 QPushButton#AccentButton、滑块、
        # 开关等收不到样式（典型表现：浅色模式下“应用并保存”白底白字不可见）。
        self._refresh_content_sheet()
        recolor(self._close_ico, t.text)
        # 标题栏按钮颜色刷新
        for btn in (self._min_btn, self._max_btn):
            btn.setStyleSheet("color:%s;" % t.text_secondary)
        # 导航项刷新主题
        for _, item in self._nav_items:
            item.update_theme(t)
        # 子页中的自定义控件（搜索框、开关、主页图标）跟随主题
        self.search_page.set_theme(t)
        self.settings_page.set_theme(t)
        self.home_page.set_theme(t)
        self.trigger_page.set_theme(t)
        if "developer" in self._pages:
            self.dev_page.set_theme(t)
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
        # 让关于页也始终指向统一窗口的权威配置字典：设置页每次 _on_apply 会替换自己的
        # self.config 为新副本，这里重新指向同一份唯一权威字典，避免 developer_mode
        # 在页间读不一致（二次解锁失效）。
        self.about_page.config = config
        self.settings_page.refresh_from_config()
        self.settings_page.set_theme(self.theme)
        self.search_page.set_theme(self.theme)
        self.home_page.set_theme(self.theme)
        self.trigger_page.set_theme(self.theme)
        if "developer" in self._pages:
            self.dev_page.set_theme(self.theme)
        self._update_backdrop()

    def _on_settings_applied(self, new_cfg):
        self.config_applied.emit(new_cfg)

    def _adjusted_content_surface(self):
        """根据 panel_alpha 调整内容区表面透明度。"""
        base = self.theme.content_surface
        alpha = float(self.config.get("panel_alpha", 0.92))
        import re
        m = re.match(r"rgba?\((\d+),\s*(\d+),\s*(\d+),\s*([\d.]+)\)", base)
        if m:
            r, g, b, a = int(m.group(1)), int(m.group(2)), int(m.group(3)), float(m.group(4))
            return "rgba(%d,%d,%d,%.2f)" % (r, g, b, max(0.0, min(1.0, a * alpha)))
        return base

    def _refresh_content_sheet(self):
        """刷新内容区（self.content）样式表：在完整主题 QSS 基础上追加其内容区表面背景。

        关键点：self.content 自身 setStyleSheet 会切断主窗口全局 QSS 的级联（Qt 行为——
        带自身样式表的控件，其派生控件不再继承更上层祖先的样式表规则），因此这里必须把完整
        style_sheet() 一并设给它，其内部所有子页（设置/库/我的颜文字…）的 QPushButton#AccentButton、
        滑块、开关等规则才能生效；否则仅设 background 会让派生控件“降级”为原生样式
        （浅色模式下 AccentButton 白底白字、与卡片融为一体不可见，正是此因）。
        """
        if not getattr(self, "_theme_ss", ""):
            return
        surface = self._adjusted_content_surface()
        self.content.setStyleSheet(
            self._theme_ss
            + "\nQStackedWidget#ContentArea { background: %s; border: none; }" % surface
        )

    def _adjusted_panel_base(self):
        """面板基底色，按 panel_alpha（语义=不透明度，值越大越实）缩放。

        - 亚克力开启：基底 RGB 取 window_tint，但不透明度直接由 panel_alpha 决定
          （映射并保底 0.6），让「不透明度」滑块在开亚克力时也真实生效；拉到 100% 即接近
          实色磨砂玻璃，不再“永远半透明、甚至比关亚克力还透”。
          原 bug：window_tint 固有 alpha 仅 0.85/0.88，乘 panel_alpha 后最多 0.85，
          导致即使不透明度拉满面板仍透明。
        - 亚克力关闭：纯色底 bg 直接按 panel_alpha 缩放（保底 0.4），纯色半透明。
        """
        import re
        base = self.theme.window_tint if self._acrylic_on else self.theme.bg
        alpha = float(self.config.get("panel_alpha", 0.92))
        m = re.match(r"rgba?\((\d+),\s*(\d+),\s*(\d+),\s*([\d.]+)\)", base)
        if m:
            r, g, b = int(m.group(1)), int(m.group(2)), int(m.group(3))
        else:
            c = QColor(base)
            r, g, b = c.red(), c.green(), c.blue()
        if self._acrylic_on:
            # 不透明度滑块映射（保底 0.6 让磨砂层始终可见；100% 时完全实色）
            a = max(0.6, min(1.0, 0.55 + 0.45 * alpha))
        else:
            a = max(0.4, min(1.0, alpha))
        return "rgba(%d,%d,%d,%.2f)" % (r, g, b, a)

    def showEvent(self, e):
        super().showEvent(e)
        self._apply_window_chrome_state()
        self._update_mask()
        self._update_backdrop()
        self._update_max_btn_glyph()
        self.home_page.refresh_stats()

    def closeEvent(self, e):
        self.finished.emit()
        super().closeEvent(e)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._apply_window_chrome_state()
        self._update_mask()

    def _update_backdrop(self):
        hwnd = int(self.winId())
        if self._acrylic_on:
            apply_backdrop(hwnd, DWMSBT_TRANSIENTWINDOW)
        else:
            apply_backdrop(hwnd, DWMSBT_NONE)
        apply_dark_mode(hwnd, self.theme.dark)

    def _toggle_maximize(self):
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()

    def _update_max_btn_glyph(self):
        self._max_btn.setText("\u25a1" if not self.isMaximized() else "\u25d9")

    def changeEvent(self, e):
        super().changeEvent(e)
        if e.type() == QEvent.WindowStateChange:
            self._update_max_btn_glyph()
            self._apply_window_chrome_state()
            self._update_mask()
            self.update()

    def _title_btn_rects_window(self):
        """三个标题栏按钮在窗口坐标系中的矩形（用于 WM_NCHITTEST 排除点击）。"""
        rects = []
        for btn in (self._min_btn, self._max_btn, self._close_btn):
            top_left = btn.mapTo(self, QPoint(0, 0))
            rects.append(QRect(top_left, btn.size()))
        return rects

    def nativeEvent(self, eventType, message):
        """处理原生消息：
        * WM_GETMINMAXINFO：让最大化贴合显示器工作区（不含任务栏），
          避免无边框窗口最大化时盖住任务栏 / 边缘留白；
        * WM_NCHITTEST：标题栏原生拖拽、窗口八向缩放（最大化时禁用缩放热区）。
        """
        if eventType != "windows_generic_MSG":
            return super().nativeEvent(eventType, message)
        msg = MSG.from_address(int(message))

        # 1) 最大化贴合工作区
        if msg.message == WM_GETMINMAXINFO:
            try:
                hwnd = int(self.winId())
                monitor = ctypes.windll.user32.MonitorFromWindow(hwnd, 2)  # MONITOR_DEFAULTTONEAREST
                mi = _MONITORINFO()
                mi.cbSize = ctypes.sizeof(_MONITORINFO)
                if ctypes.windll.user32.GetMonitorInfoW(monitor, ctypes.byref(mi)):
                    wa = mi.rcWork
                    mmi = _MINMAXINFO.from_address(int(msg.lParam))
                    mmi.ptMaxPosition.x = wa.left
                    mmi.ptMaxPosition.y = wa.top
                    mmi.ptMaxSize.x = wa.right - wa.left
                    mmi.ptMaxSize.y = wa.bottom - wa.top
            except Exception:
                pass
            return True, 0

        if msg.message != WM_NCHITTEST:
            return super().nativeEvent(eventType, message)

        x = ctypes.c_short(msg.lParam & 0xFFFF).value
        y = ctypes.c_short((msg.lParam >> 16) & 0xFFFF).value
        g = self.frameGeometry()
        lx = x - g.x()
        ly = y - g.y()
        w, h = self.width(), self.height()
        maximized = self.isMaximized()
        P = 0 if maximized else self._PAD
        TH = self._TITLEBAR_H

        # 2) 缩放热区（仅非最大化时有留白时才生效）
        if not maximized and P > 0:
            R = P  # 缩放热区覆盖整圈留白
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
        # 3) 标题栏区域：拖动移动（标题按钮除外，保证可点击）
        if P <= ly <= P + TH:
            for rect in self._title_btn_rects_window():
                if rect.contains(lx, ly):
                    return True, HTCLIENT
            return True, HTCAPTION
        return True, HTCLIENT

    def paintEvent(self, e):
        """自绘：亚克力基底（由 DWM 提供模糊）+ 细微竖向渐变 + 1px 圆角边框 + 柔和外阴影。

        最大化时取消阴影、圆角与边框，让面板贴合屏幕工作区。
        """
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        maximized = self.isMaximized()
        P = 0 if maximized else self._PAD
        r = 0 if maximized else self._RADIUS
        t = self.theme

        if maximized:
            panel = QRect(0, 0, self.width(), self.height())
            pp = QPainterPath()
            pp.addRect(QRectF(panel))
        else:
            panel = QRect(P, P, self.width() - 2 * P, self.height() - 2 * P)
            pp = QPainterPath()
            pp.addRoundedRect(QRectF(panel), r, r)

            # 1) 柔和外阴影（多层圆角矩形，落在窗口矩形内侧留白内）
            base = self._shadow_color.alpha()
            for d in range(P, 0, -1):
                a = max(1, int(base * (1 - (d - 1) / P) * 0.35))
                col = QColor(self._shadow_color.red(), self._shadow_color.green(),
                             self._shadow_color.blue(), a)
                rr = r + d * 0.5
                sp = QPainterPath()
                sp.addRoundedRect(QRectF(panel.adjusted(-d, -d, d, d)), rr, rr)
                p.fillPath(sp, col)

        # 2) 面板基底：半透明 tint（亚克力关闭时退化为纯色底）。
        #    按 panel_alpha 缩放透明度，让「面板透明度」滑块真实作用于整块面板背景
        #    （此前只缩放了被卡片遮挡的内容区表面，几乎看不出变化，表现为“透明度设置失效”）。
        tint = _parse_color(self._adjusted_panel_base())
        p.fillPath(pp, tint)

        # 3) 细微竖向渐变叠加（顶部略亮、底部略暗）
        grad = QLinearGradient(0, panel.top(), 0, panel.bottom())
        grad.setColorAt(0, _parse_color(t.window_grad_top))
        grad.setColorAt(1, _parse_color(t.window_grad_bottom))
        p.fillPath(pp, grad)

        # 4) 1px 圆角边框（最大化时不画，避免边缘被裁切出细线）
        if not maximized:
            pen = QPen(_parse_color(t.window_border))
            pen.setWidth(1)
            p.strokePath(pp, pen)

        super().paintEvent(e)
