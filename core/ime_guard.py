"""检测其他输入法的候选窗 / 输入模式，避免抢数字快捷键。

## 要解决的冲突

用户热键唤起颜文字候选条时，如果系统输入法（微软拼音、搜狗、QQ 拼音…）
的候选窗**也正开着**，两边都会响应数字键 1~9：

    按 1 → 输入法选了它的第 1 个候选
         同时我们的候选条也选了它的第 1 个

结果是"抢输入法的数字快捷键"，用户以为选错了字。

## 检测手段

三条线索，任一成立就认为"输入法正在占用键盘"：

1. **IME 类窗口可见**：`IME` / `MSCTFIME UI` / `Default IME`，
   以及搜狗/QQ 拼音等的候选窗类名（多为 `SoPY_Comp`、`QQPinyin…`）
2. **当前焦点窗口处于输入法"本地状态"**（`ImmGetOpenStatus`）
3. **有 `UI` 类窗口可见**（微软拼音的候选窗类名就是 `UI`）

这些检测都只在**弹出前**跑一次，开销是几个 user32/imm32 调用，
远小于一次材质渲染。

## 设计取舍

只做"保守跳过"：检测不确定时**不弹**，让用户用热键按钮/托盘手动唤起。
这比误判成"没有输入法"而抢键更可接受——抢键会让用户丢字。
"""
import ctypes
import sys

#: 已知的输入法候选/组字窗口类名（小写比较）
_IME_CLASS_HINTS = (
    "ime", "msctfime ui", "default ime", "ui",
    "sopy_comp", "sopy_cand", "qqpinyin", "sogou",
    "pinyin", "inputmethod", "textinputhost",
)


def _enum_visible_windows():
    """枚举当前可见的顶层窗口，返回 [(hwnd, class_name)]。"""
    if sys.platform != "win32":
        return []
    try:
        from ctypes import wintypes as w

        u32 = ctypes.WinDLL("user32", use_last_error=True)
        result = []

        Proto = ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)

        def _cb(hwnd, _lparam):
            try:
                if not u32.IsWindowVisible(hwnd):
                    return True
                buf = ctypes.create_unicode_buffer(256)
                u32.GetClassNameW(hwnd, buf, 256)
                cls = (buf.value or "").strip()
                if cls:
                    result.append((hwnd, cls))
            except Exception:
                pass
            return True

        u32.EnumWindows(Proto(_cb), 0)
        return result
    except Exception:
        return []


def ime_popup_visible():
    """系统输入法的候选窗 / 组字窗是否正开着。

    :return: (bool, 原因字符串) —— 原因用于日志，便于排查误判
    """
    if sys.platform != "win32":
        return False, "non-windows"
    try:
        for _hwnd, cls in _enum_visible_windows():
            low = cls.lower()
            for hint in _IME_CLASS_HINTS:
                # 精确匹配 "ui"/"ime" 这类短名，避免误伤普通窗口
                if hint in ("ui", "ime"):
                    if low == hint:
                        return True, "class=%s" % cls
                elif hint in low:
                    return True, "class=%s" % cls
    except Exception as e:
        return False, "error: %r" % (e,)
    return False, ""


def ime_open_for_foreground():
    """前台窗口是否处于输入法"打开"状态（正在输入中文）。

    微软拼音等在内嵌模式下不产生独立候选窗，靠这条线索兜住。
    """
    if sys.platform != "win32":
        return False, "non-windows"
    try:
        from ctypes import wintypes as w

        u32 = ctypes.WinDLL("user32", use_last_error=True)
        imm = ctypes.WinDLL("imm32", use_last_error=True)
        hwnd = u32.GetForegroundWindow()
        if not hwnd:
            return False, ""
        himc = imm.ImmGetContext(w.HWND(hwnd)) if hasattr(w, "HWND") else None
        if not himc:
            return False, ""
        try:
            open_ = imm.ImmGetOpenStatus(himc)
            # 组字窗口可见 = 正在输入
            comp = imm.ImmGetCompositionStringW(himc, 0x0008, None, 0)
            return bool(open_ and comp and comp > 0), "imm-open"
        finally:
            imm.ImmReleaseContext(w.HWND(hwnd), himc)
    except Exception:
        return False, ""


def other_ime_active():
    """是否有其他输入法正在占用键盘（会与我们的数字快捷键冲突）。

    :return: (bool, 原因) —— True 表示"这会儿别弹我们的候选条"
    """
    ok, why = ime_popup_visible()
    if ok:
        return True, why
    ok2, why2 = ime_open_for_foreground()
    if ok2:
        return True, why2
    return False, ""
