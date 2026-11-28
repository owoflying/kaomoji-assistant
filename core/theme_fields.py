"""主题字段定义：**唯一**的字段表 / 取值范围 / 枚举。

## 为什么单独一个模块

这些定义原来放在 `core/theme_profile.py` 里，而 `core/surface_spec.py`
又需要它（判断某个界面支持哪些字段）：

    surface_spec  → 需要 DEFAULT_PROFILE
    theme_profile → 需要 SPECS（表面列表）

两边互相 import，形成一个**模块级循环**。为了绕开它，
`surface_spec` 里写了一段很别扭的"惰性求值 + 读 sys.modules"
的代码——那是被循环逼出来的补丁，不是好设计。

把字段定义抽到本模块后，依赖变成干净的单向：

    theme_fields  ←  surface_spec
    theme_fields  ←  theme_profile
    theme_fields  →  （不依赖任何 core 模块）

没有循环，`surface_spec` 那段别扭的惰性求值也可以删掉了。

## 改字段时看这里

新增外观字段只要：在本文件加默认值 + 取值范围。`DEFAULT_PROFILE`、
`ALL_FIELDS`、归一化、外观页渲染、覆盖层快照全部自动跟上。
"""
import copy

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

#: 数值字段的取值范围（用于归一化）
RANGES = {
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

MATERIAL_TYPES = ("none", "blur", "frosted", "fog")
THEME_MODES = ("light", "dark", "system")
ACCENT_MODES = ("preset", "system", "custom")

#: 全部字段名（顺序与 DEFAULT_PROFILE 一致）
ALL_FIELDS = tuple(DEFAULT_PROFILE)


def blank_profile():
    """一份全新的默认主题包（深拷贝，调用方可安全修改）。"""
    return copy.deepcopy(DEFAULT_PROFILE)
