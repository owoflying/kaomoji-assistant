"""可寻址的界面元素登记表与稳定路径解析。

**为什么需要这一层**：插件要"自由修改内置界面"，就必须先能**稳定地指到**某个
控件。而实测现状是 objectName 严重重复——主页里 `Card` 有 12 个、`BodyText` 有
8 个，光靠名字根本定位不到唯一节点。

所以采用**双轨寻址**：

1. **手工 id**（推荐）：在构造界面时给关键元素登记一个全局唯一 id，
   形如 `home.greeting.title`。稳定、可读、宿主重构时只要 id 不变插件就不碎。
2. **自动路径**（兜底）：遍历控件树时按「类型+序号」生成可预测路径，
   形如 `home/Card[2]/QLabel[0]`。能触达任何一个控件（含未登记的），
   但宿主一旦调整布局层级就会失配——仅供插件自己权衡。

两条轨道共用同一个 `UiRegistry`，`resolve()` 先查 id 表再按路径遍历。

## 设计取舍

- **不含 Qt import 之外的依赖**：本模块要被 core 层使用，只能依赖 Qt 基础类型，
  不能反向 import `ui.win11_theme` 之类。
- **登记是弱引用**：控件被销毁后登记项自动失效，不会留下悬垂指针
  （Qt 的 C++ 对象与 Python 包装器生命周期不同步，必须用 shiboken 校验）。
"""
import re
import weakref


# 由「类型名[序号]」逐级拼接而成，例如 home/Card[2]/QLabel[0]
_PATH_STEP_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\[(\d+)\]$")

# 路径分隔符
SEP = "/"


def _is_alive(widget):
    """Qt C++ 对象是否仍然有效（Python 包装器可能还活着但底层已销毁）。"""
    if widget is None:
        return False
    try:
        from shiboken6 import isValid
        return isValid(widget)
    except Exception:
        try:
            return bool(widget)
        except RuntimeError:
            return False


