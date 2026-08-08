"""UIA 提权（可选功能，默认关闭）：让 UI Automation 能读取*管理员(提权)窗口*的 UI 文本。

背景：
  默认情况下，未提权的 UI Automation 客户端只能读取同/低完整性级别的窗口，
  无法读取以“管理员身份运行”的程序（如安装器、部分编辑器、系统工具）的输入框。
  Windows 为此提供了 uiAccess 机制：进程令牌的 TokenUIAccess=1 时即可跨完整性级别读取。

实现（业界通用的“RunAsUIAccess”技巧，纯 ctypes、零第三方依赖）：
  1. 在自身进程令牌上启用 SeTcbPrivilege（该特权通常仅管理员持有）；
  2. 复制一份令牌并把 TokenUIAccess 置 1；
  3. 用 NtSetInformationProcess(ProcessAccessToken) 把这份令牌应用到当前进程。

重要约束与功能隔离（确保默认行为不受影响）：
  * 本模块**仅在配置 use_uia_elevation=True 时才尝试**，否则 ensure_uiaccess 立即返回 False，
    对默认行为零副作用；
  * 任何一步失败（当前用户无 SeTcbPrivilege、非 Windows 平台、API 不可用）都只记录日志并
    返回 False，绝不抛异常、绝不拖垮主程序；
  * 成功后 UIA 读取管理员窗口会自动生效（core/uia_text.py 无需任何改动）。
"""
import sys


def ensure_uiaccess(config, log=None):
    """若配置开启「使用UIA提权」，尝试为当前进程启用 uiAccess 以读取管理员窗口。

    config: 配置字典；log: 可选 callable(level, source, message) 用于记录结果。
    返回 True 表示已为当前进程成功启用 uiAccess，False 表示未启用（关闭或失败）。
    """
    # —— 功能隔离：配置未开启时直接返回，不做任何系统调用 ——
    if not config or not config.get("use_uia_elevation", False):
        return False
    if sys.platform != "win32":
        _log(log, "warn", "uia_elev", "当前平台非 Windows，UIA 提权不可用。")
        return False
    try:
        return _enable_uiaccess(log)
    except Exception as e:  # 任何意外都不应影响主程序
        _log(log, "error", "uia_elev", "UIA 提权失败：%r" % (e,))
        return False


def _log(log, level, source, message):
    if log is not None:
        try:
            log(level, source, message)
        except Exception:
            pass


