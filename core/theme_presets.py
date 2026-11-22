"""主题色板的数据层：颜色算法、预设定义、合法性校验。

## 为什么单独一层

原来色板写死在 `ui/win11_theme.py` 的 `Theme.__init__` 里，一个
巨大的 `if self.dark:` 分支装两套颜色。加一套主题就要改类、改设置页、
改配置枚举、改测试——主题系统没法"完整"起来。

这一层把**颜色从代码变成数据**：

    data/themes.json   内置预设（可增删，不用改 Python）
    本文件             颜色算法 + 派生 + 校验 + 加载
    ui/win11_theme.py  只负责把色板渲染成 QSS

## 核心色 vs 派生色

用户/预设只需提供 8 个**核心色**，其余 16 个自动推导。
理由：让人填 24 个颜色不现实，而且手填容易配出不协调的组合
（比如 accent 改了但 accent_bg 忘了改）。

    CORE_KEYS     必须提供（预设文件里每套都要有）
    DERIVED_KEYS  自动算（lighten / mix / alpha / 明暗黑白半透明）

## 明暗与配色是正交的

每套预设同时提供 `light` 和 `dark` 两份核心色板。
`theme_mode`（light/dark/system）决定取哪一份，`theme_preset` 决定风格。
这样"午夜蓝·深色"和"暖阳·浅色"可以自由组合，不会出现
"换到深色结果预设变成暖色"这种错乱。
"""
import copy
import json
import os
import re

#: 语义状态色（与配色方案无关，但**要随明暗调整**）。
#:
#: ## 为什么单独一组
#:
#: 警告橙 `#a06000` 在浅色背景上清晰，在深色背景上几乎看不见；
#: 反过来 `#ffb86b` 在深色上好看、在白色上发虚。
#: 所以语义色不能用同一组值，必须按明暗各给一套。
#:
#: 它们也不属于"配色方案"——换午夜蓝/暖阳时，错误照样该是红的。
SEMANTIC_COLORS = {
    "light": {
        "warning": "#a06000",   # 警告：需要用户注意但不阻塞
        "success": "#107c10",   # 成功
        "danger": "#c0392b",    # 失败/错误
        "info": "#0067c0",      # 中性提示
    },
    "dark": {
        "warning": "#ffb86b",
        "success": "#6ccb5f",
        "danger": "#ff8a80",
        "info": "#60cdff",
    },
}


def semantic(dark):
    """取当前明暗下的语义状态色 dict。"""
    return dict(SEMANTIC_COLORS.get("dark" if dark else "light", {}))


#: 用户/预设必须提供的核心色。
#:
#: 挑选原则：改了它，整套主题观感会明显变化，且无法从其它色可靠推出。
CORE_KEYS = (
    "bg",              # 内容区底色
    "card",            # 卡片/输入框背景
    "card_border",     # 卡片边框
    "text",            # 主文字
    "text_secondary",  # 次要文字
    "accent",          # 强调色（按钮/选中/链接）
    "window_tint",     # 无边框窗口的亚克力基底（rgba）
    "content_surface", # 内容区表面（比侧栏略实，rgba）
)

#: 由核心色自动推导的字段（Theme 上仍然能直接取到）。
DERIVED_KEYS = (
    "sidebar", "card_hover", "text_tertiary",
    "accent_hover", "accent_bg", "on_accent",
    "nav_hover", "nav_selected", "input_bg", "input_border",
    "divider", "shadow", "toggle_off",
    "window_grad_top", "window_grad_bottom", "window_border",
    "shadow_color",
)

#: Theme 暴露的全部颜色字段（style_sheet() 里会引用）
ALL_COLOR_KEYS = CORE_KEYS + DERIVED_KEYS


# ====================================================================
# 颜色算法
#
# 只处理 Qt 能吃的两种格式：#RRGGBB 与 rgba(r,g,b,a)。
# 不引入第三方库——项目零依赖是刻意保持的约束。
# ====================================================================

_HEX_RE = re.compile(r"^#([0-9a-fA-F]{6})$")
_RGBA_RE = re.compile(
    r"^rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*"
    r"(?:,\s*([\d.]+)\s*)?\)$")


def parse_color(spec):
    """把颜色字符串解析成 (r, g, b, a)，a 为 0~1。

    解析不了返回 None（调用方负责用默认值兜底），绝不抛异常——
    主题文件是数据，用户可能手改坏。
    """
    s = str(spec or "").strip()
    m = _HEX_RE.match(s)
    if m:
        v = m.group(1)
        return (int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16), 1.0)
    m = _RGBA_RE.match(s)
    if m:
        r, g, b = int(m.group(1)), int(m.group(2)), int(m.group(3))
        a = float(m.group(4)) if m.group(4) is not None else 1.0
        return (min(255, r), min(255, g), min(255, b), max(0.0, min(1.0, a)))
    return None


def to_hex(rgb):
    """(r,g,b[,a]) -> #RRGGBB。"""
    r, g, b = (int(max(0, min(255, v))) for v in rgb[:3])
    return "#%02x%02x%02x" % (r, g, b)


