"""内置界面的可寻址 id 登记（给插件一个稳定的"把手"）。

**为什么要单独一个模块**：插件要能定位到界面元素，宿主就必须在构造界面时
把关键节点登记到全局 id 表。这些登记散落在各页面代码里会很难维护、
也容易漏登记导致"插件找不到东西"。这里集中定义 id 常量与登记函数。

## id 命名约定

    <页面>.<区块>[.<元素>]

例如：
    home.greeting            主页问候卡片（容器）
    home.greeting.title      问候标题（QLabel）
    home.welcome             欢迎卡片
    home.stats.recent        最近使用统计卡片
    home.actions.search      快捷入口「搜索颜文字」
    settings.hotkey          设置页热键行
    picker.panel             候选条面板

**只登记"有意义"的节点**：不是每个控件都登记——那样 id 表会变成控件树的
镜像，失去"稳定契约"的意义。原则是登记「插件可能会想改的东西」。
未登记的元素仍可通过路径寻址（`home/GreetingCard[0]/QLabel[1]`）触达。

## 稳定性承诺

一旦某个 id 发布出去，插件就会依赖它。因此：
  * 不要随意改名（等于破坏 API）；
  * 控件重构时若确实要删，应先在文档里标记 @deprecated 并保留一个版本。
"""
# 主页
HOME_GREETING = "home.greeting"
HOME_GREETING_EMOJI = "home.greeting.emoji"
HOME_GREETING_TITLE = "home.greeting.title"
HOME_GREETING_SUB = "home.greeting.sub"
HOME_WELCOME = "home.welcome"
HOME_WELCOME_TITLE = "home.welcome.title"
HOME_WELCOME_SUB = "home.welcome.sub"
HOME_STATS = "home.stats"
HOME_ACTIONS = "home.actions"
HOME_TIP = "home.tip"
HOME_PLUGIN_SLOT = "home.plugin_slot"

# 快捷入口（按 target 后缀）
HOME_ACTION_PREFIX = "home.actions."

# 设置页
SETTINGS_PAGE = "settings.page"

# 候选条
PICKER_PANEL = "picker.panel"

# 统一窗口外壳
SHELL_WINDOW = "shell.window"
SHELL_SIDEBAR = "shell.sidebar"
SHELL_TITLEBAR = "shell.titlebar"
SHELL_CONTENT = "shell.content"

# 关于页 / 插件页
ABOUT_PAGE = "about.page"
PLUGIN_PAGE = "plugin.page"


def register_home(registry, home_page):
    """把主页的关键节点登记进 id 表；返回登记成功的 id 数。"""
    if registry is None or home_page is None:
        return 0
    pairs = []

    greeting = getattr(home_page, "greeting_card", None)
    if greeting is not None:
        pairs.append((HOME_GREETING, greeting))
        for attr, ui_id in (("_emoji", HOME_GREETING_EMOJI),
                            ("_title", HOME_GREETING_TITLE),
                            ("_sub", HOME_GREETING_SUB)):
            w = getattr(greeting, attr, None)
            if w is not None:
                pairs.append((ui_id, w))

    for attr, base in (("welcome_card", HOME_WELCOME),
                       ("stats_row_widget", HOME_STATS),
                       ("tip_card", HOME_TIP),
                       ("plugin_cards_slot", HOME_PLUGIN_SLOT)):
        w = getattr(home_page, attr, None)
        if w is not None:
            pairs.append((base, w))

    # 欢迎卡片内的两个文本
    for attr, ui_id in (("welcome_title", HOME_WELCOME_TITLE),
                        ("welcome_sub", HOME_WELCOME_SUB)):
        w = getattr(home_page, attr, None)
        if w is not None:
            pairs.append((ui_id, w))

    for key, w in getattr(home_page, "action_cards", {}).items():
        pairs.append((HOME_ACTION_PREFIX + str(key), w))

    ok = 0
    for ui_id, widget in pairs:
        if registry.register(ui_id, widget):
            ok += 1
    return ok


def register_shell(registry, window):
    """登记统一窗口外壳的稳定节点。"""
    if registry is None or window is None:
        return 0
    pairs = [(SHELL_WINDOW, window)]
    for attr, ui_id in (("sidebar", SHELL_SIDEBAR),
                        ("titlebar", SHELL_TITLEBAR),
                        ("content", SHELL_CONTENT)):
        w = getattr(window, attr, None)
        if w is not None:
            pairs.append((ui_id, w))
    ok = 0
    for ui_id, widget in pairs:
        if registry.register(ui_id, widget):
            ok += 1
    return ok
