"""把选中的颜文字输入到之前焦点所在窗口。

提供两种方式：
  * "type"（默认）：用 pynput 直接模拟键入，对 Unicode 颜文字兼容性最好；
  * "clipboard"：写入剪贴板后发送 Ctrl+V，速度快且不依赖逐字符输入，
    但会覆盖用户剪贴板内容。
"""
from pynput.keyboard import Controller, Key


class KaomojiInjector:
    def __init__(self):
        self._controller = Controller()

    def inject(self, text, method="type"):
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
        from PySide6.QtWidgets import QApplication

        clipboard = QApplication.clipboard()
        clipboard.setText(text)
        c = self._controller
        c.press(Key.ctrl)
        c.press("v")
        c.release("v")
        c.release(Key.ctrl)
