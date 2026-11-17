"""纯 Win32 的加载指示窗口：由**子线程**创建、绘制、销毁。

## 为什么不能用 Qt

实测确认了硬约束：**所有 Qt 窗口的绘制都由 GUI 主线程执行**，
与窗口属于哪个线程无关。

    （同一 QWidget 在主线程里用 20ms 定时器刷新）
    正常运行时      ：12 次 paintEvent
    主线程阻塞 500ms： 0 次
    恢复后          ：11 次

换肤期间主线程被 `setStyleSheet` 卡住，Qt 窗口必然定格。

## 为什么 Win32 可以

Win32 的规则是「**窗口消息由创建它的线程派发**」。
只要窗口是在子线程里 `CreateWindowExW` 创建的，它的消息循环就
跑在那个子线程上，`WM_PAINT` / `WM_TIMER` 都由子线程自己处理——
**完全不依赖主线程**。

GDI 的绘制（`GetDC` / `Ellipse` / `TextOut`）也都在子线程完成。

## 这个窗口长什么样

无边框、圆角、居中的小卡片：
    ┌──────────────────┐
    │        ◜         │   旋转圆弧（子线程 WM_TIMER 驱动）
    │  正在切换主题…    │
    └──────────────────┘

颜色按深浅主题选。用 `WS_EX_LAYERED` 做整体半透明，
`WS_EX_TOOLWINDOW` 避免出现在任务栏。
"""

import ctypes
import math
import threading
from ctypes import wintypes as w

# ---- Win32 常量 ----
WS_EX_LAYERED = 0x00080000
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_TOPMOST = 0x00000008
WS_EX_TRANSPARENT = 0x00000020
WS_EX_NOACTIVATE = 0x08000000

WS_POPUP = 0x80000000
WS_VISIBLE = 0x10000000

WM_DESTROY = 0x0002
WM_CLOSE = 0x0010
WM_PAINT = 0x000F
WM_TIMER = 0x0113
WM_ERASEBKGND = 0x0014
WM_SETCURSOR = 0x0020

LWA_ALPHA = 0x00000002
TIMER_ID = 1
TIMER_MS = 40

DT_CENTER = 0x00000001
DT_VCENTER = 0x00000004

#: 卡片尺寸与圆角
CARD_W = 220
CARD_H = 112
RADIUS = 14
#: 旋转圆弧半径 / 线宽
ARC_R = 15
ARC_W = 3


