"""Windows 平台底层工具：全局热键解析、前台窗口焦点控制、DWM 相关常量与辅助函数。

用原生 RegisterHotKey 代替 pynput 的常驻键盘钩子，能彻底消除“调用卡顿”——
pynput 会在系统底层挂一个全局钩子拦截每一次按键，而 RegisterHotKey 是消息驱动、
只在组合键命中时才回调，对日常输入零干扰。
"""
import ctypes
from ctypes import wintypes

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

# ---- 修饰键 (RegisterHotKey 的 fsModifiers) ----
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008

WM_HOTKEY = 0x0312

# ---- 命名主键 -> 虚拟键码 (VK) ----
NAMED_VK = {
    "space": 0x20, "enter": 0x0D, "tab": 0x09, "esc": 0x1B,
    "backspace": 0x08, "delete": 0x2E, "insert": 0x2D,
    "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22,
    "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
    "plus": 0xBB, "minus": 0xBD, "comma": 0xBC, "period": 0xBE,
}
for _i in range(1, 13):
    NAMED_VK["f%d" % _i] = 0x70 + _i - 1

# VK -> 命名主键（反查，用于生成配置字符串）
VK_NAMED = {_v: _k for _k, _v in NAMED_VK.items()}

# 修饰键名称 -> (MOD 值, pynput 标记)
MOD_NAMES = {
    "ctrl": MOD_CONTROL, "control": MOD_CONTROL,
    "shift": MOD_SHIFT,
    "alt": MOD_ALT,
    "cmd": MOD_WIN, "super": MOD_WIN, "win": MOD_WIN,
}
MOD_PYNPUT = {
    MOD_CONTROL: "<ctrl>",
    MOD_SHIFT: "<shift>",
    MOD_ALT: "<alt>",
    MOD_WIN: "<cmd>",
}

# 设置 API 参数类型，避免 ctypes 默认推断错误
user32.GetForegroundWindow.argtypes = []
user32.GetForegroundWindow.restype = wintypes.HWND
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.SetForegroundWindow.restype = wintypes.BOOL
user32.SetFocus.argtypes = [wintypes.HWND]
user32.SetFocus.restype = wintypes.HWND
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
user32.AttachThreadInput.restype = wintypes.BOOL
user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_uint, ctypes.c_uint]
user32.RegisterHotKey.restype = wintypes.BOOL
user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
user32.UnregisterHotKey.restype = wintypes.BOOL
user32.VkKeyScanW.argtypes = [wintypes.WCHAR]
user32.VkKeyScanW.restype = ctypes.c_short
user32.SendMessageTimeoutW.argtypes = [
    wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPWSTR,
    wintypes.UINT, wintypes.UINT, ctypes.POINTER(wintypes.DWORD),
]
user32.SendMessageTimeoutW.restype = wintypes.LPARAM
user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
user32.GetWindowLongW.restype = wintypes.LONG


def _vk_from_char(ch):
    """通过 VkKeyScanW 把字符转成 VK（低字节）。"""
    try:
        res = user32.VkKeyScanW(ch)
    except Exception:
        return 0
    if res == -1:
        return 0
    return res & 0xFF


def parse_pynput(hotkey_str):
    """解析 pynput 风格字符串 -> (modifiers:int, vk:int)。

    例如 "<ctrl>+<shift>+k" -> (MOD_CONTROL | MOD_SHIFT, 0x4B)
    """
    mods = 0
    vk = 0
    for part in (hotkey_str or "").lower().split("+"):
        part = part.strip()
        if not part:
            continue
        if part.startswith("<") and part.endswith(">"):
            name = part[1:-1]
            if name in MOD_NAMES:
                mods |= MOD_NAMES[name]
                continue
            key = name  # 命名主键，如 <f1> <up>
        else:
            key = part
        if key in NAMED_VK:
            vk = NAMED_VK[key]
        elif len(key) == 1:
            vk = _vk_from_char(key)
        elif key:
            vk = _vk_from_char(key[0])
    return mods, vk


def format_pynput(mods, vk):
    """把 (modifiers, vk) 还原成 pynput 风格配置字符串。"""
    parts = []
    if mods & MOD_CONTROL:
        parts.append("<ctrl>")
    if mods & MOD_ALT:
        parts.append("<alt>")
    if mods & MOD_SHIFT:
        parts.append("<shift>")
    if mods & MOD_WIN:
        parts.append("<cmd>")
    if vk in VK_NAMED:
        parts.append("<%s>" % VK_NAMED[vk])
    elif 0x41 <= vk <= 0x5A:
        parts.append(chr(vk).lower())
    elif 0x30 <= vk <= 0x39:
        parts.append(chr(vk))
    else:
        parts.append("<key%d>" % vk)
    return "+".join(parts)


