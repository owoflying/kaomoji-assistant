"""全局键盘拦截：候选条可见时，用低层键盘钩子捕获选字 / 翻页 / 关闭键。

设计目标（对应需求“改为不需要焦点的按键捕获”）：
  * 候选条不再抢焦点，不会打断用户写字；
  * 靠这个全局钩子（而非 Qt 焦点）来接收选字/翻页/关闭指令；
  * 命中“当前真正可用”的键 -> 系统级吞掉该键（不写进正在编辑的文档）并发出动作信号；
  * 其余任何键 -> 原样送回目标程序，同时发出 dismiss 信号关闭面板
    （即“用户继续打字 => UI 自动关闭”）；
  * 忽略我们自己模拟出来的键（LLKHF_INJECTED），避免上屏后自我重触发；
  * 修饰键 / 锁定键（Ctrl/Shift/Alt/Win/Caps/Num）既不处理也不触发 dismiss，
    否则按住 Shift 打字会被误判为“继续打字”。

关于 pynput 的抑制语义（踩过坑，务必看清）：
  * `win32_event_filter` 返回 False **只表示不把事件派发给 on_press 回调**，
    事件仍会照常送达目标程序 —— 它不是“拦截”；
  * 真正的系统级拦截只有一条路：在 filter 里调用 `listener.suppress_event()`，
    它抛出 SystemHook.SuppressException，被钩子过程捕获后返回 1 阻断事件链；
  * `Listener(suppress=True)` 会对 **每一个** 经过钩子的按键都调用 suppress_event()，
    等于全局吞键 —— 曾导致“开启后无法输入任何文字”，绝不可用。
  故本模块一律 suppress=False，只对需要的键手动 suppress_event()。

抑制粒度（按“这个键在日常打字里有多常用”分级，而不是按弹出方式一刀切）：
  A. 候选条专属键 —— 1-9 / -=（翻页）/ ←→↑↓ / PageUp·PageDown / Esc
     日常写作几乎用不到，只要面板开着就归候选条，手动唤起和自动弹出一视同仁。
  B. 双关键 —— 回车 / 空格
     正常写作里高频（发消息、上屏拼音），拦错了非常烦。只有在用户明确表达了
     选字意图时才吞：手动按热键唤起，或自动弹出后用户已经用 ←→ / -= 操作过
     候选条（_engaged）。否则放行 + 关面板。
  C. 其它所有键 —— 一律放行 + 关面板（“用户继续打字 => UI 自动关闭”）。

  另外三条通用规则：
  * 动作在当前上下文没意义就不吞：只有 3 个候选时按 5、只有一页时按 -/=、
    只有 1 个候选时按 ←→，都照常打进文档；
  * 任何修饰键（Ctrl/Alt/Win/Shift）按下时一律不吞，让 Ctrl+C、Shift+1 等正常工作；
  * 吞掉 keydown 的同时吞掉配对的 keyup，避免目标程序收到孤立的 keyup。
"""
import ctypes
import threading

from PySide6.QtCore import QObject, Signal

try:
    from pynput.keyboard import Listener
except Exception:  # pragma: no cover - 非 Windows 或 pynput 缺失
    Listener = None

try:  # 查询修饰键实时状态，钩子回调里调用开销可忽略
    _user32 = ctypes.windll.user32
    _user32.GetAsyncKeyState.argtypes = (ctypes.c_int,)
    _user32.GetAsyncKeyState.restype = ctypes.c_short
except Exception:  # pragma: no cover - 非 Windows
    _user32 = None


