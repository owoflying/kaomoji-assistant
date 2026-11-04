"""应用日志：环形缓冲 + Qt 消息处理器 + 未捕获异常收集。

为什么单独成模块：日志是**全局基础设施**，托盘「查看日志」、开发者页事件流、
插件 `api.log()` 都要往里写，而它原先塞在 main.py 里，导致 main 既当入口又当
日志中心，任何模块想读日志都得反向依赖入口模块。

内容上限与过滤白名单是唯二策略点，其余模块只调用 :func:`append_log`。
"""
import sys
import time
import traceback

# 运行时日志环形缓冲：收集 Qt 消息 + 未捕获异常，供「查看日志」对话框读取。
LOG_BUFFER = []
LOG_BUFFER_MAX = 2000

# 需要静音的 Qt 噪声日志：(日志类别, 消息前缀)
_MUTED_QT_LOGS = (
    ("qt.text.font.db", "OpenType support missing for"),
)
# 供 --diag 自检统计拦下了多少条噪声
MUTED_LOG_COUNT = {"n": 0}


def append_log(level, source, message):
    """把一条日志写入环形缓冲（超过上限丢弃最旧的）。"""
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    if source:
        line = "[%s] [%s] (%s) %s" % (ts, level, source, message)
    else:
        line = "[%s] [%s] %s" % (ts, level, message)
    LOG_BUFFER.append(line)
    if len(LOG_BUFFER) > LOG_BUFFER_MAX:
        del LOG_BUFFER[:len(LOG_BUFFER) - LOG_BUFFER_MAX]


def install_qt_log_filter():
    """接管 Qt 消息输出：过滤字体探测噪声，其余写入日志缓冲并转发 stderr。

    现象（每次渲染颜文字列表都会刷屏）：
      qt.text.font.db: OpenType support missing for "Segoe UI Emoji", script 18
    成因：颜文字库里混着印度语系字符（ಠ U+0CA0 卡纳达 / દ U+0AA6 古吉拉特），
    kaomoji_font() 是「逐字回退」的多字体链，Qt 排版时会挨个探测链里的字体有没有
    该文种的 OpenType(GSUB) 表，Emoji/Symbol/YaHei 本来就不含，于是刷屏。
    字符最终由系统回退字体（Nirmala UI）正常渲染，**显示是好的**，纯属日志噪声。

    为什么不用 QLoggingCategory.setFilterRules("qt.text.font.db.warning=false")：
    实测无效——该类别的 isWarningEnabled() 确实变 False，但这条告警在 Qt 内部并
    没走 category 开关检查，消息照样发出。只能在消息处理器这一层拦。
    """
    from PySide6.QtCore import QtMsgType, qInstallMessageHandler

    level_name = {
        QtMsgType.QtDebugMsg: "debug",
        QtMsgType.QtInfoMsg: "info",
        QtMsgType.QtWarningMsg: "warning",
        QtMsgType.QtCriticalMsg: "critical",
        QtMsgType.QtFatalMsg: "fatal",
    }

    def handler(mode, ctx, msg):
        category = getattr(ctx, "category", "") or ""
        for cat, prefix in _MUTED_QT_LOGS:
            if category == cat and msg.startswith(prefix):
                MUTED_LOG_COUNT["n"] += 1
                return
        level = level_name.get(mode, "qt")
        src = category if category and category != "default" else ""
        append_log(level, src, msg)
        # 其余原样放行到 stderr，格式尽量贴近 Qt 默认
        parts = []
        if mode in (QtMsgType.QtCriticalMsg, QtMsgType.QtFatalMsg):
            parts.append("[%s]" % level)
        if category and category != "default":
            parts.append("%s:" % category)
        parts.append(msg)
        try:
            sys.stderr.write(" ".join(parts) + "\n")
            sys.stderr.flush()
        except Exception:
            pass

    qInstallMessageHandler(handler)


def install_excepthook():
    """把未捕获的 Python 异常也收进日志缓冲（含 Qt 信号槽里抛出的异常）。"""
    def hook(exc_type, exc_value, exc_tb):
        text = "".join(
            traceback.format_exception(exc_type, exc_value, exc_tb)).rstrip()
        append_log("exception", "", text)

    sys.excepthook = hook
