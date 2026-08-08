"""UIA 提权（可选功能，默认关闭）：让候选栏成为 UIAccess 顶级窗口，能和屏幕键盘(osk)互相覆盖。

背景（为什么需要这个）：
  Windows 8+ 引入了窗口 Band（段）概念，Z 序从高到低大致为
    ... ZBID_SYSTEM_TOOLS(任务管理器) < ZBID_UIACCESS(屏幕键盘 / 放大镜 / 我们的候选栏)。
  普通窗口只能在最低的 ZBID_DESKTOP 段，**无论怎么 SetWindowPos(HWND_TOPMOST) 都压不过
  屏幕键盘(osk.exe) / 任务管理器**——候选栏会被它们挡住、无法“互相覆盖”。
  而具有 uiAccess 的进程，其窗口会被放进 ZBID_UIACCESS 段，与屏幕键盘同层，从而能正确
  层叠/互相覆盖。这正是本开关的目的。

实现（业界通用、经开源项目验证的做法，纯 ctypes、零第三方依赖）：
  微软规定 uiAccess 需“数字签名 + 装在 Program Files”。但进程令牌上有 TokenUIAccess 属性，
  可在提权后通过 SetTokenInformation 设置，**绕过签名与安装路径**。难点是：
  设 TokenUIAccess 需要 SeTcbPrivilege，而该特权按“线程有效令牌”校验。
  故标准做法是（参考 arcanine300/CreateWindowInBand、killtimer0/uiaccess、HodUIAccessDLL 等）：
    1. 找到同会话的 winlogon.exe（SYSTEM，自带 SeTcbPrivilege）的令牌；
    2. 临时 SetThreadToken 冒充 SYSTEM；
    3. 在冒充态下复制“自己”的令牌并 SetTokenInformation(TokenUIAccess=1)；
       —— 这样新令牌仍是当前用户，只是带 UIAccess，不会变成 SYSTEM；
    4. RevertToSelf，并用这份令牌 CreateProcessAsUser **重启自身**。
  *重要*：修改“正在运行”进程的 UIAccess 无效，只能另起进程（所有开源项目一致结论）。

功能隔离与状态读取（确保默认行为不受影响）：
  * 本模块**仅在配置 use_uia_elevation=True 时才尝试**，否则 ensure_uiaccess 立即返回 False，
    对默认行为零副作用；
  * 任何一步失败（未以管理员身份运行、winlogon 令牌不可达、CreateProcessAsUser 失败）都只记录
    日志并回退为“无 UIAccess 正常运行”，绝不抛异常、绝不拖垮主程序；
  * 用环境变量 KAOMOJI_UIA_RELAUNCH 防止“重启后仍拿不到 UIAccess”导致的无限重启；
  * 启动后若已具备 UIAccess 则直接返回，不再重启（避免重复重启）。
"""
import os
import sys
import ctypes
from ctypes import wintypes, byref, c_void_p, POINTER, create_unicode_buffer, c_int

# ---- 常量 ----
TOKEN_QUERY = 0x0008
TOKEN_DUPLICATE = 0x0002
TOKEN_IMPERSONATE = 0x0004
TOKEN_ASSIGN_PRIMARY = 0x0001
TOKEN_ADJUST_DEFAULT = 0x0080
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
TokenSessionId = 12   # TOKEN_INFORMATION_CLASS 中 TokenSessionId 的真实枚举值
TokenUIAccess = 26
SecurityAnonymous = 0
SecurityImpersonation = 2
TokenImpersonation = 2
TokenPrimary = 1
TOKEN_ADJUST_PRIVILEGES = 0x0020
CREATE_UNICODE_ENVIRONMENT = 0x00000400
SE_PRIVILEGE_ENABLED = 0x00000002
_RelaUNCH_GUARD = "KAOMOJI_UIA_RELAUNCH"


class LUID(ctypes.Structure):
    _fields_ = [("LowPart", wintypes.DWORD), ("HighPart", wintypes.LONG)]


class LUID_AND_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("Luid", LUID), ("Attributes", wintypes.DWORD)]


class TOKEN_PRIVILEGES(ctypes.Structure):
    _fields_ = [("PrivilegeCount", wintypes.DWORD),
                ("Privileges", LUID_AND_ATTRIBUTES * 1)]


