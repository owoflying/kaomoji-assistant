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
    # 托盘右键菜单：独立 QMenu，不继承 app 全局 QSS，
    # 也经常需要单独调（比如主窗口用深色、菜单想保持浅色更好认）
    "menu": "托盘菜单",
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
    # ⚠ 判断"是否需要迁移"不能只看键是否存在。
    #
    # 实测踩到：用户的 config.json 里 **`"theme_profile": null` 这个键
    # 是存在的**（早期某次保存写进了空值），于是
    # `"theme_profile" not in cfg` 为 False，迁移被整个跳过。
    #
    # 后果非常隐蔽：主题包始终是 None，但旧平铺键里有真实值，
    # 于是各处 `prof.get(k) or cfg.get(k)` 的兜底各自为政，
    # 用户看到的现象就是"每个选项都像生效了，但组合起来不对劲，
    # 有的还不生效"——因为读取路径分裂成了两套。
    #
    # 现在把"空值"也算作未迁移。`normalize(None)` 得到默认值字典，
    # 但那种"全默认"的主题包同样意味着没迁移过，所以统一按
    # "不存在"处理。
    _existing = cfg.get("theme_profile")
    _needs_migrate = (
        "theme_profile" not in cfg
        or not isinstance(_existing, dict)
        or not _existing
    )
    if _needs_migrate:
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
        # 候选条的旧 picker_* 键 → picker 覆盖层。
        #
        # ⚠ 只有"值确实偏离默认"才算用户配过。
        #
        # 旧版本里这些键**总是存在**（出厂默认就写了 False/55/100/0），
        # 无脑迁移会给每个人凭空造一条 picker 覆盖层——用户什么都没改，
        # 界面却显示候选条"独立配置"，而它实际和主配置一模一样。
        picker = {}
        _old_defaults = {
            "picker_material_type": "none",
            "picker_material_strength": 55,
            "picker_image_opacity": 100,
            "picker_crop_x": 0.0,
            "picker_crop_y": 0.0,
            "picker_crop_w": 0.0,
            "picker_crop_h": 0.0,
        }
        for old, new in (
                ("picker_material_type", "material_type"),
                ("picker_material_strength", "material_strength"),
                ("picker_image_opacity", "image_opacity"),
                ("picker_crop_x", "crop_x"),
                ("picker_crop_y", "crop_y"),
                ("picker_crop_w", "crop_w"),
                ("picker_crop_h", "crop_h")):
            if old in cfg and cfg.get(old) != _old_defaults.get(old):
                picker[new] = cfg[old]
        # `picker_material_enabled` 同理：只有被设成 True 才说明配过。
        if cfg.get("picker_material_enabled"):
            picker["material_enabled"] = True
        # `picker_use_own_image` 是显式开关，True 就说明用户确实单独配过。
        if cfg.get("picker_use_own_image") or picker:
            ovr["picker"] = picker
        cfg["surface_overrides"] = ovr
    return cfg


def get_profile(cfg):
    """取主配置（已归一化）。

    ## 主题包是**唯一真相**

    这里曾经有一段"旧键优先于主题包"的特殊规则，只对
    `theme_mode` / `theme_preset` 生效——理由是外部代码可能直接写旧键。

    结果是**同一个配置包里两套规则并存**：这两个键按旧值读，其余
    材质参数按新值读。用户看到的现象就是"每个选项都像生效了，
    但组合起来不对劲，有的还不生效"。

    现在去掉那段特例。统一规则：

        1. `migrate_legacy()` 先把旧键并入主题包（只做一次，之后
           主题包已存在就跳过）
        2. 之后一律以主题包为准

    外部代码要改外观，应该走 `set_profile()`（它会同步写旧键），
    而不是直接改平铺键。
    """
    migrate_legacy(cfg)
    prof = normalize(cfg.get("theme_profile"))
    # 迁移后主题包就是唯一真相；把归一化结果写回，避免每次重算
    cfg["theme_profile"] = prof
    return prof


def set_profile(cfg, profile):
    """写回主配置，并**同步写旧键**。

    ## 为什么要同步写旧键

    旧键（`theme_mode` / `theme_preset` / `material_*`）还有别的代码在读
    （降级兼容、插件、既有测试）。只写主题包会让两边**长期不一致**：
    用户在界面里改成午夜蓝，读旧键的代码仍看到 classic —— 表现为
    "改了没生效"。

    同步写之后，无论读新的还是读旧的，拿到的都是同一个真相。
    """
    p = normalize(profile)
    cfg["theme_profile"] = p
    cfg["theme_mode"] = p["theme_mode"]
    cfg["theme"] = p["theme_mode"]
    cfg["theme_preset"] = p["theme_preset"]
    cfg["material_enabled"] = p["material_enabled"]
    cfg["material_type"] = p["material_type"]
    cfg["material_strength"] = p["material_strength"]
    cfg["material_saturate"] = p["material_saturate"]
    cfg["material_brightness"] = p["material_brightness"]
    cfg["material_dim"] = p["material_dim"]
    cfg["material_image_opacity"] = p["image_opacity"]
    cfg["panel_alpha"] = p["panel_alpha"]
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
