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

from core.theme_presets import ALL_COLOR_KEYS


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


#: 全局当前主题名（light/dark）。给只需要明暗的地方用。
_current_theme_name = "light"

#: 全局当前**完整主题对象**。
#:
#: ## 为什么需要这个

#: `FluentComboBox` 的下拉列表是**独立顶层窗口**，它拿不到父窗口的 config，
#: 只能读全局状态。原来只有 `_current_theme_name`（一个明暗字符串），
#: 于是它调 `Theme(名字)` —— 那只会得到 **classic 预设**的配色。

#: 结果：用户选了午夜蓝/暖阳，主窗口变了，**下拉列表还是经典灰**。

#: 所以除了名字，还要把完整色板存一份。`set_app_theme` 收到 Theme 对象时
#: 一并保存；只收到字符串时（旧调用点）清空缓存，让读取方退回按名字构造。
_current_theme_obj = None


def glass_menu_style(t):
    """托盘菜单的玻璃渐变 QSS。

    ## 为什么放在这里

    配色属于"主题定义"，应该和 `menu_style` 等同处一个文件。
    `ui/glass_menu.py` 只有控件行为，不该写颜色常量。

    ## 渐变方向

    `x1:0,y1:0 → x2:0,y2:1` 从上到下：顶部亮、底部暗，
    模拟光从上方打下——这就是"釉面玻璃"的观感。

    ## 为什么用不透明色

    QMenu 是独立顶层窗口，`WA_TranslucentBackground` + 自绘透明度
    在 offscreen / 某些 Windows 主题下会变成**全透明不可点**。
    这里全部用不透明色，只靠明暗层次营造质感，稳定可靠。
    """
    def _hex(color, fallback):
        try:
            if isinstance(color, str) and color.startswith("#") and len(color) >= 7:
                return color[:7]
        except Exception:
            pass
        return fallback

    dark = bool(getattr(t, "dark", False))
    accent = _hex(getattr(t, "accent", None), "#0078d4")
    text = _hex(getattr(t, "text", None),
                "#f3f3f3" if dark else "#1a1a1a")

    if dark:
        top, bottom = "#2b2b30", "#1a1a1d"
        border, hover = "#3d3d44", "#3a3a44"
        separator = "#45454d"
    else:
        top, bottom = "#fafafa", "#ececec"
        border, hover = "#d5d5d5", "#e4e4e4"
        separator = "#d0d0d0"

    return f"""
        QMenu {{
            background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                stop:0 {top}, stop:1 {bottom});
            border: 1px solid {border};
            border-radius: 8px;
            padding: 6px;
            color: {text};
            font-size: 13px;
        }}
        QMenu::item {{
            background: transparent;
            padding: 8px 22px 8px 14px;
            border-radius: 5px;
            color: {text};
        }}
        QMenu::item:selected {{
            background: {accent};
            color: #ffffff;
        }}
        QMenu::item:hover:!selected {{
            background: {hover};
        }}
        QMenu::item:disabled {{
            color: #888888;
        }}
        QMenu::separator {{
            height: 1px;
            background: {separator};
            margin: 4px 12px;
        }}
    """


