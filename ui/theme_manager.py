"""主题解析入口：把配置（mode/preset/accent）解析成一个 Theme。

## 为什么需要这一层

主题配置重构后，一共有四个相关键：

    theme_mode, theme_preset, accent_mode, accent_custom

如果让每个窗口/对话框各自去读 config、各自判断 accent_mode、
各自调 resolve_palette，一定会出现"某个弹窗忘了跟系统强调色"
这种漏色问题——这正是之前 QSS 漏样式的同款毛病。

所以统一收敛到一个函数：

    build_theme(config) -> Theme

全项目只通过它拿主题。它负责：
  1. 解析明暗（system 时读系统）
  2. 选预设（找不到回退 classic）
  3. 决定强调色来源（preset / system / custom）
  4. 组装成 Theme

## system 模式

`mode=system` 时调 `system_theme_name()` 读注册表。
AppRuntime 的定时器检测到系统主题变化时，会重新调 build_theme
并广播，不需要每个模块自己监听。
"""
import os

from core.theme_presets import (
    load_presets, load_user_theme, parse_color, resolve_palette,
)


def system_accent_color():
    """读 Windows 系统强调色，返回 #RRGGBB；读不到返回 None。

    ## 注册表里是什么

    `HKCU\\Software\\Microsoft\\Windows\\DWM\\AccentColor` 存的是
    **ABGR**（不是 RGB！），形如 0xAABBGGRR。直接用会把红蓝弄反。

    另外系统给的这个色通常偏暗（用于标题栏），直接拿来当控件强调色
    在深色主题下对比度不够，所以要按当前明暗适当提亮。

    :return: #RRGGBB 或 None（非 Windows / 注册表读取失败）
    """
    if os.name != "nt":
        return None
    try:
        import winreg

        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\DWM")
        try:
            value, _ = winreg.QueryValueEx(key, "AccentColor")
        finally:
            winreg.CloseKey(key)
        # ABGR -> RGB
        abgr = int(value) & 0xFFFFFFFF
        b = (abgr >> 16) & 0xFF
        g = (abgr >> 8) & 0xFF
        r = abgr & 0xFF
        return "#%02x%02x%02x" % (r, g, b)
    except Exception:
        return None


def _adjust_accent_for_mode(color, dark):
    """把系统强调色调整到当前明暗下可用。

    深色主题下系统色往往太暗（比如 #0078d4 在 #1a1a1a 上还行，
    但 #003e92 就几乎看不见），提亮一点；
    浅色主题下太亮的色（#ffff00）会刺眼，压暗一点。

    用相对亮度做判据，而不是无脑提亮。
    """
    from core.theme_presets import luminance, lighten, darken, to_hex

    c = parse_color(color)
    if c is None:
        return None
    lum = luminance(color)
    if dark and lum < 0.45:
        return to_hex(lighten(color, 0.30))
    if not dark and lum > 0.80:
        return to_hex(darken(color, 0.30))
    return color


def effective_accent(config, dark):
    """按 accent_mode 决定最终强调色；返回 None 表示用预设自带的。

    :param config: 配置 dict
    :param dark: 当前是否深色（影响系统色的提亮/压暗）
    """
    mode = (config or {}).get("accent_mode", "preset")
    if mode == "custom":
        c = (config or {}).get("accent_custom")
        return c if parse_color(c) else None
    if mode == "system":
        c = system_accent_color()
        return _adjust_accent_for_mode(c, dark) if c else None
    return None


def build_theme(config=None, mode_override=None):
    """把配置解析成一个 `ui.win11_theme.Theme`。

    :param config: 配置 dict；None 时从 config_store 读
    :param mode_override: 强制明暗（system 跟随定时器用）
    :return: Theme 实例

    ## 为什么要 mode_override

    `theme_mode=system` 时，AppRuntime 的定时器已经算出了当前系统明暗。
    传进来避免重复读注册表，也保证"这一帧里所有窗口用的是同一个明暗"。
    """
    from ui.win11_theme import Theme, resolve_theme_name

    if config is None:
        from core.config_store import load_config
        config = load_config()
    config = config or {}

    # 1) 明暗
    if mode_override in ("light", "dark"):
        mode = mode_override
    else:
        name = config.get("theme_mode") or config.get("theme") or "light"
        mode = resolve_theme_name(name)
    dark = (mode == "dark")

    # 2) 强调色（先算好，预设路径和自定义主题路径都要用）
    accent = effective_accent(config, dark)

    # 3) 预设
    preset_id = config.get("theme_preset") or "classic"
    presets = load_presets()
    palette = None

    # 自定义主题：用用户的色板替换掉该预设的对应明暗
    if preset_id == "custom":
        user = load_user_theme()
        if user:
            from core.theme_presets import derive

            core = dict(user.get("dark" if dark else "light") or {})
            if user.get("contrast"):
                core["_contrast"] = True
            # 自定义主题也要服从 accent_mode（否则跟着系统的强调色
            # 在自定义主题里会失效，用户会以为开关坏了）
            if accent:
                core["accent"] = accent
            palette = derive(core, dark)
            palette["preset"] = "custom"
            palette["name"] = user.get("name", "自定义")
            palette["dark"] = dark
            palette["mode"] = mode
        else:
            preset_id = "classic"       # 用户主题文件没了，回退

    # 4) 预设路径在这里应用强调色
    if palette is None:
        palette = resolve_palette(preset_id, mode, presets, accent=accent)

    return Theme(mode, palette=palette)


def theme_signature(config=None, mode_override=None):
    """当前主题的"指纹"，用于判断是否需要重贴 QSS。

    app_runtime 用 `_applied_style` 做幂等短路，但那是比较 QSS 字符串。
    这里给一个更轻量的比较键，配置没变时连 QSS 都不用生成。
    """
    if config is None:
        from core.config_store import load_config
        config = load_config()
    config = config or {}
    return (
        config.get("theme_mode") or config.get("theme"),
        config.get("theme_preset"),
        config.get("accent_mode"),
        config.get("accent_custom"),
        mode_override,
    )
