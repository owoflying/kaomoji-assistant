"""Win11 Settings 风格统一配色与样式工具。

提供浅色/深色主题令牌、圆角卡片/按钮/输入框的 QSS、以及常用动画辅助。
所有颜色尽量贴近 Windows 11 设置应用的 Mica/Acrylic 观感。

## 设计令牌（对齐 WinUI 3 规范）

之前的问题是**圆角、高度、间距各自为政**：圆角有 6/8/10/12 四种，
控件高度有 30/40 两种，看起来"不像 WinUI3"——其实颜色是对的，
乱的是几何。

WinUI 3 的圆角是有层级的，不是随便取：
    小控件（按钮/输入框/开关）  4px
    容器（卡片/对话框/弹出层）  8px
    窗口                     8px

高度也是统一的：标准控件 32px、紧凑 24px、大 40px。
这三档覆盖了几乎所有场景。

所以这里把数值集中成令牌，QSS 里一律引用，不再写裸数字。
要整体调外观时改这里一处即可。
"""
from PySide6.QtGui import QFont


# ====================================================================
# 设计令牌（单位 px，遵循 WinUI 3 规范）
# ====================================================================

#: 圆角：按控件层级分三档（WinUI 3 的 CornerRadius 语义）
RADIUS_CONTROL = 4      # 按钮、输入框、下拉框、开关等小控件
RADIUS_CARD = 8         # 卡片、对话框、弹出层等容器
RADIUS_WINDOW = 8       # 主窗口

#: 控件高度三档（WinUI 3 的 ControlSize 语义）
HEIGHT_COMPACT = 24
HEIGHT_STANDARD = 32
HEIGHT_LARGE = 40

#: 间距标尺（8px 网格，WinUI 3 用 4/8/12/16/24 这几档）
SPACE_XS = 4
SPACE_SM = 8
SPACE_MD = 12
SPACE_LG = 16
SPACE_XL = 24

#: 字号
FONT_CAPTION = 12
FONT_BODY = 14
FONT_SUBTITLE = 20
FONT_TITLE = 28


# 全局当前主题名（light/dark）。部分独立窗口/弹出层无法从父级便捷取到主题，
# 由 main.py 在启动及 apply_settings 时同步，供 FluentComboBox 等控件统一读取。
_current_theme_name = "light"


def set_app_theme(name):
    """设置当前应用主题名，供独立对话框/弹出层在无法遍历父窗口时读取。"""
    global _current_theme_name
    effective = resolve_theme_name(name)
    _current_theme_name = effective


def current_theme_name():
    """返回当前应用主题名（light/dark）。"""
    return _current_theme_name


def system_theme_name():
    r"""读取 Windows 系统当前主题：返回 light 或 dark。

    通过注册表 HKEY_CURRENT_USER\...\Themes\Personalize\AppsUseLightTheme 判断：
    1=浅色，0=深色。读取失败时默认返回 light。
    """
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        )
        value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
        winreg.CloseKey(key)
        return "light" if value else "dark"
    except Exception:
        return "light"


