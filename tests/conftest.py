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
