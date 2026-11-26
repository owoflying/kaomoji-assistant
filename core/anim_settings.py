"""动画开关：集中判断"某类动画是否该启用"。

## 为什么需要集中判断

项目里有 5 类动画，分散在 5 个模块里。如果每个模块自己去读配置：

    if self.config.get("anim_page_switch", True): ...

就会遇到三个问题：

1. **键名拼错不会报错**——`anim_page_switch` 写成 `anim_page_swith`
   永远取到默认值 True，用户以为开关坏了
2. **拿不到配置的模块**（如按钮悬停）还得自己想办法读磁盘
3. 默认值散落各处，改默认要改 5 个地方

所以收敛到这里：一个 `enabled(key)` 统一读写。

## 特殊：欢迎页

按需求，欢迎页的动画**不受开关控制**——它只在首次启动出现一次，
不是日常使用中会反复看到的动画，关掉没有意义。
所以 `reveal_anim` 不查这里。
"""
#: 动画分类 → (配置键, 显示名, 说明)
ANIMATIONS = (
    ("page_switch", "anim_page_switch", "页面切换",
     "切换侧边栏页面时的淡入过渡"),
    ("hover", "anim_hover", "按钮悬停",
     "鼠标移到按钮上时的高光渐显"),
    ("picker", "anim_picker", "候选条弹出",
     "候选条出现时的淡入与上浮"),
    ("theme", "anim_theme", "切换主题",
     "换肤时的后退与遮罩过渡"),
    ("toggle", "anim_toggle", "开关控件",
     "设置里开关的滑块滑动"),
)

#: 分类名 → 配置键
_KEY_OF = {name: key for name, key, _, _ in ANIMATIONS}

#: 进程级缓存的配置引用（由 install 注入；没有时按需读盘）
_config = None


def install(config):
    """注入当前配置 dict（宿主启动时调用一次）。

    动画开关不需要实时热更新——关掉一个动画后下一次触发就生效，
    不需要立刻重绘。所以这里存引用即可，不用监听变化。
    """
    global _config
    _config = config


def _current_config():
    """取配置：优先用注入的，否则读磁盘（独立窗口/早期初始化时）。"""
    if _config is not None:
        return _config
    try:
        from core.config_store import load_config

        return load_config()
    except Exception:
        return {}


def enabled(name, config=None):
    """某类动画是否启用。

    :param name: 分类名（`page_switch` / `hover` / `picker` / `theme` / `toggle`）
    :param config: 显式指定配置；None 时用注入的
    :return: bool。**不认识的名字返回 True**（宁可动，也不要静默失效）
    """
    key = _KEY_OF.get(name)
    if key is None:
        return True
    try:
        cfg = config if config is not None else _current_config()
        return bool(cfg.get(key, True))
    except Exception:
        return True


def all_enabled(config=None):
    """是否所有动画都开着（给"一键全开/全关"用）。"""
    return all(enabled(n, config) for n, _, _, _ in ANIMATIONS)


def set_all(config, value):
    """一次性开关所有动画（就地修改配置 dict）。"""
    for _, key, _, _ in ANIMATIONS:
        config[key] = bool(value)
    return config
