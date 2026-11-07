"""统一主窗口的导航与页面栈调度。

**拆出来的理由**：`UnifiedSettingsWindow` 原先 1172 行，其中侧边栏导航项增删、
开发者页/插件页的按需挂载与销毁、页面切换动画占了近 400 行。这些逻辑与
"窗口长什么样"无关，只关心"有哪些页、当前在哪一页"，适合独立成类。

宿主（`UnifiedSettingsWindow`）只需提供：
  * `self.theme` / `self.config` / `self.plugins` —— 主题与插件管理器；
  * `self.content` —— `AnimatedStackedWidget`（页面栈）；
  * `self._nav_container` —— 侧边栏导航项容器；
  * `self.on_page_shown(key)` —— 页面真正切到前台后的钩子（刷新统计等）。

**关键约束（都是踩过的坑）**：
  * 页面移除必须 `removeWidget` + `setParent(None)` + `deleteLater()`：
    `removeWidget` 只把 widget 移出栈，它仍挂在父容器上；反复开关「测试模式」
    会越积越多，每次插件变动都被多个僵尸页面重复刷新。
  * 插件页的 widget 一律由本类**创建与销毁**，manager 只登记 factory——
    core 层不持有 QWidget，避免插件卸载后回调打到已销毁对象。
  * 主题广播遍历 `self.pages.values()` 而不是硬编码页面名列表，新增页面自动覆盖。
"""
from PySide6.QtWidgets import QWidget

from ui.dev import DevPage
from ui.pages import PluginPage
from ui.win11_theme import apply_child_theme


