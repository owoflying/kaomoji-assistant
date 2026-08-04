"""把选中的颜文字输入到之前焦点所在窗口。

提供三种方式：
  * "clipboard"：写入剪贴板后发送 Ctrl+V，默认方式。
    发送的是命令键（Ctrl+V），不会被中文输入法（如微软拼音）拦截，
    因此对颜文字兼容性最好、在中文 Windows 下最稳。用后自动恢复用户原剪贴板。
  * "type"：用 pynput 逐字符模拟键入。中文输入法（如微软拼音）会把键吞进组字
    缓冲区导致乱码（如「哦哦」），故打字前先把前台线程的键盘布局切到「英文(美国)」
    （该布局不带 IME，IME 整体被剥离、键事件直通），打完再切回原布局。
    关键在于：切完要「轮询确认英文布局真正生效」后才开打——否则打字比切换抢先发出
    （电脑越快越容易发生），就会被中文输入法吞成乱码（电脑越卡反而越稳，正是这个竞态）。
  * "direct"：直接字符投递。用 PostMessageW(WM_CHAR) 把字符逐个投递到当前焦点控件，
    不走键盘、不走剪贴板、不经过 IME——微软拼音根本不参与，从根上杜绝乱码，
    也不依赖 pynput 的 SendInput。依赖目标控件处理 WM_CHAR（记事本/浏览器输入框/
    Office/聊天框等标准编辑控件都支持）；拿不到焦点控件或投递失败时自动退回剪贴板。
"""
import time

from pynput.keyboard import Controller, Key

from PySide6.QtCore import QTimer, QMimeData
from PySide6.QtWidgets import QApplication

from core import win_utils


class KaomojiInjector:
    def __init__(self):
        self._controller = Controller()

    def inject(self, text, method="clipboard"):
        if not text:
            return
        if method == "clipboard":
            self._inject_clipboard(text)
            return
        if method == "direct":
            self._inject_direct(text)
            return
        # 模拟键入模式：打字前把前台线程的键盘布局切到「英文(美国)」，剥离微软拼音这类
        # IME，键事件直通；切到位（轮询确认）后再键入，打完切回原布局，不留下“卡在英文”的副作用。
        hwnd = win_utils.get_foreground_hwnd()
        saved_layout = win_utils.get_keyboard_layout(hwnd)
        eng = win_utils.ensure_english_layout()
        switched = bool(eng and saved_layout and saved_layout != eng)
        if switched:
            win_utils.set_keyboard_layout(hwnd, eng)
            # 轮询确认英文布局真正生效后再键入，消除“切换还没落地就开打”的竞态
            # （否则打字比切换抢先 -> 被中文 IME 吞成「哦哦」）
            self._wait_layout(hwnd, eng, timeout=0.6)
        try:
            self._controller.type(text)
        except Exception:
            # 任意方式失败都退回直接键入
            try:
                self._controller.type(text)
            except Exception:
                pass
        finally:
            # 无论成败都切回用户原来的键盘布局。
            # 关键：打完字后不能立刻切回——pynput.type() 只是把键事件“注入”进系统队列就返回，
            # 前台程序还要过一会儿才从自己的消息循环里真正处理成文字。若这里马上 PostMessage 切回中文，
            # “切回消息”可能抢在尾部键事件前面被处理，导致最后几个字符被中文 IME 吞成「哦哦」。
            # 故先按字数留一点“落字”时间（随字数增长、封顶），确保键都被处理掉后再切回。
            if switched and saved_layout:
                time.sleep(0.3 + min(len(text), 40) * 0.02)
                win_utils.set_keyboard_layout(hwnd, saved_layout)

    def _wait_layout(self, hwnd, target, timeout=0.6):
        """轮询前台线程的键盘布局，直到切到 target（英文）或超时。

        切换是异步的（尤其 PostMessage(WM_INPUTLANGCHANGEREQUEST) 要等目标窗口
        消息循环处理），只有确认生效后再键入，才能彻底避开“打字抢在切换前面”的竞态。
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                if win_utils.get_keyboard_layout(hwnd) == target:
                    return True
            except Exception:
                pass
            time.sleep(0.03)
        return False

    def _inject_direct(self, text):
        """直接字符投递：把字符以 WM_CHAR 送进当前焦点控件，绕过键盘与 IME。

        不经过剪贴板（不污染用户剪贴板）、不切输入法、不依赖 pynput 的 SendInput，
        因此不会有「哦哦」乱码，也不受微软拼音影响。依赖目标控件处理 WM_CHAR
        （记事本/浏览器输入框/Office/聊天框等标准编辑控件都支持）。
        拿不到焦点控件或投递失败时，自动退回剪贴板兜底，保证颜文字一定能落进去。
        """
        hwnd = win_utils.get_focused_control_hwnd()
        if not hwnd:
            self._inject_clipboard(text)
            return
        sent = win_utils.post_wm_char(hwnd, text)
        if sent == 0:
            self._inject_clipboard(text)

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
