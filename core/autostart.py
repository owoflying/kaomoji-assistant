"""开机自启动：在 HKCU\\...\\Run 下登记 / 注销本程序。

使用当前用户（HKCU）而非系统（HKLM），无需管理员权限。
仅在打包后的 exe 形态下生效（源码运行无法可靠自启动）。
"""
import sys

try:
    import winreg
except ImportError:
    winreg = None

from core.runtime import APP_NAME

REG_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"


def exe_path():
    if getattr(sys, "frozen", False):
        return sys.executable
    return None


def is_supported():
    """当前环境是否支持自启动（仅打包 exe 形态）。"""
    return winreg is not None and exe_path() is not None


def is_enabled():
    if winreg is None:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_PATH) as key:
            winreg.QueryValueEx(key, APP_NAME)
        return True
    except OSError:
        return False


def set_enabled(enabled):
    if not is_supported():
        return False
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, REG_PATH, 0, winreg.KEY_SET_VALUE
        ) as key:
            if enabled:
                winreg.SetValueEx(
                    key, APP_NAME, 0, winreg.REG_SZ, '"%s"' % exe_path()
                )
            else:
                try:
                    winreg.DeleteValue(key, APP_NAME)
                except OSError:
                    pass
        return True
    except OSError:
        return False
