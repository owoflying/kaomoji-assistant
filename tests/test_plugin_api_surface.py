"""插件 API 表面：防止废弃接口复活、防止新增能力漏登记。

## 为什么需要这个测试

`register_home_card` 曾经在代码里躺了很久：

* 它调用 `manager._add_home_card(...)` 等三个**不存在**的方法
* 一调就 AttributeError
* 而 `docs/PLUGIN_GUIDE.md` 的能力清单里**根本没它**——
  v2 用 `api.ui.insert("home.plugin_slot", ...)` 取代了

问题不是"少实现了一个方法"，而是**没有任何机制拦住这种漂移**：
删掉的东西不会自动消失，加新方法也没人提醒同步文档。

这个测试用 `PluginAPI.PUBLIC_METHODS` 做双向往返校验：

    清单 ⊆ 实际存在      防止登记了却不存在
    实际存在 ⊆ 清单      防止偷偷加方法不登记
    → 双侧相等

新增能力时必须同时改清单 + 文档，否则测试红。
"""
import pytest

from core.plugin_ui import PluginUi
from core.plugins import PluginAPI


def _public_names(cls):
    """类上所有公开（非下划线开头）的成员名。

    ⚠ `settings` / `id` / `dir` 是**实例属性**（在 `__init__` 里赋值），
    `dir(cls)` 看不到。它们同样是插件可见的能力，所以这里补上。
    """
    names = {n for n in dir(cls) if not n.startswith("_")}
    # 实例属性（PluginAPI.__init__ 里设置的公开字段）
    names |= {"settings", "id", "dir"}
    return names


def test_declared_methods_all_exist():
    """清单里登记的每个名字都必须真实存在。"""
    actual = _public_names(PluginAPI)
    missing = sorted(PluginAPI.PUBLIC_METHODS - actual)
    assert not missing, (
        "PUBLIC_METHODS 里登记了但 PluginAPI 上不存在：%r\n"
        "（要么是笔误，要么是方法被删了没同步清单）" % (missing,))


def test_no_undeclared_public_methods():
    """PluginAPI 上不能有清单之外的公开成员。

    这条挡住"顺手加个方法但不登记"——那样文档和清单都会慢慢失真。
    """
    actual = _public_names(PluginAPI)
    # 类属性本身不算对外能力
    allowed_extra = {"PUBLIC_METHODS"}
    undeclared = sorted(actual - PluginAPI.PUBLIC_METHODS - allowed_extra)
    assert not undeclared, (
        "PluginAPI 有未登记的公开成员：%r\n"
        "如果这是要提供给插件的新能力，请：\n"
        "  1. 加进 PUBLIC_METHODS\n"
        "  2. 在 docs/PLUGIN_GUIDE.md 的能力清单里写一行" % (undeclared,))


def test_removed_api_stays_removed():
    """已被删除的废弃接口不能复活。

    `register_home_card` 由 `api.ui.insert("home.plugin_slot", ...)` 取代。
    如果它回来了，说明有人在照着旧文档/旧代码"补实现"——
    那正是上一轮踩过的坑：补一个已废弃的接口比不补更糟，
    因为它会让插件作者以为有两条路。
    """
    removed = {"register_home_card", "unregister_home_card"}
    still_there = sorted(removed & _public_names(PluginAPI))
    assert not still_there, (
        "%r 已废弃（v2 用 api.ui 取代），不要恢复。\n"
        "往主页插卡片请用：api.ui.insert(\"home.plugin_slot\", widget)"
        % (still_there,))


def test_ui_has_ready_probe():
    """`api.ui.ready()` 必须存在——它解决 setup() 阶段的读时序问题。

    没有它的话，插件在 setup() 里调 `ids()` / `exists()` 会得到
    "元素不存在"的错误印象（写操作排队、读不排队）。
    """
    names = {n for n in dir(PluginUi) if not n.startswith("_")}
    assert "ready" in names, "PluginUi 缺少 ready()，插件无法判断界面是否就绪"


@pytest.mark.parametrize("event", [
    "startup", "shutdown", "page_changed", "theme_changed",
    "before_insert", "after_insert", "config_changed",
])
def test_documented_events_are_emitted(event):
    """文档里声明的事件，代码里必须有对应的 emit 调用。

    这条防止"文档写了但根本没发"——插件订阅了却永远等不到。
    """
    import io
    import os

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    hits = []
    for rel in ("app_runtime.py", "ui/unified_window.py",
                "ui/unified_nav.py", "core/plugins.py"):
        path = os.path.join(root, rel)
        if not os.path.isfile(path):
            continue
        try:
            src = io.open(path, encoding="utf-8", errors="replace").read()
        except Exception:
            continue
        if ('emit("%s"' % event) in src or ("emit('%s'" % event) in src:
            hits.append(rel)
    assert hits, "事件 %r 在文档里声明了，但没有任何地方 emit 它" % event
