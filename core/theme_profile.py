"""主题包：一套主配置，所有窗口自动沿用；也可单独覆盖某个窗口。

## 要解决的问题

之前每个窗口各存一份 `picker_*` 之类的配置项，加一个窗口就要加一批键，
最后变成：

    material_enabled / material_type / ...     主窗口
    picker_material_enabled / picker_...       候选条
    （日志窗口？没有）
    （插件弹窗？没有）

用户反馈的核心痛点就是这个——"一些独立窗口还是没有主题功能"、
"最好实现一个完整主题包功能，有一个主配置，其他界面自动沿用"。

## 设计

    ThemeProfile（主配置）
        记载"背景图 / 材质 / 透明度 / 主题模式"这一整套外观。

    每类窗口（surface）默认**完整沿用主配置**，
    只有在用户显式改了某个窗口的设置时才产生一条**覆盖**（overlay）。

    resolve(surface) -> 实际生效的参数
        主配置  ←  表面覆盖层  （后者只覆盖它写了的那几项）

## 表面（surface）

    "main"      主窗口
    "picker"    候选条
    "log"       日志窗口
    "plugin"    插件弹窗
    "dialog"    其它对话框（更新/欢迎/组合/提取）

新增窗口只要报一个 surface 名，**无需新增任何配置键**。

## 存储

    配置里两个键：
        "theme_profile":   { ...整套主配置... }
        "surface_overrides": { "picker": {只写差异项}, ... }

旧版本散落的 `material_*` / `picker_*` 键在迁移时并入主配置，
用户升级后不会丢设置。
"""
import copy

#: 各个表面（窗口类别）→ 显示名
SURFACES = {
    "main": "主窗口",
    "picker": "候选条",
    "log": "日志窗口",
    "plugin": "插件弹窗",
    "dialog": "其它对话框",
}

#: 主题包里包含的全部字段与默认值。
#:
#: 这些字段**所有表面共用同一套语义**，所以新窗口不需要定义自己的键。
DEFAULT_PROFILE = {
    "theme_mode": "light",          # light / dark / system
    "theme_preset": "classic",
    "accent_mode": "preset",
    "accent_custom": "#0067c0",
    "panel_alpha": 0.92,            # 整窗不透明度
    "material_enabled": False,      # 是否用背景图/材质
    "material_type": "none",        # none / blur / frosted / fog
    "material_strength": 55,
    "material_saturate": 18,        # ×10
    "material_brightness": 104,     # ×100
    "material_dim": 0,
    "image_opacity": 100,           # 背景图不透明度
    # 背景图裁切：(x, y, w, h) 相对比例；全 0 = 整图
    "crop_x": 0.0,
    "crop_y": 0.0,
    "crop_w": 0.0,
    "crop_h": 0.0,
}

#: 数值字段的范围（用于归一化）
_RANGES = {
    "panel_alpha": (0.0, 1.0),
    "material_strength": (0, 100),
    "material_saturate": (10, 30),
    "material_brightness": (80, 130),
    "material_dim": (0, 100),
    "image_opacity": (0, 100),
    "crop_x": (0.0, 1.0),
    "crop_y": (0.0, 1.0),
    "crop_w": (0.0, 1.0),
    "crop_h": (0.0, 1.0),
}

_MATERIAL_TYPES = ("none", "blur", "frosted", "fog")
_THEME_MODES = ("light", "dark", "system")
_ACCENT_MODES = ("preset", "system", "custom")


def _clamp(name, value, default):
    """把数值字段夹到合法范围；类型不对就用默认值。"""
    lo, hi = _RANGES[name]
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    if v != v:                       # NaN
        return default
    return max(lo, min(hi, v))


def normalize(profile):
    """把一份（可能不完整的）主题包补全并归一化。

    缺失字段用默认值补齐，非法值回退默认——这样调用方永远拿到
    一份字段齐全、取值合法的配置。
    """
    out = {}
    src = profile or {}
    for key, default in DEFAULT_PROFILE.items():
        v = src.get(key, default)
        if key in ("theme_mode",):
            out[key] = v if v in _THEME_MODES else default
        elif key == "accent_mode":
            out[key] = v if v in _ACCENT_MODES else default
        elif key == "material_type":
            out[key] = v if v in _MATERIAL_TYPES else default
        elif key in _RANGES:
            out[key] = _clamp(key, v, default)
        elif key in ("material_enabled",):
            out[key] = bool(v)
        else:
            out[key] = v if isinstance(v, str) else default
    return out


