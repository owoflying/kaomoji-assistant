"""pytest 公共夹具。

**铁律：测试绝不写真实磁盘。** 本程序的 `save_config` / `apply_settings` /
`UserState` 等都会真实落盘，而源码态的默认路径就是**项目目录**——顺手
`AppRuntime(app, load_config())` 再 `apply_settings(...)`，就会把开发者的
`config.json` 改掉（真实踩过：一次冒烟测试把 theme 刷成了 dark）。

因此这里统一：
  * 把 `APPDATA` 指向临时目录（冻结态路径落进来）；
  * 需要 `QApplication` 时用 `QT_QPA_PLATFORM=offscreen` 建单例；
  * 提供 `isolated_data` 夹具返回临时目录，供各 store 显式传 path。

跑法（无需显示器）::

    .venv\\Scripts\\python.exe -m pytest tests -q
"""
import os
import sys

import pytest

# ---- 导入前先改环境：必须在任何 core/ui 模块被 import 之前生效 ----
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


@pytest.fixture(scope="session", autouse=True)
def _isolate_appdata(tmp_path_factory):
    """把 APPDATA 重定向到临时目录，并保证测试结束后恢复。"""
    tmp = tmp_path_factory.mktemp("appdata")
    old = os.environ.get("APPDATA")
    os.environ["APPDATA"] = str(tmp)
    yield tmp
    if old is None:
        os.environ.pop("APPDATA", None)
    else:
        os.environ["APPDATA"] = old


@pytest.fixture(scope="session")
def qapp():
    """整个会话共用一个 QApplication（Qt 不允许建第二个）。"""
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    yield app


@pytest.fixture(scope="session", autouse=True)
def _icon_font(qapp):
    """注册内置图标字体：否则导航图标是豆腐块，界面测试会静默降级。"""
    from ui.fluent_icons import ensure_icon_font
    ensure_icon_font()


def _purge_top_level_widgets():
    """销毁所有非 fixture 持有的顶层窗口。

    ## 为什么必须真的销毁，而且必须用 shiboken6.delete

    `qapp` 是 session 级的（Qt 不允许建第二个）。测试创建的窗口如果不销毁
    会一直留在 QApplication 里，而 `app.setStyleSheet()` 要遍历**全部**
    存活控件重新匹配 QSS——实测 2400 个控件时贴一次要 614ms。

    累积效果：`test_ui_api.py` 每个用例建一个完整主窗口，跑完后其它文件
    里的 `AppRuntime` 构造从 1.5 秒涨到 **16 秒**。

    ⚠ `close()` + `deleteLater()` + `processEvents()` **实测无效**：
    Python 侧还持有引用时，Qt 不会真正释放（2420 个控件一个都没少）。
    只有 `shiboken6.delete()` 直接销毁 C++ 对象才有效
    （实测 2420 → 0，贴 QSS 从 614ms 降到 1ms）。
    """
    try:
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance()
        if app is None:
            return
        try:
            from shiboken6 import delete as _destroy
        except Exception:
            _destroy = None

        for w in list(app.topLevelWidgets()):
            # 跳过 module/session 级 fixture 持有的窗口（见 keep_widget）
            if getattr(w, "_test_keep_alive", False):
                continue
            try:
                w.close()
            except RuntimeError:
                continue
            try:
                if _destroy is not None:
                    _destroy(w)
                else:
                    w.deleteLater()
            except RuntimeError:
                pass          # 已经被 C++ 侧销毁
        app.processEvents()

        # Win32 弹窗（ui/win32_spinner.py）是**纯 Win32 窗口**，不在 Qt 的
        # widget 树里，上面的循环管不到它。测试结束时如果还开着，会一直
        # 浮在桌面上干扰后续用例（实测：spinner 反复创建 3 轮只成功 2 轮）。
        # 用一个宽松的类名匹配把它们关掉。
        try:
            import ctypes
            from ctypes import wintypes as _w

            u32 = ctypes.WinDLL("user32", use_last_error=True)
            found = []

            def _cb(hwnd, _lp):
                buf = ctypes.create_unicode_buffer(128)
                u32.GetClassNameW(hwnd, buf, 128)
                if buf.value.startswith("KaomojiSpinner"):
                    found.append(hwnd)
                return True

            _EP = ctypes.WINFUNCTYPE(_w.BOOL, _w.HWND, _w.LPARAM)
            u32.EnumWindows(_EP(_cb), 0)
            for hwnd in found:
                try:
                    u32.PostMessageW(hwnd, 0x0010, 0, 0)   # WM_CLOSE
                except Exception:
                    pass
        except Exception:
            pass
    except Exception:
        pass


@pytest.fixture(autouse=True)
def _purge_widgets(qapp):
    """每条用例**开始前**回收上一条留下的窗口。

    ## 为什么放在开始前而不是结束后

    `test_stray_windows` 要在用例内部对比"切页前后的可见顶层窗口数"。
    如果清理发生在上个用例的结束后，时序上没问题；但放在开始前更稳
    ——它等价于"上条用例的资源已回收"，同时绝不会碰到当前用例正在
    观察的对象。

    实测收益：全量测试 190 秒 → 94 秒。
    """
    _purge_top_level_widgets()
    yield


def keep_widget(widget):
    """把窗口标记为「由 fixture 持有，测试间复用」，清理夹具不删它。

    用法：模块级 fixture 建完窗口后调一次。

        window = UnifiedSettingsWindow(...)
        keep_widget(window)
    """
    try:
        widget._test_keep_alive = True
    except Exception:
        pass
    return widget


@pytest.fixture
def isolated_data(tmp_path):
    """返回一个干净的临时数据目录，供各 store 显式传 path 使用。"""
    return str(tmp_path / "data")


@pytest.fixture
def quiet_log():
    """收集日志调用而不打印，断言用。"""
    records = []

    def _log(level, source, message):
        records.append((level, source, message))

    _log.records = records
    return _log


@pytest.fixture
def plugin_manager(tmp_path, qapp, quiet_log):
    """构造一个指向项目 plugins/ 目录、但配置与设置都落在临时目录的 PluginManager。"""
    from core.plugins import PluginManager
    from ui.plugin_ui_hooks import build_ui_hooks

    cfg = {
        "theme": "light",
        "use_test_features": True,
        "plugins_enabled": {},
    }
    pm = PluginManager(
        plugins_dir=os.path.join(PROJECT_ROOT, "plugins"),
        config=cfg,
        save_config=lambda c: None,
        log=quiet_log,
        settings_dir=os.path.join(str(tmp_path), "plugin_settings"),
        theme_provider=lambda: "light",
        ui_hooks=build_ui_hooks(theme_provider=lambda: "light"),
    )
    return pm
