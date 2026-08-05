"""全局鼠标钩子：面板可见时，在界面“外部”按下任意鼠标键即关闭面板。

⚠️ 默认不再启用！见 PickerWindow.__init__ 里的 use_global_mouse_hook 开关
（默认 False）。原因：

  * 系统级 WH_MOUSE_LL 低层鼠标钩子是 Windows 上“外接蓝牙鼠标被卡死/失灵”的
    已知诱因之一，部分蓝牙协议栈+驱动组合下，挂上全局低层鼠标钩子后蓝牙 HID
    鼠标就不再上报移动/点击；
  * 本程序是常驻托盘进程，关掉窗口不会退出、也不会卸载钩子，于是会出现
    “关掉程序蓝牙鼠标还是用不了，只剩触摸板能用”的现象；
  * 因此默认不装这个钩子，改由 PickerWindow._check_focus 用“前台窗口是否变化”
    轮询来兜底“点界面外部关闭”，完全不需要系统级钩子。

动机（历史）：
  * 面板用 FramelessWindowHint + WindowDoesNotAcceptFocus + WA_ShowWithoutActivating，
    永不抢焦点，所以系统不会给它发 WindowDeactivate（点击别处时焦点仍在原窗口），
    也就无法靠 Qt 原生“点击外部关闭”来收起面板；
  * 失焦自动关闭（_check_focus）只在“曾经检测到可编辑焦点”后武装，
    手动（托盘）唤起、且当前没有输入框聚焦时该标志恒为 False，同样关不掉；
  * 于是早期版本用一个系统级低层鼠标钩子（WH_MOUSE_LL）来感知“点到了面板之外”。

行为（仅当 use_global_mouse_hook=True 时才生效）：
  * 仅在 is_active_cb() 为真（面板可见）时才参与，空闲时近乎零开销；
  * 命中任意鼠标键按下时发 outside_click 信号，真正的“是否界外”判定交给面板
    （在 Qt 主线程里安全地用 self.geometry().contains(QCursor.pos()) 判断），
    钩子线程只负责“观察并通知”，绝不拦截/吞掉任何鼠标事件；
  * 面板内部（候选、翻页、空白处点一下关闭）由各自控件处理，钩子不会误伤。

与键盘钩子一样：钩子在独立线程跑，跨线程只能发 Qt 信号，绝不能在线程里直接
操作窗口；关闭动作由面板在主线程里执行（Qt 自动把信号排队到接收者线程）。
"""
import ctypes
import threading

from PySide6.QtCore import QObject, Signal

try:
    from pynput.mouse import Listener
except Exception:  # pragma: no cover - 非 Windows 或 pynput 缺失
    Listener = None


# 鼠标“按下”消息（我们只关心按下，松开无所谓）
_MOUSE_DOWN = frozenset([
    0x0201,  # WM_LBUTTONDOWN
    0x0204,  # WM_RBUTTONDOWN
    0x0207,  # WM_MBUTTONDOWN
    0x020B,  # WM_XBUTTONDOWN
])


class GlobalMouseInterceptor(QObject):
    """常驻低层鼠标钩子。is_active_cb() 为真（面板可见）时，界外按下即发信号。"""

    outside_click = Signal()

    def __init__(self, is_active_cb):
        super().__init__()
        self._is_active = is_active_cb
        self._listener = None
        self._lock = threading.Lock()

    # ---------- 生命周期 ----------
    def start(self):
        if Listener is None:
            return
        with self._lock:
            if self._listener is not None:
                return
            try:
                # 只观察、不拦截：不调用 suppress_event()，事件原样送达系统
                listener = Listener(win32_event_filter=self._filter)
                self._listener = listener
                listener.start()
            except Exception:
                self._listener = None

    def stop(self):
        with self._lock:
            if self._listener is not None:
                try:
                    self._listener.stop()
                except Exception:
                    pass
                self._listener = None

    # ---------- 内部 ----------
    def _filter(self, msg, data):
        # 只管“按下”，且在面板可见时才关心；其余一律放行（返回 True）
        if msg not in _MOUSE_DOWN:
            return True
        if not self._is_active():
            return True
        # 把“界外按下”这件事告诉面板，由它在主线程里判断并关闭。
        # 注意：Qt 信号跨线程是排队投递到接收者（面板）线程，安全。
        self.outside_click.emit()
        return True
