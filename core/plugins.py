"""插件系统：目录发现 + JSON 清单 + Python 入口加载 + 能力注册。

目录布局（每个插件一个子目录）：
    <plugins_dir>/<plugin_id>/
        plugin.json     # 清单：name/version/entry/author/description/api_version
        main.py         # 入口：必须提供 setup(api) 函数

plugin.json 示例：
    {
        "name": "示例颜文字包",
        "version": "1.0.0",
        "entry": "main.py",
        "author": "someone",
        "description": "追加一个「示例」分类",
        "api_version": 1
    }

入口 main.py 示例：
    def setup(api):
        api.add_source("示例", ["(￣▽￣)-c", "ヽ(°〇°)ﾉ"])
        api.register_transform(lambda text, ctx: text + " ★", priority=100)
        api.on("after_insert", lambda text, ctx: api.log("已插入"))

API 版本：v1 提供数据/转换/事件/配置；**v2 追加 UI 扩展能力**
（register_settings_widget / register_page / show_dialog / show_message / ask /
notify / theme_tokens）。UI 系列方法会校验清单里的 api_version，v1 插件调用会
直接抛 PluginError（而不是静默失效）。

UI 扩展的所有权约定（重要）：**manager 只登记 factory 与元信息，不持有 QWidget**。
widget 实例由 UI 宿主（PluginPage / UnifiedSettingsWindow / PluginDialog）创建、
插入布局并在插件卸载时销毁；manager 只负责在注册/卸载时 emit ui_changed 通知宿主
重新同步。这样避免了 core 层持有 Qt 对象导致的生命周期悬垂。

设计要点（安全与健壮性）：
- **异常隔离**：插件的加载、每次回调都在 try/except 中执行，任何插件抛异常
  只会标记该插件为 error 状态并记日志，绝不影响主程序与其它插件。
- **主线程执行**：所有插件回调都在 Qt 主线程同步调用，插件可以安全操作 UI
  （但耗时操作会卡界面，插件约定：setup 与 transform 必须毫秒级返回）。
- **进程内加载**：importlib 直接执行插件代码，与主程序同权限（本机桌面应用的
  常规取舍，等同 VS Code/浏览器扩展的信任模型——插件代码即用户自己安装的代码）。
- **数据不落盘**：插件提供的颜文字只合并进 KaomojiData 的内存视图（external），
  save() 永远只写内置库，插件不会污染随包分发的 kaomoji.json。
- **启用状态持久化**：存放在主配置 config["plugins_enabled"]（dict: id -> bool），
  由 main.save_config 统一落盘。
"""
import importlib.util
import json
import os
import re
import sys

from PySide6.QtCore import QObject, Signal

# 当前支持的插件 API 版本；清单里声明更高版本时拒绝加载（提示升级主程序）
# v1：数据源 / 转换 / 事件 / 配置；v2：追加 UI 扩展（设置面板 / 独立页 / 弹窗 / 通知）
SUPPORTED_API_VERSION = 2

# UI 扩展能力所需的最低 API 版本
_MIN_API_FOR_UI = 2

_ID_RE = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")

# 插件页 id 约束（用于侧边栏 key，避免与内置页 key 冲突时被误解析）
_PAGE_ID_RE = re.compile(r"^[A-Za-z0-9_\-]{1,32}$")

# 单个插件允许注册的侧边栏页数上限（防止恶意插件刷爆侧边栏）
_MAX_PLUGIN_PAGES = 8

# 清单必填字段；缺任一即判定清单无效
_MANIFEST_REQUIRED = ("name", "version", "entry")

# 允许订阅的事件；on() 里做白名单校验，防止插件拼写错误后静默失效
KNOWN_EVENTS = ("startup", "shutdown", "before_insert", "after_insert", "config_changed")


class PluginError(Exception):
    """插件加载/校验失败。message 面向用户展示（会出现在插件页错误区）。"""


