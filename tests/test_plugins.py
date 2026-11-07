"""插件系统单测：能力注册、异常隔离、生命周期、UI 钩子注入。

重点覆盖文档中反复强调的几条不变式：
  * 单插件故障只标记该插件 error，不阻断主流程；
  * 清单级拒绝（api_version 过新等）不执行入口；
  * 数据源只进内存视图，`save()` 永不写插件数据；
  * `core` 不再反向依赖 `ui`（UI 能力经 `UiHooks` 注入，缺失时安全降级）。
"""
import os

import pytest

from core.plugins import (
    KNOWN_EVENTS, STATE_DISABLED, STATE_ERROR, STATE_LOADED, SUPPORTED_API_VERSION,
    PluginError, PluginManager, _validate_manifest,
)
from core.ui_hooks import NullUiHooks, UiHooks


# ---------------------------------------------------------------- 清单校验
def test_manifest_valid():
    ok = {"name": "x", "version": "1.0.0", "entry": "main.py", "api_version": 1}
    assert _validate_manifest(ok) is None


def test_manifest_missing_required_field():
    err = _validate_manifest({"version": "1.0.0", "entry": "main.py"})
    assert err and "name" in err


def test_manifest_not_a_dict():
    assert _validate_manifest(["nope"]) is not None


def test_manifest_api_version_too_new():
    bad = {"name": "x", "version": "1", "entry": "main.py",
           "api_version": SUPPORTED_API_VERSION + 1}
    err = _validate_manifest(bad)
    assert err and "升级主程序" in err


def test_manifest_api_version_not_integer():
    bad = {"name": "x", "version": "1", "entry": "main.py", "api_version": "二"}
    assert _validate_manifest(bad) is not None


def test_manifest_api_version_must_be_positive():
    bad = {"name": "x", "version": "1", "entry": "main.py", "api_version": 0}
    assert _validate_manifest(bad) is not None


# ---------------------------------------------------------------- 发现与加载
def test_discover_finds_bundled_plugins(plugin_manager):
    plugin_manager.discover()
    assert "example_kaomoji" in plugin_manager.plugins
    assert "startup_notice" in plugin_manager.plugins


def test_new_plugins_default_to_disabled(plugin_manager):
    plugin_manager.discover()
    assert plugin_manager.plugins["example_kaomoji"]["state"] == STATE_DISABLED
    assert plugin_manager.is_enabled("example_kaomoji") is False


def test_manifest_broken_plugin_marked_error(tmp_path, qapp, quiet_log):
    pdir = tmp_path / "plugins" / "broken"
    pdir.mkdir(parents=True)
    (pdir / "plugin.json").write_text("{ not json", encoding="utf-8")
    pm = PluginManager(plugins_dir=str(tmp_path / "plugins"),
                       config={"plugins_enabled": {}}, log=quiet_log)
    pm.discover()
    rec = pm.plugins["broken"]
    assert rec["state"] == STATE_ERROR
    assert rec["error_kind"] == "manifest"


def test_entry_escaping_plugin_dir_is_rejected(tmp_path, qapp, quiet_log):
    """manifest 写 `..` 越权加载必须被拦住（realpath 前缀校验）。"""
    pdir = tmp_path / "plugins" / "evil"
    pdir.mkdir(parents=True)
    (pdir / "plugin.json").write_text(
        '{"name":"evil","version":"1","entry":"../../outside.py"}',
        encoding="utf-8")
    (tmp_path / "outside.py").write_text("def setup(api): pass", encoding="utf-8")
    pm = PluginManager(plugins_dir=str(tmp_path / "plugins"),
                       config={"plugins_enabled": {}}, log=quiet_log)
    pm.discover()
    assert pm.load("evil") is False
    assert pm.plugins["evil"]["state"] == STATE_ERROR


