"""Windows 11 视觉相关：系统背景模糊（Mica / Acrylic）、深色模式、柔和阴影。"""
import ctypes
from PySide6.QtWidgets import QGraphicsDropShadowEffect
from PySide6.QtGui import QColor

try:
    _dwmapi = ctypes.windll.dwmapi
    DWMWA_SYSTEMBACKDROP = 38            # DWMWA_SYSTEMBACKDROP_TYPE (Win11)
    DWMWA_USE_IMMERSIVE_DARK_MODE = 20
    DWMSBT_NONE = 0
    DWMSBT_MAINWINDOW = 1                # Mica
    DWMSBT_TRANSIENTWINDOW = 2           # Acrylic（适合浮层工具）
    DWMSBT_TABBEDWINDOW = 3
    _has_dwm = True
except Exception:  # 非 Windows 或 API 不可用
    _has_dwm = False
    DWMWA_SYSTEMBACKDROP = 38
    DWMWA_USE_IMMERSIVE_DARK_MODE = 20
    DWMSBT_NONE = 0
    DWMSBT_MAINWINDOW = 1
    DWMSBT_TRANSIENTWINDOW = 2
    DWMSBT_TABBEDWINDOW = 3


def apply_backdrop(hwnd, backdrop=DWMSBT_TRANSIENTWINDOW):
    """为窗口设置 Win11 系统背景模糊：1=Mica, 2=Acrylic, 3=Tabbed, 0=关闭。"""
    if not _has_dwm:
        return
    try:
        value = ctypes.c_int(int(backdrop))
        _dwmapi.DwmSetWindowAttribute(
            int(hwnd), DWMWA_SYSTEMBACKDROP,
            ctypes.byref(value), ctypes.sizeof(value),
        )
    except Exception:
        pass


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


def enable_shadow(widget, blur=22, color=QColor(0, 0, 0, 55), offset=0):
    """为无边框 + 半透明窗口添加柔和阴影（会贴合圆角内容形状）。"""
    effect = QGraphicsDropShadowEffect(widget)
    effect.setBlurRadius(blur)
    effect.setColor(color)
    effect.setOffset(0, offset)
    widget.setGraphicsEffect(effect)
    return effect
