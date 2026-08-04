"""把选中的颜文字输入到之前焦点所在窗口。

提供两种方式：
  * "clipboard"：写入剪贴板后发送 Ctrl+V，默认方式。
    发送的是命令键（Ctrl+V），不会被中文输入法（如微软拼音）拦截，
    因此对颜文字兼容性最好、在中文 Windows 下最稳。用后自动恢复用户原剪贴板。
  * "type"：用 SendInput 以 KEYEVENTF_UNICODE 直接把字符送进前台窗口。
    Unicode 输入走 WM_CHAR，彻底绕开键盘布局与中文输入法（IME），
    因此即便前台是微软拼音也不会把颜文字吞进组字缓冲区变成乱码（如「哦哦」）。
    这是比「切键盘布局」更彻底的做法——IME 根本没机会介入。
"""
import ctypes
import time
from ctypes import wintypes

from pynput.keyboard import Controller, Key

from PySide6.QtCore import QTimer, QMimeData
from PySide6.QtWidgets import QApplication

from core import win_utils


# ---- Unicode 直接键入（绕过 IME）----
# SendInput 的 KEYBOARD_INPUT 结构（x64 布局）。type==1(INPUT_KEYBOARD) 时
# 联合体按 KEYBDINPUT 解释，故这里把 KEYBDINPUT 字段内联，再用 _pad 把结构体
# 补齐到 32 字节（与 Windows INPUT 大小一致，SendInput 按 cbSize 读取）。
_INPUT_KEYBOARD = 1
_KEYEVENTF_UNICODE = 0x0004
_KEYEVENTF_KEYUP = 0x0002


class _INPUT(ctypes.Structure):
    _fields_ = [
        ("type", wintypes.DWORD),
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_ulonglong),
        ("_pad", ctypes.c_ubyte * 8),
    ]


_user32 = ctypes.windll.user32
_user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(_INPUT), ctypes.c_int]
_user32.SendInput.restype = wintypes.UINT


class KaomojiInjector:
    def __init__(self):
        self._controller = Controller()

    def inject(self, text, method="clipboard"):
        if not text:
            return
        if method == "clipboard":
            self._inject_clipboard(text)
            return
        # 模拟键入模式：用 KEYEVENTF_UNICODE 直接发 Unicode 字符，
        # 由系统转成 WM_CHAR 送到前台窗口，彻底绕过键盘布局与中文输入法(IME)，
        # 因此不会像 VK 键击那样被微软拼音吞进组字缓冲区变成「哦哦」。
        # 万一 Unicode 输入失败，退回 pynput 模拟并临时切英文布局兜底。
        try:
            self._type_unicode(text)
        except Exception:
            self._type_fallback(text)

    def _type_unicode(self, text):
        """用 SendInput + KEYEVENTF_UNICODE 逐字符键入，绕过 IME。

        把字符串拆成 UTF-16 代码单元（BMP 外字符用代理对），每个单元发 down/up 两事件。
        Unicode 输入由前台窗口直接收为 WM_CHAR，IME 不介入，故无乱码。
        """
        units = []
        for ch in text:
            cp = ord(ch)
            if cp > 0xFFFF:
                cp -= 0x10000
                units.append(0xD800 + (cp >> 10))
                units.append(0xDC00 + (cp & 0x3FF))
            else:
                units.append(cp)
        if not units:
            return
        n = len(units)
        arr = (_INPUT * (2 * n))()
        idx = 0
        for cu in units:
            down = arr[idx]
            idx += 1
            down.type = _INPUT_KEYBOARD
            down.wScan = cu
            down.dwFlags = _KEYEVENTF_UNICODE
            up = arr[idx]
            idx += 1
            up.type = _INPUT_KEYBOARD
            up.wScan = cu
            up.dwFlags = _KEYEVENTF_UNICODE | _KEYEVENTF_KEYUP
        sent = _user32.SendInput(2 * n, arr, ctypes.sizeof(_INPUT))
        if sent != 2 * n:
            raise RuntimeError("SendInput sent %d/%d" % (sent, 2 * n))

    def _type_fallback(self, text):
        """Unicode 输入失败时的兜底：临时切到英文(US)布局再键入，打完切回。"""
        hwnd = win_utils.get_foreground_hwnd()
        saved_layout = win_utils.get_keyboard_layout(hwnd)
        eng = win_utils.ensure_english_layout()
        switched = bool(eng and saved_layout and saved_layout != eng)
        if switched:
            win_utils.set_keyboard_layout(hwnd, eng)
        try:
            # 等前景窗口的消息循环处理完布局切换，避免首字符仍被 IME 截获
            time.sleep(0.08)
            self._controller.type(text)
        except Exception:
            try:
                time.sleep(0.08)
                self._controller.type(text)
            except Exception:
                pass
        finally:
            # 无论成败都切回用户原来的键盘布局
            if switched and saved_layout:
                win_utils.set_keyboard_layout(hwnd, saved_layout)

    def _snapshot_clipboard(self, clipboard):
        """深拷贝当前剪贴板内容到一个全新的 QMimeData（由我们持有所有权）。

        关键：QClipboard.mimeData() 返回的是「剪贴板内部对象」的指针，Qt 文档明确
        说明它归剪贴板所有、不应长期持有；一旦随后调用 setText/setMimeData 替换了
        剪贴板内容，旧对象会被销毁，原本的指针即悬空（use-after-free）。
        若把悬空指针再 setMimeData 回去就会随机崩溃（输入颜文字后偶发卡死/崩溃的根因）。
        故这里复制出一份独立副本，替换剪贴板后原指针销毁也不影响我们的副本。
        """
        snap = QMimeData()
        try:
            src = clipboard.mimeData()
            if src is not None:
                for fmt in src.formats():
                    snap.setData(fmt, src.data(fmt))
        except Exception:
            pass
        return snap

    def _inject_clipboard(self, text):
        clipboard = QApplication.clipboard()
        # 先快照（独立副本），再替换剪贴板，确保恢复时用的不是悬空指针
        saved = self._snapshot_clipboard(clipboard)
        clipboard.setText(text)
        c = self._controller
        c.press(Key.ctrl)
        c.press("v")
        c.release("v")
        c.release(Key.ctrl)
        # 粘贴完成后恢复原剪贴板，避免覆盖用户内容
        QTimer.singleShot(
            250, lambda s=saved: clipboard.setMimeData(s)
        )
