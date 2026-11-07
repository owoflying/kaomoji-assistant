"""⚠ 反面教材：用「邪修」手段修改自带界面 —— 请勿照抄。

════════════════════════════════════════════════════════════════════
本插件的唯一用途是**教学**：演示"没有官方 API 时，插件怎么绕过限制去改
宿主界面"，以及这条路要付出多少代价。
**生产插件请使用 `api.ui`（见 docs/PLUGIN_GUIDE.md）。**
════════════════════════════════════════════════════════════════════

## 背景：为什么当年只能邪修

早期插件 API 的 UI 能力只有「新增」：`register_page` / `register_settings_widget` /
`show_dialog` / `notify`。**没有任何方法能修改自带页面**。

但插件是以全权限 Python 代码跑在主进程里的，而 Qt 的 `QWidget.window()`
会沿父子链一路爬到顶层窗口：

    register_page 的 factory(parent)  ──parent.window()──►  UnifiedSettingsWindow
                                                                 │
                                                                 ├─ .home_page   内置主页
                                                                 ├─ .settings_page
                                                                 ├─ ._set_page()
                                                                 └─ .theme

## 三步走（邪修的具体手法）

1. **借锚点**：`register_page()` 的 factory 会收到内容区容器作为 parent ——
   这是插件能合法拿到的、已挂在宿主下的第一块踏板。
2. **爬宿主**：`holder.window()` 直达 `UnifiedSettingsWindow`，
   再用能力探测（有无 `home_page`）而非 isinstance 确认身份。
3. **直接改**：拿到 `.home_page` 后 `insertWidget`，插到 PageTitle 之后。

锚点页装完立刻撤销（改 `manager._plugin_pages` 私有注册表 + emit `ui_changed`），
所以侧边栏不会多出一个毫无意义的「__anchor__」页。

## 代价清单（这也是为什么现在有了正式 API）

| 代价 | 具体表现 |
|------|---------|
| 依附私有结构 | `home_page` 是私有属性，主程序重构（哪怕多包一层容器）就静默失效 |
| 无版本契约 | API v1/v2 从未承诺 `home_page` 存在，属于"能用但不保证" |
| 官方 API 只进不出 | `register_page` 没有注销接口，撤销锚点页只能改私有注册表 |
| 必须自己收尾 | 宿主只销毁自己创建的 widget，插进去的得自己摘 |
| 失败是静默的 | 不报错、不崩溃，就是卡片不见了 —— 用户只会说"插件坏了" |

本插件为"自己收尾"先后试错 **7 轮**才稳定：
`destroyed` 信号 → 加标志位 → 断开连接 → QTimer 轮询 →
最后才落到"监听 manager `changed` 信号 + 状态自检"。
这段折腾本身就是最好的反面论据。

## 现在的正确写法

主程序已提供**通用界面修改 API**（`api.ui`），覆盖本插件的全部功能且更安全：

    api.ui.set_text("home.greeting.title", "中午好")
    api.ui.insert("home.greeting", my_widget, index=0)
    api.ui.set_style("home.welcome", "border: 1px solid red;")

宿主自动记录每次修改，插件停用/卸载/崩溃时**完整回滚**。
对比测试见 `tests/test_ui_api.py`；本插件的契约测试见
`tests/test_home_greeting_plugin.py`（11 个用例，钉住邪修路径的边界）。

> 保留本文件是为了让后来者看清"没有正规扩展点时会发生什么"。
> 默认**不启用**（`default_enabled: false`）。
"""
import datetime

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

# 锚点页 id：只为拿到一个挂在宿主下的 widget，安装完成后立即移除
_ANCHOR_PAGE = "anchor"

# 主页根布局里各控件的 objectName（用于定位插入点）
_TITLE_OBJECT = "PageTitle"


# ======================================================================
# 问候语
# ======================================================================
def _period():
    """返回 (时段名, emoji)，边界按中文习惯划分。"""
    h = datetime.datetime.now().hour
    if h < 5:
        return "凌晨", "🌙"
    if h < 11:
        return "上午", "☀️"
    if h < 13:
        return "中午", "🍚"
    if h < 18:
        return "下午", "🌤️"
    return "晚上", "🌙"


_TIPS = {
    "凌晨": "这么晚还在忙，记得休息一下。",
    "上午": "新的一天，来点颜文字放松心情吧。",
    "中午": "午饭时间到了，别忘了吃饭。",
    "下午": "泡杯茶，继续加油。",
    "晚上": "今天辛苦了，早点休息。",
}


def _greeting_text():
    return "%s好" % _period()[0]


