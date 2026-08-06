"""Fluent System Icons 官方图标库封装（字体内置，跨 Win10/Win11 通用）。

微软 Fluent UI System Icons 字体（FluentSystemIcons-Regular.ttf）采用 MIT 许可，
可随程序分发。我们把该字体文件放在 ui/fonts/ 下，运行时用 QFontDatabase 注册，
从而不依赖系统是否自带 Segoe Fluent Icons（Win10 默认没有，会导致图标显示为豆腐块）。

codepoint 取自微软官方 fluentui-system-icons 仓库的 fonts/FluentSystemIcons-Regular.json
（24_regular 尺寸对应的十进制 PUA 码点）。
"""
import os

from PySide6.QtWidgets import QLabel
from PySide6.QtGui import QFont, QColor, QFontDatabase
from PySide6.QtCore import Qt

# 字体文件名（运行时通过 resource_path 定位，随包分发）
FONT_FILE = "FluentSystemIcons-Regular.ttf"
# 注册失败时的兜底字体名（尽量不出现豆腐块，但仍可能缺字形）
FONT_FALLBACK = "Segoe Fluent Icons"

# 本项目用到的字形（24_regular 十进制码点）
GLYPHS = {
    "home":          62593,  # ic_fluent_home_24_regular
    "library":       63742,  # ic_fluent_book_24_regular（对应「颜文字库」）
    "edit":          62430,  # ic_fluent_edit_24_regular
    "flash":         62482,  # ic_fluent_flash_auto_24_regular（闪电，对应「快捷短语」）
    "search":        63120,  # ic_fluent_search_24_regular
    "settings":      63146,  # ic_fluent_settings_24_regular
    "info":          62628,  # ic_fluent_info_24_regular
    "close":         62314,  # ic_fluent_dismiss_24_regular（关闭叉号）
    "add":           61706,  # ic_fluent_add_24_regular
    "back":          61788,  # ic_fluent_arrow_left_24_regular
    "forward":       61826,  # ic_fluent_arrow_right_24_regular
    "refresh":       61841,  # ic_fluent_arrow_sync_24_regular
    "more":          62807,  # ic_fluent_more_vertical_24_regular
    "chevron_down":  62116,  # ic_fluent_chevron_down_24_regular
    "check":         62101,  # ic_fluent_checkmark_24_regular
    "lock":          62723,  # ic_fluent_lock_shield_24_regular（无单独 lock）
    "share":         63152,  # ic_fluent_share_24_regular
    "delete":        62285,  # ic_fluent_delete_24_regular
    "global_nav":    62817,  # ic_fluent_navigation_24_regular
    "mail":          62727,  # ic_fluent_mail_24_regular
    "people":        62889,  # ic_fluent_people_24_regular
    "pin":           62978,  # ic_fluent_pin_24_regular
    "code":          62192,  # ic_fluent_code_24_regular（开发者模式标签）
}

_font_family = None
_font_registered = False
_font_error = None


def _resolve_path():
    # 延迟导入，避免循环依赖
    from core.runtime import resource_path
    return resource_path("ui", "fonts", FONT_FILE)


def ensure_icon_font():
    """注册内置图标字体（幂等）。需在 QApplication 创建之后调用。

    注意用 os.path.isfile 而非 os.path.exists：PyInstaller 的 --add-data 若把目标
    写成文件名而不是目录，会建出一个同名「目录」，此时 exists() 为真但
    addApplicationFont() 必然失败，导致静默回退到系统字体（Win10 上就是豆腐块）。
    """
    global _font_family, _font_registered, _font_error
    if _font_registered:
        return _font_family
    try:
        path = _resolve_path()
        if os.path.isfile(path):
            fid = QFontDatabase.addApplicationFont(path)
            fams = QFontDatabase.applicationFontFamilies(fid) if fid >= 0 else []
            if fams:
                _font_family = fams[0]
            else:
                _font_family = FONT_FALLBACK
                _font_error = "addApplicationFont failed (id=%s): %s" % (fid, path)
        else:
            _font_family = FONT_FALLBACK
            _font_error = "font file not found: %s" % path
    except Exception as e:  # pragma: no cover - 防御性
        _font_family = FONT_FALLBACK
        _font_error = "exception: %r" % (e,)
    _font_registered = True
    return _font_family


def font_status():
    """返回 (family, error)，用于诊断图标是否正常加载。"""
    ensure_icon_font()
    return _font_family, _font_error


def char(name):
    """返回某个图标名对应的字形字符（PUA codepoint）。"""
    return chr(GLYPHS.get(name, GLYPHS["home"]))


def _icon_qss(size, color=None):
    """图标 QLabel 的内联样式。

    ⚠️ 关键：字体必须写进「控件自身的样式表」里，不能只靠 setFont()。
    Qt 的样式表字体属性优先级高于 QWidget::setFont()，而本项目在
    ui/win11_theme.py 里有一条全局 `QWidget { font-family: "Segoe UI Variable", ... }`，
    会把 setFont() 设的图标字体整个覆盖掉 —— Win11 上系统自带 Segoe Fluent Icons
    还能靠字体回退兜住，Win10 上没有该字体就直接变豆腐块。
    控件自身的样式表优先级高于祖先的样式表，所以在这里写 font-family 才稳。
    """
    parts = [
        'font-family:"%s";' % ensure_icon_font(),
        "font-size:%dpx;" % int(size),
        "font-weight:400;",
        "background:transparent;",
    ]
    if color is not None:
        parts.append("color:%s;" % color)
    return "".join(parts)


def icon_label(name, size=16, color=None, parent=None):
    """生成一个带官方图标的 QLabel。

    name   : GLYPHS 中的图标名
    size   : 像素字号（图标随字号缩放）
    color  : 可选颜色（CSS 颜色字符串），默认跟随父级
    """
    lb = QLabel(char(name), parent)
    f = QFont(ensure_icon_font())
    f.setPixelSize(size)
    # 图标字形不希望被抗锯齿之外的文本 hint 影响；关闭粗体避免变形
    f.setWeight(QFont.Weight.Normal)
    lb.setFont(f)
    lb.setAlignment(Qt.AlignCenter)
    # 记住字号，recolor 时要连字体一起重写（否则会被全局 QSS 抢回去）
    lb.setProperty("_icon_px", int(size))
    lb.setStyleSheet(_icon_qss(size, color))
    return lb


def recolor(label, color):
    """重设已有图标 QLabel 的颜色（用于主题切换 / 选中态）。

    注意必须重写完整样式（含 font-family），只写 color 会把图标字体丢掉。
    """
    if label is None:
        return
    size = label.property("_icon_px") or 16
    label.setStyleSheet(_icon_qss(size, color))
