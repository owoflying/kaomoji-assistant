"""自动弹出监听：在用户于任意程序中打字时，识别情绪并触发候选条。

架构（键盘钩子 + 独立采样线程，两级分离）：
  * 键盘钩子回调只做最轻的事：记一个可见字符、置一个「有新输入」事件，
    微秒级返回，绝不在钩子里做 UIA/COM 调用，否则全系统打字都会卡；
  * 采样线程被事件唤醒后，先等约 40ms 让刚敲的字符落进控件（消除“多按一键”竞态），
    再节流（默认 200ms 一次）去读「当前焦点控件的真实文本」：
        1. WM_GETTEXT（core.win_utils）—— 老式 Win32 控件的亚毫秒级快速路径，
           记事本 / 桌面程序等绝大多数场景走这里，应优先尝试；
        2. UI Automation（core.uia_text）—— 覆盖浏览器 / Electron / UWP / WPF / Qt，
           这些自绘控件不响应 WM_GETTEXT，回退到此拿到 IME 合成后的中文；
        3. 可见字符缓冲 —— 英文/拼音场景的最后兜底。
  * 只在文本尾部判定情绪，避免整段历史文字把情绪「焊死」；
  * 取「最靠右」命中的情绪（detect_last），保证推荐跟着最新输入走。

防「注入后自我重触发」（三重保险，缺一不可）：
  1. 注入静默期：上屏前由主程序调用 notify_injected()，其后 _inject_quiet 秒内
     完全不采样 —— 挡掉模拟键入 / Ctrl+V 自己敲出来的按键事件；
  2. 剔除已注入文本：采样到的文本会把最近上屏过的颜文字整段删掉再判定。
     颜文字里常含 "?"「哇」等字符，不剔除就会命中「思考」等分类自我循环；
  3. 情绪锁定：某个情绪弹过一次后就锁定，直到它从扫描窗口里消失
     （用户删掉/继续打字把它挤出尾部）或检测到别的情绪，才允许再弹。
"""
import threading
import time
from collections import deque

from PySide6.QtCore import QObject, Signal

from core.emotion import detect_last
from core import win_utils

try:
    from core import uia_text
except Exception:  # pragma: no cover - 非 Windows 或 COM 不可用
    uia_text = None


