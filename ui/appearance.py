"""外观设置的唯一权威入口。

## 为什么需要这个类

外观相关的配置散在 6 个键里，而且写入路径不统一：

    theme_mode / theme_preset / accent_mode / accent_custom   主题
    panel_alpha / opacity                                     透明度
    material_enabled / material_type / material_strength ...   材质

之前每个控件各自 `self.config[key] = value` 再 `_on_apply()`，
于是出现"外观相关改动互相冲突"——比如改材质时连带把面板不透明度
重算了一遍，或者切主题时把材质开关带回旧值。

这里把外观配置收成**一个入口**：

    read(cfg)            从配置读出完整外观状态
    write(cfg, state)    把外观状态写回配置
    apply(state)         应用（校验 + 落盘 + 广播）

任何外观控件都只跟它打交道，不再直接碰 config。
"""
from PySide6.QtCore import QObject, Signal

#: 外观相关的全部配置键（在这个集合里的才会被 AppearanceState 管理）
APPEARANCE_KEYS = frozenset({
    "theme", "theme_mode", "theme_preset", "accent_mode", "accent_custom",
    "panel_alpha", "opacity",
    "material_enabled", "material_type", "material_strength",
    "material_image_opacity", "material_dim",
    "material_saturate", "material_brightness",
})

#: 材质类型的合法值
MATERIAL_TYPES = ("none", "blur", "frosted", "fog")

#: 各数值项的默认值与范围 (默认, 最小, 最大)
_DEFAULTS = {
    "panel_alpha": (0.92, 0.3, 1.0),
    "opacity": (0.98, 0.1, 1.0),
    "material_strength": (55, 0, 100),
    "material_image_opacity": (100, 0, 100),
    "material_dim": (0, 0, 100),
    # 玻璃滤镜：饱和度/亮度决定"通透感"，是材质精致度的关键
    "material_saturate": (18, 10, 30),
    "material_brightness": (104, 80, 130),
}


def _clamp(key, value):
    """把数值项夹到合法范围内；类型不对就用默认值。"""
    default, lo, hi = _DEFAULTS[key]
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    if v != v:                      # NaN
        return default
    return max(lo, min(hi, v))


class AppearanceState:
    """外观设置的一份快照（纯数据，便于比较与测试）。"""

    __slots__ = ("theme_mode", "theme_preset", "accent_mode", "accent_custom",
                 "panel_alpha", "opacity", "material_enabled", "material_type",
                 "material_strength", "material_image_opacity", "material_dim",
                 "material_saturate", "material_brightness")

    def __init__(self, **kw):
        for name in self.__slots__:
            setattr(self, name, kw.get(name))

    def as_dict(self):
        return {k: getattr(self, k) for k in self.__slots__}

    def __eq__(self, other):
        return isinstance(other, AppearanceState) and \
            self.as_dict() == other.as_dict()

    def __repr__(self):
        return "AppearanceState(%r)" % (self.as_dict(),)


def read(cfg):
    """从配置 dict 读出完整外观状态（缺失/非法值已归一）。"""
    cfg = cfg or {}
    mode = str(cfg.get("theme_mode") or cfg.get("theme") or "light").lower()
    if mode not in ("light", "dark", "system"):
        mode = "light"
    mtype = str(cfg.get("material_type") or "none").lower()
    if mtype not in MATERIAL_TYPES:
        mtype = "none"
    accent = str(cfg.get("accent_mode") or "preset").lower()
    if accent not in ("preset", "system", "custom"):
        accent = "preset"

    st = AppearanceState(
        theme_mode=mode,
        theme_preset=str(cfg.get("theme_preset") or "classic"),
        accent_mode=accent,
        accent_custom=str(cfg.get("accent_custom") or "#0067c0"),
        material_enabled=bool(cfg.get("material_enabled", False)),
        material_type=mtype,
    )
    for key in _DEFAULTS:
        setattr(st, key, _clamp(key, cfg.get(key, _DEFAULTS[key][0])))
    return st