class _GDI:
    """GDI32 函数签名（显式声明，避免 64 位句柄被截断）。"""

    def __init__(self):
        self.g = ctypes.WinDLL("gdi32", use_last_error=True)
        self.u = ctypes.WinDLL("user32", use_last_error=True)
        # ⚠ GetModuleHandleW 在 **kernel32**，不在 user32
        self.k = ctypes.WinDLL("kernel32", use_last_error=True)
        self.k.GetModuleHandleW.argtypes = [ctypes.c_wchar_p]
        self.k.GetModuleHandleW.restype = w.HINSTANCE

        self.g.CreateSolidBrush.argtypes = [w.DWORD]
        self.g.CreateSolidBrush.restype = w.HBRUSH
        self.g.CreatePen.argtypes = [ctypes.c_int, ctypes.c_int, w.DWORD]
        self.g.CreatePen.restype = w.HPEN
        self.g.DeleteObject.argtypes = [w.HGDIOBJ]
        self.g.DeleteObject.restype = w.BOOL

        self.g.SelectObject.argtypes = [w.HDC, w.HGDIOBJ]
        self.g.SelectObject.restype = w.HGDIOBJ
        self.g.DeleteDC.argtypes = [w.HDC]
        self.g.SetBkMode.argtypes = [w.HDC, ctypes.c_int]
        self.g.SetTextColor.argtypes = [w.HDC, w.DWORD]
        self.g.Ellipse.argtypes = [w.HDC, ctypes.c_int, ctypes.c_int,
                                   ctypes.c_int, ctypes.c_int]
        self.g.Arc.argtypes = [w.HDC, ctypes.c_int, ctypes.c_int,
                               ctypes.c_int, ctypes.c_int,
                               ctypes.c_int, ctypes.c_int,
                               ctypes.c_int, ctypes.c_int]
        self.g.MoveToEx.argtypes = [w.HDC, ctypes.c_int, ctypes.c_int,
                                    ctypes.c_void_p]
        self.g.LineTo.argtypes = [w.HDC, ctypes.c_int, ctypes.c_int]
        self.g.RoundRect.argtypes = [w.HDC, ctypes.c_int, ctypes.c_int,
                                     ctypes.c_int, ctypes.c_int,
                                     ctypes.c_int, ctypes.c_int]

        self.u.GetDC.argtypes = [w.HWND]
        self.u.GetDC.restype = w.HDC
        self.u.ReleaseDC.argtypes = [w.HWND, w.HDC]
        self.u.SetLayeredWindowAttributes.argtypes = [
            w.HWND, w.DWORD, ctypes.c_ubyte, w.DWORD]
        # SetTimer 的回调传 None（我们用 WM_TIMER 消息而不是回调函数）
        self.u.SetTimer.argtypes = [w.HWND, ctypes.c_size_t, w.UINT,
                                    ctypes.c_void_p]

        # ---- 窗口生命周期 / 消息 ----
        # ⚠ LoadCursorW 的第二个参数是 MAKEINTRESOURCE 宏（整数资源 ID），
        # 不是字符串。声明成 c_void_p 才能传 32512。
        self.u.LoadCursorW.argtypes = [w.HINSTANCE, ctypes.c_void_p]
        self.u.LoadCursorW.restype = w.HANDLE
        self.u.ShowWindow.argtypes = [w.HWND, ctypes.c_int]
        self.u.UpdateWindow.argtypes = [w.HWND]
        self.u.InvalidateRect.argtypes = [w.HWND, ctypes.c_void_p, w.BOOL]
        self.u.BeginPaint.argtypes = [w.HWND, ctypes.POINTER(PAINTSTRUCT)]
        self.u.BeginPaint.restype = w.HDC
        self.u.EndPaint.argtypes = [w.HWND, ctypes.POINTER(PAINTSTRUCT)]
        self.u.KillTimer.argtypes = [w.HWND, ctypes.c_size_t]
        self.u.IsWindow.argtypes = [w.HWND]
        self.u.DestroyWindow.argtypes = [w.HWND]
        self.u.PostMessageW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM]
        self.u.GetMessageW.argtypes = [ctypes.POINTER(w.MSG), w.HWND,
                                       w.UINT, w.UINT]
        self.u.TranslateMessage.argtypes = [ctypes.POINTER(w.MSG)]
        self.u.DispatchMessageW.argtypes = [ctypes.POINTER(w.MSG)]
        self.u.DrawTextW.argtypes = [w.HDC, w.LPCWSTR, ctypes.c_int,
                                     ctypes.POINTER(w.RECT), w.UINT]

        self.g.GetStockObject.argtypes = [ctypes.c_int]
        self.g.GetStockObject.restype = w.HGDIOBJ


def _rgb(r, g, b):
    """COLORREF 是 0x00BBGGRR。"""
    return (b << 16) | (g << 8) | r


def _ptr(v):
    """把消息参数转成 ctypes 能安全接受的指针值。

    回调交来的 wparam/lparam 可能是任意大的整数；直接传给声明为
    定长整型的函数会溢出。转成 `c_void_p` 就交给 ctypes 按字长截断。
    """
    try:
        return ctypes.c_void_p(int(v) & 0xFFFFFFFFFFFFFFFF)
    except Exception:
        return ctypes.c_void_p(0)


def _signed(v):
    """把可能是大正整数的消息参数按 64 位有符号重新解释。

    Qt/ctypes 的回调里，WPARAM/LPARAM 可能是 0xFFFFFFFFxxxx 这种值，
    直接传给声明成 c_ssize_t 的函数会触发 OverflowError。
    """
    try:
        v = int(v)
    except Exception:
        return 0
    if v >= (1 << 63):
        v -= 1 << 64
    return v


class PAINTSTRUCT(ctypes.Structure):
    """ctypes.wintypes 里没有 PAINTSTRUCT，自己声明。"""

    _fields_ = [
        ("hdc", w.HDC),
        ("fErase", w.BOOL),
        ("rcPaint", w.RECT),
        ("fRestore", w.BOOL),
        ("fIncUpdate", w.BOOL),
        ("rgbReserved", ctypes.c_byte * 32),
    ]


