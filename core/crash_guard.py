"""崩溃兜底：异常不让程序直接退出，弹窗告知 + 一键导出诊断信息。

## 现状的不足

原来只有一个 `install_excepthook`，它把异常塞进日志缓冲就完了：

* **用户完全不知道出事了**——窗口还在，但某个功能悄悄坏了
* **Qt 槽函数里抛的异常根本不走 `sys.excepthook`**，直接被 Qt 吞掉
  打一行 stderr 就算完（打包成 --windowed 后连 stderr 都没有）
* 日志只存在内存里，程序一退就没了，用户想反馈也没有材料

## 现在做四件事

1. **捕获三类异常**
   * `sys.excepthook` —— 普通未捕获异常
   * `threading.excepthook` —— 子线程里的异常
   * Qt 槽函数异常 —— 通过 `sys.excepthook` 的 Qt 绑定路径（PySide6
     会把槽函数里的异常转给 `sys.excepthook`，前提是它没被覆盖成空）

2. **弹窗提醒**（默认开启）
   非阻塞提示框，说明"程序遇到一个问题，功能可能不正常"，
   提供「查看详情」「导出报告」「继续使用」。

3. **同一异常不重复轰炸**
   同一个异常签名在 30 秒内只弹一次。否则一个循环里的异常会
   弹出几十个窗口，比崩溃还烦。

4. **导出崩溃报告**
   把异常堆栈、程序版本、Python/Qt 版本、操作系统、最近日志、
   当前配置（脱敏）写成一个 txt，用户可以直接发给开发者。

## 始终开启

按需求"崩溃兜底（始终开启）"，这里**不提供开关**——
出问题时用户至少能看到发生了什么，比静默失败好得多。
"""
import datetime
import os
import platform
import sys
import threading
import traceback

#: 同一异常签名的最小弹窗间隔（秒）
DEDUPE_WINDOW = 30.0

#: 已弹过的异常签名 → 上次弹窗时间
_recent = {}

#: 用户设置的弹窗回调（由 UI 层注入；None 时只记录不弹）
_alert_callback = None

#: 把回调调度到主线程的函数（由 UI 层注入）。
#:
#: ## ⚠ 为什么必须有这个
#:
#: Qt 的控件**只能在主线程创建**。子线程异常时如果在异常发生的线程里
#: 直接 `new QDialog`，会触发
#:
#:     QObject::setParent: Cannot set parent, new parent is in a different thread
#:
#: 后果不是"弹窗没显示"这么简单——Qt 内部的父子关系处于无效状态，
#: 后续的事件处理会让**整个进程无响应然后退出**，崩溃兜底等于没做。
#:
#: 所以异常处理只负责收集信息，真正弹窗必须经这里转到主线程。
_dispatch_to_main = None

#: 是否已经安装过（避免重复包装）
_installed = False

#: 保存最近一次异常的完整信息，供"导出报告"使用
_last_crash = {}


def set_dispatch(fn):
    """注入"把可调用对象调度到主线程"的函数。

    :param fn: `fn(callable) -> None`。UI 层用
               `QTimer.singleShot(0, cb)` 或
               `QMetaObject.invokeMethod(..., Qt.QueuedConnection)` 实现。
    """
    global _dispatch_to_main
    _dispatch_to_main = fn


def set_alert_callback(fn):
    """注入弹窗函数。

    :param fn: `fn(summary: str, detail: str) -> None`，由 UI 层提供。
               None 表示不弹窗（无头/测试环境）。
    """
    global _alert_callback
    _alert_callback = fn


def _signature(exc_type, exc_value, tb):
    """异常指纹：类型 + 消息 + 最内层出错位置。

    用指纹而不是完整堆栈做去重键——同一处代码抛同样的错，
    堆栈可能有细微差别（行号列号），但类型+消息+位置足够稳定。
    """
    try:
        frames = traceback.extract_tb(tb)
        where = ""
        if frames:
            last = frames[-1]
            where = "%s:%s" % (os.path.basename(last.filename), last.lineno)
        return "%s|%s|%s" % (getattr(exc_type, "__name__", "?"),
                             str(exc_value)[:120], where)
    except Exception:
        return "unknown"


def _should_alert(sig):
    """判断这次异常是否该弹窗（去重窗口内不重复弹）。"""
    import time

    now = time.monotonic()
    last = _recent.get(sig)
    if last is not None and (now - last) < DEDUPE_WINDOW:
        return False
    # 清理过期记录，避免字典无限增长
    for k in [k for k, v in _recent.items() if now - v > DEDUPE_WINDOW]:
        _recent.pop(k, None)
    _recent[sig] = now
    return True


