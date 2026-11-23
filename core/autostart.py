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


#: `task_exists()` 的缓存：True / False / None(未查过)。
#:
#: ## 为什么必须缓存
#:
#: `task_exists()` 要起一个 `schtasks` 子进程，**单次约 200ms**。
#: 而调用链 `is_enabled() → mode() → task_autostart_enabled() → task_exists()`
#: 每次刷新设置页都会跑一遍——实测切一次主题就多花 397ms（跑了两遍）。
#:
#: 自启动状态只在本进程改动它时才会变，所以进程内缓存是安全的；
#: 真改了就用 `_invalidate_task_cache()` 主动失效。
_task_exists_cache = None


def _invalidate_task_cache():
    """本进程改动过计划任务后，让缓存失效。"""
    global _task_exists_cache
    _task_exists_cache = None


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
    """计划任务是否已存在（**进程内缓存**，见 `_task_exists_cache`）。"""
    global _task_exists_cache
    if sys.platform != "win32":
        return False
    if _task_exists_cache is None:
        ok, _ = _run_schtasks(["/query", "/tn", TASK_NAME])
        _task_exists_cache = bool(ok)
    return _task_exists_cache


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


#: 计划任务的 XML 模板。
#:
#: ## 为什么不再用 `schtasks /create` 的简化参数
#:
#: `schtasks /create /tr ...` 默认生成的任务**没有 `<Hidden>`**，而且会带上
#: `<DisallowStartIfOnBatteries>true</DisallowStartIfOnBatteries>`。实测后果：
#:
#: * 登录时任务启动的那一瞬间，Windows 会在屏幕上闪一个**控制台窗口**
#:   （用户报的就是"开机自启弹一个命令行窗口"）。目标 exe 本身是 GUI 子系统
#:   （已核实 PE 头 Subsystem=2），所以黑框不是程序画的，而是任务宿主在
#:   拉起它之前短暂显示的。
#: * 笔记本用电池开机时**根本不会启动**。
#:
#: 用 `/xml` 直接给出完整定义就能规避：显式 `<Hidden>true</Hidden>`
#: 加上电源策略全设为 false。
_TASK_XML = """<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>颜文字输入辅助器：登录时自动启动</Description>
  </RegistrationInfo>
  <Triggers>
    <LogonTrigger>
      <Enabled>true</Enabled>
      <UserId>{user}</UserId>
    </LogonTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>{user}</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>HighestAvailable</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>false</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <IdleSettings>
      <StopOnIdleEnd>false</StopOnIdleEnd>
      <RestartOnIdle>false</RestartOnIdle>
    </IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>true</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Priority>7</Priority>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{exe}</Command>
    </Exec>
  </Actions>
</Task>
"""


def _current_user_sid():
    """取当前用户的 SID 字符串（任务 XML 的 `<UserId>` 要它）。

    ## 为什么不能直接用用户名

    任务计划服务接受 `DOMAIN\\User` 或 `S-1-5-21-...` 两种形式，但
    **XML 里用 `%USERDOMAIN%\\%USERNAME%` 这种环境变量写法是不展开的**
    ——它会当成一个字面量用户名，创建失败或建出一条指向不存在账号的任务。

    ## 读取方式

    `HKCU\\...\\Authentication\\Volatile\\UserSidString` 是最直接的来源，
    但该键在部分系统上不存在。所以再退一步用 `whoami /user`（系统自带，
    输出形如 `用户 SID`，取最后一段）。
    """
    # 1) 注册表（最快，通常可用）
    try:
        import winreg

        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                             r"Software\Microsoft\Windows\CurrentVersion"
                             r"\Authentication\Volatile")
        try:
            sid, _ = winreg.QueryValueEx(key, "UserSidString")
            sid = str(sid).strip()
            if sid.startswith("S-1-"):
                return sid
        finally:
            winreg.CloseKey(key)
    except Exception:
        pass

    # 2) whoami /user（稳定可用，约 40ms）
    try:
        proc = subprocess.run(
            ["whoami", "/user", "/fo", "csv", "/nh"],
            capture_output=True, text=True, timeout=5,
            creationflags=_NO_WINDOW,
        )
        out = (proc.stdout or "").strip()
        if out:
            # CSV 形如 "host\user","S-1-5-21-..."
            parts = [p.strip('"') for p in out.replace('"', '').split(",")]
            for p in parts:
                if p.startswith("S-1-"):
                    return p
    except Exception:
        pass

    # 3) 兜底：DOMAIN\Username 形式（计划任务服务认这个）
    try:
        dom = os.environ.get("USERDOMAIN", "")
        usr = os.environ.get("USERNAME", "")
        if dom and usr:
            return "%s\\%s" % (dom, usr)
        if usr:
            return usr
    except Exception:
        pass
    return ""


def enable_task_autostart():
    """创建/覆盖「登录时以最高权限、**隐藏**启动」的计划任务。

    返回 (成功, 消息)。需要管理员权限——非管理员时 Windows 返回
    `Access is denied`，这里原样带回给调用方用于提示。
    """
    exe = exe_path()
    if sys.platform != "win32":
        return False, "当前平台不支持计划任务"
    if not exe:
        return False, "仅打包后的 exe 形态支持开机自启"

    user = _current_user_sid()
    if not user:
        return False, "无法获取当前用户标识，创建计划任务失败"

    xml = _TASK_XML.format(user=user, exe=exe)

    # 用 /xml 创建：能带 <Hidden>true</Hidden> 与电源策略，
    # 避免登录时闪出控制台窗口（详见 _TASK_XML 的说明）。
    ok, out = _create_task_from_xml(xml)
    _invalidate_task_cache()
    if ok:
        return True, "已创建开机自启计划任务（登录时以管理员权限静默运行）"
    if "denied" in out.lower() or "拒绝访问" in out:
        return False, "创建计划任务需要管理员权限，请先以管理员身份运行本程序"
    return False, "创建计划任务失败：%s" % (out or "未知错误")


def _create_task_from_xml(xml):
    """把 XML 写到临时文件，用 `schtasks /create /xml` 导入。

    `/xml` 要求文件是 **UTF-16 LE 带 BOM**（Task Scheduler 的硬要求），
    写成 UTF-8 会报 "无法分析" 或乱码，所以这里显式编码。
    """
    import tempfile

    path = ""
    try:
        fd, path = tempfile.mkstemp(suffix=".xml")
        os.close(fd)
        with open(path, "w", encoding="utf-16") as f:
            f.write(xml)
        return _run_schtasks(["/create", "/tn", TASK_NAME, "/xml", path, "/f"])
    except Exception as e:
        return False, "%r" % (e,)
    finally:
        if path:
            try:
                os.remove(path)
            except Exception:
                pass


def disable_task_autostart():
    """删除计划任务。返回 (成功, 消息)。任务不存在视为成功。"""
    if sys.platform != "win32":
        return False, "当前平台不支持计划任务"
    if not task_exists():
        return True, "计划任务不存在"
    ok, out = _run_schtasks(["/delete", "/tn", TASK_NAME, "/f"])
    # 任务状态变了，缓存必须失效
    _invalidate_task_cache()
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