def test_load_registers_capabilities(plugin_manager):
    plugin_manager.discover()
    assert plugin_manager.load("example_kaomoji") is True
    rec = plugin_manager.plugins["example_kaomoji"]
    assert rec["state"] == STATE_LOADED
    # 示例插件注册了数据源 / 转换 / 设置面板 / 独立页
    assert plugin_manager._sources.get("example_kaomoji")
    assert any(t["plugin"] == "example_kaomoji"
               for t in plugin_manager._transforms)
    assert "example_kaomoji" in plugin_manager.settings_widgets()
    assert "plugin:example_kaomoji:demo" in plugin_manager.plugin_pages()


def test_setup_error_isolated_and_rolls_back(tmp_path, qapp, quiet_log):
    pdir = tmp_path / "plugins" / "boom"
    pdir.mkdir(parents=True)
    (pdir / "plugin.json").write_text(
        '{"name":"boom","version":"1","entry":"main.py","api_version":2}',
        encoding="utf-8")
    # 先注册能力，再抛异常：半成品能力必须被 _detach 回收
    (pdir / "main.py").write_text(
        "def setup(api):\n"
        "    api.add_source('半成品', ['(x)'])\n"
        "    raise RuntimeError('boom')\n",
        encoding="utf-8")
    pm = PluginManager(plugins_dir=str(tmp_path / "plugins"),
                       config={"plugins_enabled": {}}, log=quiet_log)
    pm.discover()
    assert pm.load("boom") is False
    rec = pm.plugins["boom"]
    assert rec["state"] == STATE_ERROR
    assert rec["error_kind"] == "runtime"
    assert pm._sources.get("boom") is None


def test_transform_exception_does_not_break_insert(plugin_manager):
    plugin_manager.discover()
    plugin_manager.load("example_kaomoji")
    plugin_manager._add_transform("bad", lambda t, c: 1 / 0, "bad", 1)
    # 前一个插件抛异常不应影响返回一个可用字符串
    assert isinstance(plugin_manager.run_transforms("abc", {}), str)


def test_transform_priority_order(plugin_manager):
    plugin_manager._add_transform("p", lambda t, c: t + "B", "b", 200)
    plugin_manager._add_transform("p", lambda t, c: t + "A", "a", 100)
    assert plugin_manager.run_transforms("x", {}) == "xAB"


def test_event_listener_exception_isolated(plugin_manager):
    seen = []
    plugin_manager._add_listener("p", "startup", lambda: 1 / 0)
    plugin_manager._add_listener("p", "startup", lambda: seen.append("ok"))
    plugin_manager.emit("startup")
    assert seen == ["ok"]


def test_unknown_event_rejected(plugin_manager):
    plugin_manager.discover()
    plugin_manager.load("example_kaomoji")
    api = plugin_manager.plugins["example_kaomoji"]["api"]
    with pytest.raises(PluginError):
        api.on("not_an_event", lambda: None)
    assert "startup" in KNOWN_EVENTS


def test_unload_removes_capabilities(plugin_manager):
    plugin_manager.discover()
    plugin_manager.load("example_kaomoji")
    plugin_manager.unload("example_kaomoji")
    assert not plugin_manager._sources.get("example_kaomoji")
    assert plugin_manager.plugins["example_kaomoji"]["state"] == STATE_DISABLED
    assert not plugin_manager.plugin_pages()


def test_reload_does_not_duplicate_capabilities(plugin_manager):
    """重载后能力数量不变（重载会先 _detach 再重新注册）。"""
    plugin_manager.discover()
    # reload 走 is_enabled 分支，必须先在配置里标记为启用，否则重载后不加载
    plugin_manager._set_enabled_flag("example_kaomoji", True)
    assert plugin_manager.load("example_kaomoji") is True
    before_sources = len(plugin_manager._sources["example_kaomoji"])
    before_transforms = len(plugin_manager._transforms)

    plugin_manager.reload("example_kaomoji")

    assert plugin_manager.plugins["example_kaomoji"]["state"] == STATE_LOADED
    assert len(plugin_manager._sources["example_kaomoji"]) == before_sources
    assert len(plugin_manager._transforms) == before_transforms


