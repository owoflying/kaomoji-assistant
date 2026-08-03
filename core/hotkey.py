"""基于 Windows 原生 RegisterHotKey 的全局热键管理器。

相比 pynput GlobalHotKeys 的常驻低层钩子，这种方式：
  * 不拦截系统每一次按键 -> 彻底消除“调用卡顿”；
  * 命中组合键时只向指定窗口投递一条 WM_HOTKEY 消息，由 Qt 事件循环派发，
    因此无需额外线程，也不存在跨线程操作控件的风险。

两种热键形态：
  * 简单热键（如 Ctrl+Shift+K）：修饰键 + 单个主键，走 RegisterHotKey，零钩子零卡顿。
  * 多键序列（如 k+l、-+=）：Windows 原生 API 不支持和弦，需挂一个仅比对一小段
    目标序列的轻量按键监听（仅当配置了序列热键时才挂，不配置则完全不挂任何钩子）。
"""
import time

import ctypes
from ctypes import wintypes

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import QWidget

from core.win_utils import user32, WM_HOTKEY, parse_hotkey

# 修饰键的 VK（在序列检测中忽略，避免污染序列缓冲）
_MOD_VKS = frozenset([16, 17, 18, 91, 92, 160, 161, 162, 163, 164])


class _MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("message", wintypes.UINT),
        ("wParam", wintypes.WPARAM),
        ("lParam", wintypes.LPARAM),
        ("time", wintypes.DWORD),
        ("pt", wintypes.LONG * 2),
    ]


class _HotkeyHost(QWidget):
    """隐藏的原生窗口，仅用于接收 WM_HOTKEY 消息。"""

    hotkey_pressed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.FramelessWindowHint)
        # 强制创建原生窗口句柄（不显示），RegisterHotKey 需要它
        self.winId()

    def nativeEvent(self, eventType, message):
        if eventType == "windows_generic_MSG":
            try:
                msg = ctypes.cast(int(message), ctypes.POINTER(_MSG))[0]
                if msg.message == WM_HOTKEY:
                    self.hotkey_pressed.emit()
                    return True, 0
            except Exception:
                pass
        return super().nativeEvent(eventType, message)


def _vk_of_pynput_key(key):
    """把 pynput 的按键对象转换成 VK（修饰键返回其 VK 但调用方应忽略）。"""
    if hasattr(key, "vk") and key.vk:
        return key.vk
    if hasattr(key, "char") and key.char:
        from core.win_utils import _vk_from_char
        v = _vk_from_char(key.char)
        if v:
            return v
    return None


class NativeHotkeyManager(QObject):
    """全局热键管理器：start(hotkey_str) 注册，hotkey_pressed 信号回调。"""

    hotkey_pressed = Signal()

    def __init__(self):
        super().__init__()
        self._host = _HotkeyHost()
        self._host.hotkey_pressed.connect(self.hotkey_pressed)
        self._id = 1
        self._active = False
        self._mode = None            # "simple" | "sequence" | None
        self._listener = None        # 序列模式用的 pynput 监听
        self._seq = []               # 目标序列 VK 列表
        self._buf = []               # 最近按键缓冲 [(vk, timestamp), ...]
        self._seq_window = 0.8       # 序列有效时间窗（秒）

    def start(self, hotkey_str):
        self.stop()
        parsed = parse_hotkey(hotkey_str or "")
        if parsed["type"] == "simple":
            mods, vk = parsed["mods"], parsed["vk"]
            if vk == 0:
                return
            hwnd = int(self._host.winId())
            if user32.RegisterHotKey(hwnd, self._id, mods, vk):
                self._active = True
                self._mode = "simple"
        elif parsed["type"] == "sequence":
            self._seq = list(parsed["keys"])
            if not self._seq:
                return
            self._start_seq_listener()
            if self._listener is not None:
                self._active = True
                self._mode = "sequence"

    def _start_seq_listener(self):
        try:
            from pynput import keyboard as kb
        except ImportError:
            return
        manager = self

        def on_press(key):
            vk = _vk_of_pynput_key(key)
            if vk is None or vk in _MOD_VKS:
                return
            now = time.time()
            manager._buf.append((vk, now))
            cutoff = now - manager._seq_window
            manager._buf = [(v, t) for v, t in manager._buf if t >= cutoff]
            recent = [v for v, _ in manager._buf]
            if len(recent) >= len(manager._seq) and \
               recent[-len(manager._seq):] == manager._seq:
                manager._buf = []  # 重置，避免连续触发
                # 直接 emit：跨线程时 Qt 自动排队到主线程，已实测可正确投递
                manager.hotkey_pressed.emit()

        self._listener = kb.Listener(on_press=on_press)
        self._listener.start()

    def stop(self):
        if self._mode == "simple" and self._active:
            try:
                user32.UnregisterHotKey(int(self._host.winId()), self._id)
            except Exception:
                pass
        if self._listener is not None:
            try:
                self._listener.stop()
            except Exception:
                pass
            self._listener = None
        self._active = False
        self._mode = None
        self._buf = []