def write(cfg, state):
    """把外观状态写回配置 dict（就地修改）。

    ⚠ 只写 `APPEARANCE_KEYS` 里的键，不碰其它配置——
    这是"单一入口"的关键：外观设置永远不会误改推荐/输入等无关项。
    """
    d = state.as_dict() if isinstance(state, AppearanceState) else dict(state)
    cfg["theme_mode"] = d["theme_mode"]
    # 旧键同步写，兼容降级
    cfg["theme"] = d["theme_mode"]
    cfg["theme_preset"] = d["theme_preset"]
    cfg["accent_mode"] = d["accent_mode"]
    cfg["accent_custom"] = d["accent_custom"]
    cfg["material_enabled"] = bool(d["material_enabled"])
    cfg["material_type"] = d["material_type"]
    for key in _DEFAULTS:
        cfg[key] = _clamp(key, d.get(key))
    return cfg


class AppearanceController(QObject):
    """外观配置的唯一入口。

    任何外观控件都通过它读写，不再直接操作 config。

    :param config: 当前配置 dict

    ## ⚠ 为什么**不直接改** config

    最初 `update()` 会把新值直接写进 `config` 再发 `changed` 信号。
    但宿主（AppRuntime）的 `apply_settings(new_cfg)` 是这么判断变化的：

        old = dict(self.config)      # 拷当前值
        self.config.update(new_cfg)  # 写入
        changed = {k for k in ... if old.get(k) != v}   # 比对

    而 controller 已经把 `self.config` 改过了，于是 `old` 拿到的就是
    **新值**，`changed` 恒为空 → 不触发换肤。
    实测：切到深色后 `config["theme_mode"] == "dark"` 但界面仍是浅色。

    所以现在 `update()` **只维护自己的状态并发出广播**，
    不碰外部 config；落盘与应用的职责完全交给宿主。
    """

    #: 外观状态变化（参数是 AppearanceState）
    changed = Signal(object)

    def __init__(self, config, parent=None):
        super().__init__(parent)
        self._config = config
        self._state = read(config)

    # ------------------------------------------------------------ 读
    def state(self):
        """当前外观状态（副本，改它不会影响内部）。"""
        return read(self._state.as_dict())

    def config(self):
        """底层配置 dict（只读用途，如构造主题编辑器）。

        ⚠ 不要直接改它的外观键——用 `update()`。
        """
        return self._config if self._config is not None else {}

    def as_dict(self):
        return self._state.as_dict()

    def changes_for(self, **changes):
        """算出"如果应用这些改动，配置会变成什么样"（不落盘、不改内部状态）。

        宿主拿它当 `apply_settings(new_cfg)` 的参数：宿主自己比对差异、
        自己落盘、自己广播。controller 只是帮忙算目标值。
        """
        old = self._state
        new = read(old.as_dict())
        for k, v in changes.items():
            if k in AppearanceState.__slots__:
                setattr(new, k, v)
        normalized = read(new.as_dict())

        cfg = dict(self._config or {})
        write(cfg, normalized)
        return cfg

    # ------------------------------------------------------------ 写
    def update(self, **changes):
        """更新若干外观项并广播（**不改外部 config**）。

        :return: 是否真的变了（没变就不广播）

        这是**唯一**的修改入口。控件只传自己想改的那几项，
        其余保持不动——从机制上杜绝"改 A 把 B 带歪"。
        """
        old = self._state
        new = read(old.as_dict())
        for k, v in changes.items():
            if k not in AppearanceState.__slots__:
                continue            # 忽略不属于外观的键
            setattr(new, k, v)
        # 归一化（夹范围 / 枚举校验）
        normalized = read(new.as_dict())
        if normalized == old:
            return False
        self._state = normalized
        self.changed.emit(normalized)
        return True

    def reload(self, config=None):
        """配置被外部整体替换后重新读取（不广播）。

        用于 apply_settings 落盘后同步——避免"外部改了配置，
        controller 还拿着旧状态"。
        """
        if config is not None:
            self._config = config
        self._state = read(self._config or {})