# ======================================================================
# 问候卡片（复用宿主 QSS，自动跟随深浅色）
# ======================================================================
class _GreetingCard(QFrame):
    """插进主页的问候卡片。

    刻意复用宿主的 objectName（Card / HomeTitle / BodyText），让它自动继承
    宿主全局 QSS —— 圆角、边框、深浅色都不用手动处理，也就不会与内置卡片割裂。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Card")
        self._build()
        self.refresh()

    def _build(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(16)

        self._emoji = QLabel()
        self._emoji.setObjectName("HomeTitle")
        self._emoji.setFixedWidth(40)
        self._emoji.setAlignment(Qt.AlignCenter)
        root.addWidget(self._emoji)

        box = QVBoxLayout()
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(4)

        self._title = QLabel()
        self._title.setObjectName("HomeTitle")
        box.addWidget(self._title)

        self._sub = QLabel()
        self._sub.setObjectName("BodyText")
        self._sub.setWordWrap(True)
        box.addWidget(self._sub)

        root.addLayout(box, 1)

    def refresh(self):
        """按当前时间刷新问候语（主页每次显示时调用）。"""
        period, emoji = _period()
        self._emoji.setText(emoji)
        self._title.setText("%s好" % period)
        self._sub.setText("%s · %s" % (
            datetime.datetime.now().strftime("%H:%M"),
            _TIPS.get(period, "")))


# ======================================================================
# 宿主探测（非正常手段的核心）
# ======================================================================
def _find_host_window(widget):
    """从任意挂在宿主下的 widget 沿父链爬到统一主窗口。

    用**能力探测**（是否具备 home_page / _set_page）而不是 isinstance，
    这样不必反向 import 宿主的具体类名 —— 主程序改类名或拆模块时更容易存活。
    """
    # 1) QWidget.window() 最直接
    try:
        top = widget.window()
    except RuntimeError:
        return None
    if top is not None and hasattr(top, "home_page"):
        return top
    # 2) 兜底：逐级向上找
    node = widget.parentWidget()
    while node is not None:
        if hasattr(node, "home_page"):
            return node
        node = node.parentWidget()
    return None


def _home_root_layout(home_page):
    """主页根布局（主页用 page_layout() 建的就是 QVBoxLayout）。"""
    lay = home_page.layout() if home_page is not None else None
    return lay if isinstance(lay, QVBoxLayout) else None


def _insert_index(layout):
    """定位插入点：PageTitle 之后。找不到标题时插到最前面。"""
    for i in range(layout.count()):
        item = layout.itemAt(i)
        widget = item.widget() if item is not None else None
        if widget is not None and widget.objectName() == _TITLE_OBJECT:
            return i + 1
    return 0


def _hook_refresh(home_page, card):
    """让主页每次显示时顺带刷新问候语。

    宿主在页面切到前台时会调 `home.refresh_stats()`（见
    `UnifiedSettingsWindow.on_page_shown`），这里非侵入地包一层：
    保留原行为，追加自己的刷新。返回一个"还原函数"，卸载时调用。
    """
    original = getattr(home_page, "refresh_stats", None)
    if not callable(original):
        return None

    def _wrapped(*args, **kwargs):
        result = original(*args, **kwargs)
        try:
            card.refresh()
        except RuntimeError:
            pass            # 卡片已被销毁，忽略
        return result

    _wrapped._greeting_wrapped = True
    home_page.refresh_stats = _wrapped

    def _restore():
        # 仅当没人再包一层时才还原（防御性：插件不应互相踩）
        if getattr(home_page, "refresh_stats", None) is _wrapped:
            home_page.refresh_stats = original

    return _restore


# ======================================================================
# 插件入口
# ======================================================================
def setup(api):
    state = {"card": None, "restore": None, "host": None,
             "anchor_widget": None, "anchor_slot": None}

    def _still_loaded():
        """插件此刻是否仍是「已加载」状态。

        这是判断"该不该清理"的**唯一可靠依据**：manager._detach 会先把记录改成
        disabled/error 再撤销登记，而"我们自己拆锚点页"不会改变状态。比依赖
        destroyed 信号健壮得多——那个信号既分不清谁拆的，也会被 disconnect 掉。
        """
        try:
            rec = api._manager.plugins.get(api.id)   # noqa: SLF001
        except Exception:
            return True     # 读不到就假定还活着，避免误删
        if not rec:
            return True
        return rec.get("state") == "loaded"

    def _cleanup(reason=""):
        """把插进去的东西收回来。宿主不会替我们做这件事。"""
        slot = state.get("changed_slot")
        if slot is not None:
            try:
                api._manager.changed.disconnect(slot)   # noqa: SLF001
            except (RuntimeError, TypeError):
                pass
            state["changed_slot"] = None
        card, state["card"] = state["card"], None
        restore, state["restore"] = state["restore"], None
        if restore is not None:
            try:
                restore()
            except Exception:
                pass
        if card is not None:
            try:
                card.setParent(None)
                card.deleteLater()
            except RuntimeError:
                pass            # C++ 对象已销毁
        if reason:
            api.log("主页问候：%s，已清理" % reason)

    def _install(parent_widget):
        """真正的"非正常手段"：直接改宿主的内置主页。"""
        host = _find_host_window(parent_widget)
        if host is None:
            api.log("主页问候：无法定位宿主窗口，放弃", level="warn")
            return False

        home = getattr(host, "home_page", None)
        layout = _home_root_layout(home)
        if layout is None:
            api.log("主页问候：宿主主页布局不符合预期，放弃", level="warn")
            return False

        card = _GreetingCard(home)
        layout.insertWidget(_insert_index(layout), card)
        state["card"] = card
        state["host"] = host
        state["restore"] = _hook_refresh(home, card)

        # ---- 卸载自清理 ----
        # manager._detach 不会通知插件（shutdown 事件只在应用退出时发），
        # 但 `unload()` / `reload()` / `discover()` 结束时都会 emit `changed`，
        # 且是**同步**的。挂到它上面，每次插件集合变化时自检一句
        # "我还是已加载状态吗"，不是了就自己摘掉。
        #
        # 为什么不用 destroyed 信号（试过，有坑）：
        #   宿主销毁锚点页时，可能是我们自己主动撤销，也可能是插件被停用——
        #   信号分不清这两者，要么误删卡片、要么漏删；为不误删而 disconnect，
        #   又会失去真实卸载时的通知。
        # 为什么不用 QTimer 轮询（也试过）：能工作，但停用后要等一个周期
        # 才消失，且测试里不推进时间就完全测不到。
        def _on_manager_changed():
            if not _still_loaded():
                _cleanup("插件已停用（changed 信号自检）")

        try:
            api._manager.changed.connect(_on_manager_changed)   # noqa: SLF001
            state["changed_slot"] = _on_manager_changed
        except Exception as e:
            api.log("主页问候：无法监听插件变化信号：%r" % (e,), level="warn")

        api.log("主页问候：已在主页插入问候卡片"
                "（非正常手段——插件 API 无此能力，沿 Qt 父子链修改宿主）")
        return True

    def _deferred_cleanup(reason):
        """延后一拍清理：避免在 Qt 析构栈/信号发射过程中直接改布局。

        锚点页被销毁时我们可能正处在宿主的 deleteLater 流程里，此刻动主页布局
        属于在别人的析构路径上改结构，容易引发重入崩溃。投递到下一轮事件循环。
        """
        QTimer.singleShot(0, lambda: _cleanup(reason))

    # ---- 用锚点页拿到一个"已经挂在宿主下"的 widget ----
    # register_page 的 factory 由宿主在同步插件页时调用，参数就是内容区容器
    # （AnimatedStackedWidget），window() 即可直达主窗口。
    def build_anchor(parent):
        holder = QWidget(parent)
        if _install(holder):
            # 锚点页对用户毫无意义，装完立刻从侧边栏摘掉：等宿主完成本轮同步后再撤。
            QTimer.singleShot(0, lambda: _drop_anchor_page())
        return holder

    def _drop_anchor_page():
        """撤销锚点页登记，让宿主移除它的侧边栏项与页面。

        ⚠ 直接改 manager 的私有注册表是"非正常手段"的一部分：官方 API 没有
        「注销页面」的能力（register_page 只进不出）。撤销后会 emit ui_changed，
        宿主按「已加载插件 + 注册表内容」重建侧边栏，锚点页随之消失。

        宿主的移除与销毁**不会**影响卡片：清理靠 `_still_loaded()` 自检，
        与"谁拆掉了锚点页"无关。
        """
        try:
            mgr = api._manager                      # noqa: SLF001
            key = "plugin:%s:%s" % (api.id, _ANCHOR_PAGE)
            if key in mgr._plugin_pages:            # noqa: SLF001
                del mgr._plugin_pages[key]          # noqa: SLF001
                mgr.ui_changed.emit()
                api.log("主页问候：已撤销临时锚点页")
        except Exception as e:
            api.log("主页问候：撤销锚点页失败：%r" % (e,), level="warn")

    api.register_page(_ANCHOR_PAGE, "__anchor__", build_anchor)

    # ---- 应用退出时清理（stop 插件 / 关应用都会走到）----
    api.on("shutdown", lambda: _cleanup("应用退出"))

    api.log("主页问候：插件已加载（api v%s）" % api.version)
