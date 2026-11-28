"""配置变更通道：**一处写入，明确告知要刷新什么**。

## 这个模块要解决什么

配置变更以前只有一个入口 `apply_settings(new_cfg)`，它靠**比对**
新旧配置来推断"哪些东西变了、该重绘什么"：

    changed = {k for k, v in self.config.items() if old.get(k) != v}

这套做法在整套代码里反复出事，因为它有几种**结构性缺陷**：

### 1. 嵌套结构看不出变化

外观配置在 `surface_overrides` 这个 dict 里：

    {"surface_overrides": {"picker": {"theme_mode": "dark"}}}

改候选条主题时，**顶层键 `surface_overrides` 还是同一个字典对象**，
里面的内容变了但引用没变 → diff 判不出来 → 不触发重绘。
（这正是"改了候选条却完全不刷新"的原因。）

### 2. 值被提前写入就永远"没变化"

调用方先 `cfg["theme_mode"] = "dark"` 再交给 `apply_settings`，
那 `old` 读到的已经是新值，`changed` 恒为空。
这个坑在主题、外观、编辑器预览里各踩过一次。

### 3. 推断出来的意图不可靠

"哪些键变了"和"要刷新什么"不是一一对应的：
`surface_overrides` 变化要重绘的是**某个具体界面**，
不是主窗口；而只靠键名根本推不出是哪个界面。

## 现在的做法：**调用方声明意图**

    commit(cfg, new_values, scopes=[Scope.SURFACE, ...], surface="picker")

* `new_values` —— 要写入的键值（只写这些，不做全量替换）
* `scopes`     —— **明确告诉宿主"这次变更需要刷新什么"**，
                 不靠 diff 猜
* `surface`    —— 涉及哪个界面（外观类变更需要）

宿主（AppRuntime）注册一组 handler，按 scope 分派：

    Scope.CONFIG     → 落盘（所有变更都做）
    Scope.THEME      → 重新解析主题 + 重贴 QSS
    Scope.SURFACE    → 重绘指定界面（主窗口/候选条/托盘菜单…）
    Scope.HOTKEY     → 重新注册热键
    Scope.AUTOSTART  → 同步开机自启

新增一类"改了要做什么"，加一个 scope + 一个 handler，不用碰其它代码。

## 为什么这样更不容易出 bug

    * 意图是**声明的**，不是推断的 → 不会因为 diff 失效而静默跳过
    * 每个 scope 的处理互不影响 → 改配置不会顺手把主题也重刷一遍
    * 新增界面/新增设置只需登记 scope → 不需要理解整条链路
"""
import copy

#: 变更影响范围（可以组合）。
#:
#: 这些是**语义**而不是"哪些键变了"——同一组键在不同场景下
#: 可能需要不同的刷新动作，所以由调用方显式给出。
CONFIG = "config"        # 落盘（任何变更都要）
THEME = "theme"          # 主题色板变了（重新解析主题、重贴 QSS）
SURFACE = "surface"      # 某个界面的外观变了（重绘那个界面）
HOTKEY = "hotkey"        # 唤起热键变了（重新注册）
AUTOSTART = "autostart"  # 开机自启相关配置变了
FONTS = "fonts"          # 字体大小等影响全部窗口排版的变更

#: 全部 scope
ALL_SCOPES = (CONFIG, THEME, SURFACE, HOTKEY, AUTOSTART, FONTS)


class Handler:
    """一次变更的处理回执。

    :param ok: 是否成功（失败会写日志，但不阻断后续 handler）
    :param message: 可选的说明
    """

    __slots__ = ("ok", "message")

    def __init__(self, ok=True, message=""):
        self.ok = bool(ok)
        self.message = message or ""

    def __repr__(self):
        return "Handler(ok=%s, message=%r)" % (self.ok, self.message)


OK = Handler(True)