def ensure_uiaccess(config, log=None, on_result=None):
    """若配置开启「使用UIA提权」，尝试为进程获取 UIAccess 以便与屏幕键盘互相覆盖。

    config: 配置字典；log: 可选 callable(level, source, message) 用于记录结果；
    on_result: 可选 callable(status_dict)，在「未重启」（已具备/失败/需管理员）时回调，
               便于 UI 弹出提示；成功重启进程前不会回调（进程即将退出）。

    返回 status 字典：{"attempted","granted","needs_admin","relaunched","message"}。
    注意：开启且以管理员运行时，本函数会在获取成功后**重启当前进程**（不返回）。
    """
    status = {"attempted": False, "granted": False,
              "needs_admin": False, "relaunched": False, "message": ""}
    # —— 功能隔离：配置未开启时 ——
    if not config or not config.get("use_uia_elevation", False):
        # 但若当前进程仍带 UIAccess（之前开启过并重启过自己），必须另起一个不带
        # UIAccess 的进程，才能真正让窗口退出 ZBID_UIACCESS 高 Z 序带、取消置顶。
        # （UIAccess 是进程级令牌属性，运行中改不了，只能另起进程。）
        if sys.platform == "win32" and has_uiaccess():
            status["attempted"] = True
            status["relaunched"] = True
            _relaunch_without_uiaccess(log)      # 成功则 ExitProcess，不返回
            status["relaunched"] = False
            status["message"] = "UIA 提权已关闭，但重启进程以退出 UIAccess 失败（详见日志）"
            _emit(on_result, status)
            return status
        status["message"] = "UIA 提权未启用"
        _emit(on_result, status)
        return status
    if sys.platform != "win32":
        status["message"] = "当前平台非 Windows，UIA 提权不可用"
        _log(log, "warn", "uia_elev", status["message"])
        _emit(on_result, status)
        return status
    # —— 管理员权限检查：未通过则不可用（满足“检查通过才能使用”的约定）——
    if not is_user_admin():
        status["needs_admin"] = True
        status["message"] = "需要以管理员身份运行本程序，UIA 提权才会生效"
        _log(log, "warn", "uia_elev", status["message"])
        _emit(on_result, status)
        return status
    try:
        status["attempted"] = True
        granted = _prepare_for_uiaccess(log, status)
        status["granted"] = granted
        if granted:
            status["message"] = "进程已具备 UIAccess，候选栏可与屏幕键盘互相覆盖"
        elif not status.get("message"):
            status["message"] = "未能获取 UIAccess 令牌（详见日志）"
        _emit(on_result, status)
        return status
    except Exception as e:  # 任何意外都不应影响主程序
        status["message"] = "UIA 提权异常：%r" % (e,)
        _log(log, "error", "uia_elev", status["message"])
        _emit(on_result, status)
        return status


def _log(log, level, source, message):
    if log is not None:
        try:
            log(level, source, message)
        except Exception:
            pass


def _emit(on_result, status):
    if on_result is not None:
        try:
            on_result(status)
        except Exception:
            pass


def is_user_admin():
    """当前进程是否以管理员身份运行（UAC 提权后亦为 True）。UIA 提权的前置条件。"""
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def has_uiaccess():
    """当前进程是否实际已具备 UIAccess。用于 UI 反映真实状态，避免「配置开着但未生效」的假象。"""
    try:
        kernel32 = ctypes.windll.kernel32
        h = _open_process_token(kernel32.GetCurrentProcess(), TOKEN_QUERY)
        if h is None:
            return False
        try:
            return _token_has_uiaccess(ctypes.windll.advapi32, h)
        finally:
            kernel32.CloseHandle(h)
    except Exception:
        return False