def hotkey_label_from(mods, vk):
    """生成给人看的标签，如 'Ctrl+Shift+K'。"""
    parts = []
    if mods & MOD_CONTROL:
        parts.append("Ctrl")
    if mods & MOD_ALT:
        parts.append("Alt")
    if mods & MOD_SHIFT:
        parts.append("Shift")
    if mods & MOD_WIN:
        parts.append("Win")
    if vk in VK_NAMED:
        parts.append(VK_NAMED[vk].upper())
    elif 0x41 <= vk <= 0x5A:
        parts.append(chr(vk))
    elif 0x30 <= vk <= 0x39:
        parts.append(chr(vk))
    else:
        parts.append("?")
    return "+".join(parts)


def _vk_label(vk):
    """单个 VK 给人看的字符（用于序列热键标签）。"""
    if 0x41 <= vk <= 0x5A:
        return chr(vk)
    if 0x30 <= vk <= 0x39:
        return chr(vk)
    name = VK_NAMED.get(vk)
    if name == "plus":
        return "+"
    if name == "minus":
        return "-"
    if name == "comma":
        return ","
    if name == "period":
        return "."
    if name:
        return name.upper()
    return "?"


def _key_token(vk):
    """序列里单个键的存储 token：字母/数字用原字符，符号用 <name>。"""
    if vk in VK_NAMED:
        return "<%s>" % VK_NAMED[vk]
    if 0x41 <= vk <= 0x5A:
        return chr(vk).lower()
    if 0x30 <= vk <= 0x39:
        return chr(vk)
    return "<key%d>" % vk


def parse_hotkey(hotkey_str):
    """把热键字符串解析为结构化 dict。

    返回:
      {"type": "none"}
      {"type": "simple",  "mods": int, "vk": int}        # 修饰键 + 单个主键（走 RegisterHotKey）
      {"type": "sequence","keys": [vk, ...]}             # 多键序列（走轻量按键监听）
    """
    s = (hotkey_str or "").strip().lower()
    if not s:
        return {"type": "none"}
    mods = 0
    keys = []
    for part in s.split("+"):
        part = part.strip()
        if not part:
            continue
        if part.startswith("<") and part.endswith(">"):
            name = part[1:-1]
            if name in MOD_NAMES:
                mods |= MOD_NAMES[name]
                continue
            if name in NAMED_VK:
                keys.append(NAMED_VK[name])
                continue
            if len(name) == 1:
                v = _vk_from_char(name)
                if v:
                    keys.append(v)
                    continue
            continue
        else:
            if part in MOD_NAMES:
                mods |= MOD_NAMES[part]
                continue
            if part in NAMED_VK:
                keys.append(NAMED_VK[part])
                continue
            if len(part) == 1:
                v = _vk_from_char(part)
                if v:
                    keys.append(v)
                    continue
            if part:
                v = _vk_from_char(part[0])
                if v:
                    keys.append(v)
    if len(keys) >= 2:
        return {"type": "sequence", "keys": keys}
    if len(keys) == 1:
        return {"type": "simple", "mods": mods, "vk": keys[0]}
    return {"type": "none"}


def label_from_parsed(parsed):
    if parsed["type"] == "simple":
        return hotkey_label_from(parsed["mods"], parsed["vk"])
    if parsed["type"] == "sequence":
        return " + ".join(_vk_label(v) for v in parsed["keys"])
    return ""


def format_hotkey(parsed):
    if parsed["type"] == "simple":
        return format_pynput(parsed["mods"], parsed["vk"])
    if parsed["type"] == "sequence":
        return "+".join(_key_token(v) for v in parsed["keys"])
    return ""


def hotkey_label(hotkey_str):
    """直接由配置字符串生成人看标签（简单/序列通用）。"""
    return label_from_parsed(parse_hotkey(hotkey_str))


def get_foreground_hwnd():
    """获取当前前台窗口句柄（用户正在输入的程序）。"""
    return user32.GetForegroundWindow()