class Win32SpinnerWindow:
    """在子线程里创建并绘制的加载指示窗口。

    用法：

        win = Win32SpinnerWindow("正在切换主题…", dark=False)
        win.start(cx, cy)      # 屏幕坐标中心
        ...
        win.stop()
    """

    def __init__(self, text="正在切换主题…", dark=False):
        self._text = text
        self._dark = bool(dark)
        self._angle = 0.0
        self._hwnd = None
        self._thread = None

    # ------------------------------------------------------------ 对外
    def start(self, cx, cy):
        """在 (cx, cy) 屏幕坐标处显示并启动子线程。"""
        # 上一次的线程如果卡在消息循环里，先让它退出
        if self._thread is not None and self._thread.is_alive():
            self.stop()
            self.join(1.0)
        self._angle = 0.0
        self._hwnd = None
        self._thread = threading.Thread(
            target=self._run, args=(int(cx), int(cy)), daemon=True,
            name="kaomoji-spinner")
        self._thread.start()

    def _wait_ready(self, timeout=1.5):
        """等子线程把窗口建起来（供测试/调试用，UI 路径不需要）。"""
        import time

        t0 = time.time()
        while time.time() - t0 < timeout:
            if self._hwnd:
                return True
            time.sleep(0.01)
        return False

    def stop(self):
        """通知子线程关掉窗口（非阻塞）。"""
        hwnd = self._hwnd
        if not hwnd:
            return
        _GDI().u.PostMessageW(hwnd, WM_CLOSE, 0, 0)

    def join(self, timeout=2.0):
        if self._thread is not None:
            self._thread.join(timeout)
            # 线程若仍未退出（消息循环卡住），强制标记结束，
            # 避免下一次 start() 复用到僵尸线程
            if self._thread.is_alive():
                self._hwnd = None
            self._thread = None

    # ------------------------------------------------------------ 子线程
    def _run(self, cx, cy):
        gdi = _GDI()
        u = gdi.u

        hinst = gdi.k.GetModuleHandleW(None)
        # ⚠ 类名必须**每个实例唯一**。
        # Windows 不允许两个线程同时注册同名窗口类；上一个窗口还没
        # 完全销毁时复用同名会被拒绝，连带新窗口建不出来。
        class_name = "KaomojiSpinner_%x" % id(self)

        # ---- 注册窗口类（每个线程各注册一次，同名可覆盖） ----
        # wparam/lparam 用有符号 64 位，与 DefWindowProcW 的签名一致，
        # 避免 "int too long to convert" 的溢出错误
        WNDPROC = ctypes.WINFUNCTYPE(
            ctypes.c_ssize_t, w.HWND, w.UINT, ctypes.c_ssize_t,
            ctypes.c_ssize_t)

        self._wndproc_ref = WNDPROC(self._wndproc)   # 保住引用防 GC

        class WNDCLASSEXW(ctypes.Structure):
            _fields_ = [
                ("cbSize", w.UINT), ("style", w.UINT),
                ("lpfnWndProc", WNDPROC),
                ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                ("hInstance", w.HINSTANCE), ("hIcon", w.HICON),
                ("hCursor", w.HANDLE), ("hbrBackground", w.HBRUSH),
                ("lpszMenuName", w.LPCWSTR), ("lpszClassName", w.LPCWSTR),
                ("hIconSm", w.HICON),
            ]

        u.RegisterClassExW.argtypes = [ctypes.POINTER(WNDCLASSEXW)]
        u.RegisterClassExW.restype = w.ATOM
        u.CreateWindowExW.argtypes = [
            w.DWORD, w.LPCWSTR, w.LPCWSTR, w.DWORD,
            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            w.HWND, w.HMENU, w.HINSTANCE, w.LPVOID]
        u.CreateWindowExW.restype = w.HWND
        # ⚠ 用 c_void_p 而不是 c_ssize_t：ctypes 会自动把整数按字长截断，
        # 不会因回调传来大整数而 OverflowError
        u.DefWindowProcW.argtypes = [w.HWND, w.UINT, ctypes.c_void_p,
                                     ctypes.c_void_p]
        u.DefWindowProcW.restype = ctypes.c_ssize_t
        u.DefWindowProcW.restype = ctypes.c_ssize_t
        u.PostQuitMessage.argtypes = [ctypes.c_int]

        wc = WNDCLASSEXW()
        wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
        wc.style = 0
        wc.lpfnWndProc = self._wndproc_ref
        wc.hInstance = hinst
        wc.hCursor = u.LoadCursorW(None, ctypes.c_void_p(32512))  # IDC_ARROW
        wc.lpszClassName = class_name
        u.RegisterClassExW(ctypes.byref(wc))

        style = WS_POPUP | WS_VISIBLE
        ex = (WS_EX_LAYERED | WS_EX_TOOLWINDOW | WS_EX_TOPMOST
              | WS_EX_TRANSPARENT | WS_EX_NOACTIVATE)
        x = cx - CARD_W // 2
        y = cy - CARD_H // 2

        hwnd = u.CreateWindowExW(
            ex, class_name, "正在切换主题", style,
            x, y, CARD_W, CARD_H, None, None, hinst, None)
        if not hwnd:
            return

        self._hwnd = hwnd

        # 整体轻微半透明（不影响卡片自身的不透明度观感）
        u.SetLayeredWindowAttributes(hwnd, 0, 245, LWA_ALPHA)
        u.ShowWindow(hwnd, 5)          # SW_SHOW
        u.UpdateWindow(hwnd)

        # 子线程自己的定时器：驱动旋转
        u.SetTimer(hwnd, TIMER_ID, TIMER_MS, None)

        # ---- 子线程的消息循环（与主线程完全无关） ----
        msg = w.MSG()
        while u.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            u.TranslateMessage(ctypes.byref(msg))
            u.DispatchMessageW(ctypes.byref(msg))

        # 清理
        try:
            u.KillTimer(hwnd, TIMER_ID)
            if u.IsWindow(hwnd):
                u.DestroyWindow(hwnd)
        except Exception:
            pass
        self._hwnd = None

    # ------------------------------------------------------------ 绘制
    def _wndproc(self, hwnd, msg, wparam, lparam):
        u = _GDI().u
        if msg == WM_TIMER and wparam == TIMER_ID:
            self._angle = (self._angle + 9.0) % 360.0
            u.InvalidateRect(hwnd, None, False)
            return 0
        if msg == WM_ERASEBKGND:
            return 1            # 自己画，避免闪
        if msg == WM_PAINT:
            self._paint(hwnd)
            return 0
        if msg == WM_SETCURSOR:
            return 0
        if msg == WM_CLOSE:
            u.DestroyWindow(hwnd)
            return 0
        if msg == WM_DESTROY:
            u.PostQuitMessage(0)
            return 0
        # ⚠ 必须把未处理的消息交给默认处理（否则窗口创建/销毁会出问题）。
        #
        # 但直接调 DefWindowProcW 会踩 ctypes 的签名坑：回调交来的
        # wparam/lparam 是 Python 大整数，而函数签名要 c_ssize_t，
        # 于是抛 `OverflowError: int too long to convert`。
        #
        # 解决办法：把 argtypes 声明为 c_void_p，ctypes 会自动把
        # 任意整数按平台字长截断，不会溢出。
        return u.DefWindowProcW(hwnd, msg, _ptr(wparam), _ptr(lparam))

    def _paint(self, hwnd):
        u = _GDI().u
        g = _GDI().g
        ps = PAINTSTRUCT()
        hdc = u.BeginPaint(hwnd, ctypes.byref(ps))
        if not hdc:
            return
        try:
            # 卡片底
            bg = _rgb(250, 250, 252) if not self._dark else _rgb(40, 40, 44)
            brush = g.CreateSolidBrush(bg)
            null_pen = g.GetStockObject(8)          # NULL_PEN
            g.SelectObject(hdc, brush)
            g.SelectObject(hdc, null_pen)
            g.RoundRect(hdc, 0, 0, CARD_W, CARD_H, RADIUS, RADIUS)
            g.DeleteObject(brush)

            # 旋转圆弧：-90° 起，画 280° 的弧，起点随角度移动
            fg = _rgb(0, 120, 212) if not self._dark else _rgb(96, 205, 255)
            pen = g.CreatePen(0, ARC_W, fg)
            g.SelectObject(hdc, pen)
            cx = CARD_W // 2
            cy = 42
            left, top = cx - ARC_R, cy - ARC_R
            right, bottom = cx + ARC_R, cy + ARC_R
            # GDI 的 Arc 以「终点 - 起点」的逆时针方向画弧
            start = math.radians(self._angle - 90)
            end = math.radians(self._angle - 90 + 280)
            sx = cx + int(ARC_R * math.cos(start))
            sy = cy + int(ARC_R * math.sin(start))
            ex = cx + int(ARC_R * math.cos(end))
            ey = cy + int(ARC_R * math.sin(end))
            g.Arc(hdc, left, top, right, bottom, sx, sy, ex, ey)
            g.DeleteObject(pen)

            # 文字
            txt = _rgb(40, 40, 45) if not self._dark else _rgb(235, 235, 240)
            g.SetTextColor(hdc, txt)
            g.SetBkMode(hdc, 1)                      # TRANSPARENT
            rect = w.RECT(0, 66, CARD_W, CARD_H - 8)
            u.DrawTextW(hdc, self._text, -1, ctypes.byref(rect),
                        DT_CENTER | DT_VCENTER)
        finally:
            u.EndPaint(hwnd, ctypes.byref(ps))