# ---- 虚拟键码 ----
VK_ESCAPE = 0x1B
VK_RETURN = 0x0D
VK_SPACE = 0x20
VK_PRIOR = 0x21          # PageUp
VK_NEXT = 0x22           # PageDown
VK_LEFT = 0x25
VK_UP = 0x26
VK_RIGHT = 0x27
VK_DOWN = 0x28
VK_OEM_MINUS = 0xBD
VK_OEM_PLUS = 0xBB
VK_SUBTRACT = 0x6D
VK_ADD = 0x6B
VK_LSHIFT = 0xA0
VK_RSHIFT = 0xA1
VK_LCONTROL = 0xA2
VK_RCONTROL = 0xA3
VK_LMENU = 0xA4
VK_RMENU = 0xA5
VK_LWIN = 0x5B
VK_RWIN = 0x5C
VK_CAPITAL = 0x14
VK_NUMLOCK = 0x90
VK_CONTROL = 0x11
VK_SHIFT = 0x10
VK_MENU = 0x12
VK_1 = 0x31
VK_9 = 0x39
VK_NUMPAD1 = 0x61
VK_NUMPAD9 = 0x69

# 完全忽略的键（既不当作“可处理”，也不当作“继续打字”）
_IGNORE = frozenset([
    VK_CONTROL, VK_SHIFT, VK_MENU, VK_LWIN, VK_RWIN,
    VK_LSHIFT, VK_RSHIFT, VK_LCONTROL, VK_RCONTROL,
    VK_LMENU, VK_RMENU, VK_CAPITAL, VK_NUMLOCK,
])

# “按住它时不吞任何键”的修饰键（Shift 也算：Shift+2 是打 @，不是选第 2 个）
_MOD_QUERY = (VK_CONTROL, VK_MENU, VK_LWIN, VK_RWIN, VK_SHIFT)

_WM_KEYDOWN = 0x0100
_WM_KEYUP = 0x0101
_WM_SYSKEYDOWN = 0x0104
_WM_SYSKEYUP = 0x0105

_LLKHF_INJECTED = 0x00000010
_LLKHF_LOWER_IL_INJECTED = 0x00000002

# 「候选条专属键」：日常打字几乎不会用到，只要面板开着就归候选条，
# 无论手动唤起还是自动弹出都拦截。
_PANEL_ACTIONS = frozenset(["cancel", "prev", "next", "left", "right"])
# 「双关键」：在正常写作里高频使用（回车发消息、空格上屏拼音），
# 只有在用户明确表达了选字意图时才拦截，详见 GlobalKeyInterceptor._allows。
_AMBIGUOUS_ACTIONS = frozenset(["confirm"])


def _map_key(vk):
    """把 vkCode 映射到候选条动作；返回 None 表示“其它键”。"""
    if VK_1 <= vk <= VK_9:
        return "num%d" % (vk - VK_1 + 1)
    if VK_NUMPAD1 <= vk <= VK_NUMPAD9:
        return "num%d" % (vk - VK_NUMPAD1 + 1)
    if vk in (VK_OEM_MINUS, VK_SUBTRACT, VK_PRIOR):
        return "prev"
    if vk in (VK_OEM_PLUS, VK_ADD, VK_NEXT):
        return "next"
    if vk in (VK_LEFT, VK_UP):
        return "left"
    if vk in (VK_RIGHT, VK_DOWN):
        return "right"
    if vk in (VK_RETURN, VK_SPACE):
        return "confirm"
    if vk == VK_ESCAPE:
        return "cancel"
    return None


def _mods_down():
    if _user32 is None:
        return False
    for vk in _MOD_QUERY:
        if _user32.GetAsyncKeyState(vk) & 0x8000:
            return True
    return False