def enable_debug_privilege(log=None):
    """启用本进程令牌的 SeDebugPrivilege，以便打开 SYSTEM 进程(winlogon)的令牌来冒充。

    管理员令牌默认拥有该特权但处于「禁用」态，必须显式启用；否则打开 winlogon 令牌会
    失败（ERROR_ACCESS_DENIED）。这正是此前「管理员下开启 UIA 仍无反应/无重启」的根因之一。
    """
    try:
        advapi32 = ctypes.windll.advapi32
        kernel32 = ctypes.windll.kernel32
        h = _open_process_token(kernel32.GetCurrentProcess(),
                                TOKEN_QUERY | TOKEN_ADJUST_PRIVILEGES)
        if h is None:
            _log(log, "warn", "uia_elev", "无法打开本进程令牌以启用 SeDebugPrivilege。")
            return False
        try:
            luid = LUID()
            if not advapi32.LookupPrivilegeValueW(None, "SeDebugPrivilege", byref(luid)):
                _log(log, "warn", "uia_elev", "LookupPrivilegeValue(SeDebugPrivilege) 失败。")
                return False
            tp = TOKEN_PRIVILEGES()
            tp.PrivilegeCount = 1
            tp.Privileges[0].Luid = luid
            tp.Privileges[0].Attributes = SE_PRIVILEGE_ENABLED
            if not advapi32.AdjustTokenPrivileges(
                    h, False, byref(tp), ctypes.sizeof(TOKEN_PRIVILEGES), None, None):
                _log(log, "warn", "uia_elev", "AdjustTokenPrivileges 失败。")
                return False
            # AdjustTokenPrivileges 即使特权不存在也可能返回 TRUE，需检查 LastError
            if kernel32.GetLastError() != 0:
                _log(log, "warn", "uia_elev",
                     "SeDebugPrivilege 实际未持有（LastError=%d）。" % kernel32.GetLastError())
                return False
            return True
        finally:
            kernel32.CloseHandle(h)
    except Exception as e:
        _log(log, "error", "uia_elev", "启用 SeDebugPrivilege 异常：%r" % (e,))
        return False


# ---------- 结构 ----------
class STARTUPINFOW(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("lpReserved", wintypes.LPWSTR),
        ("lpDesktop", wintypes.LPWSTR),
        ("lpTitle", wintypes.LPWSTR),
        ("dwX", wintypes.DWORD),
        ("dwY", wintypes.DWORD),
        ("dwXSize", wintypes.DWORD),
        ("dwYSize", wintypes.DWORD),
        ("dwXCountChars", wintypes.DWORD),
        ("dwYCountChars", wintypes.DWORD),
        ("dwFillAttribute", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("wShowWindow", wintypes.WORD),
        ("cbReserved2", wintypes.WORD),
        ("lpReserved2", ctypes.c_byte * 64),
        ("hStdInput", wintypes.HANDLE),
        ("hStdOutput", wintypes.HANDLE),
        ("hStdError", wintypes.HANDLE),
    ]


class PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("hProcess", wintypes.HANDLE),
        ("hThread", wintypes.HANDLE),
        ("dwProcessId", wintypes.DWORD),
        ("dwThreadId", wintypes.DWORD),
    ]


def _prepare_for_uiaccess(log, status):
    advapi32 = ctypes.windll.advapi32
    kernel32 = ctypes.windll.kernel32

    # 1) 若当前进程已具备 UIAccess，直接成功（不再重启，避免重复重启）
    h_self = _open_process_token(kernel32.GetCurrentProcess(), TOKEN_QUERY)
    if h_self is not None:
        if _token_has_uiaccess(advapi32, h_self):
            kernel32.CloseHandle(h_self)
            _log(log, "info", "uia_elev", "进程已具备 UIAccess，候选栏可与屏幕键盘互相覆盖。")
            return True
        kernel32.CloseHandle(h_self)

    # 2) 否则需要派生 UIAccess 令牌并重启自身（须以管理员身份运行）
    h_ui = _create_uiaccess_token(advapi32, kernel32, log)
    if h_ui is None:
        status["message"] = "无法获取 UIAccess 令牌（请以管理员身份运行，详见日志）"
        _log(log, "warn", "uia_elev",
             "无法获取 UIAccess 令牌：请确认本程序以“管理员身份”运行、且系统允许提权，「UIA 提权」才会生效。")
        return False

    # 3) 用 UIAccess 令牌重启自身（成功会 ExitProcess，不会返回）
    status["relaunched"] = True
    _relaunch(True, h_ui, log)
    # 仅当 CreateProcessAsUser 失败（未重启）时才走到这里
    status["relaunched"] = False
    status["message"] = "已生成 UIAccess 令牌，但重启进程失败（详见日志）"
    return False


