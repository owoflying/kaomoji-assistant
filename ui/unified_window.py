"""统一主窗口：左侧导航 + 右侧内容区，WinUI 3 Settings 风格。

整合 主页 / 颜文字库 / 我的颜文字 / 快捷短语 / 搜索 / 设置 / 关于，
替代原先独立的 settings/custom/trigger/search 对话框。

**本模块只做组装**，三块职责已拆到独立模块（原先 1172 行混在一起）：
  * 无边框窗口机械逻辑（圆角遮罩/八向缩放/最大化贴工作区/状态保持）
        → `ui/frameless_shell.py::FramelessShell`
  * 侧边栏导航、页面栈调度、开发者页与插件页的挂载销毁
        → `ui/unified_nav.py::NavController`
  * 七个业务页本身 → `ui/pages/*`

这里剩下的就是：搭骨架（标题栏 + 侧栏 + 内容区）、建七个页、
把主题/配置变更广播出去。

视觉上采用 WinUI 3 风格：
  * 无边框窗口（FramelessWindowHint）+ 圆角边框，调用 DWM 系统亚克力（acrylic）
    材质，并叠加一层细微的竖向渐变；
  * 自定义标题栏（应用名 + 关闭按钮），通过 WM_NCHITTEST 实现原生拖拽与八向缩放；
  * 导航与快捷入口全部使用内置的 Fluent System Icons 官方图标字体（随包分发，
    Win10 也能正常显示，不再依赖系统自带的 Segoe Fluent Icons）。
  * 最大化时自动隐藏圆角、阴影与留白，让面板贴合屏幕工作区（不含任务栏）。
"""
import re

from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QLabel,
    QFrame, QPushButton,
)
from PySide6.QtCore import Qt, Signal, QRect, QPoint, QEvent
from PySide6.QtGui import QColor, QFont

from ui.frameless_shell import FramelessShell
from ui.win_style import _has_dwm
from core.applog import append_log
from ui.win11_theme import (
    Theme, apply_child_theme, parse_color, resolve_theme_name,
)
from ui.fluent_icons import icon_label, recolor
from ui.animated_stack import AnimatedStackedWidget
from ui.pages import (
    HomePage, LibraryPage, CustomKaomojiPage, TriggerPage,
    SearchPage, SettingsPage, AboutPage,
)
from ui.unified_nav import NavController


def _ui_font(size, weight=QFont.Weight.Normal):
    """标题 / 导航文字字体：优先微软雅黑（Win10 自带、中文清晰），
    Segoe UI 作英文回退，整体比 Segoe UI Variable 更稳、更干净。"""
    f = QFont()
    f.setFamilies(["Microsoft YaHei UI", "Segoe UI", "Segoe UI Variable"])
    f.setPointSize(size)
    f.setWeight(weight)
    return f


# 主题里的颜色可能是 rgba() 字符串，也可能是 hex/颜色名
_RGBA_RE = re.compile(r"rgba?\((\d+),\s*(\d+),\s*(\d+)(?:,\s*([\d.]+))?\)")

# 侧栏恒为透明（透出窗口底色/亚克力），与主题无关；单独拎出来便于幂等比较——
# 每次 setStyleSheet 都会让该控件整棵子树重新 polish，值没变就别重设。
_SIDEBAR_SS = "background:transparent;"


def _rgba_parts(spec):
    """把颜色字符串拆成 (r, g, b, alpha:0~1)，hex/颜色名走 QColor 兜底。"""
    m = _RGBA_RE.match(spec or "")
    if m:
        a = float(m.group(4)) if m.group(4) is not None else 1.0
        return int(m.group(1)), int(m.group(2)), int(m.group(3)), a
    c = QColor(spec or "#000000")
    return c.red(), c.green(), c.blue(), c.alphaF()


def _rgba_str(r, g, b, a):
    return "rgba(%d,%d,%d,%.2f)" % (r, g, b, a)


