"""运行时装配测试：捕获"只在打包态才暴露"的接线错误。

## 为什么需要这个文件

实测遇到过一次：打包后的 exe 启动即崩，

    TypeError: 'PySide6.QtCore.QObject.__init__' called with wrong argument types
    QObject.__init__(AppRuntime)

原因是 `AutoUpdater(self.config, self)` —— `AppRuntime` 是普通 Python 类，
不是 `QObject`，不能当 parent 传。

**这个错误源码态测不出来**：`_schedule_auto_update()` 里
`updater.is_supported()` 在源码态返回 False，直接 return，
根本走不到那行。所以本地怎么跑都正常，一打包就崩。

## 做法

用 monkeypatch 把 `is_supported()` 强制为 True，
模拟打包态，把那条分支跑通。
"""
import sys

import pytest

pytest.importorskip("PySide6")

from core import updater as updater_mod  # noqa: E402


@pytest.fixture(scope="module")
def shared_runtime(tmp_path_factory):
    """整个模块共享一个 AppRuntime。

    ⚠ 构造一次要 3~6 秒（要建窗口、加载插件、注册热键）。
    之前用函数级 fixture，5 个测试各建一次，整套测试从 108 秒
    涨到 355 秒——这不只是测试慢，用户跑测试时也会明显卡。
    """
    tmp = tmp_path_factory.mktemp("runtime")

    from PySide6.QtWidgets import QApplication
    from core import runtime as rt_mod
    from core.config_store import load_config
    from ui.fluent_icons import ensure_icon_font
    from app_runtime import AppRuntime

    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    ensure_icon_font()

    # ⚠ 不能用 os.environ 设 KAOMOJI_SKIP_WELCOME：环境变量是进程级的，
    # 会泄漏到后续测试（实测把 test_welcome_and_graduation 的两个用例
    # 搞挂了）。改为只在本模块内 patch 判定函数。
    orig_welcome = None
    try:
        from core import version as ver
        orig_welcome = ver.should_show_welcome
        ver.should_show_welcome = lambda force=False: False
    except Exception:
        orig_welcome = None

    orig = rt_mod.app_data_dir
    rt_mod.app_data_dir = lambda: str(tmp)
    try:
        cfg = load_config()
        cfg["plugins_enabled"] = {}
        rt = AppRuntime(app, cfg)
        app.processEvents()
        yield rt, app
    finally:
        rt_mod.app_data_dir = orig
        if orig_welcome is not None:
            try:
                from core import version as ver
                ver.should_show_welcome = orig_welcome
            except Exception:
                pass


@pytest.fixture
def runtime(shared_runtime):
    """每个测试拿到的运行时；函数间需要隔离的字段在这里重置。"""
    rt, app = shared_runtime
    rt.updater = None
    rt._uia_warn_shown = False
    rt._pending_emotion = None
    app.processEvents()
    return rt


def test_auto_updater_can_be_constructed(runtime, monkeypatch):
    """AutoUpdater 必须能用 AppRuntime 构造（不带 parent）。

    这是打包态崩溃的回归测试：`AutoUpdater(config, self)` 会抛
    TypeError，因为 AppRuntime 不是 QObject。
    """
    # 伪装成打包态，让 _schedule_auto_update 走完整路径
    monkeypatch.setattr(updater_mod, "is_supported", lambda: True)
    monkeypatch.setattr(updater_mod, "should_check_now", lambda cfg: True)

    rt = runtime
    rt._schedule_auto_update()

    assert getattr(rt, "updater", None) is not None, \
        "打包态下应创建 AutoUpdater"
    assert isinstance(rt.updater, updater_mod.AutoUpdater)


def test_auto_updater_is_qobject_without_bad_parent(runtime, monkeypatch):
    """AutoUpdater 的 parent 不能是 AppRuntime。

    直接验证底层约束：QObject 只接受 QObject 或 None 作为 parent。
    """
    from PySide6.QtCore import QObject

    monkeypatch.setattr(updater_mod, "is_supported", lambda: True)
    monkeypatch.setattr(updater_mod, "should_check_now", lambda cfg: True)

    rt = runtime
    rt._schedule_auto_update()

    up = rt.updater
    assert isinstance(up, QObject)
    # parent 要么是 None，要么是 QObject
    parent = up.parent()
    assert parent is None or isinstance(parent, QObject), \
        "parent 必须是 QObject 或 None"


def test_updater_signals_connectable(runtime, monkeypatch):
    """AutoUpdater 的信号必须能正常接到运行时的方法上。"""
    monkeypatch.setattr(updater_mod, "is_supported", lambda: True)
    monkeypatch.setattr(updater_mod, "should_check_now", lambda cfg: True)

    rt = runtime
    rt._schedule_auto_update()
    up = rt.updater

    # 这些连接在 _schedule_auto_update 里已经做过，能走到这里说明没抛异常
    # 再显式验证一次信号存在
    for name in ("update_available", "up_to_date", "error",
                 "progress", "ready_to_install", "installing"):
        assert hasattr(up, name), "缺少信号 %s" % name


def test_source_mode_does_not_create_updater(runtime):
    """源码态不该创建 updater（功能不可用），且不能因此崩溃。"""
    if getattr(sys, "frozen", False):
        pytest.skip("打包态")

    rt = runtime
    if hasattr(rt, "updater"):
        # 如果之前创建过，先清掉
        rt.updater = None
    rt._schedule_auto_update()
    assert getattr(rt, "updater", None) is None, \
        "源码态不该创建 AutoUpdater"


def test_app_runtime_is_not_qobject():
    """把这条约束钉死：AppRuntime 是普通类，不能当 QObject parent。"""
    from PySide6.QtCore import QObject

    from app_runtime import AppRuntime

    assert not issubclass(AppRuntime, QObject), (
        "如果 AppRuntime 将来继承了 QObject，"
        "可以改回传 parent（但要同步更新本测试）")