def _collect_context():
    """收集导出报告需要的环境信息。"""
    info = {}
    try:
        info["时间"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        info["时间"] = "?"
    try:
        from core import version as ver

        info["程序版本"] = ver.get_app_version()
    except Exception:
        info["程序版本"] = "?"
    try:
        from PySide6 import __version__ as qtver

        info["Qt 版本"] = qtver
    except Exception:
        info["Qt 版本"] = "?"
    info["Python"] = sys.version.split()[0]
    info["平台"] = "%s %s" % (platform.system(), platform.release())
    try:
        info["位数"] = "%d-bit" % (64 if sys.maxsize > 2 ** 32 else 32)
    except Exception:
        info["位数"] = "?"
    try:
        info["可执行文件"] = sys.executable
    except Exception:
        info["可执行文件"] = "?"
    try:
        info["打包运行"] = bool(getattr(sys, "frozen", False))
    except Exception:
        info["打包运行"] = False
    return info


def _recent_logs(limit=120):
    """取最近若干条日志。"""
    try:
        from core.applog import LOG_BUFFER

        lines = list(LOG_BUFFER)[-limit:]
        return "\n".join(str(x) for x in lines)
    except Exception:
        return ""


def _sanitized_config():
    """当前配置（去掉可能含隐私的字段）。"""
    try:
        from core.config_store import load_config

        cfg = dict(load_config())
    except Exception:
        return "(读取配置失败)"
    # 热键/主题这类留着（有助于排查），但把明显的个人信息去掉
    for k in ("user_name", "email", "api_key", "token"):
        cfg.pop(k, None)
    try:
        return "\n".join("%s = %r" % (k, cfg[k]) for k in sorted(cfg))
    except Exception:
        return "(序列化失败)"


def build_report(exc_type=None, exc_value=None, exc_tb=None, extra=""):
    """生成崩溃报告文本（可直接发给开发者）。"""
    parts = []
    parts.append("=" * 60)
    parts.append("颜文字输入辅助器 · 崩溃报告")
    parts.append("=" * 60)
    parts.append("")
    parts.append("【环境信息】")
    for k, v in _collect_context().items():
        parts.append("  %s: %s" % (k, v))
    parts.append("")

    if exc_type is not None:
        parts.append("【异常堆栈】")
        try:
            parts.append("".join(traceback.format_exception(
                exc_type, exc_value, exc_tb)).rstrip())
        except Exception:
            parts.append("(堆栈格式化失败)")
        parts.append("")

    if extra:
        parts.append("【附加信息】")
        parts.append(str(extra))
        parts.append("")

    logs = _recent_logs()
    if logs:
        parts.append("【最近日志】")
        parts.append(logs)
        parts.append("")

    parts.append("【当前配置】")
    parts.append(_sanitized_config())
    parts.append("")
    return "\n".join(parts)


def export_report(path=None, **kw):
    """把崩溃报告写到文件，返回路径（失败返回 None）。

    :param path: 目标路径；None 时写到数据目录下的 crash-时间戳.txt
    """
    text = build_report(**kw)
    if path is None:
        try:
            from core.runtime import app_data_dir

            base = os.path.join(app_data_dir(), "crashes")
            os.makedirs(base, exist_ok=True)
            stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
            path = os.path.join(base, "crash-%s.txt" % stamp)
        except Exception:
            path = None
    if path is None:
        return None
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return path
    except Exception:
        return None


def _on_main_thread():
    """当前是否在 Qt 主线程上。

    没有 Qt 应用时（纯 Python 测试）永远返回 True——那时不存在线程
    限制，直接弹就行。
    """
    try:
        from PySide6.QtCore import QCoreApplication, QThread

        app = QCoreApplication.instance()
        if app is None:
            return True
        return QThread.currentThread() == app.thread()
    except Exception:
        return True


def _dispatch_alert(fn):
    """把一次弹窗调度到主线程执行；已经在主线程就直接跑。"""
    if _on_main_thread():
        try:
            fn()
        except Exception:
            pass
        return
    # 子线程：必须经 Qt 的排队机制转交，否则控件会在错误线程创建
    if _dispatch_to_main is not None:
        try:
            _dispatch_to_main(fn)
            return
        except Exception:
            pass
    # 没有调度器时**宁可不弹**，也不要在子线程里造 QWidget——
    # 那会触发 "Cannot set parent, new parent is in a different thread"，
    # 进而让整个进程无响应退出，比不弹窗严重得多。
    return


def _handle(exc_type, exc_value, exc_tb, source="main"):
    """统一处理一次未捕获异常。"""
    global _last_crash
    # 1. 记录日志（原有行为，保持）
    try:
        from core.applog import append_log

        text = "".join(traceback.format_exception(
            exc_type, exc_value, exc_tb)).rstrip()
        append_log("exception", source, text)
    except Exception:
        pass

    # 2. 保存上下文供"导出报告"用
    _last_crash = {
        "exc_type": exc_type,
        "exc_value": exc_value,
        "exc_tb": exc_tb,
    }

    # 3. 去重后弹窗提醒（**转到主线程**）
    sig = _signature(exc_type, exc_value, exc_tb)
    if _alert_callback is None or not _should_alert(sig):
        return

    summary = "%s: %s" % (getattr(exc_type, "__name__", "异常"),
                          str(exc_value)[:200])
    detail = "".join(traceback.format_exception(
        exc_type, exc_value, exc_tb)).rstrip()

    def _show():
        try:
            _alert_callback(summary, detail)
        except Exception:
            # 弹窗本身出错不能递归
            pass

    _dispatch_alert(_show)


def last_crash_report():
    """生成"最近一次崩溃"的报告文本（给导出按钮用）。"""
    if not _last_crash:
        return build_report()
    return build_report(
        _last_crash.get("exc_type"),
        _last_crash.get("exc_value"),
        _last_crash.get("exc_tb"))


def install():
    """安装崩溃兜底（幂等）。"""
    global _installed
    if _installed:
        return
    _installed = True

    def _excepthook(exc_type, exc_value, exc_tb):
        # KeyboardInterrupt 是用户主动中断，不算崩溃
        if exc_type is KeyboardInterrupt:
            return
        _handle(exc_type, exc_value, exc_tb, source="main")

    sys.excepthook = _excepthook

    # 子线程里的异常（Python 3.8+）
    try:
        def _thread_hook(args):
            if args.exc_type is SystemExit:
                return
            _handle(args.exc_type, args.exc_value, args.exc_traceback,
                    source="thread:%s" % (args.thread.name if args.thread
                                          else "?"))

        threading.excepthook = _thread_hook
    except Exception:
        pass


def uninstall():
    """还原（仅测试用）。"""
    global _installed, _alert_callback
    _installed = False
    _alert_callback = None
    _recent.clear()
    _last_crash.clear()
    sys.excepthook = sys.__excepthook__