class EmotionMonitor(QObject):
    emotion_detected = Signal(str)

    def __init__(self):
        super().__init__()
        self._listener = None
        self._buffer = ""           # 可见字符缓冲（中文捕获失败时的兜底）
        self._paused = True
        self._max_buf = 60
        self._scan_tail = 30        # 只在文本尾部这段长度内做情绪判定（取最近输入）
        # 采样线程
        self._thread = None
        self._stop_evt = threading.Event()
        self._typed_evt = threading.Event()
        self._interval = 0.20       # UIA 采样节流间隔（秒）
        self._settle = 0.04         # keydown 后等字符落进控件再读的短暂延迟（消“多按一键”）
        # 一次唤醒后连采几次的“驻留轮询”窗口：中文输入法(IME)在 keydown 后
        # 数十~数百毫秒才把中文落定到控件（如「开心」要等空格上屏），单采一次往往
        # 读不到，必须多采几次直到提交完成，否则就表现为“打完关键词还得再多按一键才弹”。
        self._dwell_count = 6       # 连采次数
        self._dwell_gap = 0.06      # 两次采样之间的间隔（秒）
        # 防重触发
        self._lock = threading.Lock()
        self._locked = None         # 已弹出过、暂不重复弹的情绪
        self._injected = deque(maxlen=8)   # 最近上屏过的颜文字
        self._inject_quiet = 1.5    # 上屏后的静默秒数
        self._quiet_until = 0.0

    # ---------- 生命周期 ----------
    def start(self):
        if self._listener is not None:
            return
        try:
            from pynput import keyboard as kb
        except Exception:
            return
        self._paused = False
        self._buffer = ""
        self._locked = None

        def on_press(key):
            self._on_press(key)

        self._listener = kb.Listener(on_press=on_press)
        self._listener.start()

        self._stop_evt.clear()
        self._thread = threading.Thread(
            target=self._sample_loop, name="emotion-sampler", daemon=True
        )
        self._thread.start()

    def stop(self):
        self._stop_evt.set()
        self._typed_evt.set()          # 唤醒采样线程让它退出
        if self._listener is not None:
            try:
                self._listener.stop()
            except Exception:
                pass
            self._listener = None
        self._thread = None
        self._paused = True
        self._buffer = ""

    def pause(self):
        self._paused = True
        self._buffer = ""

    def resume(self):
        if self._listener is not None:
            # 面板刚关闭时，目标输入框里往往还留着刚触发过的那句话，
            # 立刻恢复采样会马上又命中一次；给一小段静默期缓冲。
            self._quiet_until = max(self._quiet_until, time.time() + 0.6)
            self._paused = False

    # ---------- 上屏通知（由主程序在注入前调用） ----------
    def notify_injected(self, text):
        """告知监听器「即将把 text 上屏」，用于静默 + 后续剔除，避免自我重触发。"""
        now = time.time()
        with self._lock:
            if text:
                self._injected.append(text)
            self._quiet_until = max(self._quiet_until, now + self._inject_quiet)
            self._buffer = ""

    def notify_shown(self, emotion):
        """面板已因某情绪弹出（含手动场景）——锁定它，避免同一句话反复弹。"""
        with self._lock:
            self._locked = emotion

    # ---------- 键盘钩子（必须极快） ----------
    def _on_press(self, key):
        if self._paused or self._listener is None:
            return
        if time.time() < self._quiet_until:
            # 静默期内的按键多半是注入器自己模拟出来的，不能进缓冲
            return
        ch = None
        if getattr(key, "char", None) and len(key.char) == 1 and key.char.isprintable():
            ch = key.char
        if ch is not None:
            self._buffer = (self._buffer + ch)[-self._max_buf:]
        # 只置事件，重活交给采样线程
        self._typed_evt.set()

    # ---------- 采样线程 ----------
    def _read_focus_text(self):
        """按 WM_GETTEXT -> UIA -> 字符缓冲 的优先级读取当前输入内容。

        老式 Win32 控件（记事本、桌面程序等）走 WM_GETTEXT 是亚毫秒级快速路径，
        应优先尝试；浏览器 / Electron / UWP / WPF / Qt 等自绘控件不响应 WM_GETTEXT，
        才回退到 UI Automation（core.uia_text），后者是 COM 调用，耗时明显更高。
        """
        try:
            t = win_utils.get_focused_text(self._max_buf)
            if t:
                return t
        except Exception:
            pass
        if uia_text is not None:
            try:
                t = uia_text.get_focused_text(self._max_buf)
                if t:
                    return t
            except Exception:
                pass
        return self._buffer

    def _strip_injected(self, text):
        """把最近上屏过的颜文字从采样文本里删掉。

        颜文字里高频出现 "?"、"哇"、"泪" 之类的字符，它们正好也是情绪关键词，
        不删就会形成「上屏 -> 被自己读到 -> 再次弹出」的自激循环。
        """
        if not text:
            return text
        with self._lock:
            recent = list(self._injected)
        for kao in reversed(recent):
            if kao and kao in text:
                text = text.replace(kao, "")
        return text

    def _sample_loop(self):
        while not self._stop_evt.is_set():
            # 无人打字时阻塞等待，几乎不占 CPU
            if not self._typed_evt.wait(0.5):
                continue
            self._typed_evt.clear()
            if self._stop_evt.is_set():
                break
            if self._paused:
                continue
            # 上屏静默期：注入器正在模拟按键 / 粘贴，此刻读到的都是自己写的内容
            remain = self._quiet_until - time.time()
            if remain > 0:
                self._stop_evt.wait(min(remain, 1.0))
                continue
            # 驻留轮询：连采几次，捕捉 IME 组字完成后的中文，
            # 避免“打完关键词还得再多按一键才弹”。
            self._dwell_sample()
            # 节流：一次采样后至少间隔 _interval 再采下一次
            self._stop_evt.wait(self._interval)

    def _dwell_sample(self):
        """一次唤醒后连采若干次：覆盖 IME 提交中文的滞后窗口。

        命中情绪即停（已锁定，不再重复打扰）；未命中则继续轮询直到窗口结束。
        """
        for _ in range(self._dwell_count):
            if self._stop_evt.is_set() or self._paused:
                return
            remain = self._quiet_until - time.time()
            if remain > 0:
                self._stop_evt.wait(min(remain, 1.0))
                return
            # 等刚敲下的字符先落进目标控件再读：keydown 瞬间读取会落后一个字符。
            self._stop_evt.wait(self._settle)
            try:
                text = self._read_focus_text()
            except Exception:
                text = self._buffer
            text = self._strip_injected(text)
            if text:
                self._evaluate(text)
            # 已锁定（命中过情绪）则无需继续轮询
            with self._lock:
                if self._locked is not None:
                    return
            self._stop_evt.wait(self._dwell_gap)

    # ---------- 情绪判定 ----------
    def _evaluate(self, text):
        hit = detect_last(text[-self._scan_tail:])
        if hit is None:
            # 扫描窗口里已经没有任何情绪词 -> 解锁，下次命中可以重新弹
            with self._lock:
                self._locked = None
            return
        emotion = hit[0]
        with self._lock:
            if emotion == self._locked:
                return          # 这个情绪刚弹过且还挂在尾部，不重复打扰
            self._locked = emotion
            self._buffer = ""
        # 直接 emit：跨线程时 Qt 自动排队到主线程，已实测可正确投递
        self.emotion_detected.emit(emotion)