def _glass_controls_css(t):
    """材质开启时给按钮/输入框追加的"玻璃控件"规则。

    ## 为什么需要

    卡片已经通过 `MaterialCard` 自绘拿到了玻璃质感（背后真实背景 +
    釉面高光），但按钮和输入框还是普通 QSS 的实色底。用户反馈
    "背景很高级了但这些控件的感觉还是很廉价"——对比之下更明显。

    ## 能做什么、不能做什么

    QSS **画不出**釉面高光渐变（实测 `qlineargradient` 在 QPushButton
    上被 Qt 的样式引擎忽略）。所以这里只能做两件确实有效的事：

    1. **把底色调透**：浅色主题的 `input_bg` 是 85% 白，几乎不透；
       降到 40% 左右就能透出背后的背景图和卡片
    2. **边框改成浅色描边**：像贴在玻璃上的高光边，而不是"实色块的
       深色轮廓"——这是廉价感的主要来源

    深色主题下反向处理：底色略微提亮、边框用更亮的白。
    """
    if getattr(t, "dark", False):
        fill = "rgba(255,255,255,0.10)"
        fill_hover = "rgba(255,255,255,0.16)"
        fill_press = "rgba(255,255,255,0.06)"
        edge = "rgba(255,255,255,0.22)"
    else:
        fill = "rgba(255,255,255,0.42)"
        fill_hover = "rgba(255,255,255,0.62)"
        fill_press = "rgba(255,255,255,0.30)"
        edge = "rgba(255,255,255,0.85)"

    return f"""
        /* ===== 玻璃控件（材质开启时启用）=====
           QSS 无法画釉面高光渐变，所以只做两件事：
           把底色调透 + 边框改成浅色高光边。 */
        /* 用 QWidget X 形式提高优先级，确保覆盖上面的基础规则 */
        QWidget QPushButton {{
            background: {fill};
            border: 1px solid {edge};
        }}
        QWidget QPushButton:hover {{
            background: {fill_hover};
        }}
        QWidget QPushButton:pressed {{
            background: {fill_press};
        }}
        QWidget QPushButton:disabled {{
            background: rgba(255,255,255,0.16);
            border: 1px solid {t.input_border};
        }}
        QWidget QLineEdit, QWidget QTextEdit, QWidget QPlainTextEdit,
        QWidget QSpinBox, QWidget QDoubleSpinBox {{
            background: {fill};
            border: 1px solid {edge};
        }}
        QWidget QLineEdit:focus, QWidget QTextEdit:focus,
        QWidget QPlainTextEdit:focus, QWidget QSpinBox:focus,
        QWidget QDoubleSpinBox:focus {{
            background: {fill_hover};
            border: 1px solid {t.accent};
        }}
        QWidget QComboBox {{
            background: {fill};
            border: 1px solid {edge};
        }}
        """


def set_app_theme(name_or_theme, theme_obj=None):
    """设置当前应用主题，供独立对话框/弹出层在无法取父级时读取。

    :param name_or_theme: 明暗名字符串，或一个 `Theme` 对象
    :param theme_obj: 显式传入的完整主题（`name_or_theme` 是字符串时用）

    传 Theme 对象时，`current_theme()` 能返回**完整色板**（含预设与强调色）；
    只传字符串时退回"按名字构造 classic"，行为与旧版一致。
    """
    global _current_theme_name, _current_theme_obj

    # 鸭子类型判断而不是 isinstance(Theme)：Theme 类定义在本函数之后，
    # 模块加载顺序上拿不到这个名字。
    if hasattr(name_or_theme, "style_sheet") and hasattr(name_or_theme, "bg"):
        _current_theme_name = getattr(name_or_theme, "name",
                                      _current_theme_name)
        _current_theme_obj = name_or_theme
        return
    _current_theme_name = resolve_theme_name(name_or_theme)
    _current_theme_obj = theme_obj


def current_theme_name():
    """返回当前应用主题名（light/dark）。"""
    return _current_theme_name


def current_theme():
    """返回当前应用的**完整主题对象**。

    ## 为什么独立弹层该用这个

    `FluentComboBox` 的下拉是独立顶层窗口，拿不到父窗口 config。
    原来它用 `Theme(current_theme_name())`——那只得到 classic 配色，
    用户选了午夜蓝后下拉列表还是经典灰。

    这里优先返回缓存的完整对象；没有缓存时才按名字构造（旧行为兜底）。
    """
    if _current_theme_obj is not None:
        return _current_theme_obj
    # Theme 类在本函数之后才定义，运行时才解析名字——这是安全的
    return globals()["Theme"](_current_theme_name)


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