def _token_has_uiaccess(advapi32, h_token):
    val = wintypes.DWORD(0)
    ret_len = wintypes.DWORD(0)
    ok = advapi32.GetTokenInformation(
        h_token, TokenUIAccess, byref(val), ctypes.sizeof(val), byref(ret_len))
    return ok != 0 and val.value != 0


def _open_process_token(proc, desired):
    advapi32 = ctypes.windll.advapi32
    h = wintypes.HANDLE()
    if advapi32.OpenProcessToken(proc, desired, byref(h)):
        return h
    return None


def _find_winlogon_pid(psapi, kernel32, advapi32, session_id):
    """枚举进程，找到同会话 winlogon.exe 的 PID（返回 None 表示未找到）。"""
    buf = (wintypes.DWORD * 2048)()
    needed = wintypes.DWORD(0)
    # 注意：数组直接传入即可（ctypes 会退化为 LPDWORD 指向首元素），
    # 不能用 byref(buf)——那样会得到“指向数组的指针”，与 POINTER(DWORD) 不匹配。
    if not psapi.EnumProcesses(buf, ctypes.sizeof(buf), byref(needed)):
        return None
    count = needed.value // ctypes.sizeof(wintypes.DWORD)
    name_buf = create_unicode_buffer(520)
    for i in range(count):
        pid = buf[i]
        if not pid:
            continue
        h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not h:
            continue
        try:
            if not psapi.GetProcessImageFileNameW(h, name_buf, 520):
                continue
            base = name_buf.value.rsplit("\\", 1)[-1].lower()
            if base != "winlogon.exe":
                continue
            ht = wintypes.HANDLE()
            if not advapi32.OpenProcessToken(h, TOKEN_QUERY | TOKEN_DUPLICATE, byref(ht)):
                continue
            try:
                sid = wintypes.DWORD(0)
                rlen = wintypes.DWORD(0)
                if (advapi32.GetTokenInformation(
                        ht, TokenSessionId, byref(sid),
                        ctypes.sizeof(sid), byref(rlen)) and sid.value == session_id):
                    return pid
            finally:
                kernel32.CloseHandle(ht)
        finally:
            kernel32.CloseHandle(h)
    return None


def _create_uiaccess_token(advapi32, kernel32, log):
    """按参考实现派生带 TokenUIAccess 的“自身”令牌；失败返回 None。"""
    # 打开 winlogon(SYSTEM) 的令牌需要 SeDebugPrivilege；管理员令牌默认拥有但处于
    # 禁用态，必须显式启用，否则打开 winlogon 令牌会失败——这正是此前“管理员下开启
    # UIA 仍无反应/无重启”的根因之一。
    if not enable_debug_privilege(log):
        return None
    psapi = ctypes.windll.psapi

    h_self = _open_process_token(kernel32.GetCurrentProcess(), TOKEN_QUERY | TOKEN_DUPLICATE)
    if h_self is None:
        return None
    try:
        # 当前会话 id
        sid = wintypes.DWORD(0)
        rlen = wintypes.DWORD(0)
        if not advapi32.GetTokenInformation(
                h_self, TokenSessionId, byref(sid),
                ctypes.sizeof(sid), byref(rlen)):
            return None

        # 找到同会话 winlogon 的 PID 并取其令牌（SYSTEM，自带 SeTcbPrivilege）
        pid = _find_winlogon_pid(psapi, kernel32, advapi32, sid.value)
        if pid is None:
            return None
        h_proc = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not h_proc:
            return None
        try:
            h_sys = wintypes.HANDLE()
            if not advapi32.OpenProcessToken(
                    h_proc, TOKEN_QUERY | TOKEN_DUPLICATE, byref(h_sys)):
                return None
        finally:
            kernel32.CloseHandle(h_proc)
        try:
            # 复制出 winlogon 的模拟令牌
            h_imp = wintypes.HANDLE()
            if not advapi32.DuplicateTokenEx(
                    h_sys, TOKEN_IMPERSONATE, None,
                    SecurityImpersonation, TokenImpersonation, byref(h_imp)):
                return None
        finally:
            kernel32.CloseHandle(h_sys)
        try:
            # 临时冒充 SYSTEM：之后设 TokenUIAccess 的 SeTcbPrivilege 校验会通过
            if not advapi32.SetThreadToken(None, h_imp):
                return None
            try:
                # 复制“自己”的令牌为 primary，并打上 UIAccess（仍是当前用户）
                h_new = wintypes.HANDLE()
                if not advapi32.DuplicateTokenEx(
                        h_self,
                        TOKEN_QUERY | TOKEN_DUPLICATE | TOKEN_ASSIGN_PRIMARY | TOKEN_ADJUST_DEFAULT,
                        None, SecurityAnonymous, TokenPrimary, byref(h_new)):
                    return None
                ui = wintypes.BOOL(1)
                if not advapi32.SetTokenInformation(
                        h_new, TokenUIAccess, byref(ui), ctypes.sizeof(wintypes.BOOL)):
                    kernel32.CloseHandle(h_new)
                    return None
                return h_new
            finally:
                advapi32.RevertToSelf()
        finally:
            kernel32.CloseHandle(h_imp)
    finally:
        kernel32.CloseHandle(h_self)


