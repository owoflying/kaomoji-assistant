"""Fluent 风格下拉框：统一所有 QComboBox 的弹出列表主题与 DWM 暗色模式。

Windows 11 上 QComboBox 的弹出列表是一个独立的顶层窗口，默认会跟随系统的
深色/浅色设置，而不是跟随应用当前主题。这会导致应用设为浅色时，下拉列表
仍然是深色背景（陈年老 bug）。

修复方式：在 showPopup() 后获取弹出窗口的 native HWND，调用 DWM
沉浸式深色模式 API，使其与应用主题保持一致；同时沿用全局 QSS 的
QComboBox QAbstractItemView 样式。
"""
from PySide6.QtWidgets import QComboBox

from ui.win11_theme import Theme, current_theme_name
from ui.win_style import apply_dark_mode, _has_dwm


class FluentComboBox(QComboBox):
    def __init__(self, theme=None, parent=None):
        super().__init__(parent)
        self._theme = theme

    def set_theme(self, theme):
        """主题切换时更新，供父页面统一调用。"""
        self._theme = theme

    def _resolve_theme(self):
        """解析当前主题：优先使用显式传入的主题，否则读取全局当前主题名。"""
        t = self._theme
        if isinstance(t, Theme):
            return t
        if isinstance(t, str):
            return Theme(t)
        return Theme(current_theme_name())

    def showPopup(self):
        """弹出列表后立刻同步 DWM 暗色模式，避免弹出窗跟随系统主题。"""
        super().showPopup()
        if not _has_dwm:
            return
        try:
            view = self.view()
            if view is None:
                return
            popup = view.window()
            if popup is None or popup == self.window():
                return
            # 弹出列表是独立顶层窗口，需要单独设置沉浸式深色模式
            hwnd = int(popup.winId())
            apply_dark_mode(hwnd, self._resolve_theme().dark)
        except Exception:
            # 任何 native 层异常都不应阻塞下拉框弹出
            pass
