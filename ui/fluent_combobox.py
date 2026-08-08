"""Fluent 风格下拉框：统一所有 QComboBox 的弹出列表主题。

Windows（尤其 Win10）上 QComboBox 的弹出列表是一个独立的顶层窗口，默认会
跟随系统的深色/浅色设置来绘制（原生主题），而不是跟随应用当前主题。这导致
应用设为浅色时，下拉列表仍然是深色背景（陈年老 bug）。

修复方式：在 showPopup() 里**不依赖系统原生主题**，直接用 Qt 自带 QSS 把
弹窗（列表 + 视口 + 外层容器 + 滚动条）按应用主题画成不透明色块，覆盖掉原生
深色底。Win10 / Win11 都稳定生效。

DWM 沉浸式深色模式（Win11）仅作为 best-effort 顺带同步外框，失败也无妨。
"""
from PySide6.QtWidgets import QComboBox, QWidget

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

    def _popup_stylesheet(self, t):
        """根据主题生成弹窗全套 QSS（显式色值，覆盖原生主题）。"""
        return (
            "QAbstractItemView {{"
            "  background-color: {bg};"
            "  color: {fg};"
            "  border: 1px solid {bd};"
            "  border-radius: 8px;"
            "  padding: 6px;"
            "  outline: none;"
            "  selection-background-color: {ac};"
            "  selection-color: #ffffff;"
            "}}"
            "QAbstractItemView::item {{"
            "  background-color: transparent;"
            "  color: {fg};"
            "  border-radius: 6px;"
            "  padding: 6px 10px;"
            "  min-height: 24px;"
            "}}"
            "QAbstractItemView::item:disabled {{ color: {bd}; }}"
            "QAbstractItemView::item:hover {{ background-color: {hv}; }}"
            "QAbstractItemView::item:selected {{"
            "  background-color: {ac}; color: #ffffff;"
            "}}"
            "QScrollBar:vertical {{ background: {bg}; width: 10px; margin: 2px; }}"
            "QScrollBar::handle:vertical {{"
            "  background: {bd}; border-radius: 5px; min-height: 24px;"
            "}}"
            "QScrollBar::corner {{ background: {bg}; }}"
        ).format(bg=t.card, fg=t.text, bd=t.card_border, ac=t.accent, hv=t.nav_hover)

    def showPopup(self):
        """弹出列表后强制用 Qt QSS 按其应用主题渲染，避免跟随系统原生主题。"""
        super().showPopup()
        try:
            view = self.view()
            if view is None:
                return
            t = self._resolve_theme()
            qss = self._popup_stylesheet(t)
            # 1) 列表本体
            view.setStyleSheet(qss)
            # 2) 视口（避免透明/原生背景透出）
            vp = view.viewport()
            if vp is not None:
                vp.setStyleSheet(
                    "background-color: {bg}; color: {fg};".format(bg=t.card, fg=t.text)
                )
            # 3) 外层容器（QComboBoxPrivateContainer）：设为不透明、无边框，覆盖原生深底
            container = view.parent()
            if isinstance(container, QWidget):
                container.setStyleSheet(
                    "background-color: {bg}; border: none;"
                    "border-radius: 8px;".format(bg=t.card)
                )
            # 4) 弹窗顶层窗口本身也强制同色底，Win10 下原生深色框（黑边/黑条）由此盖掉
            popup = view.window()
            if isinstance(popup, QWidget) and popup != self.window():
                try:
                    popup.setStyleSheet(
                        "background-color: {bg}; border: none;".format(bg=t.card)
                    )
                except Exception:
                    pass
                # 5) best-effort：Win11 上用 DWM 同步外框（Win10 无效也无妨）
                if _has_dwm:
                    try:
                        apply_dark_mode(int(popup.winId()), t.dark)
                    except Exception:
                        pass
        except Exception:
            # 任何异常都不应阻塞下拉框弹出
            pass
