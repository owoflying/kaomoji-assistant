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
SUPPORTED_API_VERSION = 1

_ID_RE = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")

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


class PluginManager(QObject):
    """插件生命周期管理：发现 -> 校验 -> 加载 -> 注册能力 -> 事件分发。"""

    # 插件集合发生变化（启用/停用/重载/扫描），UI 侧（插件页/库页）据此刷新
    changed = Signal()

    def __init__(self, plugins_dir, config, save_config=None,
                 log=None, settings_dir=None, parent=None):
        super().__init__(parent)
        self.plugins_dir = plugins_dir
        self._config = config                      # 主配置的权威字典引用（只读写 plugins_enabled）
        self._save_config = save_config
        self._log_fn = log or (lambda *a, **k: None)
        self.settings_dir = settings_dir
        # plugin_id -> 记录 dict；见 discover()
        self.plugins = {}
        # 运行期能力注册表
        self._sources = {}        # plugin_id -> [{"name":..., "items":[...]}]
        self._transforms = []     # [{"plugin","fn","priority"}]
        self._listeners = {}      # event -> [{"plugin","fn"}]

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
        """回收某插件注册的全部能力（数据源/转换/监听器）。"""
        self._sources.pop(plugin_id, None)
        self._transforms = [t for t in self._transforms if t["plugin"] != plugin_id]
        for event in list(self._listeners):
            self._listeners[event] = [
                l for l in self._listeners[event] if l["plugin"] != plugin_id]
            if not self._listeners[event]:
                del self._listeners[event]

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