def _relaunch(target_uiaccess, h_token, log):
    """用给定令牌（其 TokenUIAccess 应已被设为 target_uiaccess）重启自身；成功则 ExitProcess，不返回。

    target_uiaccess 只用于设置防无限重启守卫：把它编码进环境变量，使其与「目标相反」的
    重启互不干扰（例如先开启→重启带 UIAccess(守卫="1")；再关闭→需要再重启去掉 UIAccess，
    此时守卫目标="0"，不会被上一次的 "1" 误拦）。
    """
    advapi32 = ctypes.windll.advapi32
    kernel32 = ctypes.windll.kernel32
    guard_val = str(int(target_uiaccess))

    # 若当前进程已经是「为达到同一目标而重启过」的第二次，则放弃，避免反复重启进程
    if os.environ.get(_RelaUNCH_GUARD) == guard_val:
        _log(log, "warn", "uia_elev",
             "已为同一目标重启过一次仍未生效，停止重试以免反复重启进程。")
        kernel32.CloseHandle(h_token)
        return False

    # 继承当前环境，并打上守卫标记，传给子进程
    env = dict(os.environ)
    env[_RelaUNCH_GUARD] = guard_val
    block = "".join("%s=%s\0" % (k, v) for k, v in env.items()) + "\0"
    env_buf = create_unicode_buffer(block)

    # 以可写副本传入当前命令行（CreateProcessAsUser 可能改动它）
    cmd = create_unicode_buffer(kernel32.GetCommandLineW())

    si = STARTUPINFOW()
    si.cb = ctypes.sizeof(STARTUPINFOW)
    pi = PROCESS_INFORMATION()

    ok = advapi32.CreateProcessAsUserW(
        h_token, None, cmd, None, None, False, CREATE_UNICODE_ENVIRONMENT,
        env_buf, None, byref(si), byref(pi))
    if ok:
        if pi.hProcess:
            kernel32.CloseHandle(pi.hProcess)
        if pi.hThread:
            kernel32.CloseHandle(pi.hThread)
        _log(log, "info", "uia_elev",
             "已以%s令牌重启进程。" % ("UIAccess" if target_uiaccess else "普通"))
        kernel32.ExitProcess(0)
    else:
        err = kernel32.GetLastError()
        _log(log, "warn", "uia_elev",
             "CreateProcessAsUser 失败 (0x%X)，UIA 提权状态切换未生效。" % (err & 0xFFFFFFFF))
        kernel32.CloseHandle(h_token)
        return False


