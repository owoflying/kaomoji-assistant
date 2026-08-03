"""把选中的颜文字输入到之前焦点所在窗口。

提供两种方式：
  * "clipboard"：写入剪贴板后发送 Ctrl+V，默认方式。
    发送的是命令键（Ctrl+V），不会被中文输入法（如微软拼音）拦截，
    因此对颜文字兼容性最好、在中文 Windows 下最稳。用后自动恢复用户原剪贴板。
  * "type"：用 pynput 逐字符模拟键入。在中文输入法激活时会把字符吞进组字
    缓冲区，导致出现乱码（如「哦哦」），故不作为默认；仅推荐输入法固定为
    英文模式的用户使用。
"""
from pynput.keyboard import Controller, Key

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication


class KaomojiInjector:
    def __init__(self):
        self._controller = Controller()

    def inject(self, text, method="clipboard"):
        if not text:
            return
        try:
            if method == "clipboard":
                self._inject_clipboard(text)
            else:
                self._controller.type(text)
        except Exception:
            # 任意方式失败都退回直接键入
            try:
                self._controller.type(text)
            except Exception:
                pass

    def _inject_clipboard(self, text):
        clipboard = QApplication.clipboard()
        saved = clipboard.mimeData()  # 记住用户原来的剪贴板（含图片等）
        clipboard.setText(text)
        c = self._controller
        c.press(Key.ctrl)
        c.press("v")
        c.release("v")
        c.release(Key.ctrl)
        # 粘贴完成后恢复原剪贴板，避免覆盖用户内容
        if saved is not None:
            QTimer.singleShot(
                250, lambda: clipboard.setMimeData(saved)
            )