def resolve_theme_name(name):
    """把配置中的主题名解析为实际生效的 light/dark（system 会跟随系统）。"""
    n = str(name).lower()
    if n == "system":
        return system_theme_name()
    return "dark" if n == "dark" else "light"


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
            self.text_secondary = "#c5c5c5"
            self.text_tertiary = "#8b8b8b"
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
            self.text_secondary = "#5c5c5c"
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
            border-radius: {RADIUS_CARD}px;
        }}
        QFrame#CardHover:hover {{
            background: {t.card_hover};
        }}
        /* ToggleSwitch 完全自绘，禁用原生按钮在 hover/focus 时的黑色背景条 */
        QAbstractButton#ToggleSwitch, QAbstractButton#ToggleSwitch:hover,
        QAbstractButton#ToggleSwitch:focus {{
            background: transparent;
            border: none;
            outline: none;
        }}
        /* 设置项行容器必须透明，避免子控件 hover/focus 时露出原生黑色背景 */
        QWidget#SettingsRow {{
            background: transparent;
            border: none;
        }}
        QPushButton {{
            background: {t.input_bg};
            border: 1px solid {t.input_border};
            border-radius: {RADIUS_CONTROL}px;
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
            border-radius: {RADIUS_CONTROL}px;
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
        /* ⚠ 文本框必须显式给样式。
           全局 QSS 一旦设置，Qt 对**没匹配到规则**的控件会退回系统默认
           绘制——而系统默认是浅色。深色主题下就变成"白底浅字"，
           内容几乎看不见（实测：「使用统计」的排行框、「剪贴板提取」
           的列表都踩过）。

           这类"遗漏样式"比写错颜色更隐蔽：界面上大部分地方正常，
           只有某个对话框一打开才发现有问题。 */
        QPlainTextEdit, QTextEdit {{
            background: {t.card};
            color: {t.text};
            border: 1px solid {t.card_border};
            border-radius: {RADIUS_CARD}px;
            padding: 8px 10px;
            selection-background-color: {t.accent};
            selection-color: {t.bg};
        }}
        QPlainTextEdit:focus, QTextEdit:focus {{
            border: 1px solid {t.accent};
        }}
        /* 选项卡：WinUI3 的 Pivot 是"选中项下方一条短横线"，
           不是传统 Tab 的方块。这里按 WinUI3 的观感做。 */
        QTabWidget::pane {{
            border: none;
            background: transparent;
            top: -1px;
        }}
        QTabBar {{
            background: transparent;
        }}
        QTabBar::tab {{
            background: transparent;
            color: {t.text_secondary};
            padding: 8px 14px;
            margin-right: 4px;
            border: none;
            border-bottom: 2px solid transparent;
            font-size: 13px;
        }}
        QTabBar::tab:hover {{
            color: {t.text};
            background: {t.nav_hover};
            border-top-left-radius: 6px;
            border-top-right-radius: 6px;
        }}
        QTabBar::tab:selected {{
            color: {t.text};
            border-bottom: 2px solid {t.accent};
            font-weight: 600;
        }}
        QLineEdit, QComboBox, QSpinBox {{
            background: {t.input_bg};
            border: 1px solid {t.input_border};
            border-radius: {RADIUS_CONTROL}px;            padding: 6px 10px;
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
            border-radius: {RADIUS_CONTROL}px;
            margin: 4px 6px;
            padding: 10px 12px;
            min-height: {HEIGHT_STANDARD}px;
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
            border-radius: {RADIUS_CONTROL}px;
            padding: 4px 10px;
            font-size: 13px;
            color: {t.text};
            min-height: {HEIGHT_COMPACT}px;
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
            background: transparent;  /* 必须显式透明：否则 Win10 会回退原生下拉按钮（右侧黑条） */
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
            border-radius: {RADIUS_CARD}px;
            padding: 6px;
            outline: none;
            selection-background-color: {t.accent};
            selection-color: #ffffff;
        }}
        QComboBox QAbstractItemView::item {{
            background-color: transparent;
            color: {t.text};
            border-radius: {RADIUS_CONTROL}px;
            padding: 6px 10px;
            min-height: {HEIGHT_COMPACT}px;
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
            border-radius: {RADIUS_CARD}px;
            padding: 6px;
            color: {t.text};
            font-size: 13px;
        }}
        QMenu::item {{
            background: transparent;
            padding: 7px 18px 7px 14px;
            border-radius: {RADIUS_CONTROL}px;
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
        /* ---------- 兜底：通用前景/背景 ----------

           全局 QSS 是「匹配式」的：**没匹配到规则**的控件会用系统默认
           绘制，而系统默认是浅色。深色主题下就是"白底浅字"。

           这类问题特别隐蔽——界面上大部分地方正常，只有某个对话框
           打开才发现。本项目实际踩过两次：使用统计的排行框、
           剪贴板提取的列表，都是因为缺 QPlainTextEdit / QCheckBox
           的规则。

           加一组最通用的兜底，让"完全没写过样式"的控件至少配色正确；
           配合 `tests/test_theme_coverage.py` 的覆盖检查，双保险。 */
        QWidget {{
            color: {t.text};
        }}
        QWidget:disabled {{
            color: {t.text_tertiary};
        }}
        QRadioButton {{
            color: {t.text};
            background: transparent;
        }}
        QToolTip {{
            background: {t.card};
            color: {t.text};
            border: 1px solid {t.card_border};
            padding: 4px 8px;
        }}
        /* 消息框：QMessageBox 是独立顶层窗口，主窗口的 QSS 不会自动生效。
           不给它样式的话，深色主题下弹出来的框是白底浅字。 */
        QMessageBox {{
            background: {t.bg};
        }}
        QMessageBox QLabel {{
            color: {t.text};
            background: transparent;
        }}
        /* 对话框按钮组与堆叠容器：透明即可，让内部控件自己上色 */
        QDialogButtonBox {{
            background: transparent;
        }}
        QStackedWidget {{
            background: transparent;
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
            border-radius: {RADIUS_CARD}px;
            padding: 6px;
            color: {t.text};
            font-size: 13px;
        }}
        QMenu::item {{
            background: transparent;
            padding: 7px 18px 7px 14px;
            border-radius: {RADIUS_CONTROL}px;
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


def child_theme_qss(theme, own_stylesheet=""):
    """给外部（插件）提供的子控件拼出「完整主题 QSS + 自身规则」的样式表。

    Qt 行为：带自身 setStyleSheet 的控件，其派生控件不再继承更上层祖先的样式表。
    因此直接 `child.setStyleSheet("background:red")` 会让 child 内部的按钮/滑块/
    开关全部掉回原生样式（本项目已踩三次）。这里把完整主题 QSS 垫在前面、
    插件自己的规则追加在后，保证级联不断且自定义仍生效。
    """
    base = theme.style_sheet()
    own = (own_stylesheet or "").strip()
    return base + ("\n" + own if own else "")


def apply_child_theme(widget, theme):
    """把 widget 现有的自定义样式重排到「完整主题 QSS 之后」，保住级联。

    用于插件产出的 QWidget（设置面板 / 独立页 / 弹窗内容）：无论插件是否给自身
    设过样式，都能拿到完整主题规则。

    幂等：插件自己的那部分样式只会在首次调用时被记到 `_own_qss` 属性里，
    之后每次主题切换都基于「新的完整 QSS + 原始自定义」重建，
    避免把上一次垫进去的 base 反复叠加导致样式表越来越长。
    """
    if widget is None:
        return
    try:
        own = widget.property("_own_qss")
        if not isinstance(own, str):
            own = widget.styleSheet() or ""
            widget.setProperty("_own_qss", own)
        widget.setStyleSheet(child_theme_qss(theme, own))
    except RuntimeError:
        pass  # C++ 对象已销毁


def parse_color(spec):
    """把主题里的 rgba()/hex 颜色字符串安全地转成 QColor。

    Qt6 的 QColor 不接受「rgba(r,g,b,浮点a)」（alpha 必须 0-255 整数），
    直接 QColor(spec) 会静默得到黑色 —— 必须走这里的正则兜底
    （alpha <= 1 时按比例映射到 0-255）。
    """
    from PySide6.QtGui import QColor
    import re as _re
    c = QColor(spec)
    if c.isValid():
        return c
    m = _re.match(r"rgba?\((\d+),\s*(\d+),\s*(\d+)(?:,\s*([\d.]+))?\)", str(spec))
    if m:
        r, g, b = int(m.group(1)), int(m.group(2)), int(m.group(3))
        a = float(m.group(4)) if m.group(4) else 1.0
        return QColor(r, g, b, int(a * 255) if a <= 1.0 else int(a))
    return QColor(0, 0, 0)
