"""开机自启动（计划任务方式，可带管理员权限）。

## 为什么不再用注册表 Run

原先用 `HKCU\\...\\Run` 登记自启动，优点是**无需管理员权限**，缺点是：
写入的进程就是登录时启动的进程，权限与当前用户登录令牌完全一致——
若程序需要 UIAccess（「UIA 提权」功能），启动后还得再弹一次 UAC 申请管理员，
每次开机都要用户点「是」。

## 计划任务的方式

Windows 上「开机自启 + 自动带管理员权限」的标准做法是**计划任务**：

    schtasks /create /tn KaomojiAssistant /tr "<exe>" \\
             /sc onlogon /rl HIGHEST /f

- `/sc onlogon` —— 用户登录时触发，不需要用户手动启动；
- `/rl HIGHEST` —— 以当前用户**最高权限**运行。创建时如果当前不是管理员会
  被拒绝（`Access is denied`，实测），所以这一步本身就需要先提权；
- 创建成功后，登录时任务由**任务计划服务**启动，不会弹 UAC —— 这是它与
  「注册表 Run + 每次弹 UAC」的本质区别。

## 两种模式并存

| 模式 | 实现 | 管理员权限 | UAC 弹窗 |
|------|------|-----------|---------|
| `registry` | HKCU Run | 否 | 无（但启动后无管理员） |
| `task` | 计划任务 HIGHEST | **是** | 仅创建时一次 |

默认仍用 `registry`（对普通用户最省事）；当用户开启「UIA 提权」并同意
"开机自启也自动提权"时，切换到 `task` 模式。

## 失败处理

- 创建计划任务需要管理员，非管理员时返回错误信息而不是抛异常；
- `schtasks` 输出是本地化的，**不解析其文本**，只用退出码判断成败；
- 删除/查询同样走退出码。

## 为什么用 schtasks 而不是 COM API

COM（`ITaskService`）能拿到更精确的错误码与状态，但要写几十行 IDispatch
包装、且在不同 Windows 版本上结构体偏移有差异。`schtasks` 是随系统分发的
稳定命令行工具，退出码足够判断成败，可维护性明显更好。
"""
import os
import subprocess
import sys

#: 计划任务名（固定，便于查询/删除）
TASK_NAME = "KaomojiAssistant"

#: 自启动模式
MODE_REGISTRY = "registry"
MODE_TASK = "task"

#: 创建子进程时不弹黑框
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def exe_path():
    """打包后的 exe 路径；源码态返回 None（源码运行无法可靠自启动）。"""
    if getattr(sys, "frozen", False):
        return sys.executable
    return None


def is_supported():
    """当前环境是否支持自启动（仅打包 exe 形态）。"""
    return sys.platform == "win32" and exe_path() is not None


def _run_schtasks(args):
    """执行 schtasks 并返回 (成功, 输出)。不解析本地化文本，只看退出码。"""
    try:
        proc = subprocess.run(
            ["schtasks"] + list(args),
            capture_output=True, text=True, timeout=20,
            creationflags=_NO_WINDOW,
        )
        out = ((proc.stdout or "") + (proc.stderr or "")).strip()
        return proc.returncode == 0, out
    except Exception as e:
        return False, "%r" % (e,)


# ---------------------------------------------------------------- 计划任务
def task_exists():
    """计划任务是否已存在。"""
    if sys.platform != "win32":
        return False
    ok, _ = _run_schtasks(["/query", "/tn", TASK_NAME])
    return ok


def task_autostart_enabled():
    """计划任务的自启动是否已就绪：任务存在且指向当前 exe。"""
    if not task_exists():
        return False
    exe = exe_path()
    if not exe:
        return False
    try:
        proc = subprocess.run(
            ["schtasks", "/query", "/tn", TASK_NAME, "/xml"],
            capture_output=True, text=True, timeout=20,
            creationflags=_NO_WINDOW,
        )
        xml = proc.stdout or ""
        return os.path.normcase(exe) in os.path.normcase(xml)
    except Exception:
        return False


