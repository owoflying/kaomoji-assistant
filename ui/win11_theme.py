"""Win11 Settings 风格统一配色与样式工具。

提供浅色/深色主题令牌、圆角卡片/按钮/输入框的 QSS、以及常用动画辅助。
所有颜色尽量贴近 Windows 11 设置应用的 Mica/Acrylic 观感。
"""
from PySide6.QtCore import Qt, QEasingCurve, QPropertyAnimation, QAbstractAnimation
from PySide6.QtWidgets import QGraphicsOpacityEffect
from PySide6.QtGui import QFont


# 全局当前主题名（light/dark）。部分独立窗口/弹出层无法从父级便捷取到主题，
# 由 main.py 在启动及 apply_settings 时同步，供 FluentComboBox 等控件统一读取。
_current_theme_name = "light"


def set_app_theme(name):
    """设置当前应用主题名，供独立对话框/弹出层在无法遍历父窗口时读取。"""
    global _current_theme_name
    _current_theme_name = "dark" if str(name).lower() == "dark" else "light"


def current_theme_name():
    """返回当前应用主题名（light/dark）。"""
    return _current_theme_name


class Theme:
    """当前主题色板；根据名称 light/dark 返回对应颜色。"""

    def __init__(self, name="light"):
        self.name = name
        self.dark = name == "dark"
        if self.dark:
            # 深色：纯净暗色石英风格 —— 减淡灰度、提升质感
            self.bg = "#1a1a1a"                # 内容区底色
            self.sidebar = "transparent"       # 侧边栏跟随窗口 Mica 底
            self.card = "#2a2a2a"              # 卡片背景
            self.card_hover = "#303030"
            self.card_border = "#3d3d3d"
            self.text = "#ffffff"
            self.text_secondary = "#9ca3af"
            self.text_tertiary = "#6b7280"
            self.accent = "#60cdff"            # Win11 强调蓝（深色模式）
            self.accent_hover = "#7bd4ff"
            self.accent_bg = "rgba(96,205,255,0.15)"
            self.nav_hover = "rgba(255,255,255,0.06)"
            self.nav_selected = "rgba(255,255,255,0.08)"
            self.input_bg = "rgba(255,255,255,0.06)"
            self.input_border = "rgba(255,255,255,0.12)"
            self.divider = "rgba(255,255,255,0.08)"
            self.shadow = "0,0,0"
            self.toggle_off = "#5e5e5e"      # 开关未选中时的轨道灰
            # 无边框窗口：亚克力基底（半透明 tint，让 DWM 亚克力模糊透出）
            self.window_tint = "rgba(30,30,30,0.85)"
            self.window_grad_top = "rgba(255,255,255,0.04)"
            self.window_grad_bottom = "rgba(0,0,0,0.10)"
            self.window_border = "rgba(255,255,255,0.10)"
            self.shadow_color = "rgba(0,0,0,0.30)"
            self.content_surface = "rgba(40,40,40,0.60)"   # 内容区表面（比侧栏亚克力略实）
        else:
            # 浅色：纯白石英风格 —— 更纯净的白色、柔和阴影、细腻质感
            self.bg = "#ffffff"
            self.sidebar = "transparent"
            self.card = "#ffffff"
            self.card_hover = "#f7f7f7"
            self.card_border = "#e8e8e8"
            self.text = "#1f1f1f"
            self.text_secondary = "#5f5f5f"
            self.text_tertiary = "#9ca3af"
            self.accent = "#0067c0"            # Win11 强调蓝（浅色模式）
            self.accent_hover = "#0a72cf"
            self.accent_bg = "rgba(0,103,192,0.10)"
            self.nav_hover = "rgba(0,0,0,0.04)"
            self.nav_selected = "rgba(0,0,0,0.06)"
            self.input_bg = "rgba(255,255,255,0.85)"
            self.input_border = "rgba(0,0,0,0.10)"
            self.divider = "rgba(0,0,0,0.06)"
            self.shadow = "0,0,0"
            self.toggle_off = "#b6b6b6"      # 开关未选中时的轨道灰
            # 无边框窗口：亚克力基底（更白的 tint，让 DWM 亚克力模糊透出）
            self.window_tint = "rgba(255,255,255,0.88)"
            self.window_grad_top = "rgba(255,255,255,0.08)"
            self.window_grad_bottom = "rgba(0,0,0,0.03)"
            self.window_border = "rgba(0,0,0,0.08)"
            self.shadow_color = "rgba(0,0,0,0.12)"
            self.content_surface = "rgba(255,255,255,0.70)"   # 内容区表面（比侧栏亚克力略实）

    def hex(self, key):
        return getattr(self, key)

    def style_sheet(self):
        """返回应用到统一窗口根 widget 的全局 QSS。"""
        t = self
        # 把内置图标字体追加到全局字体回退链「末尾」：正文仍由前面的界面字体渲染，
        # 而 PUA 区的图标码点在前面字体都缺字形时能回退到它。
        # 这是兜底——图标 QLabel 自身样式表里已显式指定该字体（见 ui/fluent_icons.py），
        # 因为 QSS 的 font-family 优先级高于 setFont()，只靠 setFont 会被这条规则覆盖。
        try:
            from ui.fluent_icons import ensure_icon_font
            icon_family = ', "%s"' % ensure_icon_font()
        except Exception:
            icon_family = ""
        return f"""
        QWidget {{
            font-family: "Segoe UI Variable", "Segoe UI", "Microsoft YaHei UI", sans-serif{icon_family};
            color: {t.text};
            outline: none;
        }}
        QLabel#PageTitle {{
            font-size: 28px;
            font-weight: 600;
            color: {t.text};
            margin-bottom: 6px;
        }}
        QLabel#HomeTitle {{
            font-size: 18px;
            font-weight: 600;
            color: {t.text};
        }}
        QLabel#StatValue {{
            font-size: 22px;
            font-weight: 600;
            color: {t.accent};
        }}
        QLabel#SectionTitle {{
            font-size: 16px;
            font-weight: 600;
            color: {t.text};
            margin-top: 8px;
        }}
        QLabel#CardTitle {{
            font-size: 14px;
            font-weight: 600;
            color: {t.text};
        }}
        QLabel#BodyText {{
            font-size: 13px;
            color: {t.text_secondary};
        }}
        QLabel#Caption {{
            font-size: 12px;
            color: {t.text_tertiary};
        }}
        QFrame#Card {{
            background: {t.card};
            border: 1px solid {t.card_border};
            border-radius: 12px;
        }}
        QFrame#CardHover:hover {{
            background: {t.card_hover};
        }}
        QPushButton {{
            background: {t.input_bg};
            border: 1px solid {t.input_border};
            border-radius: 6px;
            padding: 6px 16px;
            font-size: 13px;
            color: {t.text};
        }}
        QPushButton:hover {{
            background: {t.nav_hover};
        }}
        QPushButton:pressed {{
            background: {t.nav_selected};
        }}
        QPushButton#AccentButton {{
            background: {t.accent};
            color: #ffffff;
            border: 1px solid rgba(0,0,0,0.12);
            font-weight: 600;
        }}
        QPushButton#AccentButton:hover {{
            background: {t.accent_hover};
        }}
        QPushButton#AccentButton:pressed {{
            background: {t.accent};
        }}
        QPushButton#DangerButton {{
            background: #d13438;
            color: #ffffff;
            border: 1px solid rgba(0,0,0,0.12);
            font-weight: 600;
        }}
        QPushButton#DangerButton:hover {{
            background: #b62a2e;
        }}
        QPushButton#DangerButton:pressed {{
            background: #d13438;
        }}
        QPushButton#TitleButton, QPushButton#TitleClose {{
            background: transparent;
            border: none;
            border-radius: 6px;
            padding: 0;
            color: {t.text_secondary};
            font-family: "Segoe UI", "Microsoft YaHei UI", sans-serif;
            font-size: 14px;
        }}
        QPushButton#TitleButton:hover, QPushButton#TitleClose:hover {{
            background: {t.nav_hover};
            color: {t.text};
        }}
        QPushButton#TitleButton:pressed, QPushButton#TitleClose:pressed {{
            background: {t.nav_selected};
        }}
        QPushButton#TitleClose:hover {{
            background: #e81123;
            color: white;
        }}
        QDialog {{
            background: {t.bg};
        }}
        QLineEdit, QComboBox, QSpinBox {{
            background: {t.input_bg};
            border: 1px solid {t.input_border};
            border-radius: 6px;
            padding: 6px 10px;
            font-size: 13px;
            color: {t.text};
        }}
        QLineEdit:focus, QComboBox:focus, QSpinBox:focus {{
            border: 1px solid {t.accent};
        }}
        QListWidget {{
            background: transparent;
            border: none;
            outline: none;
        }}
        QListWidget::item {{
            background: {t.card};
            border: 1px solid {t.card_border};
            border-radius: 10px;
            margin: 4px 6px;
            padding: 10px 12px;
            min-height: 30px;
            color: {t.text};
        }}
        QListWidget::item:selected {{
            background: {t.accent_bg};
            border: 1px solid {t.accent};
        }}
        QListWidget::item:hover {{
            background: {t.nav_hover};
            border: 1px solid {t.input_border};
        }}
        QListWidget::item:selected:hover {{
            background: {t.accent_bg};
            border: 1px solid {t.accent};
        }}
        QScrollBar:vertical {{
            background: transparent;
            width: 10px;
            margin: 4px 2px;
        }}
        QScrollBar::handle:vertical {{
            background: {t.card_border};
            border-radius: 5px;
            min-height: 40px;
        }}
        QScrollBar::handle:vertical:hover {{
            background: {t.text_tertiary};
        }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
            height: 0;
            border: none;
            background: transparent;
        }}
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
            background: transparent;
        }}
        QSlider::groove:horizontal {{
            height: 6px;
            background: {t.input_border};
            border-radius: 3px;
        }}
        QSlider::handle:horizontal {{
            width: 18px;
            height: 18px;
            margin: -7px 0;
            background: {t.accent};
            border: 2px solid {t.card};
            border-radius: 9px;
        }}
        QSlider::handle:horizontal:hover {{
            background: {t.accent_hover};
        }}
        QCheckBox {{
            font-size: 13px;
            color: {t.text};
        }}
        QCheckBox::indicator {{
            width: 18px;
            height: 18px;
            border-radius: 4px;
            border: 1px solid {t.input_border};
            background: transparent;  /* 未选中=空白方框（不填充） */
        }}
        QCheckBox::indicator:checked {{
            background: {t.accent};
            border: 1px solid {t.accent};
            color: #ffffff;  /* 选中=填充强调色方框 + 白色对勾 */
        }}
        QComboBox {{
            background: {t.input_bg};
            border: 1px solid {t.input_border};
            border-radius: 6px;
            padding: 4px 10px;
            font-size: 13px;
            color: {t.text};
            min-height: 22px;
        }}
        QComboBox:hover {{
            border: 1px solid {t.input_border};
        }}
        QComboBox:focus, QComboBox:on {{
            border: 1px solid {t.accent};
        }}
        QComboBox::drop-down {{
            subcontrol-origin: padding;
            subcontrol-position: top right;
            border: none;
            width: 26px;
        }}
        QComboBox::down-arrow {{
            image: none;
            border-left: 5px solid transparent;
            border-right: 5px solid transparent;
            border-top: 6px solid {t.text_secondary};
            width: 0px;
            height: 0px;
        }}
        QComboBox QAbstractItemView {{
            background-color: {t.card};
            color: {t.text};
            border: 1px solid {t.card_border};
            border-radius: 8px;
            padding: 6px;
            outline: none;
            selection-background-color: {t.accent};
            selection-color: #ffffff;
        }}
        QComboBox QAbstractItemView::item {{
            background-color: transparent;
            color: {t.text};
            border-radius: 6px;
            padding: 6px 10px;
            min-height: 24px;
        }}
        QComboBox QAbstractItemView::item:hover {{
            background-color: {t.nav_hover};
        }}
        QComboBox QAbstractItemView::item:selected {{
            background-color: {t.accent};
            color: #ffffff;
        }}
        QFrame#Divider {{
            background: {t.divider};
            max-height: 1px;
        }}
        QScrollArea, QScrollArea QWidget#SettingsBody {{
            background: transparent;
            border: none;
        }}
        QMenu {{
            background: {t.card};
            border: 1px solid {t.card_border};
            border-radius: 8px;
            padding: 6px;
            color: {t.text};
            font-size: 13px;
        }}
        QMenu::item {{
            background: transparent;
            padding: 7px 18px 7px 14px;
            border-radius: 6px;
            color: {t.text};
        }}
        QMenu::item:selected {{
            background: {t.accent};
            color: #ffffff;
        }}
        QMenu::separator {{
            height: 1px;
            background: {t.divider};
            margin: 6px 8px;
        }}
        """


    def menu_style(self):
        """QMenu 专属样式表（菜单项/分隔符），供托盘菜单等独立弹出菜单显式 setStyleSheet。

        独立 QMenu（无父 widget）不一定继承 app 全局 QSS，因此由调用方显式应用并随主题刷新。
        """
        t = self
        return f"""
        QMenu {{
            background: {t.card};
            border: 1px solid {t.card_border};
            border-radius: 8px;
            padding: 6px;
            color: {t.text};
            font-size: 13px;
        }}
        QMenu::item {{
            background: transparent;
            padding: 7px 18px 7px 14px;
            border-radius: 6px;
            color: {t.text};
        }}
        QMenu::item:selected {{
            background: {t.accent};
            color: #ffffff;
        }}
        QMenu::separator {{
            height: 1px;
            background: {t.divider};
            margin: 6px 8px;
        }}
        """