def test_discover_keeps_module_and_api_of_loaded_plugin(plugin_manager):
    """discover 只重扫清单；已加载插件的 module/api 引用必须沿用。"""
    plugin_manager.discover()
    plugin_manager.load("example_kaomoji")
    api_before = plugin_manager.plugins["example_kaomoji"]["api"]
    plugin_manager.discover()
    assert plugin_manager.plugins["example_kaomoji"]["api"] is api_before


def test_removed_plugin_directory_detaches_capabilities(tmp_path, qapp, quiet_log):
    """目录被删掉后能力必须回收，否则数据源/转换仍在生效。"""
    src = tmp_path / "plugins"
    pdir = src / "tmp_plugin"
    pdir.mkdir(parents=True)
    (pdir / "plugin.json").write_text(
        '{"name":"t","version":"1","entry":"main.py"}', encoding="utf-8")
    (pdir / "main.py").write_text(
        "def setup(api):\n    api.add_source('测试', ['(a)'])\n", encoding="utf-8")
    pm = PluginManager(plugins_dir=str(src), config={"plugins_enabled": {}},
                       log=quiet_log)
    pm.discover()
    assert pm.load("tmp_plugin") is True
    # 删目录后重扫
    (pdir / "plugin.json").unlink()
    (pdir / "main.py").unlink()
    pm.discover()
    assert "tmp_plugin" not in pm.plugins
    assert not pm._sources.get("tmp_plugin")


# ---------------------------------------------------------------- UI 钩子分层
def test_core_does_not_import_ui():
    """分层不变式：core/ 下任何模块都不得 import ui.*（依赖方向只能是 ui → core）。"""
    import core as core_pkg
    core_dir = os.path.dirname(os.path.abspath(core_pkg.__file__))
    offenders = []
    for name in os.listdir(core_dir):
        if not name.endswith(".py"):
            continue
        with open(os.path.join(core_dir, name), encoding="utf-8") as f:
            for lineno, line in enumerate(f, 1):
                s = line.strip()
                if s.startswith(("from ui.", "from ui ", "import ui.")):
                    offenders.append("%s:%d" % (name, lineno))
    assert offenders == [], "core 层反向依赖了 ui：%s" % offenders


def test_null_ui_hooks_degrade_gracefully(plugin_manager):
    """没注入 UI 实现时（离屏/纯 core 场景）插件 UI 能力必须安全降级。"""
    plugin_manager._ui_hooks = NullUiHooks()
    assert plugin_manager._theme_tokens() == {}
    assert plugin_manager._open_message("p", "hi", "t", "ok", None) is False
    assert plugin_manager._notify("t", "m", 1000) is False
    assert plugin_manager._open_dialog("p", None, "t", 0, 0, False) is None


def test_ui_hooks_can_be_partially_injected():
    """只注入 notify 时，其它能力应保持安全降级而不是崩溃。"""
    calls = []
    hooks = UiHooks(notify=lambda t, m, ms: calls.append((t, m)) or True)
    assert hooks.notify("标题", "内容", 1000) is True
    assert calls == [("标题", "内容")]
    assert hooks.theme_tokens() == {}
    assert hooks.show_message("x", "t", "ok", None) is False


def test_ui_hook_exception_is_swallowed(plugin_manager):
    def _boom(*a, **k):
        raise RuntimeError("UI 挂了")
    plugin_manager._ui_hooks = UiHooks(notify=_boom)
    assert plugin_manager._notify("t", "m", 1) is False


# ---------------------------------------------------------------- 耗时告警
def test_slow_transform_emits_warning(plugin_manager, quiet_log):
    import time

    def _slow(text, ctx):
        time.sleep(0.08)
        return text

    plugin_manager._add_transform("slowp", _slow, "slow", 10)
    plugin_manager.run_transforms("x", {})
    assert any("耗时" in m for _lvl, _src, m in quiet_log.records)


def test_fast_transform_does_not_warn(plugin_manager, quiet_log):
    plugin_manager._add_transform("fastp", lambda t, c: t, "fast", 10)
    plugin_manager.run_transforms("x", {})
    assert not any("耗时" in m for _lvl, _src, m in quiet_log.records)