class NavController:
    """管理侧边栏导航项与内容栈中的业务页。

    :param host: 提供 theme/config/plugins/content/_nav_container 的窗口对象
    :param make_nav_item: (icon_name, text) -> 导航项 widget，宿主传入以免循环依赖
    """

    #: 内置导航（key, 显示文字, 图标名）；顺序即侧边栏顺序。
    #: ⚠ key 必须与宿主 `register_page()` 时用的 key 完全一致——两者错开一格
    #: 就会表现为「侧边栏能点但页面不切」（拆分时踩过：这里曾写成 edit/flash，
    #: 而页面注册用的是 custom/triggers）。
    NAV_ITEMS = (
        ("home",     "主页",     "home"),
        ("library",  "颜文字库", "library"),
        ("custom",   "我的颜文字", "custom"),
        ("triggers", "快捷短语", "triggers"),
        ("search",   "搜索",     "search"),
        ("settings", "设置",     "settings"),
        ("about",    "关于",     "about"),
    )

    def __init__(self, host, make_nav_item):
        self.host = host
        self._make_nav_item = make_nav_item
        #: key -> 页面 widget（内置页 + 开发者页 + 插件页 + 插件扩展页）
        self.pages = {}
        #: [(key, nav_item)]，顺序即侧边栏顺序
        self.nav_items = []
        #: 插件通过 api.register_page() 注册的扩展页：key -> widget / nav item
        self.plugin_pages = {}
        self.plugin_nav = {}

    # ---------- 基础：导航项增删 ----------
    def _nav_layout(self):
        return self.host._nav_container.layout()

    def add_existing_item(self, key, item):
        """登记一个由宿主已放入布局的导航项（内置导航用）。"""
        self.nav_items.append((key, item))
        return item

    def add_nav_item(self, key, title, icon, nav_layout=None):
        """在导航栏末尾（stretch 之前）插入一个导航项并登记。

        插件页、开发者页、插件扩展页三处原先各写一遍同样的
        「建 item + 连 clicked + insertWidget(count-1) + 登记」，漏一处就会
        出现「页面在栈里但侧边栏点不到」。
        """
        nav_layout = nav_layout if nav_layout is not None else self._nav_layout()
        if any(k == key for k, _ in self.nav_items):
            return None
        item = self._make_nav_item(icon, title)
        item.clicked.connect(lambda k=key: self.host._set_page(k))
        nav_layout.insertWidget(max(0, nav_layout.count() - 1), item)
        self.nav_items.append((key, item))
        return item

    def remove_nav_item(self, key, nav_layout=None):
        """从侧边栏摘掉并销毁一个导航项（找不到时静默返回 None）。"""
        nav_layout = nav_layout if nav_layout is not None else self._nav_layout()
        item = None
        for i, (k, it) in enumerate(self.nav_items):
            if k == key:
                item = self.nav_items.pop(i)[1]
                break
        if item is not None:
            try:
                nav_layout.removeWidget(item)
                item.setParent(None)
                item.deleteLater()
            except RuntimeError:
                pass
        return item

    # ---------- 页面栈 ----------
    def register_page(self, key, widget):
        """把页面挂进内容栈并登记到 pages 表。"""
        self.pages[key] = widget
        self.host.content.addWidget(widget)
        return widget

    def drop_page(self, key, *, shutdown=False):
        """从栈与 pages 表中彻底移除并销毁一个页面。

        :param shutdown: 是否先调用页面的 shutdown()（PluginPage 需要释放缓存的
                         插件设置面板）。销毁顺序：pause -> removeWidget ->
                         setParent(None) -> shutdown -> deleteLater。
        """
        page = self.pages.pop(key, None)
        if page is None:
            return None
        if isinstance(page, DevPage):
            page.set_active(False)        # 先停后台计时器，避免移除过程中仍刷新
        try:
            self.host.content.removeWidget(page)
            page.setParent(None)
        except RuntimeError:
            pass
        if shutdown:
            try:
                page.shutdown()
            except Exception:
                pass
        try:
            page.deleteLater()
        except RuntimeError:
            pass
        return page

    # ---------- 页面切换 ----------
    def set_page(self, key, animate=True):
        """切换当前页面；不存在的 key 直接忽略（幂等）。"""
        target = None
        for i, (k, item) in enumerate(self.nav_items):
            selected = k == key
            item.set_selected(selected)
            if selected:
                target = i
        if target is None:
            return
        page = self.pages.get(key)
        if page is None:
            return
        host = self.host
        # 窗口未显示时没有有效 geometry，动画会导致布局错乱，强制走静态切换
        if animate and not host.isVisible():
            animate = False
        # 进入/离开时管理开发者页后台计时器：离开即停止（含退出动画期间），
        # 避免淡出过程中后台仍刷新文本导致「文字跳动/闪烁」；进入时启动。
        leaving = host.content.currentWidget()
        if leaving is not page:
            if isinstance(leaving, DevPage):
                leaving.set_active(False)
            if isinstance(page, DevPage):
                page.set_active(True)
        if animate:
            # 真实交叉淡入淡出 + 轻微上浮，旧页淡出、新页淡入，无文字残留
            host.content.slide_to(page, 230, rise=10)
        else:
            host.content.setCurrentWidget(page)
        hook = getattr(host, "on_page_shown", None)
        if callable(hook):
            hook(key)

    def current_page(self):
        return self.host.content.currentWidget()

    # ---------- 插件扩展页（api.register_page） ----------
    def sync_plugin_pages(self):
        """按 PluginManager 的 UI 注册表增删插件侧边栏页。

        约定：manager 只登记 spec/factory，widget 实例由本类创建、插入
        QStackedWidget 并在插件卸载（registry 里查不到）时自动移除销毁，
        避免出现「插件已卸载但页面还在」的悬垂界面。
        """
        host = self.host
        if host.plugins is None:
            return
        nav_layout = self._nav_layout()
        try:
            specs = host.plugins.plugin_pages()
        except Exception:
            specs = {}
        # 测试模式关闭时，插件页整体不可用：清掉所有插件扩展页
        if not host.config.get("use_test_features", False):
            specs = {}
        # 1) 新增
        for key, spec in specs.items():
            if key in self.plugin_pages:
                continue
            widget = self._build_plugin_page(key, spec)
            if widget is None:
                continue
            self.plugin_pages[key] = widget
            self.register_page(key, widget)
            item = self.add_nav_item(
                key, spec.get("title") or "插件页", spec.get("icon") or "plugin",
                nav_layout)
            if item is not None:
                self.plugin_nav[key] = item
        # 2) 移除：registry 里已不存在（插件停用/卸载/重扫丢失）
        current = host.content.currentWidget()
        leaving_key = next((k for k, w in self.plugin_pages.items()
                            if w is current), None)
        for key in [k for k in self.plugin_pages if k not in specs]:
            self._remove_plugin_page(key, nav_layout)
        if leaving_key and leaving_key not in self.plugin_pages:
            self.set_page("home")
        if specs and nav_layout is not None:
            nav_layout.update()
            host.sidebar.update()

    def _build_plugin_page(self, key, spec):
        """调用插件 factory 构建页面；失败时返回 None（不占侧边栏位置）。"""
        try:
            widget = spec["factory"](self.host.content)
        except Exception as e:
            self._note_plugin_page_error(key, spec, e)
            return None
        if not isinstance(widget, QWidget):
            self._note_plugin_page_error(key, spec, TypeError("factory 未返回 QWidget"))
            return None
        try:
            # 插件自建样式会切断祖先级联，先垫「完整主题 QSS + 插件自定义」
            apply_child_theme(widget, self.host.theme)
            widget.setProperty("pluginId", spec.get("plugin", ""))
        except Exception:
            pass
        return widget

    def _note_plugin_page_error(self, key, spec, exc):
        msg = ("插件 %s 的页面 %r 构建失败：%s: %s"
               % (spec.get("plugin", "?"), spec.get("title") or key,
                  type(exc).__name__, exc))
        try:
            self.host.plugins._log("error", "plugins", msg)
        except Exception:
            pass

    def _remove_plugin_page(self, key, nav_layout=None):
        """从导航与内容栈中移除并销毁一个插件扩展页。"""
        self.plugin_pages.pop(key, None)
        self.plugin_nav.pop(key, None)
        self.remove_nav_item(key, nav_layout)
        self.drop_page(key)

    # ---------- 「插件」总页（测试功能门控） ----------
    def sync_plugin_tab(self):
        """测试功能开关变化后同步「插件」导航项与页面的显隐。"""
        host = self.host
        if host.plugins is None:
            return
        wanted = bool(host.config.get("use_test_features", False))
        has = "plugin" in self.pages
        if wanted and not has:
            self.add_nav_item("plugin", "插件", "plugin")
            host.plugin_page = PluginPage(host.plugins, host.theme.name)
            self.register_page("plugin", host.plugin_page)
            host.plugin_page.set_theme(host.theme)
            host.sidebar.update()
        elif not wanted and has:
            leaving = host.content.currentWidget() is self.pages["plugin"]
            nav_layout = self._nav_layout()
            self.remove_nav_item("plugin", nav_layout)
            nav_layout.update()
            host.sidebar.update()
            # 页面必须真正销毁（见模块文档：僵尸页面会重复刷新）
            self.drop_page("plugin", shutdown=True)
            host.plugin_page = None
            if leaving:
                self.set_page("home")
        # 关闭测试模式时，插件的侧边栏扩展页也必须一并移除（否则会逃逸到正式环境）
        if not wanted:
            self.sync_plugin_pages()

    # ---------- 开发者页 ----------
    def enable_dev_tab(self):
        """关于页解锁开发者模式后实时添加「开发者」标签（无需重开窗口）。

        分两步：先把导航项同步加入侧边栏并立即刷新，给用户即时反馈；
        重型 DevPage 构建放到下一事件循环，既避免第 8 次点击瞬间卡顿，
        又让侧边栏先完成布局、标签页随后淡入。
        """
        from PySide6.QtCore import QTimer
        host = self.host
        if "developer" in self.pages or getattr(host, "_dev_enabling", False):
            return
        if host._developer_mode:
            return
        host._dev_enabling = True
        if not any(k == "developer" for k, _ in self.nav_items):
            self.add_nav_item("developer", "开发者", "code", self._nav_layout())
            host.sidebar.update()
        QTimer.singleShot(0, self.finish_enable_dev_tab)

    def finish_enable_dev_tab(self):
        host = self.host
        host._dev_enabling = False
        if "developer" in self.pages:
            return
        host.dev_page = DevPage(
            host.dev_refs, host.config, host.save_config, host.theme.name)
        host.dev_page.developer_mode_disabled.connect(host._disable_dev_tab)
        host.dev_page.test_features_changed.connect(host._on_test_features_changed)
        self.register_page("developer", host.dev_page)
        host.dev_page.set_theme(host.theme)
        host._developer_mode = True
        host.config["developer_mode"] = True
        # 若当前停在关于页，自动跳到开发者页，让用户立刻看到入口
        self.set_page("developer")

    def disable_dev_tab(self):
        """移除「开发者」标签页与导航项（config 归位由宿主负责）。"""
        host = self.host
        nav_layout = self._nav_layout()
        self.remove_nav_item("developer", nav_layout)
        nav_layout.update()
        host.sidebar.update()
        self.drop_page("developer")
        host.dev_page = None
