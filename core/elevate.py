"""通过 UAC 弹窗申请管理员权限，然后提权重启自身。

## 为什么需要这个

`ui_elevation` 要在进程令牌上设 `TokenUIAccess=1`，而这一步需要
**`SeTcbPrivilege`**。实测：非管理员进程的令牌里**根本没有这个特权**
（`GetTokenInformation(TokenPrivileges)` 查询不到），所以：

- 不是"权限不够可以先试试"，而是**根本做不到**；
- 唯一获得方式是通过 UAC 把进程提到管理员（High 完整性级别），
  此时令牌才带上 `SeTcbPrivilege`。

原先的做法是提示用户"请以管理员身份运行本程序"——需要用户自己找到 exe
→ 右键 → 以管理员身份运行，体验很差。这里改成程序自己弹 UAC：

    ShellExecuteW(None, "runas", exe, args, None, SW_SHOWNORMAL)

`"runas"` 会自动弹出系统的 UAC 确认框，用户点"是"后以管理员身份启动新进程。

## 为什么要重启而不是就地提权

Windows **不支持**提升已在运行进程的权限（`TokenElevation` 是创建进程时
决定的）。想提权只有一条路：**用新令牌/新提权方式启动一个新进程**，
旧进程退出。这与 UIAccess 本身的约束一致（UIAccess 也是进程级令牌属性）。

## 防重复弹窗

提权重启后新进程会再次走到"检查是否需要提权"的逻辑。若不拦，就会无限
弹窗。用环境变量 `KAOMOJI_ELEVATE_GUARD` 标记"这次是提权启动的"，
新进程看到它就直接跳过，避免循环。

## 与 UIAccess 重启的协同

完整链路：

    普通启动 → 用户开启「UIA 提权」 → 检测到非管理员
      → 弹 UAC 申请管理员（本模块）
      → 以管理员重启
      → 再次检测：已是管理员，但还没有 UIAccess
      → 派生 UIAccess 令牌并重启（ui_elevation 的既有流程）
      → 最终进程具备 UIAccess

即"两次重启"：先解决管理员，再解决 UIAccess。
"""
import os
import sys
import ctypes

#: 提权重启的防循环标记（记录目标状态）
ELEVATE_GUARD = "KAOMOJI_ELEVATE_GUARD"

#: ShellExecuteW 的返回值阈值：> 32 表示成功
_SW_SHOWNORMAL = 1
_ERROR_CANCELLED = 1223


class ElevateResult:
    """一次提权申请的结果。

    :ivar started:  是否成功启动了提权进程
    :ivar cancelled: 用户是否在 UAC 框点了"否"（或直接关掉）
    :ivar message:  面向用户的说明
    """

    __slots__ = ("started", "cancelled", "message", "error_code")

    def __init__(self, started=False, cancelled=False, message="", error_code=0):
        self.started = started
        self.cancelled = cancelled
        self.message = message
        self.error_code = error_code

    def __bool__(self):
        return bool(self.started)

    def __repr__(self):
        return "<ElevateResult started=%s cancelled=%s code=%s>" % (
            self.started, self.cancelled, self.error_code)


def is_elevated():
    """当前进程是否已提权到管理员（High 完整性）。"""
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def guard_is_active():
    """本次启动是否由"提权申请"引发（用于防循环）。"""
    return os.environ.get(ELEVATE_GUARD) == "1"


def mark_guarded():
    """标记当前进程是提权启动的。"""
    os.environ[ELEVATE_GUARD] = "1"


def clear_guard():
    """清除标记（下一次用户主动操作时允许再次申请）。"""
    os.environ.pop(ELEVATE_GUARD, None)


def request_elevation(args=None):
    """弹 UAC 申请管理员权限，成功后以管理员身份**启动新实例**。

    :param args: 传给新进程的命令行参数列表（不含 exe 本身）。
    :return: :class:`ElevateResult`

    注意：调用成功时**新进程已在跑**，调用方应立即退出旧进程
    （见 `ui_elevation` 里的调用处），否则会出现两个实例。
    """
    if sys.platform != "win32":
        return ElevateResult(message="当前平台不支持提权")

    exe = sys.executable
    if not exe:
        return ElevateResult(message="无法定位当前程序路径")

    if args is None:
        args = sys.argv[1:]

    try:
        shell32 = ctypes.windll.shell32
        shell32.ShellExecuteW.restype = ctypes.c_void_p
        # lpVerb = "runas" → 触发 UAC 弹窗
        ret = shell32.ShellExecuteW(
            None,              # hwnd
            "runas",           # 提权动词
            exe,               # 目标程序
            _quote_args(args), # 参数
            None,              # 工作目录（继承）
            _SW_SHOWNORMAL,
        )
        code = int(ret) if ret is not None else 0
    except Exception as e:
        return ElevateResult(message="申请提权失败：%r" % (e,), error_code=-1)

    # ShellExecuteW 返回 > 32 为成功；1223 = 用户取消（点了"否"）
    if code > 32:
        return ElevateResult(
            started=True,
            message="已以管理员身份启动新进程",
        )
    if code == _ERROR_CANCELLED:
        return ElevateResult(
            cancelled=True,
            error_code=code,
            message="已取消提权，UIA 提权需要管理员权限才能生效",
        )
    return ElevateResult(
        error_code=code,
        message="提权启动失败（ShellExecute 返回 %d）" % code,
    )


def _quote_args(args):
    """把参数列表拼成命令行字符串，含空格的参数加引号。"""
    out = []
    for a in args or []:
        s = str(a)
        if not s:
            continue
        if " " in s or "\t" in s:
            out.append('"%s"' % s.replace('"', '\\"'))
        else:
            out.append(s)
    return " ".join(out)


def spawn_with_guard(args=None):
    """以提权方式启动新实例，并把"防循环标记"通过环境变量传过去。

    ShellExecuteW 无法直接传环境变量，所以采用一个折中方案：
    在命令行里追加一个 `--elevated-by-app` 标记参数，由 `main.py`
    解析后调用 `mark_guarded()`。这样既能防循环，又不需要改动
    ShellExecute 的调用方式。
    """
    a = list(args if args is not None else sys.argv[1:])
    if "--elevated-by-app" not in a:
        a.append("--elevated-by-app")
    return request_elevation(a)
