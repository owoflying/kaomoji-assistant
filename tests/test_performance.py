"""性能回归：防止后续改动把启动时间又拖回去。

## 为什么要测

本轮把启动从 4.9 秒降到 2.4 秒，主要靠**页面懒加载**。
如果以后有人往 `_build_pages` 里加一行"顺手把某页也建出来"，
启动就会悄悄退回去，而且没人会注意到——除非有测试盯着。

## 测什么

1. 启动时**只建首页**（其余按需）
2. 懒加载页面访问时会被构造，且只构造一次
3. autostart 的 `schtasks` 查询有缓存（每次主题切换都起子进程是纯浪费）
"""
import os
import sys

import pytest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

pytestmark = pytest.mark.skipif(sys.platform != "win32",
                               reason="Qt 窗口测试在 Windows 上跑")


@pytest.fixture(scope="module")
def window():
    """一个真实的统一窗口（离屏）。"""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    import tempfile

    tmp = tempfile.mkdtemp(prefix="perf_test_")
    from core import runtime

    orig = runtime.app_data_dir
    runtime.app_data_dir = lambda: os.path.join(tmp, "KaomojiAssistant")
    try:
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance() or QApplication([])
        from ui.fluent_icons import ensure_icon_font

        ensure_icon_font()
        from core.config_store import load_config
        from ui.unified_window import UnifiedSettingsWindow

        cfg = load_config()
        cfg["developer_mode"] = False
        cfg["plugins_enabled"] = {}
        w = UnifiedSettingsWindow(
            _data(), cfg, _state(), _ukao(), _triggers(),
            save_config=lambda c: None, open_log=lambda: None)
        app.processEvents()
        yield w
    finally:
        runtime.app_data_dir = orig


def _data():
    from core.kaomoji_data import KaomojiData
    return KaomojiData()


def _state():
    from core.user_state import UserState
    return UserState(max_recent=30)


def _ukao():
    from core.user_kaomoji import UserKaomoji
    return UserKaomoji()


def _triggers():
    from core.user_triggers import UserTriggers
    return UserTriggers()


def test_only_home_is_built_at_startup(window):
    """启动时不能把所有页面都建出来。

    全量构造 8 个页面要 2.6~3.3 秒，是第一版启动慢的主因。
    """
    heavy = ("library", "custom", "triggers", "search", "settings", "about")
    built = [k for k in heavy if window.has_page(k)]
    assert built == [], "这些页面不该在启动时构造：%r" % built


def test_lazy_page_built_on_demand(window):
    """访问懒加载页面会把它建出来，且只建一次。"""
    assert not window.has_page("about")
    first = window.about_page
    assert first is not None
    assert window.has_page("about")
    # 再访问一次拿到同一个实例（不重复构造）
    assert window.about_page is first


def test_widget_count_stays_reasonable(window):
    """启动后窗口里的控件数要克制。

    实测每多一个控件，主题切换就多约 2.3ms（Qt 要重新解析样式）。
    524 个控件时切换一次要 1.2 秒。这个上限是为了防止无节制堆控件。
    """
    from PySide6.QtWidgets import QWidget

    n = len(window.findChildren(QWidget))
    assert n < 400, "窗口里控件太多（%d 个），主题切换会明显变慢" % n


def test_autostart_task_query_is_cached():
    """`task_exists()` 必须缓存，不能每次都起 schtasks 子进程。

    实测单次调用约 200ms，而刷新设置页会连着调好几次。
    """
    from core import autostart

    calls = []
    orig = autostart._run_schtasks

    def spy(args):
        calls.append(args)
        return False, ""

    autostart._run_schtasks = spy
    autostart._invalidate_task_cache()
    try:
        for _ in range(5):
            autostart.task_exists()
    finally:
        autostart._run_schtasks = orig
        autostart._invalidate_task_cache()

    assert len(calls) == 1, \
        "task_exists 应只真正查询一次，实际查了 %d 次" % len(calls)


def test_autostart_cache_invalidated_on_change():
    """改过计划任务后缓存要失效，否则界面显示的还是旧状态。"""
    from core import autostart

    autostart._invalidate_task_cache()
    # 直接操作缓存变量，验证失效函数有效
    autostart._task_exists_cache = True
    autostart._invalidate_task_cache()
    assert autostart._task_exists_cache is None
