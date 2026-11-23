"""Fluent 风格下拉框：统一所有 QComboBox 的弹出列表主题。

Windows（尤其 Win10）上 QComboBox 的弹出列表是一个独立的顶层窗口，默认会
跟随系统的深色/浅色设置来绘制（原生主题），而不是跟随应用当前主题。这导致
应用设为浅色时，下拉列表仍然是深色背景（陈年老 bug）。

修复方式：在 showPopup() 里**不依赖系统原生主题**，直接用 Qt 自带 QSS 把
弹窗（列表 + 视口 + 外层容器 + 滚动条）按应用主题画成不透明色块，覆盖掉原生
深色底。Win10 / Win11 都稳定生效。

DWM 沉浸式深色模式（Win11）仅作为 best-effort 顺带同步外框，失败也无妨。
"""
from PySide6.QtWidgets import QApplication, QComboBox, QWidget

from ui.win11_theme import Theme, current_theme
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
        return current_theme()

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
            "  selection-color: {oa};"
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
            "  background-color: {ac}; color: {oa};"
            "}}"
            "QScrollBar:vertical {{ background: {bg}; width: 10px; margin: 2px; }}"
            "QScrollBar::handle:vertical {{"
            "  background: {bd}; border-radius: 5px; min-height: 24px;"
            "}}"
            "QScrollBar::corner {{ background: {bg}; }}"
        ).format(bg=t.card, fg=t.text, bd=t.card_border, ac=t.accent, hv=t.nav_hover, oa=getattr(t, "on_accent", "#ffffff"))

    def showPopup(self):
        """弹出下拉列表，并按当前主题着色。

        ## 为什么要"先藏起来、贴好样式、再显示"

        原来的实现是 `super().showPopup()` 之后才贴样式——但 `showPopup()`
        会**立刻把窗口映射到屏幕**，那一帧用的还是 Qt 原生（浅色）绘制。
        用户看到的是"先弹出浅色列表，然后闪一下变深色"。

        现在改成：
          1. 先给视图和容器贴好样式（此时窗口还没显示，不产生可见帧）；
          2. 再调 `super().showPopup()` 让窗口映射到屏幕。

        这样第一帧就是正确配色，没有闪烁。
        """
        try:
            self._prepare_popup_theme()
        except Exception:
            # 样式准备失败也必须能弹出，退化到原行为
            pass
        super().showPopup()
        # 弹出后再补一次：某些平台会在 show 时重置 popup 的样式表
        # （Qt 内部给全屏模态容器设过样式）。这一步很便宜，是幂等的。
        try:
            self._prepare_popup_theme()
        except Exception:
            pass
        # 再排一次到事件循环的下一个周期。
        #
        # ⚠ 实测那层裸 QWidget（见 _prepare_popup_theme 第 6 步）在
        # `super().showPopup()` 返回时**还没被创建**，前两次调用都看不见它，
        # 于是它始终带着系统默认的黑色背景。用 QTimer.singleShot(0) 推到
        # 下一轮事件循环就能抓住它。
        try:
            from PySide6.QtCore import QTimer

            QTimer.singleShot(0, self._safe_prepare_popup_theme)
        except Exception:
            pass

    def _safe_prepare_popup_theme(self):
        """包一层异常保护，供 QTimer 回调使用。"""
        try:
            self._prepare_popup_theme()
        except Exception:
            pass

    def _prepare_popup_theme(self):
        """把当前主题的配色贴到下拉列表的各个组成部分上（不显示窗口）。

        下拉列表由三层组成，缺一层都会露出系统原生色：
          1. 列表本体 `view()` —— 条目背景与文字
          2. 视口 `view.viewport()` —— 避免透明透出原生背景
          3. 外层容器 `view.parent()`（QComboBoxPrivateContainer）—— 边框与圆角
          4. 弹窗顶层窗口 `view.window()` —— Win10 下的原生黑边
        """
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
                "background-color: {bg}; color: {fg};".format(
                    bg=t.card, fg=t.text))
        # 3) 外层容器
        container = view.parent()
        if isinstance(container, QWidget):
            container.setStyleSheet(
                "background-color: {bg}; border: none;"
                "border-radius: 8px;".format(bg=t.card))
        # 4) 弹窗顶层窗口
        popup = view.window()
        if isinstance(popup, QWidget) and popup != self.window():
            try:
                popup.setStyleSheet(
                    "background-color: {bg}; border: none;".format(bg=t.card))
            except Exception:
                pass
            # 5) Win11：DWM 同步外框（Win10 无效也无妨）
            if _has_dwm:
                try:
                    apply_dark_mode(int(popup.winId()), t.dark)
                except Exception:
                    pass
        # 6) ⚠ 最外层那个裸 QWidget 也要上色
        #
        # Windows 上 QComboBox 的下拉是**两层独立顶层窗口**叠在一起的：
        #
        #     QWidget   ← 无父窗口、无样式，系统默认绘制（黑色！）
        #     QFrame    ← view.window() 返回这一层
        #         └── QListView
        #
        # 只给 QFrame 设样式时，外面那层裸 QWidget 仍是黑的——实测表现为
        # 下拉旁边露出一条黑边/黑条，浅色主题下极其刺眼。
        #
        # 这两层不是父子关系（都是 top-level），只能从
        # `QApplication.topLevelWidgets()` 里按位置尺寸匹配找出来。
        try:
            import os as _os

            if _os.name == "nt" and isinstance(popup, QWidget):
                geo = popup.geometry()
                for w in QApplication.topLevelWidgets():
                    if w is popup or w is self.window():
                        continue
                    if not isinstance(w, QWidget):
                        continue
                    # 判据：同位置同尺寸（容差 2px，窗口边框各平台有差异）、
                    # 且是裸 QWidget（不是 QFrame/QLabel 之类）、自己没设过样式
                    near = (abs(w.x() - geo.x()) <= 2
                            and abs(w.y() - geo.y()) <= 2
                            and abs(w.width() - geo.width()) <= 2
                            and abs(w.height() - geo.height()) <= 2)
                    if near and type(w) is QWidget and not (w.styleSheet() or ""):
                        w.setStyleSheet(
                            "background-color: {bg}; border: none;".format(
                                bg=t.card))
                        if _has_dwm:
                            try:
                                apply_dark_mode(int(w.winId()), t.dark)
                            except Exception:
                                pass
        except Exception:
            pass