class GlobalKeyInterceptor(QObject):
    """常驻低层键盘钩子。`is_active_cb()` 为真时才真正参与按键处理。"""

    action = Signal(str)
    dismiss = Signal()

    def __init__(self, is_active_cb):
        super().__init__()
        self._is_active = is_active_cb
        self._listener = None
        self._lock = threading.Lock()
        # (候选数, 是否多页, 是否手动唤起)；整体替换，读写天然原子
        self._state = (0, False, False)
        # 本次显示期间，用户是否已经用导航键跟候选条互动过
        self._engaged = False
        # 已被吞掉 keydown 的 vk，用于连带吞掉它的 keyup（只在钩子线程访问）
        self._eaten = set()

    # ---------- 生命周期 ----------
    def start(self):
        if Listener is None:
            return
        with self._lock:
            if self._listener is not None:
                return
            try:
                # suppress 必须为 False！True = 吞掉全键盘（见模块文档）
                listener = Listener(
                    suppress=False, win32_event_filter=self._filter
                )
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
        self._eaten.clear()

    # ---------- 由候选条同步当前上下文 ----------
    def set_state(self, count, paging, manual):
        """count: 当前页候选数；paging: 是否多页；manual: 是否用户手动唤起。

        每次渲染都会调用（含翻页），故这里不碰 `_engaged`。
        """
        self._state = (int(count), bool(paging), bool(manual))

    def reset_session(self):
        """面板每次显示时调用：清掉上一次的互动状态。"""
        self._engaged = False
        self._eaten.clear()

    # ---------- 内部 ----------
    def _allows(self, action):
        """判断这个动作在当前上下文是否该由候选条吃掉。"""
        count, paging, manual = self._state
        # 1-9：只吞真实存在的候选序号。只有 3 个候选时按 5 -> 正常打进文档
        if action.startswith("num"):
            try:
                n = int(action[3:])
            except ValueError:
                return False
            return 1 <= n <= count
        # 候选条专属键：只要该动作此刻真的有意义就吞，不看是手动还是自动弹出
        if action in _PANEL_ACTIONS:
            if action == "cancel":
                return True                      # Esc 永远关面板
            if action in ("prev", "next"):
                return paging                    # 单页时翻页无意义 -> 让 '-' 正常输入
            return count > 1 or paging           # left/right：只有 1 个候选时不拦
        # 回车 / 空格：正常写作里高频（发消息、上屏拼音），拦错了非常烦。
        # 仅在用户已明确表达选字意图时才吞：
        #   * 手动按热键唤起 —— 意图本身就很明确；
        #   * 或自动弹出后，用户已经用 ←→ / -= 操作过候选条（_engaged）。
        if action in _AMBIGUOUS_ACTIONS:
            return count > 0 and (manual or self._engaged)
        return False

    def _eat(self):
        """系统级吞掉当前事件（抛出 SuppressException，由 pynput 钩子过程处理）。"""
        listener = self._listener
        if listener is not None:
            listener.suppress_event()   # 必定抛异常，不会返回

    def _filter(self, msg, data):
        vk = data.vkCode
        # 1) keyup：只吞掉那些 keydown 已被我们吞过的键，避免孤立 keyup
        if msg in (_WM_KEYUP, _WM_SYSKEYUP):
            if vk in self._eaten:
                self._eaten.discard(vk)
                self._eat()
            return True
        if msg not in (_WM_KEYDOWN, _WM_SYSKEYDOWN):
            return True
        # 2) 面板未显示：完全放行，本钩子近乎零开销
        if not self._is_active():
            if self._eaten or self._engaged:
                self._eaten.clear()
                self._engaged = False
            return True
        # 3) 我们自己模拟出来的键（上屏注入）放行，避免自我重触发
        if data.flags & (_LLKHF_INJECTED | _LLKHF_LOWER_IL_INJECTED):
            return True
        # 4) 修饰/锁定键：既不处理也不触发关闭
        if vk in _IGNORE:
            return True
        # 5) 带修饰键的组合（Ctrl+C / Shift+1 / Alt+Tab…）一律不碰
        if _mods_down():
            self.dismiss.emit()
            return True
        action = _map_key(vk)
        if action is not None and self._allows(action):
            # 用导航键操作过候选条 => 用户确实在选字，之后回车/空格也归候选条
            if action in ("prev", "next", "left", "right"):
                self._engaged = True
            # 吞掉这个键（不写进文档），把动作交给候选条主线程执行
            self.action.emit(action)
            self._eaten.add(vk)
            self._eat()
            return False        # 理论上不可达：_eat() 已抛出
        # 6) 其它任意键：原样送回目标程序，并通知关闭面板（用户继续打字）
        self.dismiss.emit()
        return True