def migrate_legacy(cfg):
    """把旧版本散落的键并入 `theme_profile` / `surface_overrides`。

    ## 迁移规则

    * `material_*` / `panel_alpha` / `theme_*` / `accent_*`  → 主配置
    * `picker_*`                                            → picker 表面的覆盖层
    * `material_image_opacity`                              → image_opacity

    旧键**保留不删**（降级时还能读），但新代码只读主题包。
    """
    if not cfg:
        return cfg
    if "theme_profile" not in cfg:
        legacy = {}
        # 主配置：从旧键抄一份
        for old, new in (
                ("panel_alpha", "panel_alpha"),
                ("theme_mode", "theme_mode"),
                ("theme", "theme_mode"),
                ("theme_preset", "theme_preset"),
                ("accent_mode", "accent_mode"),
                ("accent_custom", "accent_custom"),
                ("material_enabled", "material_enabled"),
                ("material_type", "material_type"),
                ("material_strength", "material_strength"),
                ("material_saturate", "material_saturate"),
                ("material_brightness", "material_brightness"),
                ("material_dim", "material_dim"),
                ("material_image_opacity", "image_opacity")):
            if old in cfg and new not in legacy:
                legacy[new] = cfg[old]
        cfg["theme_profile"] = normalize(legacy)

    if "surface_overrides" not in cfg:
        ovr = {}
        # 候选条的旧 picker_* 键 → picker 覆盖层
        picker = {}
        for old, new in (
                ("picker_material_enabled", "material_enabled"),
                ("picker_material_type", "material_type"),
                ("picker_material_strength", "material_strength"),
                ("picker_image_opacity", "image_opacity"),
                ("picker_crop_x", "crop_x"),
                ("picker_crop_y", "crop_y"),
                ("picker_crop_w", "crop_w"),
                ("picker_crop_h", "crop_h")):
            if old in cfg:
                picker[new] = cfg[old]
        # `picker_use_own_image` 是"要不要独立外观"的开关：
        # 看过就等价于"picker 有覆盖层"，不看就是完整沿用主配置。
        if cfg.get("picker_use_own_image") or picker:
            ovr["picker"] = picker
        cfg["surface_overrides"] = ovr
    return cfg


def get_profile(cfg):
    """取主配置（已归一化）。"""
    migrate_legacy(cfg)
    return normalize(cfg.get("theme_profile"))


def set_profile(cfg, profile):
    """写回主配置。"""
    cfg["theme_profile"] = normalize(profile)
    return cfg


def get_overrides(cfg):
    """取所有表面的覆盖层。"""
    migrate_legacy(cfg)
    raw = cfg.get("surface_overrides") or {}
    return {k: dict(v) for k, v in raw.items() if isinstance(v, dict)}


def has_override(cfg, surface):
    """某个表面是否有独立配置。"""
    return surface in get_overrides(cfg)


def set_override(cfg, surface, patch):
    """给某个表面写覆盖项（只写差异）。

    :param patch: dict；传空 dict 表示"恢复沿用主配置"
    """
    migrate_legacy(cfg)
    ovr = cfg.setdefault("surface_overrides", {})
    if not patch:
        ovr.pop(surface, None)
    else:
        ovr.setdefault(surface, {}).update(patch)
    return cfg


def clear_override(cfg, surface):
    """恢复某个表面为"沿用主配置"。"""
    migrate_legacy(cfg)
    (cfg.get("surface_overrides") or {}).pop(surface, None)
    return cfg


def resolve(cfg, surface="main"):
    """算出某个表面**实际生效**的完整参数。

        主配置  ←  该表面的覆盖层

    :return: dict，字段与 DEFAULT_PROFILE 一致且已归一化
    """
    profile = get_profile(cfg)
    if surface == "main":
        # 主窗口永远用主配置本身
        return profile
    patch = get_overrides(cfg).get(surface) or {}
    if not patch:
        return profile
    merged = copy.copy(profile)
    merged.update({k: v for k, v in patch.items() if k in DEFAULT_PROFILE})
    return normalize(merged)


def effective_image_slot(cfg, surface):
    """某个表面该用哪张背景图。

    :return: 背景图命名空间名（见 ui/window_material.SLOT_*）

    规则：默认全部沿用主窗口的 `background`；
    只有 picker 显式设了独立图才用自己的 `picker_background`。
    """
    if surface == "picker" and has_override(cfg, "picker"):
        return "picker_background"
    return "background"