def _enable_uiaccess(log):
    import ctypes
    from ctypes import wintypes, byref, c_ulong, c_void_p, POINTER

    advapi32 = ctypes.windll.advapi32
    kernel32 = ctypes.windll.kernel32
    ntdll = ctypes.windll.ntdll

    # ---- 常量 ----
    TOKEN_QUERY = 0x0008
    TOKEN_ADJUST_PRIVILEGES = 0x0020
    TOKEN_DUPLICATE = 0x0002
    TOKEN_ADJUST_DEFAULT = 0x0080
    TOKEN_ASSIGN_PRIMARY = 0x0001
    SE_PRIVILEGE_ENABLED = 0x00000002
    ERROR_NOT_ALL_ASSIGNED = 1300
    SecurityImpersonation = 2
    TokenPrimary = 1
    TokenUIAccess = 26
    ProcessAccessToken = 11

    class LUID(ctypes.Structure):
        _fields_ = [("LowPart", wintypes.DWORD), ("HighPart", wintypes.LONG)]

    class TOKEN_PRIVILEGES(ctypes.Structure):
        _fields_ = [("PrivilegeCount", wintypes.DWORD),
                    ("Luid", LUID),
                    ("Attributes", wintypes.DWORD)]

    # ---- 设置参数类型，避免 ctypes 默认推断错误 ----
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    advapi32.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, POINTER(wintypes.HANDLE)]
    advapi32.OpenProcessToken.restype = wintypes.BOOL
    advapi32.LookupPrivilegeValueW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, POINTER(LUID)]
    advapi32.LookupPrivilegeValueW.restype = wintypes.BOOL
    advapi32.AdjustTokenPrivileges.argtypes = [
        wintypes.HANDLE, wintypes.BOOL, POINTER(TOKEN_PRIVILEGES),
        wintypes.DWORD, c_void_p, c_void_p,
    ]
    advapi32.AdjustTokenPrivileges.restype = wintypes.BOOL
    advapi32.DuplicateTokenEx.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, c_void_p, c_int, c_int, POINTER(wintypes.HANDLE),
    ]
    advapi32.DuplicateTokenEx.restype = wintypes.BOOL
    advapi32.SetTokenInformation.argtypes = [wintypes.HANDLE, c_int, c_void_p, wintypes.DWORD]
    advapi32.SetTokenInformation.restype = wintypes.BOOL
    ntdll.NtSetInformationProcess.argtypes = [wintypes.HANDLE, c_int, c_void_p, c_ulong]
    ntdll.NtSetInformationProcess.restype = ctypes.c_long

    # 1) 打开自身进程令牌
    h_token = wintypes.HANDLE()
    if not advapi32.OpenProcessToken(
            kernel32.GetCurrentProcess(),
            TOKEN_ADJUST_PRIVILEGES | TOKEN_QUERY,
            byref(h_token)):
        _log(log, "warn", "uia_elev", "OpenProcessToken 失败，UIA 提权需要管理员权限。")
        return False

    # 2) 启用 SeTcbPrivilege（通常仅管理员持有）
    luid = LUID()
    if not advapi32.LookupPrivilegeValueW(None, "SeTcbPrivilege", byref(luid)):
        _log(log, "warn", "uia_elev", "LookupPrivilegeValue(SeTcbPrivilege) 失败。")
        return False
    tp = TOKEN_PRIVILEGES()
    tp.PrivilegeCount = 1
    tp.Luid = luid
    tp.Attributes = SE_PRIVILEGE_ENABLED
    ctypes.SetLastError(0)
    if not advapi32.AdjustTokenPrivileges(h_token, False, byref(tp), 0, None, None):
        _log(log, "warn", "uia_elev", "AdjustTokenPrivileges 失败，UIA 提权需要管理员权限。")
        return False
    if ctypes.GetLastError() == ERROR_NOT_ALL_ASSIGNED:
        _log(log, "warn", "uia_elev",
             "无法启用 SeTcbPrivilege（需管理员身份运行），UIA 提权未生效。")
        return False

    # 3) 复制令牌并把 TokenUIAccess 置 1
    h_dup = wintypes.HANDLE()
    if not advapi32.DuplicateTokenEx(
            h_token,
            TOKEN_ADJUST_DEFAULT | TOKEN_QUERY | TOKEN_DUPLICATE | TOKEN_ASSIGN_PRIMARY,
            None, SecurityImpersonation, TokenPrimary, byref(h_dup)):
        _log(log, "warn", "uia_elev", "DuplicateTokenEx 失败。")
        return False
    one = c_ulong(1)
    if not advapi32.SetTokenInformation(h_dup, TokenUIAccess, byref(one), ctypes.sizeof(c_ulong)):
        _log(log, "warn", "uia_elev", "SetTokenInformation(TokenUIAccess) 失败。")
        return False

    # 4) 把带 uiAccess 的令牌应用到当前进程
    status = ntdll.NtSetInformationProcess(
        kernel32.GetCurrentProcess(), ProcessAccessToken,
        byref(h_dup), ctypes.sizeof(wintypes.HANDLE))
    if status != 0:
        _log(log, "warn", "uia_elev",
             "NtSetInformationProcess 返回 0x%X，UIA 提权未生效。" % (status & 0xFFFFFFFF))
        return False

    _log(log, "info", "uia_elev", "UIA 提权成功：当前进程已可读取管理员窗口的 UI。")
    return True