def theme_mode_of(config):
    """从配置 dict 里取明暗模式名（兼容旧 theme 键），不解析 system。

    给那些只关心"用户选了什么"而不关心"当前实际是什么"的地方用
    （比如判断要不要启动 system 跟随定时器）。

    :return: "light" / "dark" / "system"
    """
    c = config or {}
    v = c.get("theme_mode") or c.get("theme", "light")
    v = str(v).lower()
    return v if v in ("light", "dark", "system") else "light"


def resolved_mode_of(config):
    """从配置 dict 里取**解析后**的明暗（system 已跟随系统）。

    绝大部分 UI 代码该用这个：它保证拿到的永远是 light 或 dark。
    """
    return resolve_theme_name(theme_mode_of(config))


class Theme:
    """当前主题色板。

    ## 两种构造方式（都支持）

        Theme("light")                     # 旧调用：明暗名，走 classic 预设
        Theme("dark", preset="midnight")  # 指定预设
        Theme(palette=full_dict)          # 直接给完整色板（系统强调色/自定义）

    ## 为什么改成数据驱动

    原来色板写死在 `if self.dark:` 分支里，加一套主题要改类、改设置页、
    改配置枚举、改测试。现在颜色来自 `core/theme_presets.py` +
    `data/themes.json`，`Theme` 只负责把它们挂成属性并渲染成 QSS。

    所有 `self.xxx` 颜色属性保持不变——10+ 个调用方不用动。
    """

    def __init__(self, name="light", preset=None, palette=None):
        # 解析明暗：name 可能是 "light"/"dark"/"system"
        resolved = resolve_theme_name(name)
        self.name = resolved
        self.dark = resolved == "dark"

        if palette is None:
            from core.theme_presets import resolve_palette

            palette = resolve_palette(preset or "classic", resolved)
        self._palette = palette
        self.preset = palette.get("preset", preset or "classic")
        self.preset_name = palette.get("name", self.preset)

        # 把色板里的每个颜色挂成同名属性（保持旧接口）
        for key in ALL_COLOR_KEYS:
            setattr(self, key, palette.get(key, "#808080"))
        # 语义状态色（警告/成功/失败/提示）：随明暗变化，不随配色方案变化
        from core.theme_presets import semantic

        for k, v in semantic(resolved == "dark").items():
            setattr(self, k, v)
        # dark 以传入的明暗为准（palette 里可能带，但 name 优先级更高）
        self.dark = resolved == "dark"

    def hex(self, key):
        return getattr(self, key, "#808080")

    def copy_with(self, **overrides):
        """基于当前主题派生一个新主题（换强调色时不用重建整条链）。"""
        p = dict(self._palette)
        p.update(overrides)
        return Theme(self.name, palette=p)

    def style_sheet(self, glass=False):
        """返回应用到统一窗口根 widget 的全局 QSS。

        :param glass: 材质开启时传 True —— 追加一段玻璃控件规则，
                      把按钮/输入框的底色调透、边框改成浅色描边，
                      让它们透出背后的背景图，观感与卡片统一。
        """
        glass_css = _glass_controls_css(self) if glass else ""
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
            color: {t.on_accent};
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
        /* ---------- QSpinBox 的上下按钮 ----------

           ⚠ 必须显式给样式。全局 QSS 里只写了 `QSpinBox {{...}}` 时，
           **按钮和箭头仍走 Qt 原生绘制**——而原生箭头的颜色随控件状态
           变化（焦点/hover/禁用各不相同）。

           实测表现：设置页里「最大最近记录」的箭头是强调青色，
           而下面「每页候选数」的是灰色，两个同类型控件看起来不一致，
           像是样式坏了。

           这里把按钮区画成与输入框同底的透明块，箭头用主题次要文字色，
           并用一个内联 SVG 三角形保证任何 DPI 下都清晰。
           `subcontrol-position` 显式靠右，避免与 padding 冲突。 */
        QSpinBox {{
            padding-right: 22px;   /* 给按钮区留位置，别让数字压到箭头下 */
        }}
        QSpinBox::up-button {{
            subcontrol-origin: border;
            subcontrol-position: top right;
            width: 20px;
            border: none;
            border-top-right-radius: {RADIUS_CONTROL}px;
            background: transparent;
        }}
        QSpinBox::down-button {{
            subcontrol-origin: border;
            subcontrol-position: bottom right;
            width: 20px;
            border: none;
            border-bottom-right-radius: {RADIUS_CONTROL}px;
            background: transparent;
        }}
        QSpinBox::up-button:hover, QSpinBox::down-button:hover {{
            background: {t.nav_hover};
        }}
        QSpinBox::up-button:pressed, QSpinBox::down-button:pressed {{
            background: {t.nav_selected};
        }}
        /* ⚠ 箭头用 border 画三角形，**不要改成内联 SVG**。
           实测 `image: url("data:image/svg+xml;utf8,...")` 在 Qt 6.11 上
           不渲染——QImageReader 明明报告支持 svg，但 QSS 的 `image`
           属性就是不认 data URL，箭头直接消失。
           若将来要换成图标，请用磁盘上的 .svg 文件路径，不要用 data URL。 */
        QSpinBox::up-arrow {{
            image: none;
            width: 0px;
            height: 0px;
            border-left: 4px solid transparent;
            border-right: 4px solid transparent;
            border-bottom: 5px solid {t.text_secondary};
        }}
        QSpinBox::down-arrow {{
            image: none;
            width: 0px;
            height: 0px;
            border-left: 4px solid transparent;
            border-right: 4px solid transparent;
            border-top: 5px solid {t.text_secondary};
        }}
        QSpinBox::up-arrow:hover {{
            border-bottom-color: {t.text};
        }}
        QSpinBox::down-arrow:hover {{
            border-top-color: {t.text};
        }}
        QSpinBox:disabled {{
            color: {t.text_tertiary};
        }}
        QSpinBox::up-arrow:disabled {{
            border-bottom-color: {t.text_tertiary};
        }}
        QSpinBox::down-arrow:disabled {{
            border-top-color: {t.text_tertiary};
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
        /* ⚠ 箭头**不在这里画**。
           `QComboBox::down-arrow {{ border-*: ... }}` 拼三角形在 Qt 6 的
           Windows 样式下会渲染成一个实心小方块（实测放大看就是 `▪`）。
           真正的箭头由 `FluentComboBox.paintEvent` 用 QPainter 绘制。
           这里只把原生箭头关掉、留出位置。 */
        QComboBox::down-arrow {{
            image: none;
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
            selection-color: {t.on_accent};
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
            color: {t.on_accent};
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
            color: {t.on_accent};
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
        /* 调色板对话框：QColorDialog 是系统级对话框，主体配色由系统决定，
           但标题栏与按钮文字要跟随主题，否则深色下白底白字。 */
        QColorDialog {{
            background: {t.bg};
        }}
        QColorDialog QLabel {{
            color: {t.text};
            background: transparent;
        }}
        {glass_css}
        """


    def menu_style(self, glass=False):
        """QMenu 专属样式表（菜单项/分隔符），供托盘菜单等独立弹出菜单显式 setStyleSheet。

        :param glass: 材质开启时传 True —— 菜单背景设为**透明**，
                      让 ui/glass_menu.GlassMenu 自绘的玻璃底露出来。
                      否则 QSS 的纯色背景会把自绘层整个盖住。

        独立 QMenu（无父 widget）不一定继承 app 全局 QSS，因此由调用方显式应用并随主题刷新。
        """
        t = self
        bg = "transparent" if glass else t.card
        border = "transparent" if glass else t.card_border
        return f"""
        QMenu {{
            background: {bg};
            border: 1px solid {border};
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
            color: {t.on_accent};
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