def _validate_manifest(manifest):
    """校验清单字段，返回 (error_message or None)。"""
    if not isinstance(manifest, dict):
        return "plugin.json 不是 JSON 对象"
    for key in _MANIFEST_REQUIRED:
        val = manifest.get(key)
        if not isinstance(val, str) or not val.strip():
            return "plugin.json 缺少必填字段 %r" % key
    if not isinstance(manifest.get("description", ""), str):
        return "plugin.json 的 description 必须是字符串"
    try:
        api_ver = int(manifest.get("api_version", 1))
    except (TypeError, ValueError):
        return "plugin.json 的 api_version 必须是整数"
    if api_ver > SUPPORTED_API_VERSION:
        return ("插件 API 版本 v%d 过新（本程序支持 v%d），请升级主程序"
                % (api_ver, SUPPORTED_API_VERSION))
    if api_ver < 1:
        return "plugin.json 的 api_version 必须 >= 1"
    return None


class PluginAPI:
    """提供给单个插件的能力接口（实例按插件隔离）。

    插件入口 setup(api) 拿到的就是它；禁止插件访问 manager 内部状态，
    所有能力都收敛在这几个方法里，方便审计与后续加权限。
    """

    def __init__(self, manager, plugin_id, manifest, plugin_dir, settings):
        self._manager = manager
        self.id = plugin_id
        self._manifest = dict(manifest)
        self.dir = plugin_dir
        # 插件私有配置（从 <settings_dir>/<id>.json 载入的活字典，可原地改）
        self.settings = settings if isinstance(settings, dict) else {}

    # ---- 元信息 ----
    @property
    def name(self):
        return self._manifest.get("name", self.id)

    @property
    def version(self):
        return self._manifest.get("version", "0.0.0")

    @property
    def manifest(self):
        return dict(self._manifest)

    # ---- 能力 1：日志 ----
    def log(self, message, level="info"):
        """写一条插件日志到主程序日志缓冲（托盘「查看日志」可见）。"""
        self._manager._log(level, "plugin:%s" % self.id, str(message))

    # ---- 能力 2：数据源（追加颜文字分类） ----
    def add_source(self, category, items):
        """追加一个颜文字分类；重复调用同一分类名会合并条目（自动去重）。"""
        self._manager._add_source(self.id, category, items)

    # ---- 能力 3：输出转换 ----
    def register_transform(self, fn, name="", priority=100):
        """注册颜文字上屏前的改写函数 fn(text, ctx) -> str。

        priority 小者先执行（同一插件内多次注册按 priority 稳定排序）。
        返回非空字符串时作为新的上屏文本；返回 None/非字符串时忽略本次结果。
        """
        if not callable(fn):
            raise PluginError("register_transform 需要可调用对象")
        self._manager._add_transform(self.id, fn, str(name), int(priority))

    # ---- 能力 4：事件订阅 ----
    def on(self, event, fn):
        """订阅生命周期事件；event 必须是 KNOWN_EVENTS 之一。

        事件签名：
            startup()                      所有插件加载完成后（仅启动时一次）
            shutdown()                     主程序退出前
            before_insert(text, ctx)       转换完成、注入前（text 为最终文本）
            after_insert(text, ctx)        注入调度后
            config_changed(config)         设置被应用并保存后
        """
        if event not in KNOWN_EVENTS:
            raise PluginError("未知事件 %r（可用：%s）" % (event, ", ".join(KNOWN_EVENTS)))
        if not callable(fn):
            raise PluginError("on() 需要可调用对象")
        self._manager._add_listener(self.id, event, fn)

    # ---- 能力 5：读主配置 / 私有配置 ----
    def get_config(self, key, default=None):
        """只读访问主配置（主题、热键等）；修改请走设置页，这里不提供写入口。"""
        return self._manager._config_get(key, default)

    def save_settings(self):
        """把 self.settings 持久化到 <settings_dir>/<id>.json。"""
        self._manager._save_settings(self.id, self.settings)

    # ------------------------------------------------------------------
    # 以下为 UI 扩展能力（需要清单声明 api_version >= 2）
    # ------------------------------------------------------------------
    def _require_ui(self):
        ver = int(self._manifest.get("api_version", 1) or 1)
        if ver < _MIN_API_FOR_UI:
            raise PluginError(
                "UI 扩展需要插件声明 api_version >= 2（当前 %d）；"
                "请在 plugin.json 中加入 \"api_version\": 2" % ver)

    # ---- 能力 6：卡片内嵌设置面板 ----
    def register_settings_widget(self, factory, title="插件设置"):
        """在插件页自己的卡片下方嵌入一块设置区。

        factory(parent) -> QWidget；首次展开时才调用（懒加载），同一实例会被复用
        （不会因插件页刷新丢失未保存的输入）。factory 抛异常时卡片内显示错误占位，
        不影响主程序与其它插件。
        """
        self._require_ui()
        if not callable(factory):
            raise PluginError("register_settings_widget 需要 factory(parent) 可调用对象")
        key = self._manager._add_settings_widget(
            self.id, factory, str(title or "插件设置"))
        return key

    # ---- 能力 7：侧边栏独立页 ----
    def register_page(self, page_id, title, factory, icon=None):
        """往主窗口侧边栏注册一个插件自己的页面。

        factory(parent) -> QWidget。页面由统一窗口托管（加入 QStackedWidget），
        插件卸载/停用时自动移除并销毁。返回侧边栏 key（形如 plugin:<id>:<page_id>）。
        """
        self._require_ui()
        if not callable(factory):
            raise PluginError("register_page 需要 factory(parent) 可调用对象")
        pid = str(page_id or "").strip()
        if not _PAGE_ID_RE.match(pid):
            raise PluginError("page_id 只能含字母/数字/下划线/连字符（最长 32）")
        return self._manager._add_page(
            self.id, pid, str(title or pid), factory, icon)

    # ---- 能力 8：主题化弹窗 / 消息框 ----
    def show_dialog(self, factory=None, title="", width=0, height=0, modal=True):
        """弹出一个与主窗口同风格（无边框+圆角+亚克力色）的对话框。

        factory(parent) -> QWidget 提供内容；modal=True 时同步阻塞，返回 1/0。
        """
        self._require_ui()
        if factory is not None and not callable(factory):
            raise PluginError("show_dialog 的 factory 必须是可调用对象或 None")
        return self._manager._open_dialog(
            self.id, factory, str(title or self.name),
            int(width), int(height), bool(modal))

    def show_message(self, text, title="提示", ok_text="确定", cancel_text=None):
        """轻量消息框。带 cancel_text 时返回 True/False，否则恒 True。"""
        self._require_ui()
        return self._manager._open_message(
            self.id, str(text or ""), str(title or self.name),
            str(ok_text or "确定"),
            str(cancel_text) if cancel_text else None)

    def ask(self, text, title="确认", yes_text="确定", no_text="取消"):
        """是/否 询问框，返回 bool。"""
        self._require_ui()
        return self._manager._open_message(
            self.id, str(text or ""), str(title or "确认"),
            str(yes_text or "确定"), str(no_text or "取消"))

    # ---- 能力 9：系统托盘通知 ----
    def notify(self, title, message, ms=4000):
        """发一条托盘气泡通知；托盘不可用时静默失败。"""
        self._require_ui()
        return self._manager._notify(
            str(title or self.name), str(message or ""), int(ms))

    # ---- 能力 10：主题令牌（供插件自着色） ----
    def theme_tokens(self):
        """返回当前主题的只读颜色令牌字典（dark/text/accent/card 等）。

        插件自建控件若想跟随深浅色，可读取这里的色值自行 setStyleSheet；
        更省事的做法是什么都不做——宿主会把完整主题 QSS 垫在插件 widget 上。
        """
        return self._manager._theme_tokens()