def _relaunch_without_uiaccess(log):
    """配置已关闭但当前进程仍带 UIAccess：复制自身令牌并清掉 TokenUIAccess，
    用这份「普通」令牌重启自身，使窗口退出 ZBID_UIACCESS 高 Z 序带、取消置顶。

    注意：UIAccess 是进程级令牌属性，运行中改不了，只能另起一个不带它的进程
    （与「开启时另起带 UIAccess 的进程」完全对称）。
    """
    advapi32 = ctypes.windll.advapi32
    kernel32 = ctypes.windll.kernel32
    h_self = _open_process_token(
        kernel32.GetCurrentProcess(),
        TOKEN_QUERY | TOKEN_DUPLICATE | TOKEN_ASSIGN_PRIMARY | TOKEN_ADJUST_DEFAULT)
    if h_self is None:
        _log(log, "warn", "uia_elev", "无法打开本进程令牌以退出 UIAccess。")
        return False
    try:
        h_new = wintypes.HANDLE()
        if not advapi32.DuplicateTokenEx(
                h_self,
                TOKEN_QUERY | TOKEN_DUPLICATE | TOKEN_ASSIGN_PRIMARY | TOKEN_ADJUST_DEFAULT,
                None, SecurityAnonymous, TokenPrimary, byref(h_new)):
            _log(log, "warn", "uia_elev", "复制自身令牌失败（退出 UIAccess）。")
            return False
    finally:
        kernel32.CloseHandle(h_self)
    # _relaunch 接管 h_new 生命周期（成功 ExitProcess / 失败内部 CloseHandle）
    ui = wintypes.BOOL(0)
    if not advapi32.SetTokenInformation(
            h_new, TokenUIAccess, byref(ui), ctypes.sizeof(wintypes.BOOL)):
        _log(log, "warn", "uia_elev", "清除 TokenUIAccess 失败（退出 UIAccess）。")
        kernel32.CloseHandle(h_new)
        return False
    return _relaunch(0, h_new, log)


def _install_argtypes():
    """集中设置被调用 API 的参数/返回类型，避免 ctypes 默认推断错误。"""
    advapi32 = ctypes.windll.advapi32
    kernel32 = ctypes.windll.kernel32
    psapi = ctypes.windll.psapi

    advapi32.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, POINTER(wintypes.HANDLE)]
    advapi32.OpenProcessToken.restype = wintypes.BOOL
    advapi32.GetTokenInformation.argtypes = [
        wintypes.HANDLE, c_int, c_void_p, wintypes.DWORD, POINTER(wintypes.DWORD)]
    advapi32.GetTokenInformation.restype = wintypes.BOOL
    advapi32.DuplicateTokenEx.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, c_void_p, c_int, c_int, POINTER(wintypes.HANDLE)]
    advapi32.DuplicateTokenEx.restype = wintypes.BOOL
    advapi32.SetThreadToken.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    advapi32.SetThreadToken.restype = wintypes.BOOL
    advapi32.RevertToSelf.argtypes = []
    advapi32.RevertToSelf.restype = wintypes.BOOL
    advapi32.SetTokenInformation.argtypes = [wintypes.HANDLE, c_int, c_void_p, wintypes.DWORD]
    advapi32.SetTokenInformation.restype = wintypes.BOOL
    advapi32.LookupPrivilegeValueW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, POINTER(LUID)]
    advapi32.LookupPrivilegeValueW.restype = wintypes.BOOL
    advapi32.AdjustTokenPrivileges.argtypes = [
        wintypes.HANDLE, wintypes.BOOL, c_void_p, wintypes.DWORD, c_void_p, c_void_p]
    advapi32.AdjustTokenPrivileges.restype = wintypes.BOOL

    shell32 = ctypes.windll.shell32
    shell32.IsUserAnAdmin.argtypes = []
    shell32.IsUserAnAdmin.restype = c_int
    advapi32.CreateProcessAsUserW.argtypes = [
        wintypes.HANDLE, wintypes.LPCWSTR, wintypes.LPWSTR,
        c_void_p, c_void_p, wintypes.BOOL, wintypes.DWORD,
        wintypes.LPCWSTR, wintypes.LPCWSTR,
        POINTER(STARTUPINFOW), POINTER(PROCESS_INFORMATION)]
    advapi32.CreateProcessAsUserW.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    psapi.GetProcessImageFileNameW.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD]
    psapi.GetProcessImageFileNameW.restype = wintypes.DWORD
    kernel32.GetCommandLineW.restype = wintypes.LPWSTR
    kernel32.GetLastError.restype = wintypes.DWORD
    kernel32.ExitProcess.argtypes = [wintypes.UINT]
    kernel32.ExitProcess.restype = None

    psapi.EnumProcesses.argtypes = [POINTER(wintypes.DWORD), wintypes.DWORD, POINTER(wintypes.DWORD)]
    psapi.EnumProcesses.restype = wintypes.BOOL
    psapi.GetProcessImageFileNameW.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD]
    psapi.GetProcessImageFileNameW.restype = wintypes.DWORD


_install_argtypes()