class UiRegistry:
    """id -> 控件 的登记表，负责稳定定位与路径生成。

    典型用法（宿主侧）::

        registry.register("home.greeting.title", label)
        registry.resolve("home.greeting.title")     # -> label
        registry.resolve("home/Card[2]/QLabel[0]")  # -> 按路径遍历

    插件侧只看到 `resolve()` 与 `walk()`。
    """

    def __init__(self):
        # id -> weakref.ref(控件)。用弱引用避免插件/宿主互相持有导致泄漏，
        # 也让"控件已被销毁"能被自动发现。
        self._by_id = {}
        # 可选的根容器提供者：() -> [QWidget]，供路径遍历使用
        self._roots_provider = None
        # 界面是否已就绪（窗口建好、元素登记完）。
        # 未就绪时插件调 api.ui 找不到任何元素——见 PluginUi 的延迟重放机制。
        self._ready = False
        # 就绪回调：插件在 setup() 期间排队的操作，等这里统一重放
        self._on_ready = []

    # ---------- 登记 ----------
    def register(self, ui_id, widget):
        """把一个控件登记到指定 id 上（同 id 重复登记会覆盖）。"""
        ui_id = str(ui_id or "").strip()
        if not ui_id or widget is None:
            return False
        try:
            self._by_id[ui_id] = weakref.ref(widget)
        except TypeError:
            return False
        return True

    def unregister(self, ui_id):
        self._by_id.pop(str(ui_id or "").strip(), None)

    def set_roots_provider(self, fn):
        """设置路径遍历的根容器提供者（通常返回主窗口的各页面）。"""
        self._roots_provider = fn

    # ---------- 就绪状态 ----------
    @property
    def ready(self):
        """界面元素是否已登记完毕（窗口已建、id 表已填充）。"""
        return self._ready

    def mark_ready(self):
        """标记界面就绪，并重放所有排队的"等界面好了再做"的回调。

        **为什么需要这个**：装配顺序是 `_build_core`（加载插件、执行 setup）
        先于 `_build_windows`（建窗口、登记元素）。插件在 `setup()` 里调
        `api.ui.set_text(...)` 时，元素表还是空的，调用会静默失败。

        与其要求插件自己去等，不如让宿主推迟应用：插件照常写"设置期间就改界面"，
        这些操作被排进队列，等 `mark_ready()` 时统一重放。
        """
        self._ready = True
        callbacks, self._on_ready = self._on_ready, []
        for fn in callbacks:
            try:
                fn()
            except Exception:
                # 单个排队操作失败不能影响其它插件
                pass

    def when_ready(self, fn):
        """界面已就绪则立即执行，否则排队等 `mark_ready()`。"""
        if self._ready:
            try:
                fn()
            except Exception:
                pass
            return False
        self._on_ready.append(fn)
        return True

    # ---------- 查询 ----------
    def ids(self):
        """当前所有有效的登记 id（已销毁的自动剔除）。"""
        out = []
        for ui_id, ref in list(self._by_id.items()):
            w = ref() if ref is not None else None
            if _is_alive(w):
                out.append(ui_id)
            else:
                self._by_id.pop(ui_id, None)
        return sorted(out)

    def resolve(self, target):
        """按 id 或路径定位控件，找不到返回 None。

        先查 id 表（精确、稳定），再退回路径遍历（灵活、但依赖结构）。
        """
        target = str(target or "").strip()
        if not target:
            return None
        # 1) id 精确命中
        ref = self._by_id.get(target)
        if ref is not None:
            w = ref()
            if _is_alive(w):
                return w
            self._by_id.pop(target, None)
        # 2) 路径遍历
        return self._resolve_path(target)

    # ---------- 路径遍历 ----------
    def _roots(self):
        if not callable(self._roots_provider):
            return []
        try:
            return list(self._roots_provider() or [])
        except Exception:
            return []

    def _resolve_path(self, path):
        """解析 `root/Type[i]/Type[j]` 形式的路径。

        每一级都在「当前控件的**直接子控件**」里按类型分组取第 i 个。
        用直接子控件而不是 findChildren，是为了让路径语义可预测：
        `home/Card[2]` 明确表示"home 下第 3 个直接子级 Card"。
        """
        steps = [s for s in str(path).split(SEP) if s]
        if len(steps) < 2:
            return None
        root_name, rest = steps[0], steps[1:]
        root = next((r for r in self._roots()
                     if getattr(r, "objectName", lambda: "")() == root_name
                     or type(r).__name__ == root_name), None)
        if root is None:
            return None
        node = root
        for step in rest:
            m = _PATH_STEP_RE.match(step)
            if not m:
                return None
            type_name, index = m.group(1), int(m.group(2))
            same_type = [c for c in (node.children() if node is not None else [])
                         if type(c).__name__ == type_name and hasattr(c, "isWidgetType")]
            if index >= len(same_type):
                return None
            node = same_type[index]
        return node

    def path_of(self, widget, max_depth=24):
        """反向生成某个控件的路径（找不到返回 None），便于插件调试。

        ⚠ 必须与 `_resolve_path` 使用**同一套根**：路径的首段是 roots provider
        提供的页面（如 `home`），而不是整棵窗口树。否则生成出来的路径
        （`UnifiedSettingsWindow/QWidget[0]/...`）永远反查不回来。
        """
        if not _is_alive(widget):
            return None
        # 1) 找到该控件归属的根（沿父链向上，第一个命中 roots 的祖先/自身）
        roots = [r for r in self._roots() if _is_alive(r)]
        node = widget
        root = None
        while node is not None:
            if any(node is r for r in roots):
                root = node
                break
            parent = node.parent() if hasattr(node, "parent") else None
            if parent is None or not hasattr(parent, "isWidgetType"):
                break
            node = parent
        if root is None:
            return None
        root_name = getattr(root, "objectName", lambda: "")() or type(root).__name__
        if widget is root:
            return root_name
        # 2) 自底向上拼接「类型[序号]」
        steps = []
        cur = widget
        while cur is not None and cur is not root:
            parent = cur.parent() if hasattr(cur, "parent") else None
            if parent is None:
                return None
            siblings = [c for c in parent.children()
                        if hasattr(c, "isWidgetType") and c.isWidgetType()
                        and type(c).__name__ == type(cur).__name__]
            try:
                idx = siblings.index(cur)
            except ValueError:
                return None
            steps.append("%s[%d]" % (type(cur).__name__, idx))
            cur = parent
        if cur is not root:
            return None
        return SEP.join([root_name] + list(reversed(steps)))

    def walk(self, root=None):
        """深度遍历控件树，产出 (path, widget)。供插件/开发者页探查可用节点。"""
        roots = [root] if root is not None else self._roots()
        for r in roots:
            if not _is_alive(r):
                continue
            root_name = getattr(r, "objectName", lambda: "")() or type(r).__name__
            yield from self._walk_node(root_name, r)

    def _walk_node(self, path, node):
        yield path, node
        counts = {}
        for child in node.children():
            if not hasattr(child, "isWidgetType") or not child.isWidgetType():
                continue
            tname = type(child).__name__
            idx = counts.get(tname, 0)
            counts[tname] = idx + 1
            yield from self._walk_node("%s%s%s[%d]" % (path, SEP, tname, idx), child)


#: 进程级默认登记表（由 AppRuntime 创建并注入窗口）
_default = UiRegistry()


def default_registry():
    return _default