def to_rgba(rgb, alpha=None):
    """(r,g,b[,a]) -> 'rgba(r,g,b,a)' 字符串。"""
    r, g, b = (int(max(0, min(255, v))) for v in rgb[:3])
    a = rgb[3] if alpha is None and len(rgb) > 3 else alpha
    if a is None:
        a = 1.0
    a = max(0.0, min(1.0, float(a)))
    # 保留两位小数就够，避免 QSS 里出现一长串浮点
    return "rgba(%d,%d,%d,%s)" % (r, g, b, ("%.3f" % a).rstrip("0").rstrip(".") or "0")


def mix(c1, c2, t):
    """按 t(0~1) 混合两个颜色；t=0 取 c1，t=1 取 c2。"""
    a = parse_color(c1) or (0, 0, 0, 1.0)
    b = parse_color(c2) or (0, 0, 0, 1.0)
    t = max(0.0, min(1.0, float(t)))
    r = a[0] + (b[0] - a[0]) * t
    g = a[1] + (b[1] - a[1]) * t
    bl = a[2] + (b[2] - a[2]) * t
    return (round(r), round(g), round(bl), 1.0)


def lighten(color, amount):
    """向白色靠近 amount(0~1)。"""
    return mix(color, "#ffffff", amount)


def darken(color, amount):
    """向黑色靠近 amount(0~1)。"""
    return mix(color, "#000000", amount)


def with_alpha(color, alpha):
    """给颜色套一个 alpha，输出 rgba 字符串。"""
    c = parse_color(color) or (0, 0, 0, 1.0)
    return to_rgba((c[0], c[1], c[2]), alpha)


def luminance(color):
    """相对亮度（0~1），用于判断某个底色是深还是浅。"""
    c = parse_color(color) or (0, 0, 0, 1.0)
    return (0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]) / 255.0


# ====================================================================
# 派生色
# ====================================================================

def derive(core, dark):
    """由核心色算出完整色板。

    :param core: dict，至少包含 CORE_KEYS 的键
    :param dark: 是否深色（决定半透明叠加用白还是黑）
    :return: 包含 ALL_COLOR_KEYS 的 dict

    ## 原则

    * **hover / 边框**这类"轻微变化"用 mix 到黑或白，而不是硬编码——
      这样换任何底色都自然，不会在暖色主题里冒出一块冷灰。
    * **nav / divider / shadow** 用黑/白半透明叠加（WinUI3 的
      "分层"做法），深浅由 dark 决定。
    * 高对比度预设会把 `contrast` 标成 True，此时禁用所有半透明，
      改用实色（半透明在低视力场景下等于看不见）。
    """
    out = {}
    for k in CORE_KEYS:
        out[k] = core.get(k) or _fallback_core(dark).get(k, "#808080")

    contrast = bool(core.get("_contrast"))
    bg, card, text = out["bg"], out["card"], out["text"]
    accent = out["accent"]

    # ---- 由底色推 hover / 三级文字 ----
    hover_target = "#ffffff" if dark else "#000000"
    out["card_hover"] = to_hex(mix(card, hover_target, 0.05))
    out["text_tertiary"] = to_hex(mix(out["text_secondary"], card, 0.42))

    # ---- 强调色派生 ----
    out["accent_hover"] = to_hex(lighten(accent, 0.12) if dark
                                 else darken(accent, 0.10))
    out["accent_bg"] = (accent if contrast
                        else with_alpha(accent, 0.16 if dark else 0.10))
    # 「强调色上的文字色」：选中项/主按钮的文字。
    #
    # ⚠ 不能固定用白色：高对比度深色预设的强调色是 `#ffff00`（亮黄），
    # 白字在上面完全看不见。按强调色亮度自动选黑或白。
    out["on_accent"] = ("#000000" if luminance(accent) > 0.55
                        else "#ffffff")

    # ---- 中性叠加层（黑白半透明）----
    if contrast:
        # 高对比度：全部实色，保证边界清晰可见
        out["sidebar"] = bg
        out["nav_hover"] = to_hex(mix(bg, text, 0.14))
        out["nav_selected"] = to_hex(mix(bg, accent, 0.22))
        out["input_bg"] = bg
        out["input_border"] = text
        out["divider"] = text
        out["window_border"] = text
    else:
        tint = (255, 255, 255) if dark else (0, 0, 0)
        out["sidebar"] = "transparent"
        out["nav_hover"] = to_rgba(tint, 0.06 if dark else 0.04)
        out["nav_selected"] = to_rgba(tint, 0.08 if dark else 0.06)
        out["input_bg"] = to_rgba(tint, 0.06) if dark else "rgba(255,255,255,0.85)"
        out["input_border"] = to_rgba(tint, 0.12 if dark else 0.10)
        out["divider"] = to_rgba(tint, 0.08 if dark else 0.06)
        out["window_border"] = to_rgba(tint, 0.10 if dark else 0.08)

    # ---- 开关与窗口绘制 ----
    out["toggle_off"] = to_hex(mix(card, text, 0.30))
    out["shadow"] = "0,0,0"
    out["window_grad_top"] = (to_rgba((255, 255, 255), 0.04 if dark else 0.08))
    out["window_grad_bottom"] = (to_rgba((0, 0, 0), 0.10 if dark else 0.03))
    out["shadow_color"] = ("rgba(0,0,0,0.30)" if dark
                           else "rgba(0,0,0,0.12)")
    return out


