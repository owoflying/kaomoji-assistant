"""用 UI Automation 读取「当前焦点控件」的真实文本 —— 纯 ctypes 实现，零第三方依赖。

为什么必须上 UIA：
  * WM_GETTEXT 只能读标准 Win32 编辑控件（记事本等）；
  * 浏览器（Chrome/Edge）、Electron（VSCode/微信/QQ/Discord）、UWP、WPF、
    Qt 自绘控件……全都读不到，这就是「自动弹出一直不弹」的根因；
  * UI Automation 是 Windows 官方无障碍框架，上述控件几乎都实现了
    TextPattern / ValuePattern，能拿到 IME 合成后的真实中文。

实现方式：
  * 直接用 ctypes 走 COM vtable 调用 IUIAutomation，不需要 comtypes / pywin32，
    用户不用额外 pip install（装了 comtypes 也不冲突，本模块完全独立）；
  * 每一步都判 HRESULT，任何失败都安静降级返回 ''，绝不抛到调用方；
  * 所有 COM 对象都显式 Release，避免高频轮询造成句柄泄漏。

性能提示：UIA 一次调用约几毫秒~几十毫秒，**不要在键盘钩子回调里同步调用**，
应放到独立的轮询线程里节流调用（见 core/emotion_monitor.py）。
"""
import ctypes
import threading
from ctypes import (
    POINTER, byref, c_void_p, c_int, c_long, c_short, c_ushort, c_uint,
    c_double, c_ulong,
)

