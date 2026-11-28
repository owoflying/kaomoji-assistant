"""静默失败的可观测版本：`swallow()`。

## 这个模块为什么存在

全项目有 500+ 处 `except Exception:`，其中**近一半直接 pass**：

    try:
        w.setStyleSheet(...)
    except Exception:
        pass

单看每一处都能理解（"UI 出问题不该让配置保存失败"）。但它们合起来的
后果是：**出了问题一点线索都没有**。排查一次典型故障要人肉复现、
打断点、读源码——这正是这个项目反复踩同一个坑的原因。

但它也不能简单改成"抛出"：这些地方多数确实**不该阻断主流程**
（样式没贴上，用户还能用；主题某个窗口没刷新，下次会好）。

## 取舍：静默，但留痕

    swallow("托盘菜单换肤", lambda: menu.setStyleSheet(ss))

出问题时日志里会留下：

    [warn] (ui) 托盘菜单换肤 失败：TypeError: ...

用户无感（仍然继续运行），但排查时**一句 grep 就能定位**。
需要完整堆栈时把环境变量 `KAOMOJI_STRICT=1` 打开，
异常会直接抛出——用于开发期定位"到底是哪一行"。

## 用法

适合的：

    * 纯 UI 的样式/重绘/动画（失败不影响数据）
    * 可选资源的读取（图标字体、自定义配色文件）
    * 清理类操作（删临时文件、还原状态）

**不适合**的：

    * 落盘、数据写入（失败必须让用户知道）
    * 数据解析（静默会导致数据悄悄丢失）
    * 插件加载（用户需要看到哪个插件坏了）

那些地方应该显式记录 + 反馈，而不是用本模块兜住。
"""
import os


def strict():
    """是否处于"严格模式"（异常直接抛出，便于开发期定位）。"""
    v = os.environ.get("KAOMOJI_STRICT", "")
    return v not in ("", "0", "false", "False")


def swallow(what, fn=None, *, level="warn", source="ui", extra=None):
    """执行 `fn()`，出错时记一条日志而不是静默吞掉。

    :param what: 一句话说明在做什么（会出现在日志里）
    :param fn: 要执行的函数；也可以是 `lambda: expr`
    :param level: 日志级别，默认 warn
    :param source: 日志来源标签
    :param extra: 附加到日志末尾的信息（可选）
    :return: `fn()` 的返回值；出错时返回 None

    ## 两种写法

        # 1. 包一个调用
        swallow("刷新托盘菜单", lambda: menu.setStyleSheet(ss))

        # 2. 包一段代码（配合 with）
        with swallow_ctx("保存窗口位置"):
            cfg["win_pos"] = self.pos().toTuple()

    两种都不会把异常抛出去（除严格模式），但都会在日志里留痕。
    """
    if fn is None:
        return None
    try:
        return fn()
    except Exception as e:
        _report(what, e, level=level, source=source, extra=extra)
        return None


class swallowed:
    """`with swallowed("说明"):` —— 上下文管理器形式。

    适合包住一段**多行**代码（用 lambda 包多行很别扭时）。
    """

    __slots__ = ("_what", "_level", "_source", "_extra")

    def __init__(self, what, *, level="warn", source="ui", extra=None):
        self._what = what
        self._level = level
        self._source = source
        self._extra = extra

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc is None:
            return False
        if strict():
            return False          # 严格模式：让它抛出去
        _report(self._what, exc, level=self._level,
                source=self._source, extra=self._extra)
        return True               # 吞掉，但已记录


def _report(what, exc, *, level="warn", source="ui", extra=None):
    """把一次被容忍的失败写进日志。"""
    try:
        from core.applog import append_log

        msg = "%s 失败：%s: %s" % (
            what, type(exc).__name__, exc)
        if extra:
            msg += "（%s）" % (extra,)
        append_log(level, source, msg)
    except Exception:
        # 连日志都写不了就只能放弃——但**不要**再抛，
        # 那会把"容忍失败"变成"制造失败"。
        pass