class _NavItem(QWidget):
    """单个导航项：左侧高亮指示条 + 图标 + 文字 + 悬停/选中背景。"""

    clicked = Signal()

    def __init__(self, icon_name, text, theme, parent=None):
        super().__init__(parent)
        self._icon_name = icon_name
        self._text = text
        self._theme = theme
        self._selected = False
        self._anim = None
        self.setFixedHeight(40)
        self.setCursor(Qt.PointingHandCursor)
        self.setAttribute(Qt.WA_Hover, True)

        root = QHBoxLayout(self)
        root.setContentsMargins(8, 0, 10, 0)
        root.setSpacing(12)

        self.indicator = QWidget()
        self.indicator.setFixedSize(3, 16)
        self.ico = icon_label(icon_name, 16, theme.text_secondary)
        self.txt = QLabel(text)
        self.txt.setFont(_ui_font(10.5))
        self.txt.setStyleSheet("background:transparent;")
        root.addWidget(self.indicator)
        root.addWidget(self.ico)
        root.addWidget(self.txt, 1)
        self._apply_style()

    def update_theme(self, theme):
        self._theme = theme
        self.ico.deleteLater()
        layout = self.layout()
        layout.removeWidget(self.ico)
        self.ico = icon_label(self._icon_name, 16, theme.text_secondary)
        layout.insertWidget(1, self.ico)
        self._apply_style()

    def _apply_style(self):
        t = self._theme
        bg = t.nav_selected if self._selected else "transparent"
        self.indicator.setStyleSheet(
            "background:%s; border-radius:2px;" % (t.accent if self._selected else "transparent"))
        self.txt.setStyleSheet("color:%s; background:transparent;" % (
            t.text if self._selected else t.text_secondary))
        self.setStyleSheet(
            "_NavItem{background:%s;border-radius:6px;}" % bg)

    def set_selected(self, selected):
        if self._selected == selected:
            return
        self._selected = selected
        self._apply_style()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self.rect().contains(e.position().toPoint()):
            self.clicked.emit()
        super().mouseReleaseEvent(e)


