"""插件侧的「通用界面修改」门面（`api.ui`）。

这一层是插件能看到的全部界面修改能力。它把三件事包成好用的方法：

    find / walk / ids               —— 定位元素
    set_text / set_style / ...      —— 修改属性（自动记录，可回滚）
    insert / remove / replace       —— 修改结构（自动记录，可回滚）
    on / emit                       —— 绑定事件

**权限模型**：默认允许修改任意已登记元素（含其他插件界面），但每次跨作用域
修改都会被审计回调记下"谁改了谁的什么"。这是刻意设计——本项目插件是同进程
全权限代码，与其假装隔离，不如**把修改全部记下来可回滚、可审计**。

## 为什么在这里再包一层

`UiMutator` 是纯机制（不依赖 registry 之外的任何东西），本类负责：
  * 把 registry / mutator 的装配细节藏起来；
  * 提供更友好的返回值（bool / 控件 / None，而不是抛异常）；
  * 统一做"插件停用后调用即失效"的保护（回滚后 mutator 会被移除）。
"""
import weakref


class PluginUi:
    """`api.ui` 的实现：一个插件一个实例，共享宿主注入的 registry 与 mutator。"""

    def __init__(self, manager, plugin_id):
        self._manager = manager
        self.plugin_id = plugin_id

    # ---------- 内部 ----------
    @property
    def _registry(self):
        return getattr(self._manager, "_ui_registry", None)

    @property
    def _mutator(self):
        hub = getattr(self._manager, "_ui_hub", None)
        if hub is None:
            return None
        return hub.for_plugin(self.plugin_id)

    def _log(self, level, message):
        try:
            self._manager._log(level, "plugin:%s" % self.plugin_id, message)
        except Exception:
            pass

    def _ready(self):
        """界面元素是否已就绪。"""
        reg = self._registry
        return reg is not None and reg.ready

    def _defer(self, op):
        """界面尚未就绪时把操作排进队列，等界面好了重放。

        **为什么需要**：装配顺序是「先加载插件（执行 setup）→ 再建窗口登记元素」。
        插件在 `setup()` 里调 `api.ui.set_text(...)` 时元素表还是空的。
        若不排队，这些调用会静默失败——插件作者只会看到"改了没生效"。

        排队后：插件代码无需关心时序，照常写即可。

        :return: 是否成功排队。**没有 registry 时返回 False**——
                 那不是"稍后生效"，而是"根本没有宿主界面"，不该骗调用方说成功。
        """
        reg = self._registry
        if reg is None:
            return False
        reg.when_ready(op)
        return True

    # ---------- 定位 ----------
    def find(self, target):
        """按 id 或路径找控件；找不到返回 None。

        ``api.ui.find("home.greeting.title")``          # 手工 id（推荐）
        ``api.ui.find("home/GreetingCard[0]/QLabel[1]")``  # 自动路径（兜底）
        """
        reg = self._registry
        return reg.resolve(target) if reg is not None else None

    def ids(self):
        """所有已登记、当前有效的元素 id（插件可据此发现可改的东西）。"""
        reg = self._registry
        return reg.ids() if reg is not None else []

    def walk(self, root=None):
        """遍历控件树，产出 (path, widget)。用于探查未登记的元素。"""
        reg = self._registry
        return reg.walk(root) if reg is not None else iter(())

    def path_of(self, widget):
        """反查某控件的路径。"""
        reg = self._registry
        return reg.path_of(widget) if reg is not None else None

    def exists(self, target):
        """目标是否存在（比 find 更直白的可读性封装）。"""
        return self.find(target) is not None

    def text_of(self, target, default=""):
        """读取控件当前文字（读操作不记录、不回滚）。"""
        w = self.find(target)
        if w is None or not hasattr(w, "text"):
            return default
        try:
            return w.text()
        except Exception:
            return default

    # ---------- 属性修改 ----------
    def _apply(self, method, *args):
        """统一入口：就绪则立刻改，未就绪则排队等界面建好再改。

        所有修改方法都经过这里，因此插件作者**不需要关心 setup() 的时序**。
        """
        m = self._mutator
        if m is None:
            return False
        if not self._ready():
            def _later():
                mut = self._mutator
                if mut is not None:
                    getattr(mut, method)(*args)
            # 没有 registry 时 _defer 返回 False（没有宿主界面，不能算受理）
            return self._defer(_later)
        return bool(getattr(m, method)(*args))

    def set_text(self, target, text):
        """改文字。返回是否成功（目标不存在返回 False，不抛异常）。"""
        return self._apply("set_text", target, text)

    def set_visible(self, target, visible):
        """显示/隐藏控件。"""
        return self._apply("set_visible", target, visible)

    def set_style(self, target, qss, append=True):
        """设置样式表。append=True 时追加在原有样式后（保住宿主 QSS 级联）。"""
        return self._apply("set_style", target, qss, append)

    def set_enabled(self, target, enabled):
        """启用/禁用控件。"""
        return self._apply("set_enabled", target, enabled)

    def set_property(self, target, name, value):
        """设置任意 Qt 属性（可配合 QSS 的动态属性选择器）。"""
        return self._apply("set_property", target, name, value)

    # ---------- 结构修改 ----------
    def insert(self, target, widget, index=-1):
        """把自己造的控件插进目标容器的布局里。"""
        return self._apply("insert", target, widget, index)

    def remove(self, target):
        """把内置控件从界面摘掉（可回滚）。"""
        return self._apply("remove", target)

    def replace_render(self, target, factory):
        """替换某个容器**内部的内容**：原内容收起，换成 factory(parent) 的产物。

        注意与 `swap_widget` 的区别：这里容器本身还在，只是"里面装的东西"换了。
        """
        return self._apply("replace_render", target, factory)

    def swap_widget(self, target, factory):
        """把目标控件**整个**换成 plugin 产物（连它占的位置一起接管）。

        适合"整个卡片/整行我不满意，重画一个"。
        """
        return self._apply("swap_widget", target, factory)

    # ---------- 事件 ----------
    def on(self, target, signal_name, slot):
        """给某个控件的信号绑定回调，插件卸载时自动断开。

        ``api.ui.on("home.actions.search", "clicked", handler)``

        实现上直接连 Qt 信号；回滚时通过 `disconnect` 撤销。绑定本身不改变
        界面状态，因此记录为一条"可回滚的修改"即可。
        """
        if not self._ready():
            # 界面未就绪：排到"界面好了"再绑，否则拿不到控件
            return self._defer(lambda: self.on(target, signal_name, slot))
        w = self.find(target)
        if w is None or not callable(slot):
            return False
        try:
            sig = getattr(w, signal_name, None)
            if sig is None or not hasattr(sig, "connect"):
                return False
            sig.connect(slot)
        except Exception as e:
            self._log("warn", "绑定信号 %s.%s 失败：%r" % (target, signal_name, e))
            return False
        m = self._mutator
        if m is not None:
            # 把"断开这条连接"记成一条可回滚修改，卸载时自动解除绑定
            m._record(str(target), "connect:%s" % signal_name,
                      _make_disconnect(w, signal_name, slot))
        return True

    # ---------- 自省 / 回滚 ----------
    def pending(self):
        """本插件尚未回滚的修改数。"""
        hub = getattr(self._manager, "_ui_hub", None)
        return hub.pending(self.plugin_id) if hub is not None else 0

    def rollback(self):
        """立即回滚本插件的全部界面修改（通常由宿主在卸载时调用）。"""
        hub = getattr(self._manager, "_ui_hub", None)
        if hub is None:
            return 0
        n = hub.rollback(self.plugin_id)
        if n:
            self._log("info", "已回滚 %d 项界面修改" % n)
        return n


def _make_disconnect(widget, signal_name, slot):
    """构造一个"断开信号连接"的 undo 动作。"""
    ref = weakref.ref(widget)

    def _undo():
        obj = ref()
        if obj is None:
            return
        try:
            from shiboken6 import isValid
            if not isValid(obj):
                return
        except Exception:
            pass
        sig = getattr(obj, signal_name, None)
        if sig is not None and hasattr(sig, "disconnect"):
            try:
                sig.disconnect(slot)
            except (RuntimeError, TypeError):
                pass

    return _undo