def _fallback_core(dark):
    """核心色缺失时的兜底（保证 derive 永远不炸）。"""
    if dark:
        return {"bg": "#1a1a1a", "card": "#2a2a2a", "card_border": "#3d3d3d",
                "text": "#ffffff", "text_secondary": "#c5c5c5",
                "accent": "#60cdff", "window_tint": "rgba(30,30,30,0.85)",
                "content_surface": "rgba(40,40,40,0.60)"}
    return {"bg": "#ffffff", "card": "#ffffff", "card_border": "#e8e8e8",
            "text": "#1f1f1f", "text_secondary": "#5c5c5c",
            "accent": "#0067c0", "window_tint": "rgba(255,255,255,0.88)",
            "content_surface": "rgba(255,255,255,0.70)"}


def validate_palette(palette):
    """校验一份核心色板；返回 (是否合法, 错误信息列表)。

    不强制要求 8 个键全齐——缺的会用兜底值补，这样用户手工写
    一个只改了 accent 的主题文件也能用。但**颜色格式必须能解析**。
    """
    errors = []
    if not isinstance(palette, dict):
        return False, ["色板必须是对象（dict）"]
    for k, v in palette.items():
        if k.startswith("_"):
            continue
        if k not in CORE_KEYS:
            continue        # 忽略未知键（向前兼容）
        if parse_color(v) is None:
            errors.append("%s 的颜色值无法解析：%r" % (k, v))
    return (len(errors) == 0), errors


# ====================================================================
# 预设文件
# ====================================================================

def presets_path():
    """内置预设文件路径。"""
    try:
        from core.runtime import resource_path
        return resource_path(os.path.join("data", "themes.json"))
    except Exception:
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        return os.path.join(here, "data", "themes.json")


def user_theme_path():
    """用户自定义主题文件（可写，放在 APPDATA 而非包内）。"""
    try:
        from core.runtime import app_data_dir
        return os.path.join(app_data_dir(), "user_theme.json")
    except Exception:
        return os.path.join(os.path.expanduser("~"), "kaomoji_user_theme.json")


def load_presets(path=None):
    """加载内置预设；文件损坏时返回内置的最小兜底，绝不让程序起不来。

    :return: dict，{preset_id: {"name", "description", "light", "dark"}}
    """
    p = path or presets_path()
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        presets = data.get("presets") or {}
        if isinstance(presets, dict) and presets:
            return presets
    except Exception:
        pass
    return _minimal_presets()


def _minimal_presets():
    """兜底：只有经典一套，用代码里的默认值拼出来。"""
    return {
        "classic": {
            "name": "经典",
            "description": "Windows 11 风格（内置兜底）",
            "light": _fallback_core(False),
            "dark": _fallback_core(True),
        }
    }


def resolve_palette(preset_id, mode, presets=None, accent=None):
    """取某预设在某明暗下的**完整色板**（含派生色）。

    :param preset_id: 预设 id；不存在时回退到 classic
    :param mode: "light" / "dark"
    :param presets: 预设字典（不传则从文件加载）
    :param accent: 可选的强调色覆盖（系统强调色 / 用户自定义）
    :return: dict，包含 ALL_COLOR_KEYS，另有 name / preset / dark
    """
    ps = presets if presets is not None else load_presets()
    p = ps.get(preset_id) or ps.get("classic") or {}
    if not p:
        p = _minimal_presets()["classic"]

    dark = (mode == "dark")
    core = copy.deepcopy(p.get("dark" if dark else "light")
                         or _fallback_core(dark))
    # 内置预设可能显式标注了 contrast
    if p.get("contrast"):
        core["_contrast"] = True
    if accent and parse_color(accent):
        core["accent"] = accent

    full = derive(core, dark)
    full["dark"] = dark
    full["mode"] = mode
    full["preset"] = preset_id if preset_id in ps else "classic"
    full["name"] = p.get("name", preset_id)
    return full


def preset_choices(presets=None):
    """给设置页用：[(id, 显示名), ...]，按文件里的顺序。"""
    ps = presets if presets is not None else load_presets()
    return [(pid, (p or {}).get("name", pid)) for pid, p in ps.items()]


# ====================================================================
# 用户自定义主题
# ====================================================================

def load_user_theme():
    """读用户自定义主题；不存在/损坏返回 None。"""
    p = user_theme_path()
    if not os.path.isfile(p):
        return None
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return None
        return data
    except Exception:
        return None


def save_user_theme(data):
    """写用户自定义主题；返回 (成功, 消息)。"""
    p = user_theme_path()
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True, "已保存"
    except Exception as e:
        return False, "保存失败：%r" % (e,)


def delete_user_theme():
    try:
        p = user_theme_path()
        if os.path.isfile(p):
            os.remove(p)
        return True
    except Exception:
        return False
