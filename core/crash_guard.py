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

#: 短时间内的弹窗总量限制：(统计窗口秒数, 最多弹几个)
#:
#: 只按签名去重挡不住**连环异常**——一次操作引发的一串异常各自
#: 指纹都不同，实测能连弹五六个窗口把屏幕盖满。这里给个总量上限：
#: 超出的只写日志，不再弹窗。
BURST_WINDOW = 20.0
MAX_BURST = 3

#: 已弹过的异常签名 → 上次弹窗时间
_recent = {}

#: 最近弹窗的时间戳（用于总量限流）
_burst = []

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

    ⚠ 全程兜住：`traceback.extract_tb` 在 tb 损坏时会抛，
    `str(exc_value)` 在自定义异常里也可能抛。
    """
    try:
        frames = traceback.extract_tb(tb)
        where = ""
        if frames:
            last = frames[-1]
            where = "%s:%s" % (os.path.basename(last.filename), last.lineno)
        return "%s|%s|%s" % (
            _safe_str(getattr(exc_type, "__name__", "?"), 60),
            _safe_str(exc_value, 120), where)
    except Exception:
        return "unknown"


def _should_alert(sig):
    """判断这次异常是否该弹窗。

    ## 两层限流

    ### 1. 同一异常指纹去重（DEDUPE_WINDOW 内不重复弹）

    用「类型 + 消息 + 出错位置」做指纹。同一处代码抛同样的错只弹一次。

    ### 2. 短时间内弹出总量上限（新增）

    只按指纹去重还不够。实测遇到过：一次操作引发**连环异常**
    ——每个异常的指纹都不同（行号/消息略有差异），于是短时间内
    弹出五六个几乎一样的窗口，把屏幕盖满，用户根本没法操作。

    现在限制：`BURST_WINDOW` 秒内最多弹 `MAX_BURST` 个，
    超出的只写日志不弹窗（日志里仍然一条不少）。
    """
    import time

    now = time.monotonic()

    # ---- 2. 总量限流 ----
    _burst[:] = [t for t in _burst if now - t < BURST_WINDOW]
    if len(_burst) >= MAX_BURST:
        return False

    # ---- 1. 指纹去重 ----
    last = _recent.get(sig)
    if last is not None and (now - last) < DEDUPE_WINDOW:
        return False
    # 清理过期记录，避免字典无限增长
    for k in [k for k, v in _recent.items() if now - v > DEDUPE_WINDOW]:
        _recent.pop(k, None)
    _recent[sig] = now
    _burst.append(now)
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


#: 取最近日志的读取器（由 `core.applog` 注入）。
#:
#: ## 为什么用注入而不是直接 import
#:
#: `crash_guard` 需要读日志来生成崩溃报告，而 `applog` 需要调用
#: `crash_guard.install()` 装异常钩子——两边互相依赖。
#:
#: 它们都在函数内 import 所以 Python 不报错，但这是一条**双向箭头**：
#: 任何一个模块都可能在另一个还没初始化完时被调用，行为难以推理。
#:
#: 现在改成单向：`applog` 在模块末尾把读取器注入进来，
#: `crash_guard` 只依赖"有一个可调用对象"，不关心它来自哪。
LOG_READER = None


def set_log_reader(fn):
    """注入"取最近日志行"的读取器（`fn(limit) -> str`）。"""
    global LOG_READER
    LOG_READER = fn if callable(fn) else None


#: 写日志的函数（由 `core.applog` 注入）。
#:
#: 与 `LOG_READER` 同理：`crash_guard` 不该认识 `applog`，
#: 只依赖"有一个能写日志的可调用对象"。
LOG_WRITER = None


def set_log_writer(fn):
    """注入日志写入器 `fn(level, source, message)`。"""
    global LOG_WRITER
    LOG_WRITER = fn if callable(fn) else None


def _write_log(level, source, message):
    """写一条日志（没有注入写入器时静默丢弃）。"""
    if LOG_WRITER is None:
        return
    try:
        LOG_WRITER(level, source, message)
    except Exception:
        pass


def _recent_logs(limit=120):
    """取最近若干条日志（没有注入读取器时返回空串）。"""
    if LOG_READER is None:
        return ""
    try:
        text = LOG_READER(limit)
        return text if isinstance(text, str) else ""
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


def _safe_str(obj, limit=200):
    """把任意对象转成字符串，**绝不抛异常**。

    异常对象的 `__str__` 可能本身就是坏的（自定义异常里写着
    `raise`、返回非字符串等）。这里要保证无论如何都能拿到一个值——
    否则处理崩溃的代码会因为格式化而崩，那就什么都弹不出来了。
    """
    try:
        s = str(obj)
        if not isinstance(s, str):
            s = repr(obj)
    except Exception:
        try:
            s = repr(obj)
        except Exception:
            s = "<无法显示的异常>"
    try:
        return s[:limit]
    except Exception:
        return "<异常信息过长>"


def _safe_traceback(exc_type, exc_value, exc_tb):
    """格式化堆栈，失败时退回简化版本，**绝不抛异常**。"""
    try:
        return "".join(traceback.format_exception(
            exc_type, exc_value, exc_tb)).rstrip()
    except Exception:
        pass
    # 退化：至少给出类型和消息
    return "%s: %s" % (_safe_str(exc_type, 80), _safe_str(exc_value, 400))


def _handle(exc_type, exc_value, exc_tb, source="main"):
    """统一处理一次未捕获异常。

    ## 核心原则：**处理崩溃的代码自己绝不能崩**

    用户反馈"随便来个报错都能不弹窗直接崩溃"。原因是这个函数里
    任何一步抛出异常，都会从 `sys.excepthook` 逃逸出去——
    而 Python 在 excepthook 里抛异常时直接终止进程。

    所以每一步都单独兜住：

        1. 日志        失败就算了
        2. 保存上下文  失败就算了
        3. 弹窗        失败就算了

    而且**所有格式化都延迟到弹窗时**——`_handle` 本身只做最简单的
    赋值和判断，把"可能抛异常的事情"推到 `_show()` 里（那里还有
    一层 try）。
    """
    global _last_crash

    # 1. 保存上下文（赋值，不会失败）
    try:
        _last_crash = {
            "exc_type": exc_type,
            "exc_value": exc_value,
            "exc_tb": exc_tb,
        }
    except Exception:
        pass

    # 2. 记录日志（通过注入的写入器，不直接 import applog）
    try:
        _write_log("exception", source,
                   _safe_traceback(exc_type, exc_value, exc_tb))
    except Exception:
        pass

    # 3. 去重判断
    try:
        sig = _signature(exc_type, exc_value, exc_tb)
        should = _should_alert(sig)
    except Exception:
        should = True          # 去重本身出错时宁可弹

    if _alert_callback is None or not should:
        return

    # 4. 弹窗（所有格式化都在这里做，且整体兜住）
    def _show():
        try:
            callback = _alert_callback
            if callback is None:
                return
            summary = "%s: %s" % (
                _safe_str(getattr(exc_type, "__name__", "异常"), 80),
                _safe_str(exc_value, 200))
            detail = _safe_traceback(exc_type, exc_value, exc_tb)
            callback(summary, detail)
        except Exception:
            # 弹窗本身出错不能递归，也不能影响主流程
            pass

    try:
        _dispatch_alert(_show)
    except Exception:
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


def _dump_to_disk(exc_type, exc_value, exc_tb, source="main"):
    """把崩溃信息追加写到一个固定文件里，**不依赖 Qt**。

    位置：`<数据目录>/crashes/last-crash.log`

    为什么还要单独写这个（`export_report` 已经能导出）：

    弹窗依赖 Qt 事件循环。崩溃如果发生在事件循环还没起来、或者
    事件循环已经被破坏的时候，弹窗永远出不来。这时磁盘上的记录
    就是**唯一**的排查材料。

    实现上只用标准库 + 字符串拼接，全程 try——写不出来也不能抛。
    """
    try:
        import datetime

        try:
            from core.runtime import app_data_dir

            base = os.path.join(app_data_dir(), "crashes")
        except Exception:
            base = os.path.join(os.path.expanduser("~"), ".kaomoji-crashes")
        os.makedirs(base, exist_ok=True)
        path = os.path.join(base, "last-crash.log")

        try:
            stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            stamp = "?"
        lines = ["", "=" * 60,
                 "[%s] source=%s" % (stamp, source),
                 _safe_traceback(exc_type, exc_value, exc_tb),
                 ""]
        with open(path, "a", encoding="utf-8", errors="replace") as f:
            f.write("\n".join(lines))
        return path
    except Exception:
        return None


def last_disk_dump():
    """读取磁盘上最近一次崩溃记录（给排查用）。"""
    try:
        from core.runtime import app_data_dir

        path = os.path.join(app_data_dir(), "crashes", "last-crash.log")
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()[-8000:]
    except Exception:
        return ""


def install():
    """安装崩溃兜底（幂等）。

    ## 外围还有一层"绝不抛"的保护

    `sys.excepthook` 里抛出异常时，Python 的行为是**直接终止进程**，
    连原有 hook 都不会再跑。所以两个 hook 的外层都包了 try——

    即使 `_handle` 内部出现任何意料之外的问题（某个库的异常对象
    特别离谱、格式化时踩到递归等），也不会让进程直接死掉，
    而是退回到"写 stderr"这种最原始的方式。
    """
    global _installed
    if _installed:
        return
    _installed = True

    # 记住原始 hook，退化时还能用
    _orig_excepthook = sys.__excepthook__

    def _excepthook(exc_type, exc_value, exc_tb):
        # KeyboardInterrupt 是用户主动中断，不算崩溃
        if exc_type is KeyboardInterrupt:
            return
        try:
            _handle(exc_type, exc_value, exc_tb, source="main")
        except BaseException:
            # 兜底：处理崩溃的代码自己崩了，至少把信息留在 stderr
            try:
                _orig_excepthook(exc_type, exc_value, exc_tb)
            except BaseException:
                pass
        # 最后防线：不管上面成没成，都往磁盘写一份。
        #
        # ## 为什么需要它
        #
        # 弹窗依赖 Qt 事件循环。如果崩溃发生在事件循环还没起来的阶段
        # （或事件循环已经被破坏），弹窗永远出不来。那时用户唯一能
        # 拿到的东西就是磁盘上这个文件。
        # 写文件是最低限度的保障：不依赖 Qt、不依赖主线程。
        try:
            _dump_to_disk(exc_type, exc_value, exc_tb, source="main")
        except BaseException:
            pass

    sys.excepthook = _excepthook

    # 子线程里的异常（Python 3.8+）
    try:
        def _thread_hook(args):
            if args.exc_type is SystemExit:
                return
            try:
                name = "?"
                try:
                    name = args.thread.name if args.thread else "?"
                except Exception:
                    pass
                _handle(args.exc_type, args.exc_value, args.exc_traceback,
                        source="thread:%s" % name)
                # 与主线程 hook 同样的磁盘备份
                _dump_to_disk(args.exc_type, args.exc_value,
                              args.exc_traceback,
                              source="thread:%s" % name)
            except BaseException:
                # 同上：绝不从 excepthook 里逃逸
                pass

        threading.excepthook = _thread_hook
    except Exception:
        pass


def uninstall():
    """还原（仅测试用）。"""
    global _installed, _alert_callback
    _installed = False
    _alert_callback = None
    _recent.clear()
    _burst.clear()
    _last_crash.clear()
    sys.excepthook = sys.__excepthook__