class ScopeRouter:
    """按 scope 分派变更。宿主注册 handler，调用方提交意图。

    ## 用法

        router = ScopeRouter()
        router.register(THEME, lambda event: runtime.reapply_theme())
        router.register(SURFACE, lambda event: runtime.repaint_surface(
            event.surface))

        router.commit(cfg, {"theme_mode": "dark"},
                      scopes=[THEME, SURFACE], surface="picker")

    ## 顺序保证

    先 `CONFIG`（落盘），再其它 scope。这样即使后续重绘失败，
    配置也已经保存了——用户下次启动仍是新值，不会"改了等于没改"。
    """

    def __init__(self):
        self._handlers = {}
        self._log = None

    # ---------------------------------------------------------- 注册
    def register(self, scope, handler):
        """注册某个 scope 的处理函数 `handler(event) -> None | Handler`。"""
        if not callable(handler):
            raise TypeError("handler 必须可调用")
        self._handlers.setdefault(scope, []).append(handler)

    def set_logger(self, fn):
        """注入日志函数 `fn(level, source, message)`。"""
        self._log = fn

    def clear(self):
        self._handlers.clear()

    # ---------------------------------------------------------- 提交
    def commit(self, cfg, new_values=None, scopes=(), surface=None):
        """把变更写进 `cfg` 并按 scope 分派。

        :param cfg: 配置字典（原地更新，保持对象身份——
            PluginManager 等持有它的引用）
        :param new_values: 要写入的键值对；None 表示"配置已在外部改好，
            只做分派"
        :param scopes: 影响范围列表（见模块级常量）
        :param surface: 涉及的界面（`SURFACE` scope 会用到）
        :return: 实际执行的 `[(scope, Handler), ...]`

        ## 为什么不覆盖整个 dict

        `new_values` 是**增量**：只写这里出现的键。全量替换会让外部
        持有 `cfg` 引用却手工改过的值被无声抹掉。
        """
        if new_values:
            cfg.update(copy.deepcopy(new_values))

        # 规范化：CONFIG 永远第一个（先落盘），其余按调用方给的顺序
        wanted = []
        for s in scopes:
            if s and s not in wanted:
                wanted.append(s)
        if CONFIG in wanted:
            wanted.remove(CONFIG)
        wanted.insert(0, CONFIG)

        event = {
            "cfg": cfg,
            "values": dict(new_values or {}),
            "surface": surface,
            "scopes": wanted,
        }

        results = []
        for scope in wanted:
            handlers = self._handlers.get(scope) or []
            for h in handlers:
                try:
                    r = h(event)
                    results.append((scope, r if isinstance(r, Handler)
                                    else OK))
                except Exception as e:
                    results.append((scope, Handler(False, repr(e))))
                    self._log_error("配置变更处理失败 [%s]" % scope, e)
        return results

    # ---------------------------------------------------------- 内部
    def _log_error(self, title, exc):
        if self._log is None:
            return
        try:
            self._log("error", "config", "%s：%r" % (title, exc))
        except Exception:
            pass


def infer_scopes(changed_keys, surface=None):
    """从变更的键名推断 scope（**兼容层**，新代码请显式传 scopes）。

    ## 为什么保留它

    现有调用点很多（设置页、开发者页、插件、测试）。一次性全部改成
    显式声明风险太大，所以留一个推断函数给旧路径用，行为与以前
    一致；新代码一律显式传。

    ## 推断规则

        theme_* / accent_*              → THEME + SURFACE
        material_* / panel_alpha /
        image_opacity / crop_*          → SURFACE（含主窗口时也要重贴主题）
        hotkey                          → HOTKEY
        autostart / use_uia_elevation   → AUTOSTART
        font_* 或 ui_scale              → FONTS + SURFACE
        surface_overrides                → SURFACE

    ⚠ 推断的局限：`surface_overrides` 变了但**不知道是哪个界面**，
    只能按"全部界面都重绘"处理。所以外观页这类明确知道界面的调用方
    **必须**显式传 `surface=`，否则会顺带重绘无关界面。
    """
    keys = set(changed_keys or ())
    scopes = []

    def _add(s):
        if s not in scopes:
            scopes.append(s)

    theme_keys = {k for k in keys
                  if k.startswith(("theme_mode", "theme_preset",
                                   "accent_mode", "accent_custom"))}
    if theme_keys:
        _add(THEME)
        _add(SURFACE)

    surface_keys = {k for k in keys
                    if k.startswith(("material_", "panel_alpha",
                                     "image_opacity", "crop_",
                                     "surface_overrides"))}
    if surface_keys:
        _add(SURFACE)

    if "hotkey" in keys:
        _add(HOTKEY)
    if keys & {"autostart", "use_uia_elevation"}:
        _add(AUTOSTART)
    if keys & {"ui_scale", "font_scale"} or any(
            k.startswith("font_") for k in keys):
        _add(FONTS)
        _add(SURFACE)

    return scopes
