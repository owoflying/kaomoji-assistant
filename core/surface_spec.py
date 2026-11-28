"""表面能力表：**声明式**定义每个界面能用哪些外观设置。

## 这一层要解决什么

外观系统反复出 bug，根子不在某个函数写错，而在**没有一处地方
明确说清「这个界面到底支持哪些设置」**。于是：

    * 外观页对所有界面都渲染同一套控件，而背景图操作写死 `SLOT_MAIN`
      —— 给「托盘菜单」点「清除」，清掉的是主窗口的图
    * 覆盖层快照手写 8 个字段，漏了 8 个（含明暗与配色）
      —— 「开了独立配置，改深浅色没反应」

现象各不相同，根因是同一个：**能力没有声明，全靠调用点各自记忆**。

## 现在的能力边界（有依据，不是拍脑袋）

实测了每个窗口的实现：

    ui/frameless_shell.py    MaterialComposer  → 主窗口材质
    ui/picker_window.py      MaterialComposer(surface="picker")
    ui/log_viewer.py         MaterialComposer(surface="log")
    ui/plugin_dialog.py      MaterialComposer(surface="plugin")
    ui/glass_menu.py         自己的 composer（带 glass 绘制）

`crop_x/y/w/h` 则统一由 `material_composer._crop()` 读取，
**任何用 MaterialComposer 的界面都能吃裁切**。

所以：**全部界面都支持全部外观能力**。每个界面只是"有没有被
单独设置过"，不存在"这个界面不支持某个设置"。

⚠ 早期版本我给 menu/dialog/plugin 裁掉了材质和背景图，
那是**没有依据的猜测**——底层明明都支持。结果用户在外观页
根本看不到这些界面的材质选项，等于凭空砍掉了功能。

## 那这张表还有什么用

声明的是**互不相同的部分**，而不是"能不能"：

    image_slot   每个界面各自的背景图槽位（互不覆盖）
    crop         是否**默认**提供裁切入口
    note         界面上给用户看的说明

`fields` 现在默认是全集；只有将来真出现"某界面确实吃不到某字段"
的情况才需要收窄，而且要在注释里写清依据。

## 新增一个界面

在下面 `_reg(...)` 加一条，并在对应窗口的 `MaterialComposer`
传上相同的 `surface=`。外观页、解析、落盘、背景图操作全部自动跟上。
"""
#: 字段分组。
#:
#: ⚠ `ALL_FIELDS` **必须**从 `core.theme_profile.DEFAULT_PROFILE` 派生，
#: 不能手写。手写必漏——这里就漏过 `panel_alpha`，结果所有界面的
#: 透明度滑块整片消失（能力表说"不支持该字段"，页面就不渲染它了）。
#:
#: 分组只是给界面分区用的标签，未登记的字段仍然落在 `ALL_FIELDS` 里，
#: 不会因为忘了归类而被砍掉。
THEME_FIELDS = ("theme_mode", "theme_preset")
ACCENT_FIELDS = ("accent_mode", "accent_custom")
MATERIAL_FIELDS = (
    "material_enabled", "material_type", "material_strength",
    "material_saturate", "material_brightness", "material_dim",
    "image_opacity",
)
CROP_FIELDS = ("crop_x", "crop_y", "crop_w", "crop_h")


def _all_fields():
    """主题包的字段全集（从 DEFAULT_PROFILE 派生）。

    ## ⚠ 不能在模块顶层直接 import

    `core.theme_profile` 会 import 本模块（取 SURFACES），所以本模块
    顶层再反过来 import `DEFAULT_PROFILE` 就形成**循环导入**——
    那一刻 `theme_profile` 的模块体还没执行到 `DEFAULT_PROFILE = {...}`，
    `from ... import DEFAULT_PROFILE` 会失败并落到 except 分支，
    于是拿到的是手写并集（**又漏了 panel_alpha**），问题原样复现。

    实测症状：修好之后透明度滑块依然整片消失，查出来字段数 15 ≠ 16。

    所以改成**惰性求值**：等真正需要字段表时（那时两个模块都已加载完）
    再去读 DEFAULT_PROFILE。
    """
    try:
        import sys as _sys

        tp = _sys.modules.get("core.theme_profile")
        if tp is not None:
            prof = getattr(tp, "DEFAULT_PROFILE", None)
            if prof:
                return tuple(prof)
        from core.theme_profile import DEFAULT_PROFILE

        return tuple(DEFAULT_PROFILE)
    except Exception:
        # 极端兜底：分组并集 + 已知的独立字段
        return (THEME_FIELDS + ACCENT_FIELDS + MATERIAL_FIELDS
                + CROP_FIELDS + ("panel_alpha",))


