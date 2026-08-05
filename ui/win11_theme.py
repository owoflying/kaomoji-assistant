"""Win11 Settings 风格统一配色与样式工具。

提供浅色/深色主题令牌、圆角卡片/按钮/输入框的 QSS、以及常用动画辅助。
所有颜色尽量贴近 Windows 11 设置应用的 Mica/Acrylic 观感。
"""
from PySide6.QtCore import Qt, QEasingCurve, QPropertyAnimation, QAbstractAnimation
from PySide6.QtWidgets import QGraphicsOpacityEffect
from PySide6.QtGui import QFont


class Theme:
    """当前主题色板；根据名称 light/dark 返回对应颜色。"""

    def __init__(self, name="light"):
        self.name = name
        self.dark = name == "dark"
        if self.dark:
            # 深色：Mica 暗底 + 稍亮的卡片表面
            self.bg = "#202020"                # 内容区底色
            self.sidebar = "transparent"       # 侧边栏跟随窗口 Mica 底
            self.card = "#2c2c2c"              # 卡片背景
            self.card_hover = "#323232"
            self.card_border = "#3a3a3a"
            self.text = "#ffffff"
            self.text_secondary = "#9ca3af"
            self.text_tertiary = "#6b7280"
            self.accent = "#60cdff"            # Win11 强调蓝（深色模式）
            self.accent_hover = "#7bd4ff"
            self.accent_bg = "rgba(96,205,255,0.15)"
            self.content_surface = "#242424"   # 右侧内容区表面（比侧栏 mica 略深）
            self.nav_hover = "rgba(255,255,255,0.06)"
            self.nav_selected = "rgba(255,255,255,0.08)"
            self.input_bg = "rgba(255,255,255,0.06)"
            self.input_border = "rgba(255,255,255,0.12)"
            self.divider = "rgba(255,255,255,0.08)"
            self.shadow = "0,0,0"
            self.toggle_off = "#5e5e5e"      # 开关未选中时的轨道灰
        else:
            # 浅色：米白 Mica 底 + 纯白卡片
            self.bg = "#f3f3f3"
            self.sidebar = "transparent"
            self.card = "#ffffff"
            self.card_hover = "#f9f9f9"
            self.card_border = "#e5e5e5"
            self.text = "#1f1f1f"
            self.text_secondary = "#5f5f5f"
            self.text_tertiary = "#9ca3af"
            self.accent = "#0067c0"            # Win11 强调蓝（浅色模式）
            self.accent_hover = "#0a72cf"
            self.accent_bg = "rgba(0,103,192,0.10)"
            self.content_surface = "#fbfbfb"   # 右侧内容区表面（比侧栏 mica 略亮）
            self.nav_hover = "rgba(0,0,0,0.04)"
            self.nav_selected = "rgba(0,0,0,0.06)"
            self.input_bg = "rgba(255,255,255,0.7)"
            self.input_border = "rgba(0,0,0,0.10)"
            self.divider = "rgba(0,0,0,0.06)"
            self.shadow = "0,0,0"
            self.toggle_off = "#b6b6b6"      # 开关未选中时的轨道灰

    def hex(self, key):
        return getattr(self, key)

    def style_sheet(self):
        """返回应用到统一窗口根 widget 的全局 QSS。"""
        t = self
        return f"""
        QWidget {{
            font-family: "Segoe UI Variable", "Segoe UI", "Microsoft YaHei UI", sans-serif;
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
            border-radius: 8px;
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
            color: white;
            border: none;
        }}
        QPushButton#AccentButton:hover {{
            background: {t.accent_hover};
        }}
        QPushButton#AccentButton:pressed {{
            background: {t.accent};
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
            background: transparent;
            border-radius: 6px;
            padding: 7px 10px;
            min-height: 30px;
            color: {t.text};
        }}
        QListWidget::item:selected {{
            background: {t.accent_bg};
        }}
        QListWidget::item:hover {{
            background: {t.nav_hover};
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
            background: {t.input_bg};
        }}
        QCheckBox::indicator:checked {{
            background: {t.accent};
            border: 1px solid {t.accent};
        }}
        QComboBox::drop-down {{
            border: none;
            width: 24px;
        }}
        QFrame#Divider {{
            background: {t.divider};
            max-height: 1px;
        }}
        QScrollArea, QScrollArea QWidget#SettingsBody {{
            background: transparent;
            border: none;
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


def nav_icon(char):
    """返回导航图标字符；优先尝试 Segoe Fluent Icons，否则用 emoji。"""
    # 这里用 emoji 作为稳妥 fallback，保证跨字体可见
    return char


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
