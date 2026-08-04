"""把选中的颜文字输入到之前焦点所在窗口。

提供两种方式：
  * "clipboard"：写入剪贴板后发送 Ctrl+V，默认方式。
    发送的是命令键（Ctrl+V），不会被中文输入法（如微软拼音）拦截，
    因此对颜文字兼容性最好、在中文 Windows 下最稳。用后自动恢复用户原剪贴板。
  * "type"：用 pynput 逐字符模拟键入。在中文输入法激活时会把字符吞进组字
    缓冲区，导致出现乱码（如「哦哦」）。本模式下会自动把前台线程的键盘布局
    切到「英文(美国)」再键入、打完切回原布局，从而剥离微软拼音这类 IME、
    让键事件直通，规避乱码；即便如此，clipboard 模式仍更稳，故作为默认推荐。
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
        # 模拟键入模式：打字前把前台线程的键盘布局切到「英文(美国)」，
        # 这样微软拼音这类 IME 被整体剥离（英文布局不带 IME），键事件直通，
        # 颜文字不会被吞进组字缓冲区变成乱码（如「哦哦」）；打完再切回原布局，
        # 不留下“卡在英文”的副作用。
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
            # 任意方式失败都退回直接键入
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