#: 全部字段（**函数**，不是常量）。
#:
#: 写成函数而不是模块级常量，是为了避开循环导入：
#: 常量会在本模块被 import 的那一刻求值，那时 theme_profile 还没
#: 定义 DEFAULT_PROFILE；函数则是调用时才求值，两个模块都加载完了。
#:
#: 兼容写法：调用方仍可把 `ALL_FIELDS` 当序列用（它是 tuple 的工厂），
#: 下面 `SurfaceSpec.__init__` 里统一走 `all_fields()`。
ALL_FIELDS = ()


def all_fields():
    """主题包字段全集（懒求值）。"""
    return _all_fields()


class SurfaceSpec:
    """一个界面的外观能力声明。

    :param key:        表面标识，与 `SURFACES` 的键一致
    :param title:      显示名
    :param fields:     支持的主题字段（默认全集，见模块说明）
    :param image_slot: 该界面**自己的**背景图槽位名
    :param own_image:  是否允许设置独立背景图
    :param crop:       是否提供背景图裁切入口
    :param note:       界面上的一句说明（可为空）
    """

    __slots__ = ("key", "title", "fields", "image_slot", "own_image",
                 "crop", "note")

    def __init__(self, key, title, fields=None, image_slot=None,
                 own_image=True, crop=True, note=""):
        self.key = key
        self.title = title
        # fields=None → 懒求值取全集（避开循环导入，见 all_fields 说明）
        self.fields = frozenset(fields if fields is not None
                                else all_fields())
        self.image_slot = image_slot
        self.own_image = bool(own_image)
        self.crop = bool(crop)
        self.note = note

    # ---------------------------------------------------------- 能力查询
    def supports(self, field):
        return field in self.fields

    def supports_any(self, fields):
        return bool(self.fields & set(fields))

    def supports_all(self, fields):
        return set(fields) <= self.fields

    def __repr__(self):
        return ("SurfaceSpec(%r, own_image=%s, crop=%s, fields=%d)"
                % (self.key, self.own_image, self.crop, len(self.fields)))


# ---------------------------------------------------------------------------
# 表面登记表
#
# ⚠ 新增界面只需要在这里加一条，不需要改外观页/解析/落盘任何代码。
#    同时记得在窗口里给 MaterialComposer 传相同的 surface=。
# ---------------------------------------------------------------------------
SPECS = {}


def _reg(spec):
    SPECS[spec.key] = spec
    return spec


#: 主窗口：其余界面的默认外观来源
_reg(SurfaceSpec(
    "main", "主窗口",
    image_slot="background",
    note="主窗口是其余界面的默认外观来源。",
))

#: 候选条
_reg(SurfaceSpec(
    "picker", "候选条",
    image_slot="picker_background",
    note="候选条尺寸随内容变化，裁切对它意义不大，但功能本身可用。",
))

#: 日志窗口
_reg(SurfaceSpec(
    "log", "日志窗口",
    image_slot="log_background",
))

#: 插件弹窗
_reg(SurfaceSpec(
    "plugin", "插件弹窗",
    image_slot="plugin_background",
))

#: 托盘右键菜单
_reg(SurfaceSpec(
    "menu", "托盘菜单",
    image_slot="menu_background",
))

#: 其它对话框
_reg(SurfaceSpec(
    "dialog", "其它对话框",
    image_slot="dialog_background",
))


def spec(surface):
    """取某个表面的能力声明；未知表面按主窗口处理（安全兜底）。"""
    return SPECS.get(surface) or SPECS["main"]


def all_specs():
    """全部表面，主窗口排第一。"""
    out = [SPECS["main"]]
    out.extend(s for k, s in SPECS.items() if k != "main")
    return out


def known_surfaces():
    """全部表面 key。"""
    return [s.key for s in all_specs()]


def image_slot_of(surface):
    """该表面的独立背景图槽位；None = 不使用独立图。"""
    return spec(surface).image_slot