# ---------- 超时保护：UIA 调用绝不能阻塞调用方线程 ----------
class _UIAWorker:
    """把可能阻塞的 UIA/COM 调用放到独立线程里跑，并设置超时上限。

    为什么必须这么做：UI Automation 是跨进程 COM 调用，当目标程序（浏览器 / Electron /
    VSCode / 微信……）卡死或无响应时，UIA 调用可能无限期挂起。若在主线程调用
    （候选条定位 _position_caret / 失焦检测 _check_focus），会直接冻住整个 GUI，
    表现为“按了热键候选条死活不弹”；若在采样线程调用，会拖死自动弹出的采样线程，
    导致一次卡顿后自动弹出彻底失效。

    设计要点：
      * 任何时刻最多只有一个 UIA 调用在飞（_busy 守护），避免目标持续假死时线程越积越多；
      * 调用方线程只在 join(timeout) 内等待，超时即放弃本轮、回退默认值，
        绝不被目标程序的卡顿拖住；
      * 跑 UIA 的线程是 daemon，即便超时返回、真正的 COM 调用仍悬着，也会在目标恢复后自行结束。
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._busy = False

    def call(self, fn, timeout, default):
        # 另一处调用正在尝试获取锁（极短窗口）-> 直接放弃本轮
        if not self._lock.acquire(blocking=False):
            return default
        try:
            if self._busy:
                return default
            self._busy = True
            holder = {"v": default}

            def runner():
                try:
                    holder["v"] = fn()
                except Exception:
                    holder["v"] = default
                finally:
                    self._busy = False

            th = threading.Thread(target=runner, daemon=True)
            th.start()
            th.join(timeout)
            if th.is_alive():
                return default      # 仍卡着：放弃本轮，留给后台线程自行收尾
            return holder.get("v", default)
        finally:
            self._lock.release()


_uia_worker = _UIAWorker()


ole32 = ctypes.windll.ole32
oleaut32 = ctypes.windll.oleaut32

S_OK = 0
CLSCTX_INPROC_SERVER = 0x1
COINIT_MULTITHREADED = 0x0

# ---- UIA 常量 ----
UIA_ValuePatternId = 10002
UIA_TextPatternId = 10014
UIA_IsPasswordPropertyId = 30097

TextPatternRangeEndpoint_Start = 0
TextUnit_Character = 0

VT_BOOL = 11


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_uint32),
        ("Data2", ctypes.c_uint16),
        ("Data3", ctypes.c_uint16),
        ("Data4", ctypes.c_ubyte * 8),
    ]


class _VARIANT(ctypes.Structure):
    """只用于接收 VT_BOOL / VT_BSTR，故用定长缓冲占位即可（x64 下 VARIANT 为 24 字节）。"""
    _fields_ = [
        ("vt", c_ushort), ("r1", c_ushort), ("r2", c_ushort), ("r3", c_ushort),
        ("data", ctypes.c_byte * 16),
    ]


def _guid(s):
    g = GUID()
    ole32.CLSIDFromString(ctypes.c_wchar_p(s), byref(g))
    return g


CLSID_CUIAutomation = _guid("{FF48DBA4-60EF-4201-AA87-54103EEF594E}")
CLSID_CUIAutomation8 = _guid("{E22AD333-B25F-460C-83D0-0581107395C9}")
IID_IUIAutomation = _guid("{30CBE57D-D9D0-452A-AB13-7AC5AC4825EE}")

oleaut32.SysStringLen.argtypes = [c_void_p]
oleaut32.SysStringLen.restype = c_uint
oleaut32.SysFreeString.argtypes = [c_void_p]
oleaut32.SysFreeString.restype = None
oleaut32.VariantClear.argtypes = [POINTER(_VARIANT)]
oleaut32.VariantClear.restype = c_long


# ---------- COM vtable 调用基元 ----------
def _call(ptr, index, argtypes, *args):
    """按 vtable 下标调用 COM 方法，返回 HRESULT。"""
    vtbl = ctypes.cast(ptr, POINTER(POINTER(c_void_p)))[0]
    proto = ctypes.WINFUNCTYPE(c_long, c_void_p, *argtypes)
    return proto(vtbl[index])(ptr, *args)


def _release(p):
    """IUnknown::Release（vtable 下标 2）。"""
    try:
        if p is not None and getattr(p, "value", None):
            _call(p, 2, [])
    except Exception:
        pass


def _bstr(p):
    """把 BSTR 转成 python str 并释放。"""
    if p is None or not p.value:
        return ""
    try:
        n = oleaut32.SysStringLen(p)
        return ctypes.wstring_at(p, n) if n else ""
    except Exception:
        return ""
    finally:
        try:
            oleaut32.SysFreeString(p)
        except Exception:
            pass


# ---------- IUIAutomation 实例（按线程缓存，COM 对象有套间亲和性） ----------
_tls = threading.local()


def _automation():
    uia = getattr(_tls, "uia", None)
    if uia is not None:
        return uia
    if getattr(_tls, "bad", False):
        return None
    try:
        # UIA 客户端建议跑在 MTA，避免与目标进程互相等待造成死锁。
        # 若该线程已初始化为 STA 会返回 RPC_E_CHANGED_MODE，忽略即可。
        ole32.CoInitializeEx(None, COINIT_MULTITHREADED)
    except Exception:
        pass
    p = c_void_p()
    ok = False
    for clsid in (CLSID_CUIAutomation8, CLSID_CUIAutomation):
        try:
            hr = ole32.CoCreateInstance(
                byref(clsid), None, CLSCTX_INPROC_SERVER,
                byref(IID_IUIAutomation), byref(p),
            )
        except Exception:
            hr = -1
        if hr == S_OK and p.value:
            ok = True
            break
    if not ok:
        _tls.bad = True
        return None
    _tls.uia = p
    return p


def available():
    """当前线程能否使用 UI Automation。"""
    return _automation() is not None


# ---------- 具体读取逻辑 ----------
def _is_password(elem):
    """IUIAutomationElement::GetCurrentPropertyValue（下标 10）读 IsPassword。"""
    v = _VARIANT()
    try:
        hr = _call(elem, 10, [c_int, POINTER(_VARIANT)],
                   UIA_IsPasswordPropertyId, byref(v))
        if hr != S_OK:
            return False
        if v.vt == VT_BOOL:
            b = ctypes.cast(byref(v, 8), POINTER(c_short)).contents.value
            return b != 0
        return False
    except Exception:
        return False
    finally:
        try:
            oleaut32.VariantClear(byref(v))
        except Exception:
            pass


def _range_text(rng, max_chars):
    """IUIAutomationTextRange::GetText（下标 12）。"""
    b = c_void_p()
    try:
        if _call(rng, 12, [c_int, POINTER(c_void_p)], int(max_chars), byref(b)) != S_OK:
            return ""
        return _bstr(b)
    except Exception:
        return ""


def _from_text_pattern(elem, max_len):
    """优先取「光标前 max_len 个字符」，取不到再退回整段文档尾部。"""
    pat = c_void_p()
    # IUIAutomationElement::GetCurrentPattern -> 下标 16
    if _call(elem, 16, [c_int, POINTER(c_void_p)],
             UIA_TextPatternId, byref(pat)) != S_OK or not pat.value:
        return ""
    try:
        # --- 1) 光标附近：GetSelection(5) -> [0] -> Clone(3) -> MoveEndpointByUnit(14) ---
        arr = c_void_p()
        if _call(pat, 5, [POINTER(c_void_p)], byref(arr)) == S_OK and arr.value:
            try:
                n = c_int(0)
                if _call(arr, 3, [POINTER(c_int)], byref(n)) == S_OK and n.value > 0:
                    rng = c_void_p()
                    if _call(arr, 4, [c_int, POINTER(c_void_p)], 0, byref(rng)) == S_OK and rng.value:
                        try:
                            clone = c_void_p()
                            if _call(rng, 3, [POINTER(c_void_p)], byref(clone)) == S_OK and clone.value:
                                try:
                                    moved = c_int(0)
                                    _call(clone, 14,
                                          [c_int, c_int, c_int, POINTER(c_int)],
                                          TextPatternRangeEndpoint_Start,
                                          TextUnit_Character,
                                          -int(max_len), byref(moved))
                                    s = _range_text(clone, max_len * 2 + 8)
                                    if s and s.strip():
                                        return s
                                finally:
                                    _release(clone)
                        finally:
                            _release(rng)
            finally:
                _release(arr)
        # --- 2) 兜底：DocumentRange(7) 取前 4096 字符 ---
        doc = c_void_p()
        if _call(pat, 7, [POINTER(c_void_p)], byref(doc)) == S_OK and doc.value:
            try:
                return _range_text(doc, 4096)
            finally:
                _release(doc)
        return ""
    except Exception:
        return ""
    finally:
        _release(pat)


def _from_value_pattern(elem):
    """IUIAutomationValuePattern::get_CurrentValue（下标 4）。适配 <input>/普通编辑框。"""
    pat = c_void_p()
    if _call(elem, 16, [c_int, POINTER(c_void_p)],
             UIA_ValuePatternId, byref(pat)) != S_OK or not pat.value:
        return ""
    try:
        b = c_void_p()
        if _call(pat, 4, [POINTER(c_void_p)], byref(b)) != S_OK:
            return ""
        return _bstr(b)
    except Exception:
        return ""
    finally:
        _release(pat)


# ---- 聚焦元素包围盒（用作“光标位置”代理，覆盖浏览器/Electron/VSCode 等自绘控件） ----
UIA_BoundingRectanglePropertyId = 30001
VT_R8 = 5
VT_ARRAY = 0x2000


class _SAFEARRAYBOUND(ctypes.Structure):
    _fields_ = [("cElements", c_ulong), ("lLbound", c_long)]


class _SAFEARRAY(ctypes.Structure):
    # ctypes 会按 64 位对齐规则自动在 cLocks 后插入 4 字节填充，使 pvData 落在偏移 16，
    # 与 Windows 64 位 SAFEARRAY 布局一致。
    _fields_ = [
        ("cDims", c_ushort), ("fFeatures", c_ushort),
        ("cbElements", c_ulong), ("cLocks", c_ulong),
        ("pvData", c_void_p), ("rgsabound", _SAFEARRAYBOUND),
    ]


def _bounding_rect(elem):
    """IUIAutomationElement::GetCurrentPropertyValue(下标 10, UIA_BoundingRectangle)
    返回 SAFEARRAY<double> [left, top, width, height]，转换为屏幕矩形 (l,t,r,b)。
    任何失败都返回 None，由调用方回退。
    """
    v = _VARIANT()
    try:
        hr = _call(elem, 10, [c_int, POINTER(_VARIANT)],
                   UIA_BoundingRectanglePropertyId, byref(v))
        if hr != S_OK:
            return None
        if v.vt != (VT_ARRAY | VT_R8):
            return None
        # VARIANT 中数组指针位于偏移 8 处
        ptr = c_void_p.from_address(ctypes.addressof(v) + 8).value
        if not ptr:
            return None
        sa = _SAFEARRAY.from_address(ptr)
        n = sa.rgsabound.cElements
        if n < 4 or not sa.pvData:
            return None
        arr = ctypes.cast(sa.pvData, POINTER(c_double))
        left = arr[0]; top = arr[1]; width = arr[2]; height = arr[3]
        return (int(left), int(top), int(left + width), int(top + height))
    except Exception:
        return None
    finally:
        try:
            oleaut32.VariantClear(byref(v))
        except Exception:
            pass


def _get_focused_rect_raw():
    """返回当前键盘焦点元素的屏幕矩形 (l,t,r,b)；取不到返回 None。

    用于把候选条贴在输入框附近——GUIThreadInfo 的 hwndCaret 在浏览器/Electron 里
    往往为空，此时用 UIA 焦点元素的包围盒作为“光标位置”代理。
    """
    uia = _automation()
    if uia is None:
        return None
    elem = c_void_p()
    if _call(uia, 8, [POINTER(c_void_p)], byref(elem)) != S_OK or not elem.value:
        return None
    try:
        if _is_password(elem):
            return None
        return _bounding_rect(elem)
    except Exception:
        return None
    finally:
        _release(elem)


def _is_focused_editable_raw():
    """当前键盘焦点是否落在“可编辑文本控件”上（编辑框/文档/多行文本框等）。

    用于在面板可见时判断“焦点是否还在输入框”：若焦点离开了文本控件（点到按钮、
    标题栏、桌面等），面板应自动关闭。UIA 不可用时保守返回 True（不主动关闭）。
    """
    uia = _automation()
    if uia is None:
        return True
    elem = c_void_p()
    if _call(uia, 8, [POINTER(c_void_p)], byref(elem)) != S_OK or not elem.value:
        return False  # 没有任何焦点元素 -> 不在输入框
    try:
        if _is_password(elem):
            return True  # 密码框也是可编辑的
        pat = c_void_p()
        # TextPattern（富文本/网页编辑/文档）
        if (_call(elem, 16, [c_int, POINTER(c_void_p)],
                  UIA_TextPatternId, byref(pat)) == S_OK and pat.value):
            _release(pat)
            return True
        # ValuePattern（普通单行输入框 <input> 等）
        if (_call(elem, 16, [c_int, POINTER(c_void_p)],
                  UIA_ValuePatternId, byref(pat)) == S_OK and pat.value):
            _release(pat)
            return True
        return False
    finally:
        _release(elem)


def _get_focused_text_raw(max_len=60):
    """读取当前键盘焦点元素的文本（最近 max_len 个字符）；失败/密码框返回 ''。

    覆盖范围：Win32、WPF、UWP、Qt、Chrome/Edge 网页输入框、Electron 应用等
    —— 只要实现了 UIA TextPattern 或 ValuePattern 就能读到。
    """
    uia = _automation()
    if uia is None:
        return ""
    elem = c_void_p()
    try:
        # IUIAutomation::GetFocusedElement -> 下标 8
        if _call(uia, 8, [POINTER(c_void_p)], byref(elem)) != S_OK or not elem.value:
            return ""
    except Exception:
        return ""
    try:
        if _is_password(elem):
            return ""
        t = _from_text_pattern(elem, max_len)
        if not t:
            t = _from_value_pattern(elem)
        if not t:
            return ""
        t = t.replace("\r", "")
        return t[-max_len:]
    except Exception:
        return ""
    finally:
        _release(elem)


# ---------- 带超时保护的对外入口（防止目标程序卡死时 UIA 同步调用挂起） ----------
def get_focused_rect(timeout=0.30):
    """带超时保护的版本：目标程序卡死导致 UIA 调用挂起时，最多等 timeout 秒后放弃并返回
    None，避免阻塞调用方线程（候选条在主线程定位时若目标进程假死，同步 UIA 会冻住整个 GUI）。
    """
    return _uia_worker.call(_get_focused_rect_raw, timeout, None)


def is_focused_editable(timeout=0.30):
    """带超时保护：见 get_focused_rect 注释。目标卡死时回退为 True（当作“仍在输入框”，
    不主动关闭候选条），宁可保留也不冻结/误关。
    """
    return _uia_worker.call(_is_focused_editable_raw, timeout, True)


def get_focused_text(max_len=60, timeout=0.35):
    """带超时保护：目标程序卡死时，UIA 调用最多阻塞 timeout 秒后回退为 ''，
    绝不拖死采样线程（否则自动弹出会在一次卡顿后彻底失效）。
    """
    return _uia_worker.call(
        lambda: _get_focused_text_raw(max_len), timeout, "")
