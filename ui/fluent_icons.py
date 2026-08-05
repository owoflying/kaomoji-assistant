"""Segoe Fluent Icons 官方图标库封装。

Windows 11 自带 "Segoe Fluent Icons" 系统字体，所有官方图标字形位于 Unicode
私有区（PUA）。把 QLabel 的字体设为该字体、文本设为对应 codepoint，即可渲染与
WinUI 3 完全一致的官方图标，替代原先的 emoji。

codepoint 取自微软官方 Segoe Fluent Icons 文档：
https://learn.microsoft.com/windows/apps/design/style/segoe-fluent-icons-font
"""
from PySide6.QtWidgets import QLabel
from PySide6.QtGui import QFont, QColor
from PySide6.QtCore import Qt

# Windows 11 系统图标字体名
FONT = "Segoe Fluent Icons"

# 本项目用到的字形（codepoint 取自官方文档，PUA 区）
GLYPHS = {
    "home":          0xE80F,  # Home
    "library":       0xE736,  # ReadingMode（书本，对应「颜文字库」）
    "edit":          0xE70F,  # Edit（铅笔，对应「我的颜文字」）
    "flash":         0xE945,  # LightningBolt（闪电，对应「快捷短语」）
    "search":        0xE721,  # Search
    "settings":      0xE713,  # Settings（齿轮）
    "info":          0xE946,  # Info（关于）
    "close":         0xE894,  # Close（关闭叉号）
    "add":           0xE710,  # Add
    "back":          0xE72B,  # Back
    "forward":       0xE72A,  # Forward
    "refresh":       0xE72C,  # Refresh
    "more":          0xE712,  # More
    "chevron_down":  0xE70D,  # ChevronDown
    "check":         0xE739,  # CheckMark
    "lock":          0xE72E,  # Lock
    "share":         0xE72D,  # Share
    "delete":        0xE74D,  # Delete
    "global_nav":    0xE700,  # GlobalNavButton
    "mail":          0xE715,  # Mail
    "people":        0xE716,  # People
    "pin":           0xE718,  # Pin
}


def char(name):
    """返回某个图标名对应的字形字符（PUA codepoint）。"""
    return chr(GLYPHS.get(name, 0xE700))


def icon_label(name, size=16, color=None, parent=None):
    """生成一个带官方图标的 QLabel。

    name   : GLYPHS 中的图标名
    size   : 像素字号（图标随字号缩放）
    color  : 可选颜色（CSS 颜色字符串），默认跟随父级
    """
    lb = QLabel(char(name), parent)
    f = QFont(FONT)
    f.setPixelSize(size)
    # 图标字形不希望被抗锯齿之外的文本 hint 影响；关闭粗体避免变形
    f.setWeight(QFont.Weight.Normal)
    lb.setFont(f)
    lb.setAlignment(Qt.AlignCenter)
    if color is not None:
        lb.setStyleSheet("color:%s;background:transparent;" % color)
    else:
        lb.setStyleSheet("background:transparent;")
    return lb


def recolor(label, color):
    """重设已有图标 QLabel 的颜色（用于主题切换 / 选中态）。"""
    if label is None:
        return
    label.setStyleSheet("color:%s;background:transparent;" % color)