def set_foreground(hwnd):
    """把指定窗口强行置于前台（跨进程也行，借助 AttachThreadInput）。"""
    if not hwnd:
        return
    try:
        fg = user32.GetForegroundWindow()
        ft = user32.GetWindowThreadProcessId(fg, ctypes.byref(wintypes.DWORD(0)))
        ct = kernel32.GetCurrentThreadId()
        attached = False
        if ft and ft != ct:
            user32.AttachThreadInput(ft, ct, True)
            attached = True
        user32.SetForegroundWindow(hwnd)
        user32.SetFocus(hwnd)
        if attached:
            user32.AttachThreadInput(ft, ct, False)
    except Exception:
        pass


# ---- 键盘布局（输入法）控制：模拟键入前切英文布局、完成后切回 ----
#
# 关键背景：微软拼音这类 IME 是挂在「中文(简体)键盘布局」(KLID=0804) 上的，
# 而英文(美国)布局 (KLID=00000409) 不带任何 IME。所谓“输入法开着”本质上是
# 当前线程的键盘布局是中文布局、且其 IME 处于中文模式。
#
# 之前尝试用 imm32 的 ImmSetOpenStatus(False) 关闭 IME 的“打开”状态——但微软拼音
# 并不买账：它仍会把逐字符键事件吞进组字缓冲区，导致颜文字被转成拼音候选 / 乱码（如「哦哦」）。
# 因此这里改用更彻底的办法：直接把前台线程的键盘布局切到「英文(美国)」，整个 IME 被剥离，
# 键事件直通，打完再切回用户原来的布局。这才是真正规避乱码的手段。
WM_INPUTLANGCHANGEREQUEST = 0x0050
KLF_ACTIVATE = 0x00000001
_ENGLISH_US_KLID = "00000409"
_eng_hkl = None

# API 参数/返回类型声明
user32.LoadKeyboardLayoutW.argtypes = [wintypes.LPCWSTR, wintypes.UINT]
user32.LoadKeyboardLayoutW.restype = wintypes.HKL
user32.GetKeyboardLayout.argtypes = [wintypes.DWORD]
user32.GetKeyboardLayout.restype = wintypes.HKL
user32.ActivateKeyboardLayout.argtypes = [wintypes.HKL, wintypes.UINT]
user32.ActivateKeyboardLayout.restype = wintypes.HKL
user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.PostMessageW.restype = wintypes.BOOL


def ensure_english_layout():
    """加载（并缓存）英文(美国)键盘布局的 HKL，失败返回 None。"""
    global _eng_hkl
    if _eng_hkl:
        return _eng_hkl
    try:
        hkl = user32.LoadKeyboardLayoutW(_ENGLISH_US_KLID, KLF_ACTIVATE)
        if hkl:
            _eng_hkl = hkl
    except Exception:
        pass
    return _eng_hkl


def get_keyboard_layout(hwnd):
    """获取前台窗口线程当前使用的键盘布局 HKL；取不到时返回 None。"""
    try:
        tid = user32.GetWindowThreadProcessId(hwnd, ctypes.byref(wintypes.DWORD(0)))
        if not tid:
            return user32.GetKeyboardLayout(0)
        return user32.GetKeyboardLayout(tid)
    except Exception:
        return None


def set_keyboard_layout(hwnd, hkl):
    """把前台窗口线程的键盘布局切到指定 HKL（如英文(美国)），从而剥离 IME。

    主路径：向前台窗口投递 WM_INPUTLANGCHANGEREQUEST，由它自己的消息循环切换输入法。
    这是最干净、对微软拼音双向（切到英文 / 切回中文）都有效的做法；切换是异步的，
    由调用方轮询 get_keyboard_layout 确认真正生效后再继续（见 injector._wait_layout），
    从而消除“打字抢在切换前面”的乱码竞态。
    仅当投递失败时才退回 AttachThreadInput + ActivateKeyboardLayout 兜底。
    任何失败都静默忽略，绝不抛到调用方。
    """
    if not hwnd or not hkl:
        return
    # 主路径：WM_INPUTLANGCHANGEREQUEST 投给目标窗口，由其自身切换（双向可靠）
    try:
        user32.PostMessageW(hwnd, WM_INPUTLANGCHANGEREQUEST, 0, hkl)
        return
    except Exception:
        pass
    # 兜底：AttachThreadInput + ActivateKeyboardLayout（极少走到）
    try:
        fg = user32.GetForegroundWindow()
        ft = user32.GetWindowThreadProcessId(fg, ctypes.byref(wintypes.DWORD(0)))
        ct = kernel32.GetCurrentThreadId()
        attached = False
        if ft and ft != ct and user32.AttachThreadInput(ft, ct, True):
            attached = True
        user32.ActivateKeyboardLayout(hkl, KLF_ACTIVATE)
        if attached:
            user32.AttachThreadInput(ft, ct, False)
    except Exception:
        pass