class UnifiedSettingsWindow(FramelessShell):
    """统一设置/管理窗口（外壳 + 导航 + 页面组装的组合层）。"""

    output_selected = Signal(str)       # 搜索/库页选中颜文字 -> 注入
    config_applied = Signal(dict)       # 设置页应用配置
    finished = Signal()                 # 窗口关闭（类似 QDialog.finished）

    # 圆角 / 留白 / 标题栏尺寸（由 FramelessShell 使用）
    _RADIUS = 12
    _PAD = 12          # 窗口边缘到面板内容的距离（也是阴影与缩放热区宽度）
    _TITLEBAR_H = 40

    def __init__(self, data, config, state, user_kao, triggers,
                 save_config=None, open_log=None, dev_refs=None,
                 plugins=None, parent=None, ui_registry=None):
        super().__init__(parent)
        self.data = data
        self.config = config
        self.state = state
        self.user_kao = user_kao
        self.triggers = triggers
        self.save_config = save_config
        self.open_log = open_log
        self.dev_refs = dev_refs or {}
        self.plugins = plugins      # PluginManager（可为 None：插件功能整体禁用）
        # 界面元素登记表：插件靠它按 id 定位控件（见 core/ui_registry.py）
        self.ui_registry = ui_registry
        # 开发者模式权威状态：用独立标志位，不依赖 self.config["developer_mode"]，
        # 避免与设置页自动保存时的字典替换/刷新相互干扰。
        self._developer_mode = bool(config.get("developer_mode", False))

        self.theme = Theme(resolve_theme_name(config.get("theme", "light")))
        self._shadow_color = parse_color(self.theme.shadow_color)
        self._theme_ss = ""          # 缓存当前主题的完整 QSS，供 _refresh_content_sheet 复用
        self._content_surface = None  # 缓存内容区表面色，QSS 重贴的幂等判据之一

        self._init_window()
        self._init_ui()
        self._apply_theme()

    # ==================================================================
    # 骨架搭建
    # ==================================================================
    def _init_window(self):
        super()._init_window()
        self.setMinimumSize(900, 640)
        self.resize(1024, 720)
        self._update_mask()

    def _init_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        self.central = central
        root = QVBoxLayout(central)
        self._root_layout = root
        root.setContentsMargins(self._PAD, self._PAD, self._PAD, self._PAD)
        root.setSpacing(0)

        root.addWidget(self._build_titlebar())

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
        self._nav_layout_box = QVBoxLayout(self._nav_container)
        self._nav_layout_box.setContentsMargins(0, 0, 0, 0)
        self._nav_layout_box.setSpacing(4)

        # 导航控制器：内置导航由 NavController.NAV_ITEMS 驱动
        self._nav = NavController(self, self._make_nav_item)
        for icon_name, text, key in NavController.NAV_ITEMS:
            item = self._make_nav_item(icon_name, text)
            item.clicked.connect(lambda k=key: self._set_page(k))
            self._nav_layout_box.addWidget(item)
            self._nav.add_existing_item(key, item)
        # 开发者模式专属导航项：仅当已解锁才出现（插入到末尾的 stretch 之前）
        if self.config.get("developer_mode", False):
            self._nav.add_nav_item("developer", "开发者", "code")
        # 「插件」页：**正式功能**，不再受 use_test_features 门控
        if self.plugins is not None:
            self._nav.add_nav_item("plugin", "插件", "plugin")
        self._nav_layout_box.addStretch(1)
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

        self._build_pages()
        self._wire_page_signals()
        self._register_ui_elements()
        self._set_page("home", animate=False)

    def _register_ui_elements(self):
        """把所有内置界面的稳定 id 登记到 registry，供插件定位。

        路径遍历的根由 `set_roots_provider` 提供：这里返回各业务页，
        让 `home/GreetingCard[0]/QLabel[1]` 这类路径能解析。
        """
        reg = self.ui_registry
        if reg is None:
            return
        from core import ui_ids
        try:
            ui_ids.register_shell(reg, self)
            names = ui_ids.register_home(reg, self.home_page)
            reg.set_roots_provider(self._ui_roots)
            append_log("info", "ui", "已登记 %d 个界面元素 id（主页）" % names)
            # 界面就绪：重放插件在 setup() 期间排队的 api.ui 操作。
            # 装配顺序是「先加载插件、后建窗口」，插件 setup 时元素表还是空的，
            # 那些调用被排队到这里才真正生效。
            pending = len(getattr(reg, "_on_ready", []))
            reg.mark_ready()
            if pending:
                append_log("info", "ui", "界面就绪，已重放 %d 项插件界面修改" % pending)
        except Exception as e:
            append_log("warn", "ui", "登记界面元素失败：%r" % (e,))

    def _ui_roots(self):
        """路径遍历的根容器：各业务页（其 objectName 即路径首段）。"""
        out = []
        for key, page in self._nav.pages.items():
            if page is None:
                continue
            if not page.objectName():
                page.setObjectName(key)
            out.append(page)
        return out

    def _build_titlebar(self):
        """自定义标题栏：最小化 / 最大化 / 关闭三键。"""
        self.titlebar = QWidget()
        self.titlebar.setFixedHeight(self._TITLEBAR_H)
        troot = QHBoxLayout(self.titlebar)
        troot.setContentsMargins(8, 0, 8, 0)
        troot.setSpacing(8)
        troot.addStretch(1)

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
        return self.titlebar

    def _make_nav_item(self, icon_name, text):
        return _NavItem(icon_name, text, self.theme)

    def _title_btn_rects(self):
        """三个标题栏按钮在窗口坐标系中的矩形（用于 WM_NCHITTEST 排除点击）。"""
        return [QRect(btn.mapTo(self, QPoint(0, 0)), btn.size())
                for btn in (self._min_btn, self._max_btn, self._close_btn)]

    def _build_pages(self):
        """创建七个业务页 + 开发者页 + 插件页，并挂进内容栈。"""
        self.home_page = HomePage(self.config, self.state, self.user_kao, self.triggers, self.data)
        self.library_page = LibraryPage(self.data)
        self.custom_page = CustomKaomojiPage(self.user_kao)
        self.trigger_page = TriggerPage(self.triggers, self.theme)
        self.search_page = SearchPage(self.data, self.user_kao, self.theme.name)
        self.settings_page = SettingsPage(self.config)
        self.about_page = AboutPage(self.config, self.state, self.save_config, self.open_log)

        for key, page in (
            ("home", self.home_page), ("library", self.library_page),
            ("custom", self.custom_page), ("triggers", self.trigger_page),
            ("search", self.search_page), ("settings", self.settings_page),
            ("about", self.about_page),
        ):
            self._nav.register_page(key, page)

        # 开发者模式标签页：仅当已解锁时创建并加入栈
        if self.config.get("developer_mode", False):
            from ui.dev import DevPage
            self.dev_page = DevPage(
                self.dev_refs, self.config, self.save_config, self.theme.name)
            self.dev_page.developer_mode_disabled.connect(self._disable_dev_tab)
            self.dev_page.test_features_changed.connect(self._on_test_features_changed)
            self._nav.register_page("developer", self.dev_page)
        # 插件页：**正式功能**，只要有 PluginManager 就创建
        self.plugin_page = None
        if self.plugins is not None:
            from ui.pages import PluginPage
            self.plugin_page = PluginPage(self.plugins, self.theme.name)
            self._nav.register_page("plugin", self.plugin_page)
        # 插件集合变化（启用/停用/重载/重扫）时刷新库页分类与插件页列表
        if self.plugins is not None:
            self.plugins.changed.connect(self._on_plugins_changed)
            # UI 登记项变化（注册/注销设置面板、独立页）时同步界面
            self.plugins.ui_changed.connect(self._nav.sync_plugin_pages)
            self._nav.sync_plugin_pages()

    def _wire_page_signals(self):
        self.home_page.nav_request.connect(self._set_page)
        self.library_page.selected.connect(self.output_selected.emit)
        self.search_page.selected.connect(self.output_selected.emit)
        self.settings_page.config_applied.connect(self._on_settings_applied)
        # 关于页连点版本号解锁开发者模式后，实时添加「开发者」标签（无需重开窗口）
        self.about_page.developer_mode_enabled.connect(self._nav.enable_dev_tab)

    # ==================================================================
    # 页面调度（转发给 NavController，保留原方法名以免破坏调用方）
    # ==================================================================
    def _set_page(self, key, animate=True):
        self._nav.set_page(key, animate)

    def on_page_shown(self, key):
        """页面切到前台后的刷新钩子。"""
        if key == "home":
            self.home_page.refresh_stats()
        elif key == "search":
            self.search_page.focus_query()

    def _add_nav_item(self, key, title, icon, nav_layout=None):
        return self._nav.add_nav_item(key, title, icon, nav_layout)

    def _remove_nav_item(self, key, nav_layout=None):
        return self._nav.remove_nav_item(key, nav_layout)

    def _sync_plugin_pages(self):
        self._nav.sync_plugin_pages()

    def _sync_plugin_tab(self):
        self._nav.sync_plugin_tab()

    def _enable_dev_tab(self):
        self._nav.enable_dev_tab()

    def _disable_dev_tab(self):
        """关闭开发者模式：置 config 标志为 false 并保存，实时移除标签页，跳回设置页。"""
        if not self._developer_mode:
            return
        self._developer_mode = False
        self.config["developer_mode"] = False
        # 关闭开发者模式时连带关闭「测试模式」：测试功能总开关只存在于开发者页，
        # 关掉开发者模式应一并关掉测试模式，否则测试功能会逃逸到正式环境。
        # use_test_features=False 后，下方 save_config 会按 TEST_FEATURE_DEFAULTS
        # 把仍在测试中的功能项强制归位默认值。
        self.config["use_test_features"] = False
        # 同步清空关于页的开发者模式状态，否则它仍记着 developer_mode=True，
        # 再次连点版本号会被短路、不再发出 developer_mode_enabled，无法二次进入。
        self.about_page.reset_developer_mode()
        self._nav.disable_dev_tab()
        # ⚠ **不再卸载插件**：插件已是正式功能，与开发者模式无关。
        # 历史：插件曾在测试门控下，关闭开发者模式时必须 unload，才能让它通过
        # api.ui 做的界面修改回滚（否则"插件插的东西还留在界面上"）。
        # 现在插件不归测试模式管，这里自然也不需要动它。
        # 跳回设置页（保留与进入一致的淡入动画体验）
        self._set_page("settings")
        # 保存放到 UI 更新之后：即使落盘异常，也不能让页签继续显示在界面上。
        if self.save_config:
            try:
                self.save_config(self.config)
            except Exception:
                pass
        # 落盘后同步让设置页重新评估测试项可见性，与开发者页切换开关走同一刷新路径
        self._on_test_features_changed()

    def _unload_all_plugins(self):
        """卸载所有已加载插件（连带回滚它们做的界面修改）。

        关闭测试模式时必须调用：插件虽然被配置门控"禁用"了，但如果已经在内存里
        加载，它的回调与界面修改仍然生效。`unload()` 会走 `_detach`，
        而 `_detach` 里包含 `MutatorHub.rollback(plugin_id)`。
        """
        if self.plugins is None:
            return 0
        n = 0
        try:
            loaded = [pid for pid, rec in list(self.plugins.plugins.items())
                      if rec.get("state") == "loaded"]
        except Exception:
            return 0
        for pid in loaded:
            try:
                self.plugins.unload(pid)
                n += 1
            except Exception:
                pass
        if n:
            try:
                append_log("info", "plugins",
                           "测试模式关闭：已卸载 %d 个插件并回滚其界面修改" % n)
            except Exception:
                pass
        return n

    def _on_plugins_changed(self):
        """插件集合变化：库页分类与插件页列表需要重建。"""
        try:
            self.library_page.reload_categories()
        except Exception:
            pass
        if self.plugin_page is not None:
            self.plugin_page.refresh()

    # ==================================================================
    # 主题与配置
    # ==================================================================
    def _apply_theme(self):
        t = self.theme
        ss = t.style_sheet()
        surface = self._adjusted_content_surface()
        # QSS 重贴会让窗口内所有控件重新 polish：本窗实测 setStyleSheet ≈170ms，
        # 内容区再贴一次 ≈130ms。设置页任何一处改动都会走 apply_config → 这里，
        # 若不做幂等短路，改个滑块也要白卡 300ms。样式串与内容区表面色都没变时
        # 直接跳过这两步重贴，后面的控件级刷新（廉价）照常执行。
        if ss != self._theme_ss or surface != self._content_surface:
            self._theme_ss = ss
            self._content_surface = surface
            self.setStyleSheet(ss)
            # 内容区表面较侧栏略实，形成 Win11 设置式层次（半透明以透出亚克力）。
            # 关键：content 必须应用「完整」主题 QSS，否则它的 setStyleSheet 会切断
            # 主窗口全局 QSS 的级联，导致其内部子页的 QPushButton#AccentButton、
            # 滑块、开关等收不到样式（浅色模式下白底白字不可见）。
            self._refresh_content_sheet()
            # 子页的自定义控件同样是全量重贴 QSS（实测 ≈50ms），只有主题真变了才需要。
            self._apply_theme_to_pages(t)
        # 下面几步都是「把值写回小控件」，幂等——值没变就别重设样式表，
        # 否则每次 setStyleSheet 都会让该控件及其子控件重新 polish。
        if self.sidebar.styleSheet() != _SIDEBAR_SS:
            self.sidebar.setStyleSheet(_SIDEBAR_SS)
        recolor(self._close_ico, t.text)
        for btn in (self._min_btn, self._max_btn):
            want = "color:%s;" % t.text_secondary
            if btn.styleSheet() != want:
                btn.setStyleSheet(want)
        # 导航项刷新主题
        for _, item in self._nav.nav_items:
            item.update_theme(t)
        # 把「已解析」的主题名同步给插件管理器，供插件弹窗/消息框取深浅色
        if self.plugins is not None:
            try:
                self.plugins.set_theme_name(t.name)
            except Exception:
                pass
        self._update_backdrop()
        self.update()

    def apply_config(self, config):
        """外部配置变更后刷新窗口主题与内部页。"""
        self.config = config
        self.theme = Theme(resolve_theme_name(config.get("theme", "light")))
        self._acrylic_on = _has_dwm
        self._shadow_color = parse_color(self.theme.shadow_color)
        self._apply_theme()
        self.settings_page.config = config
        # 让关于页也始终指向统一窗口的权威配置字典：设置页每次 _on_apply 会替换自己的
        # self.config 为新副本，这里重新指向同一份唯一权威字典，避免 developer_mode
        # 在页间读不一致（二次解锁失效）。
        self.about_page.config = config
        self.settings_page.refresh_from_config()
        # 测试功能可见性可能随外部应用配置变化（如关闭测试模式），同步插件页显隐
        pages_before = set(self._nav.pages)
        self._sync_plugin_tab()
        if set(self._nav.pages) != pages_before:
            # 页面集合变了才需要补刷：新增/移除的页是 _apply_theme() 之后才挂进来的。
            # 常规 apply_config 里 _apply_theme() 已经刷过一遍，再来一次纯属浪费
            # （实测每次多花 ≈50ms，加在热键录制「完成」这种高频路径上很致命）。
            self._apply_theme_to_pages(self.theme)
            self._update_backdrop()

    def _apply_theme_to_pages(self, t):
        """把主题广播给所有暴露 set_theme 的子页，避免多处各写一遍。"""
        # 插件扩展页：先重垫「完整主题 QSS + 插件自定义」（幂等），保证原生控件
        # 的深浅色规则始终最新；随后页面的 set_theme(theme)（若有）再做额外定制。
        for w in list(self._nav.plugin_pages.values()):
            try:
                apply_child_theme(w, t)
            except Exception:
                pass
        for page in self._nav.pages.values():
            if hasattr(page, "set_theme"):
                page.set_theme(t)

    def _on_settings_applied(self, new_cfg):
        self.config_applied.emit(new_cfg)

    def _on_test_features_changed(self):
        """开发者页切换「测试功能」总开关后，让设置页重新评估测试项的可见性。

        use_test_features 已由 dev_page 写入共享 config 并存盘，这里仅把最新
        配置同步给设置页并刷新可见性，避免整页重排带来的额外开销。

        ⚠ **不再卸载插件**：插件已成为正式功能，关闭测试模式不应影响它。
        （历史：插件曾在测试门控下，关闭时必须 unload 才能回滚其界面修改。）
        """
        self.settings_page.config = self.config
        self.settings_page.apply_test_feature_visibility()
        self._sync_plugin_tab()

    # ---------- 面板基底 / 内容区表面（按 panel_alpha 缩放） ----------
    def _adjusted_content_surface(self):
        """根据 panel_alpha 调整内容区表面透明度。"""
        r, g, b, a = _rgba_parts(self.theme.content_surface)
        alpha = float(self.config.get("panel_alpha", 0.92))
        return _rgba_str(r, g, b, max(0.0, min(1.0, a * alpha)))

    def _refresh_content_sheet(self):
        """刷新内容区样式表：在完整主题 QSS 基础上追加其内容区表面背景。

        关键点：self.content 自身 setStyleSheet 会切断主窗口全局 QSS 的级联，
        因此必须把完整 style_sheet() 一并设给它；否则仅设 background 会让派生
        控件「降级」为原生样式（浅色模式下 AccentButton 白底白字、不可见）。
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
          （映射并保底 0.6），让「不透明度」滑块在开亚克力时也真实生效；拉到 100%
          即接近实色磨砂玻璃，不再"永远半透明、甚至比关亚克力还透"。
          原 bug：window_tint 固有 alpha 仅 0.85/0.88，乘 panel_alpha 后最多 0.85。
        - 亚克力关闭：纯色底 bg 直接按 panel_alpha 缩放（保底 0.4）。
        """
        base = self.theme.window_tint if self._acrylic_on else self.theme.bg
        alpha = float(self.config.get("panel_alpha", 0.92))
        r, g, b, _ = _rgba_parts(base)
        if self._acrylic_on:
            a = max(0.6, min(1.0, 0.55 + 0.45 * alpha))
        else:
            a = max(0.4, min(1.0, alpha))
        return _rgba_str(r, g, b, a)

    # ---------- 生命周期 ----------
    def showEvent(self, e):
        super().showEvent(e)
        self._update_mask()
        self._update_backdrop()
        self._update_max_btn_glyph()
        self.home_page.refresh_stats()

    def closeEvent(self, e):
        self.finished.emit()
        super().closeEvent(e)
        # 窗口状态的保存/清零统一放在 hideEvent：close() 与 hide()（选中颜文字后
        # 收起面板）两条路径都会经过它，不会漏。

    def _update_max_btn_glyph(self):
        self._max_btn.setText("\u25a1" if not self.isMaximized() else "\u25d9")

    def changeEvent(self, e):
        super().changeEvent(e)
        if e.type() == QEvent.WindowStateChange:
            self._update_max_btn_glyph()
