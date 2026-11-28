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
#:
#: ⚠ 这里不再手工维护，而是从 `core.surface_spec.SPECS` 派生。
#: 新增界面只需在能力表里登记一条，这个字典自动跟上——
#: 两处各写一份正是"界面列表和实际能力对不上"的来源。
from core.surface_spec import all_specs as _all_specs  # noqa: E402

SURFACES = {s.key: s.title for s in _all_specs()}

#: 各表面自己的背景图槽位（供 ui/window_material 之外的地方查询）
SURFACE_IMAGE_SLOTS = {s.key: s.image_slot for s in _all_specs()}

#: 字段定义统一放在 `core/theme_fields.py`——那里不依赖任何 core 模块，
#: 所以 `surface_spec` 和本模块都能安全引用，不再形成循环导入。
from core.theme_fields import (  # noqa: E402
    ACCENT_MODES, ALL_FIELDS, DEFAULT_PROFILE, MATERIAL_TYPES, RANGES,
    THEME_MODES,
)

#: 本模块对外提供的公开名字（`ALL_FIELDS` 等是**转发**给下游用的，
#: 本模块内部不一定用到，但外部一直从 here 取，保留以免破坏调用点）
__all__ = [
    "ACCENT_MODES", "ALL_FIELDS", "DEFAULT_PROFILE", "MATERIAL_TYPES",
    "RANGES", "THEME_MODES",
    "SURFACES", "SURFACE_IMAGE_SLOTS", "clear_override",
    "effective_image_slot", "get_overrides", "get_profile",
    "has_override", "migrate_legacy", "normalize", "resolve",
    "set_override", "set_profile",
]

#: 兼容旧引用名（本模块内部一直用 `_RANGES` 等私有名）
_RANGES = RANGES
_MATERIAL_TYPES = MATERIAL_TYPES
_THEME_MODES = THEME_MODES
_ACCENT_MODES = ACCENT_MODES


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
    """取所有表面的覆盖层（已按能力表过滤掉不支持的字段）。

    ⚠ **空覆盖层也会保留**（值仍是 `{}`）。

    空覆盖层表示"这个界面已独立，但还没改过任何值"。如果这里把它
    过滤掉，`has_override()` 就会认为"没开独立"，于是开关自己弹回去、
    改动写不进去——正是"候选条改了不刷新"的成因。
    """
    migrate_legacy(cfg)
    raw = cfg.get("surface_overrides") or {}
    out = {}
    for k, v in raw.items():
        if isinstance(v, dict):
            out[k] = _filter_by_capability(k, v)
    return out


def _filter_by_capability(surface, patch):
    """把覆盖项里**该界面不支持**的字段剔除。

    ## 为什么必须在数据层过滤

    外观页理论上只渲染该界面支持的能力，但覆盖层可能来自旧版本、
    来自手工编辑的 config.json、或者将来某个调用点写错了。与其
    到处假设「调用方不会写多余字段」，不如在落盘边界统一兜住：

    * 写：`set_override` 只存支持的字段
    * 读：`get_overrides` 只返回支持的字段

    这样即使上层出 bug，也不会让「候选条」突然带上裁切参数、
    或让「对话框」出现背景图，导致后续 `resolve()` 出现无法解释的值。
    """
    from core.surface_spec import spec

    allowed = spec(surface).fields
    return {k: v for k, v in patch.items() if k in allowed}


def has_override(cfg, surface):
    """某个表面是否**开着**独立配置。

    ## 语义：看"键在不在"，不看"里面有没有东西"

    覆盖层可以是空的 `{}` —— 那表示"这个界面已独立，但还没改过任何值"，
    此时 `resolve()` 会把主配置的值原样给它（因为合并时覆盖层没有键）。

    如果这里用 `bool(ovr.get(surface))` 判断，空覆盖层会被当成
    "没开独立"，于是：

        * 界面上「独立配置」开关一打开就自己弹回去
        * `_push` 走 else 分支直接 return —— 改了任何东西都不生效

    实测症状：给候选条开独立后改主题，下拉框看着变了，
    候选条却永远不刷新（因为 `_push` 一次都没执行）。
    """
    migrate_legacy(cfg)
    raw = cfg.get("surface_overrides") or {}
    return isinstance(raw.get(surface), dict)


def set_override(cfg, surface, patch):
    """给某个表面写覆盖项（只写差异，且只写该界面支持的字段）。

    :param patch: dict

        * 非空 → 把这些字段写进覆盖层（该界面不支持的会被过滤掉）
        * 空 dict → **保留空的覆盖层**（表示"已独立、尚未改任何值"）

    想彻底取消独立配置请用 `clear_override()`。
    """
    migrate_legacy(cfg)
    ovr = cfg.setdefault("surface_overrides", {})
    cleaned = _filter_by_capability(surface, patch or {})
    ovr.setdefault(surface, {}).update(cleaned)
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

    :return: 背景图命名空间名（见 ui/window_material.SLOT_*）；None 表示
             "这个界面不支持独立背景图，必须沿用主窗口那张"

    ## 旧实现的严重缺陷

    原来是硬编码的：

        if surface == "picker" and has_override(cfg, "picker"):
            return "picker_background"
        return "background"

    只有 picker 有独立槽位，其它表面**一律返回 "background"**。
    于是给「托盘菜单」点「清除背景图」，清掉的是**主窗口的图**——
    用户明确反馈过这个问题。

    ## 现在

    槽位由能力表（`core.surface_spec`）声明，每个支持独立图的界面
    有自己互不覆盖的槽位。新增界面只要在表里登记 `image_slot` 即可。
    """
    from core.surface_spec import spec

    sp = spec(surface)
    if surface == "main":
        return "background"
    if not sp.own_image or not sp.image_slot:
        # 没有自己的槽位 → 返回 None。
        #
        # **取图**和**删图**是两回事：
        #   * 删图：拿到 None 就知道"这个界面没有独立图可删"，
        #     从数据层堵死"给菜单点清除却删了主图"（用户反馈过）
        #   * 取图：调用方（material_composer）必须把 None 回落成
        #     SLOT_MAIN，否则菜单/对话框会突然没有背景图
        return None
    if not has_override(cfg, surface):
        # 还没打开「独立配置」→ 沿用主窗口那张
        return "background"
    # 开了独立配置 → **完全用自己的槽位**
    #
    # 这里刻意不判断"自己的图存不存在"：
    # 开了独立配置就意味着这个界面不再继承主窗口的外观，
    # 没设图时它就该显示纯色面板，而不是"偷偷用回主窗口那张"。
    #
    # （曾经加过"没图就回落主窗口"的兜底，结果是：用户明明开了独立、
    #  还没设图，界面却突然出现主窗口的背景图，和预期完全相反。）
    return sp.image_slot