# ---- 直接字符投递 (WM_CHAR)：绕过键盘/IME，把字符直接送进目标控件 ----
WM_CHAR = 0x0102


def get_focused_control_hwnd():
    """拿到当前真正拥有焦点的控件 hwnd（用于直接投递 WM_CHAR）。

    优先用 GetGUIThreadInfo 取前台线程的焦点控件（编辑框本身）；
    取不到时退回整个前台窗口。任何失败都静默退回前台窗口，绝不抛错。
    """
    try:
        fg = user32.GetForegroundWindow()
        if not fg:
            return None
        tid = user32.GetWindowThreadProcessId(fg, ctypes.byref(wintypes.DWORD(0)))
        info = _GUITHREADINFO()
        info.cbSize = ctypes.sizeof(_GUITHREADINFO)
        if tid and user32.GetGUIThreadInfo(tid, ctypes.byref(info)):
            if info.hwndFocus:
                return info.hwndFocus
        return fg
    except Exception:
        return None


def get_focused_control_class():
    """返回当前焦点控件的窗口类名（如 Edit / RichEdit50W / Chrome_RenderWidgetHostHWND）。

    取不到时返回 ''。开发者模式用于展示“焦点原始流”，确认焦点判定是否正确。
    """
    hwnd = get_focused_control_hwnd()
    if not hwnd:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(256)
        n = user32.GetClassNameW(hwnd, buf, 256)
        if n <= 0:
            return ""
        return buf.value or ""
    except Exception:
        return ""


def is_native_edit(hwnd):
    """判断 hwnd 是否为原生 Win32 编辑框（Edit / RichEdit 类）。

    原生编辑框可用 WM_GETTEXT 亚毫秒级读取真实文本；
    浏览器 / Electron / Qt / WPF 等自绘控件不响应 WM_GETTEXT
    （只能拿到窗口标题等无效文本），必须交给 UIA 读取。
    任何失败都静默返回 False（保守地回退到 UIA 路径）。
    """
    if not hwnd:
        return False
    try:
        buf = ctypes.create_unicode_buffer(256)
        n = user32.GetClassNameW(hwnd, buf, 256)
        if n <= 0:
            return False
        cls = (buf.value or "").lower()
        return cls == "edit" or cls.startswith("richedit")
    except Exception:
        return False


def post_wm_char(hwnd, text):
    """把 text 以 UTF-16 代码单元为单位，逐个 PostMessageW(WM_CHAR) 投递到 hwnd。

    - 不走键盘、不走剪贴板、不经过 IME：微软拼音这类输入法根本不参与，从根上杜绝乱码。
    - BMP 字符 = 1 个代码单元一次 WM_CHAR；四字节 emoji 等自动拆成高/低 surrogate 两次。
    - 投递是异步的（入队即返回），同一队列内顺序天然保持。
    返回成功投递的代码单元数；hwnd 无效或 text 为空时返回 0。
    """
    if not hwnd or not text:
        return 0
    units = text.encode("utf-16-le")
    sent = 0
    for i in range(0, len(units), 2):
        unit = int.from_bytes(units[i:i + 2], "little")
        try:
            if user32.PostMessageW(hwnd, WM_CHAR, unit, 1):
                sent += 1
        except Exception:
            break
    return sent


# ---- 获取输入光标（caret）屏幕坐标，用于输入法式就近弹出 ----
class _RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class _GUITHREADINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_ulong),
        ("flags", ctypes.c_ulong),
        ("hwndActive", wintypes.HWND),
        ("hwndFocus", wintypes.HWND),
        ("hwndCapture", wintypes.HWND),
        ("hwndMenuOwner", wintypes.HWND),
        ("hwndMoveSize", wintypes.HWND),
        ("hwndCaret", wintypes.HWND),
        ("rcCaret", _RECT),
    ]