def fade_in(widget, duration=180):
    """让 widget 从 0 淡入到 1；若已有 opacity effect 则复用。"""
    effect = widget.graphicsEffect()
    if not isinstance(effect, QGraphicsOpacityEffect):
        effect = QGraphicsOpacityEffect(widget)
        effect.setOpacity(0)
        widget.setGraphicsEffect(effect)
    else:
        effect.setOpacity(0)
    anim = QPropertyAnimation(effect, b"opacity", widget)
    anim.setDuration(duration)
    anim.setStartValue(0.0)
    anim.setEndValue(1.0)
    anim.setEasingCurve(QEasingCurve.OutQuad)
    anim.start(QAbstractAnimation.DeleteWhenStopped)
    return anim


def kaomoji_font(size=14):
    """返回适合渲染颜文字（含全角括号、片假名、特殊符号、emoji）的字体栈。

    关键：用 setFamilies 给出「按字形逐字回退」的字体链，而不是强制单一
    “Segoe UI Symbol”——后者覆盖不全，缺失字形会变成豆腐块或基线错位。
    顺序：彩色 emoji -> 符号字体 -> 中文/UI 字体兜底，确保绝大多数颜文字正确显示。
    """
    f = QFont()
    f.setFamilies([
        "Segoe UI Emoji",
        "Segoe UI Symbol",
        "Microsoft YaHei UI",
        "Segoe UI Variable",
        "Segoe UI",
        "Arial Unicode MS",
    ])
    f.setPointSize(size)
    return f