class PluginManager(QObject):
    """插件生命周期管理：发现 -> 校验 -> 加载 -> 注册能力 -> 事件分发。"""

    # 插件集合发生变化（启用/停用/重载/扫描），UI 侧（插件页/库页）据此刷新
    changed = Signal()
    # UI 登记项变化（插件注册了设置面板 / 独立页，或被卸载），
    # 宿主（PluginPage / UnifiedSettingsWindow）据此增删对应 widget。
    ui_changed = Signal()

    def __init__(self, plugins_dir, config, save_config=None,
                 log=None, settings_dir=None, theme_provider=None, parent=None):
        super().__init__(parent)
        self.plugins_dir = plugins_dir
        self._config = config                      # 主配置的权威字典引用（只读写 plugins_enabled）
        self._save_config = save_config
        self._log_fn = log or (lambda *a, **k: None)
        self.settings_dir = settings_dir
        # 当前主题名提供者（由 main 注入，避免 core 反向依赖 ui.win11_theme）
        self._theme_provider = theme_provider
        # plugin_id -> 记录 dict；见 discover()
        self.plugins = {}
        # 运行期能力注册表
        self._sources = {}        # plugin_id -> [{"name":..., "items":[...]}]
        self._transforms = []     # [{"plugin","fn","priority"}]
        self._listeners = {}      # event -> [{"plugin","fn"}]
        # UI 注册表：只登记 spec/factory，widget 实例由 UI 宿主持有
        self._settings_widgets = {}   # plugin_id -> {"factory","title"}
        self._plugin_pages = {}       # page_key -> {"plugin","page_id","title","factory","icon"}
        self._dialogs = {}            # plugin_id -> [PluginDialog]
        self._notify_fn = None        # 托盘气泡回调（main 注入）
        self._theme_override = None   # UI 侧显式同步的主题名（优先于 provider）

    # ---------- 提供给外部（main）的 UI 钩子 ----------
    def set_ui_hooks(self, notify=None):
        """注入 UI 相关回调（目前只有托盘通知）。

        托盘在 main 里比 PluginManager 晚创建，因此用后置注入而不是构造参数。
        """
        if notify is not None:
            self._notify_fn = notify

    def set_theme_name(self, name):
        """显式同步当前主题名（已解析过的 light/dark，不含 "system"）。

        theme_provider 是启动时的闭包，若主程序的 config 字典在设置应用时被整体
        替换，闭包可能读到旧值；UI 侧每次应用新主题时再显式同步一次，双保险。
        """
        if isinstance(name, str) and name and name != "system":
            self._theme_override = name

    def current_theme_name(self):
        if getattr(self, "_theme_override", None):
            return self._theme_override
        try:
            name = self._theme_provider() if callable(self._theme_provider) else None
        except Exception:
            name = None
        return name or "light"

    # ---------- 日志 ----------
    def _log(self, level, source, message):
        try:
            self._log_fn(level, source, message)
        except Exception:
            pass

    # ---------- 配置读写 ----------
    def _config_get(self, key, default=None):
        try:
            return self._config.get(key, default)
        except Exception:
            return default

    def _enabled_map(self):
        val = self._config.get("plugins_enabled") if isinstance(self._config, dict) else None
        return val if isinstance(val, dict) else {}

    def is_enabled(self, plugin_id):
        return bool(self._enabled_map().get(plugin_id, False))

    def _set_enabled_flag(self, plugin_id, enabled):
        # 拷贝一份再写回：settings_page._on_apply 会整体替换 config 字典，
        # 直接持有子 dict 引用可能指向已被丢弃的旧字典。
        m = dict(self._enabled_map())
        m[plugin_id] = bool(enabled)
        self._config["plugins_enabled"] = m
        if callable(self._save_config):
            try:
                self._save_config(self._config)
            except Exception as e:
                self._log("warn", "plugins", "保存插件启用状态失败：%r" % (e,))

    # ---------- 插件私有配置 ----------
    def _settings_path(self, plugin_id):
        if not self.settings_dir:
            return None
        return os.path.join(self.settings_dir, "%s.json" % plugin_id)

    def _load_settings(self, plugin_id):
        path = self._settings_path(plugin_id)
        if not path or not os.path.isfile(path):
            return {}
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _save_settings(self, plugin_id, settings):
        path = self._settings_path(plugin_id)
        if not path:
            return
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(settings, f, ensure_ascii=False, indent=2)
        except Exception as e:
            self._log("warn", "plugins", "保存插件 %s 配置失败：%r" % (plugin_id, e))

    # ---------- 发现 ----------
    def discover(self):
        """扫描插件目录，重建 self.plugins（已加载的保持原状态，不在此处加载）。"""
        found = {}
        old = self.plugins
        try:
            entries = sorted(os.listdir(self.plugins_dir))
        except OSError:
            entries = []
        for name in entries:
            pdir = os.path.join(self.plugins_dir, name)
            manifest_path = os.path.join(pdir, "plugin.json")
            if not os.path.isdir(pdir) or not os.path.isfile(manifest_path):
                continue
            if not _ID_RE.match(name):
                self._log("warn", "plugins", "跳过目录 %s：目录名只能含字母/数字/下划线/连字符" % name)
                continue
            try:
                with open(manifest_path, "r", encoding="utf-8") as f:
                    manifest = json.load(f)
            except Exception as e:
                found[name] = self._record(name, pdir, None, "error",
                                           "plugin.json 解析失败：%s" % e)
                continue
            err = _validate_manifest(manifest)
            if err:
                found[name] = self._record(name, pdir, manifest, "error", err)
                continue
            # 新发现的插件默认停用；已存在的保留原状态
            prev = old.get(name)
            rec = self._record(
                name, pdir, manifest,
                prev["state"] if prev else "disabled",
                prev["error"] if prev else None)
            found[name] = rec
        # 已被删除的插件目录：从注册表中移除（unload 由调用方按需处理）
        self.plugins = found

    def _record(self, plugin_id, pdir, manifest, state, error):
        return {
            "id": plugin_id,
            "dir": pdir,
            "manifest": manifest or {},
            "state": state,        # disabled / loaded / error
            "error": error,
            "module": None,
            "api": None,
        }

    # ---------- 加载 / 卸载 ----------
    def load(self, plugin_id):
        """加载并执行一个插件；返回是否成功。失败时记录 state=error。"""
        rec = self.plugins.get(plugin_id)
        if rec is None:
            return False
        if rec["state"] == "loaded":
            return True
        if rec["state"] == "error" and rec["error"]:
            # 清单级拒绝（字段缺失 / api_version 过新 / JSON 解析失败）：
            # 不尝试执行入口，直接按失败返回（错误信息保留在 rec["error"]）
            self._log("warn", "plugins", "插件 %s 已被清单校验拒绝：%s"
                      % (plugin_id, rec["error"]))
            self.changed.emit()
            return False
        # 先卸载旧能力，避免重载时重复注册
        self._detach(plugin_id)
        try:
            module, api = self._exec_plugin(rec)
        except Exception as e:
            rec["state"] = "error"
            rec["error"] = "%s: %s" % (type(e).__name__, e)
            rec["module"] = None
            rec["api"] = None
            # setup 可能执行到一半才抛异常：此时数据源/转换/监听器/UI 登记都可能有
            # 半成品，必须回收，否则脏能力会一直留在注册表里（下次重载还会叠加）。
            self._detach(plugin_id)
            self._log("error", "plugins", "插件 %s 加载失败：%s" % (plugin_id, rec["error"]))
            self.changed.emit()
            return False
        rec["state"] = "loaded"
        rec["error"] = None
        rec["module"] = module
        rec["api"] = api
        self._log("info", "plugins",
                  "插件 %s v%s 已加载（来源分类 %d 个，转换 %d 个）"
                  % (plugin_id, rec["manifest"].get("version", "?"),
                     len(self._sources.get(plugin_id, [])),
                     sum(1 for t in self._transforms if t["plugin"] == plugin_id)))
        self.changed.emit()
        # setup 执行期间注册 UI 能力时会 emit ui_changed，但那时 rec["state"] 还是
        # disabled，宿主的 plugin_pages()/settings_widgets() 按「已加载」过滤会把它们
        # 滤掉（表现为「新启用插件的独立页/设置面板不出现，重载一次才出现」）。
        # 这里在置为 loaded 之后再补发一次，保证宿主能看到完整的 UI 登记。
        self.ui_changed.emit()
        return True

    def unload(self, plugin_id):
        """卸载插件：回收其注册的全部能力，并从 sys.modules 摘除模块。"""
        rec = self.plugins.get(plugin_id)
        self._detach(plugin_id)
        if rec is not None:
            mod = rec.get("module")
            if mod is not None:
                sys.modules.pop(getattr(mod, "__name__", None), None)
            rec["module"] = None
            rec["api"] = None
            rec["state"] = "disabled"
            rec["error"] = None
        self.changed.emit()

    def reload(self, plugin_id):
        """重载插件（重扫清单 -> 卸载 -> 按启用状态重新加载）。"""
        rec = self.plugins.get(plugin_id)
        enabled = self.is_enabled(plugin_id)
        self.unload(plugin_id)
        self.discover()
        self._set_enabled_flag(plugin_id, enabled)
        if enabled:
            return self.load(plugin_id)
        self.changed.emit()
        return True

    def load_all(self):
        """按目录名顺序加载全部已启用插件；返回成功数。"""
        ok = 0
        for plugin_id in sorted(self.plugins):
            if self.is_enabled(plugin_id):
                if self.load(plugin_id):
                    ok += 1
        return ok

    def set_enabled(self, plugin_id, enabled):
        """启用/停用插件：写配置、加载/卸载、合并数据源，并广播 changed。"""
        self._set_enabled_flag(plugin_id, enabled)
        if enabled:
            self.load(plugin_id)     # 失败会记 state=error，UI 上直接可见
        else:
            self.unload(plugin_id)

    def _exec_plugin(self, rec):
        manifest = rec["manifest"]
        entry = os.path.join(rec["dir"], manifest.get("entry", "main.py"))
        # 入口路径必须仍在插件目录内，防止 manifest 写 "..\\..\\x.py" 越权加载
        real_entry = os.path.realpath(entry)
        real_dir = os.path.realpath(rec["dir"]) + os.sep
        if not real_entry.startswith(real_dir):
            raise PluginError("entry 必须位于插件目录内")
        if not os.path.isfile(real_entry):
            raise PluginError("入口文件不存在：%s" % manifest.get("entry", "main.py"))
        modname = "kaomoji_plugin_%s" % rec["id"]
        spec = importlib.util.spec_from_file_location(modname, real_entry)
        if spec is None or spec.loader is None:
            raise PluginError("无法创建模块加载器（entry=%s）" % manifest.get("entry"))
        module = importlib.util.module_from_spec(spec)
        # 先注册再执行：插件内部 import 自身辅助文件 / 相互引用时可用
        sys.modules[spec.name] = module
        try:
            spec.loader.exec_module(module)
            setup = getattr(module, "setup", None)
            if not callable(setup):
                raise PluginError("入口缺少 setup(api) 函数")
            api = PluginAPI(self, rec["id"], manifest, rec["dir"],
                            self._load_settings(rec["id"]))
            setup(api)
        except Exception:
            sys.modules.pop(spec.name, None)
            raise
        return module, api

    def _detach(self, plugin_id):
        """回收某插件注册的全部能力（数据源/转换/监听器/UI 登记/弹窗）。

        UI widget 本身不由 manager 持有（宿主持有并负责从布局摘除），这里只撤销
        spec 登记并 emit ui_changed，宿主收到后会发现自己的 widget 已不在注册表里
        从而销毁它——避免 manager 直接动 Qt 对象导致的悬垂引用。
        """
        self._sources.pop(plugin_id, None)
        self._transforms = [t for t in self._transforms if t["plugin"] != plugin_id]
        for event in list(self._listeners):
            self._listeners[event] = [
                l for l in self._listeners[event] if l["plugin"] != plugin_id]
            if not self._listeners[event]:
                del self._listeners[event]
        had_settings = self._settings_widgets.pop(plugin_id, None) is not None
        killed_pages = [k for k, v in self._plugin_pages.items()
                        if v["plugin"] == plugin_id]
        for k in killed_pages:
            del self._plugin_pages[k]
        # 该插件开着的弹窗必须关掉，否则插件已卸载而窗口还在，回调会打到空气上
        self.close_plugin_dialogs(plugin_id)
        if had_settings or killed_pages:
            self.ui_changed.emit()
        # 该插件开着的弹窗必须关掉，否则插件已卸载而窗口还在，回调会打到空气上
        self.close_plugin_dialogs(plugin_id)
        if had_settings or killed_pages:
            self.ui_changed.emit()

    # ---------- 能力注册（由 PluginAPI 调用） ----------
    def _add_source(self, plugin_id, category, items):
        name = str(category or "").strip()
        if not name or not items:
            return
        bucket = self._sources.setdefault(plugin_id, [])
        clean = [str(x) for x in items if isinstance(x, (str, int))]
        for cat in bucket:
            if cat["name"] == name:
                for it in clean:
                    if it not in cat["items"]:
                        cat["items"].append(it)
                return
        bucket.append({"name": name, "items": list(clean)})

    def _add_transform(self, plugin_id, fn, name, priority):
        self._transforms.append(
            {"plugin": plugin_id, "fn": fn, "name": name, "priority": priority})
        self._transforms.sort(key=lambda t: (t["priority"], t["plugin"]))

    def _add_listener(self, plugin_id, event, fn):
        self._listeners.setdefault(event, []).append({"plugin": plugin_id, "fn": fn})

    # ---------- UI 能力登记（由 PluginAPI 调用） ----------
    def _add_settings_widget(self, plugin_id, factory, title):
        self._settings_widgets[plugin_id] = {"factory": factory, "title": title}
        self.ui_changed.emit()
        return "%s:settings" % plugin_id

    def _add_page(self, plugin_id, page_id, title, factory, icon=None):
        owned = sum(1 for v in self._plugin_pages.values() if v["plugin"] == plugin_id)
        if owned >= _MAX_PLUGIN_PAGES:
            raise PluginError("单个插件最多注册 %d 个侧边栏页" % _MAX_PLUGIN_PAGES)
        key = "plugin:%s:%s" % (plugin_id, page_id)
        self._plugin_pages[key] = {
            "plugin": plugin_id, "page_id": page_id, "title": title,
            "factory": factory, "icon": icon,
        }
        self.ui_changed.emit()
        return key

    # ---------- UI 注册表的只读视图（供宿主同步） ----------
    def settings_widgets(self):
        """{plugin_id: {"factory","title"}}（只含当前已加载插件的）。"""
        return {pid: spec for pid, spec in self._settings_widgets.items()
                if self._is_loaded(pid)}

    def plugin_pages(self):
        """{page_key: spec}（只含当前已加载插件的，按插件 id 与页 id 排序）。"""
        items = [(k, v) for k, v in self._plugin_pages.items()
                 if self._is_loaded(v["plugin"])]
        return dict(sorted(items, key=lambda kv: (kv[1]["plugin"], kv[1]["page_id"])))

    def _is_loaded(self, plugin_id):
        rec = self.plugins.get(plugin_id)
        return bool(rec and rec.get("state") == "loaded")

    # ---------- 弹窗 / 消息框 / 通知 ----------
    def _open_dialog(self, plugin_id, factory, title, width, height, modal):
        # 延迟 import：core 层不在模块顶层依赖 ui.*（保持分层，也利于离屏测试）
        from ui.plugin_dialog import show_plugin_dialog
        from ui.win11_theme import Theme, apply_child_theme

        theme = Theme(self.current_theme_name())
        body = None
        if factory is not None:
            body = factory(None)
            # 插件自建 body 若带样式表会切断祖先级联，这里把完整主题 QSS 垫在前
            apply_child_theme(body, theme)
        dlg = show_plugin_dialog(body, title, theme, None, width, height, modal)
        self._dialogs.setdefault(plugin_id, []).append(dlg)
        return dlg.result() if modal else dlg

    def _open_message(self, plugin_id, text, title, ok_text, cancel_text):
        from ui.plugin_dialog import show_message
        from ui.win11_theme import Theme

        return show_message(text, title, Theme(self.current_theme_name()),
                            None, ok_text, cancel_text)

    def _notify(self, title, message, ms):
        fn = self._notify_fn
        if not callable(fn):
            return False
        try:
            fn(title, message, ms)
            return True
        except Exception as e:
            self._log("warn", "plugins", "托盘通知失败：%r" % (e,))
            return False

    def _theme_tokens(self):
        from ui.win11_theme import Theme

        t = Theme(self.current_theme_name())
        return {
            "dark": t.dark, "name": t.name,
            "text": t.text, "text_secondary": t.text_secondary,
            "text_tertiary": t.text_tertiary,
            "accent": t.accent, "accent_hover": t.accent_hover,
            "card": t.card, "card_hover": t.card_hover,
            "card_border": t.card_border, "divider": t.divider,
            "input_bg": t.input_bg, "input_border": t.input_border,
            "nav_hover": t.nav_hover, "nav_selected": t.nav_selected,
        }

    def close_plugin_dialogs(self, plugin_id=None):
        """关闭插件弹窗；plugin_id 为 None 时关闭全部。"""
        targets = [plugin_id] if plugin_id else list(self._dialogs)
        for pid in targets:
            for dlg in list(self._dialogs.pop(pid, [])):
                try:
                    dlg.close()
                    dlg.deleteLater()
                except RuntimeError:
                    pass    # C++ 对象已销毁

    # ---------- 数据源合并 ----------
    def refresh_sources(self, data):
        """把当前所有已加载插件的数据源合并进 KaomojiData 的外部分类视图。"""
        cats = []
        for plugin_id in sorted(self._sources):
            cats.extend(self._sources[plugin_id])
        try:
            data.set_external_categories(cats)
        except Exception as e:
            self._log("warn", "plugins", "合并插件数据源失败：%r" % (e,))

    # ---------- 转换与事件 ----------
    def run_transforms(self, text, ctx=None):
        """按优先级把文本依次交给各插件转换；任何异常只记日志不阻断注入。"""
        for t in self._transforms:
            try:
                out = t["fn"](text, ctx if ctx is not None else {})
                if isinstance(out, str) and out:
                    text = out
            except Exception as e:
                self._log("error", "plugin:%s" % t["plugin"],
                          "转换函数 %s 抛异常：%s: %s" % (t["name"] or "<anon>",
                                                     type(e).__name__, e))
        return text

    def emit(self, event, *args):
        """同步分发事件给所有订阅者；单个订阅者异常不影响其它订阅者。"""
        for l in list(self._listeners.get(event, [])):
            try:
                l["fn"](*args)
            except Exception as e:
                self._log("error", "plugin:%s" % l["plugin"],
                          "事件 %s 处理异常：%s: %s" % (event, type(e).__name__, e))

    # ---------- 统计（供主页/开发者页展示） ----------
    def stats(self):
        loaded = [r for r in self.plugins.values() if r["state"] == "loaded"]
        return {
            "total": len(self.plugins),
            "loaded": len(loaded),
            "sources": sum(len(v) for v in self._sources.values()),
            "transforms": len(self._transforms),
        }