def get_caret_screen_pos():
    """返回候选条应当贴附的“输入位置”屏幕坐标 (QPoint)，取不到则返回 None。

    优先级：
      1. GetGUIThreadInfo 的真实光标 (hwndCaret) —— 标准 Win32 编辑控件最准；
      2. UIA 焦点元素的包围盒下沿 —— 覆盖浏览器 / Electron / VSCode 等自绘控件，
         这些控件没有真正的 hwndCaret；
      3. 焦点窗口矩形下沿 —— 兜底，至少贴在输入框附近而非屏幕顶部。
    """
    try:
        from PySide6.QtCore import QPoint
        gui = _GUITHREADINFO()
        gui.cbSize = ctypes.sizeof(_GUITHREADINFO)
        fg = user32.GetForegroundWindow()
        if not fg:
            return None
        tid = user32.GetWindowThreadProcessId(fg, ctypes.byref(wintypes.DWORD(0)))
        if not user32.GetGUIThreadInfo(tid, ctypes.byref(gui)):
            return None
        if gui.hwndCaret:
            pt = _POINT(gui.rcCaret.left, gui.rcCaret.top)
            user32.ClientToScreen(gui.hwndCaret, ctypes.byref(pt))
            return QPoint(int(pt.x), int(pt.y))
        # 兜底 1：UIA 焦点元素包围盒（浏览器/Electron/VSCode 等）
        try:
            from core import uia_text
            if uia_text is not None:
                r = uia_text.get_focused_rect()
                if r:
                    return QPoint(int(r[0]), int(r[3]))  # 控件下沿左侧
        except Exception:
            pass
        # 兜底 2：焦点窗口矩形下沿
        hwnd = gui.hwndFocus or fg
        if hwnd:
            rect = _RECT()
            if user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                return QPoint(int(rect.left), int(rect.bottom))
        return None
    except Exception:
        return None


def get_focused_text(max_len=200):
    """读取当前前台焦点可编辑控件的文本（最近 max_len 字符）。取不到返回 ''。

    用 WM_GETTEXT 读取标准 Win32 编辑控件（记事本、多数桌面程序）的真实文本，
    能拿到 IME 合成后的中文；浏览器 / Electron 等自绘控件可能取不到 -> 返回 ''。
    密码框（ES_PASSWORD）会被跳过，避免读取敏感内容。
    """
    try:
        fg = user32.GetForegroundWindow()
        if not fg:
            return ""
        gui = _GUITHREADINFO()
        gui.cbSize = ctypes.sizeof(_GUITHREADINFO)
        tid = user32.GetWindowThreadProcessId(fg, ctypes.byref(wintypes.DWORD(0)))
        if not user32.GetGUIThreadInfo(tid, ctypes.byref(gui)):
            return ""
        # 仅当焦点落在真正的可编辑控件（hwndFocus / hwndCaret）时才读取；
        # 两者皆空说明焦点不在编辑区（在桌面 / 窗口自身），不要回退去读顶层窗口标题，
        # 否则标题文本会进入情绪检测，可能误触发候选条自动弹出。
        hwnd = gui.hwndFocus or gui.hwndCaret
        if not hwnd:
            return ""

        # 跳过密码框
        GWL_STYLE = -16
        ES_PASSWORD = 0x0020
        try:
            style = user32.GetWindowLongW(hwnd, GWL_STYLE)
            if style & ES_PASSWORD:
                return ""
        except Exception:
            pass

        WM_GETTEXT = 0x000D
        SMTO_ABORTIFHUNG = 0x0001
        buf = ctypes.create_unicode_buffer(max_len + 1)
        # 跨线程读取，带超时，避免目标线程卡死时挂起
        user32.SendMessageTimeoutW(
            hwnd, WM_GETTEXT, max_len + 1, buf, SMTO_ABORTIFHUNG, 50, None
        )
        text = buf.value or ""
        if len(text) > max_len:
            text = text[-max_len:]
        return text
    except Exception:
        return ""


def _init_prototypes():
    """集中声明所有用到的 user32 API 参数/返回类型（模块加载时调用一次）。

    之前这些 argtypes/restype 散落在各函数体内、每次调用都重复赋值；
    统一到此处后既消除热路径上的重复设置，也让类型签名集中可控。
    """
    user32.GetGUIThreadInfo.restype = ctypes.c_int
    user32.GetGUIThreadInfo.argtypes = [ctypes.c_ulong, ctypes.POINTER(_GUITHREADINFO)]
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetClassNameW.restype = ctypes.c_int
    user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(_POINT)]
    user32.ClientToScreen.restype = ctypes.c_int
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(_RECT)]
    user32.GetWindowRect.restype = ctypes.c_int


_init_prototypes()