def enable_task_autostart():
    """创建/覆盖「登录时以最高权限启动」的计划任务。

    返回 (成功, 消息)。需要管理员权限——非管理员时 Windows 返回
    `Access is denied`，这里原样带回给调用方用于提示。
    """
    exe = exe_path()
    if sys.platform != "win32":
        return False, "当前平台不支持计划任务"
    if not exe:
        return False, "仅打包后的 exe 形态支持开机自启"

    # /rl HIGHEST 是关键：以当前用户最高权限运行，登录时由任务计划服务
    # 启动，不会弹 UAC。
    ok, out = _run_schtasks([
        "/create", "/tn", TASK_NAME,
        "/tr", '"%s"' % exe,
        "/sc", "onlogon",
        "/rl", "HIGHEST",
        "/f",
    ])
    if ok:
        return True, "已创建开机自启计划任务（登录时以管理员权限运行）"
    if "denied" in out.lower() or "拒绝访问" in out:
        return False, "创建计划任务需要管理员权限，请先以管理员身份运行本程序"
    return False, "创建计划任务失败：%s" % (out or "未知错误")


def disable_task_autostart():
    """删除计划任务。返回 (成功, 消息)。任务不存在视为成功。"""
    if sys.platform != "win32":
        return False, "当前平台不支持计划任务"
    if not task_exists():
        return True, "计划任务不存在"
    ok, out = _run_schtasks(["/delete", "/tn", TASK_NAME, "/f"])
    if ok:
        return True, "已删除开机自启计划任务"
    return False, "删除计划任务失败：%s" % (out or "未知错误")


# ---------------------------------------------------------------- 注册表
def _registry_enabled():
    try:
        import winreg
    except ImportError:
        return False
    from core.runtime import APP_NAME
    try:
        with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Run") as key:
            winreg.QueryValueEx(key, APP_NAME)
        return True
    except OSError:
        return False


def _set_registry(enabled):
    try:
        import winreg
    except ImportError:
        return False
    from core.runtime import APP_NAME
    try:
        with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Run",
                0, winreg.KEY_SET_VALUE) as key:
            if enabled:
                winreg.SetValueEx(
                    key, APP_NAME, 0, winreg.REG_SZ, '"%s"' % exe_path())
            else:
                try:
                    winreg.DeleteValue(key, APP_NAME)
                except OSError:
                    pass
        return True
    except OSError:
        return False


# ---------------------------------------------------------------- 对外统一入口
def mode():
    """当前生效的自启动方式：``"task"`` / ``"registry"`` / ``"none"``。

    优先报告计划任务——它带管理员权限，是更强的形态。
    """
    if task_autostart_enabled():
        return MODE_TASK
    if _registry_enabled():
        return MODE_REGISTRY
    return "none"


def is_enabled():
    """自启动是否已启用（任一方式）。"""
    return mode() != "none"


def set_enabled(enabled):
    """启用/停用自启动（注册表方式）。

    保留这个入口是为了兼容现有调用方；需要管理员权限的自启动请用
    `enable_task_autostart()`。
    """
    if not is_supported():
        return False
    return _set_registry(enabled)


def set_autostart(enabled, use_task=False):
    """统一设置自启动；``use_task=True`` 时用计划任务（带管理员权限）。

    返回 (成功, 消息)。切换模式时会清理另一种方式，避免"两处都登记"
    导致登录时启动两次。
    """
    if not enabled:
        # 关闭：两种方式都清掉
        ok_task, msg_task = disable_task_autostart()
        ok_reg = _set_registry(False)
        if ok_task and ok_reg:
            return True, "已关闭开机自启"
        return False, msg_task

    if use_task:
        # 打开计划任务时，顺手清掉注册表项，避免重复启动
        _set_registry(False)
        return enable_task_autostart()

    # 普通注册表方式：若已有计划任务，先删掉（否则会启动两次）
    if task_exists():
        disable_task_autostart()
    if not is_supported():
        return False, "仅打包后的 exe 形态支持开机自启"
    ok = _set_registry(True)
    return ok, ("已启用开机自启（普通权限）" if ok else "写入注册表失败")
