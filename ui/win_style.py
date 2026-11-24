"""Windows 视觉相关：深色模式标题栏等系统属性。

## 系统亚克力已**完全移除**

这里原本有 `apply_backdrop()` 调 `DwmSetWindowAttribute(
DWMWA_SYSTEMBACKDROP_TYPE)` 开系统亚克力（Mica/Acrylic）。

移除原因：

1. **Win10 上完全无效**。该属性是 Win11 (build 22000+) 才有的，Win10 返回
   `0x80070057`（E_INVALIDARG）。实测本机（Win10 19045）确认。
2. **会与自绘材质冲突**。Win11 上系统亚克力会接管窗口背景绘制，而我们的
   窗口用 `paintEvent` 自绘面板（含自定义背景图和毛玻璃）。两者叠加会出现
   颜色发灰、圆角边缘漏底色等问题。
3. **它只能模糊桌面**，无法模糊我们自己的背景图——与"自定义背景图 + 毛玻璃"
   的需求天然矛盾。

现在背景材质全部由 `ui/window_material.py` 自绘，跨系统一致。
"""
import ctypes

try:
    _dwmapi = ctypes.windll.dwmapi
    DWMWA_USE_IMMERSIVE_DARK_MODE = 20
    _has_dwm = True
except Exception:  # 非 Windows 或 API 不可用
    _has_dwm = False
    DWMWA_USE_IMMERSIVE_DARK_MODE = 20


def is_win11():
    """当前是否 Windows 11（build 22000+）。"""
    import sys

    if sys.platform != "win32":
        return False
    try:
        return sys.getwindowsversion().build >= 22000
    except Exception:
        return False




def apply_dark_mode(hwnd, dark=True):
    """开启/关闭沉浸式深色模式（影响系统绘制的部分，如右键菜单）。"""
    if not _has_dwm:
        return
    try:
        value = ctypes.c_int(1 if dark else 0)
        _dwmapi.DwmSetWindowAttribute(
            int(hwnd), DWMWA_USE_IMMERSIVE_DARK_MODE,
            ctypes.byref(value), ctypes.sizeof(value),
        )
    except Exception:
        pass
