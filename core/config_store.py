"""配置读写与「测试功能」门禁。

配置是运行时本地 JSON（`%APPDATA%/KaomojiAssistant/config.json` 或源码根目录），
不随包分发。这里集中三件事：

1. 默认值表——新增配置项只要往 `DEFAULT_CONFIG` 加一行，旧配置文件缺字段也不会报错；
2. 落盘前的门禁归位——`use_test_features` 关闭时把未毕业功能强制恢复默认值，
   防止「只隐藏了 UI、功能却仍在生效」的逃逸；
3. 磁盘读写——唯一的配置读入口与写入口；
4. **类型校验**——手改 config.json 写错类型（如 `"max_recent": "30"`）时
   按默认值回退并记一条日志，绝不让错误类型流进业务代码
   （`int("三十")` 会在启动期直接抛异常）。

刻意不依赖 Qt：配置层要能在无界面环境下被测试。
"""
import json
import os

from core import runtime

DEFAULT_CONFIG = {
    "hotkey": "<ctrl>+<shift>+k",
    "theme": "light",
    "panel_alpha": 0.92,
    "opacity": 0.98,
    "input_method": "direct",
    "max_recent": 30,
    "auto_popup": True,
    #: 打字时按情绪自动推荐颜文字（0 = 完全关闭）
    "emotion_suggest": True,
    #: 是否使用 AI（神经网络）判断情绪。
    #:
    #: 关掉后完全走纯算法（关键词匹配）：可预测、无模型文件读写，
    #: 但看不懂"今天一点都不开心"这类否定句。
    "emotion_use_ai": True,
    #: 推荐灵敏度 0~100：越低越保守（少打扰），越高越容易触发
    "emotion_sensitivity": 50,
    #: 是否根据用户选择持续调整模型（个性化学习）
    "emotion_learning": True,
    "page_size": 3,
    "autostart": False,
    "auto_hide_on_blur": True,
    "developer_mode": False,
    "use_uia_elevation": False,
    "use_test_features": False,
    # 自动更新：启动后延迟检查新版本，下载校验通过后提示安装
    "auto_update": True,
    # 插件启用状态表：{"<plugin_id>": True/False}；未出现的插件视为停用
    "plugins_enabled": {},
}

# 处于测试模式的功能项；值为「关闭态默认」。
#
# **已毕业的功能不再列在这里**。历史记录：
#   * `use_uia_elevation` 曾在此，已转为正式功能（首次使用会在欢迎页给出
#     风险说明并要求勾选确认，见 ui/welcome_dialog.py）；
#   * 插件系统同批毕业，不再受 `use_test_features` 门控。
# 现在这个表只剩「仍在测试中的功能」，新增未毕业功能时加到这里。
TEST_FEATURE_DEFAULTS = {}

# 输入方式中始终可用的默认项。
#
# 三种方式现已全部转为正式功能（原先把非 direct 的视为测试功能，
# 关闭测试模式会被强制回退到 direct）。保留这个常量是为了向后兼容
# 既有调用方与测试，语义改为「所有输入方式都可用」。
ALWAYS_AVAILABLE_INPUT_METHODS = frozenset({"direct", "clipboard", "type"})

# 取值受约束的配置项：值不在集合内即视为非法（回退默认）。
_CONFIG_ENUMS = {    "theme": ("light", "dark", "system"),
    "input_method": ("clipboard", "direct", "type"),
}

# 数值项的取值范围（闭区间），越界即回退默认。
_CONFIG_RANGES = {
    "panel_alpha": (0.3, 1.0),
    "opacity": (0.1, 1.0),
    "max_recent": (0, 200),
    "page_size": (1, 20),
    "emotion_sensitivity": (0, 100),
}


_SHARED_SENTINEL = object()             # 「默认表里没有这个键」的哨兵


def _check_type(key, default, value):
    """value 与默认值的类型是否兼容。

    bool 必须严格匹配——Python 里 `isinstance(True, int)` 为真，若不特判，
    `"auto_popup": 1` 会被当成合法布尔静默接受，而 `"max_recent": True`
    会被当成整数 30 之外的值（True==1），两种情况都是隐藏的类型错误。
    """
    if isinstance(default, bool):
        return isinstance(value, bool)
    if default is None:                 # 无默认值约束的键（未来新增）不校验
        return True
    # 数值项允许 int/float 互通（手写 `"opacity": 1` 很常见），但 bool 除外
    # （`"opacity": true` 是笔误——True==1 会被当成合法数值悄悄生效）。
    # 越界由 _CONFIG_RANGES 单独判定。
    if isinstance(default, (int, float)):
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return isinstance(value, type(default))


def validate_config(raw, defaults=None):
    """按 `DEFAULT_CONFIG` 的类型/枚举/范围校验，返回 (清洗后的 dict, 问题列表)。

    纯函数、无副作用，便于离屏单测。规则：
      * 未知键——保留（向前兼容，插件/未来版本可能写入）；
      * 类型不符——回退默认值；
      * 枚举/范围越界——回退默认值；
      * 缺省键——用默认值补齐（与 load_config 的合并语义一致）。
    """
    defaults = DEFAULT_CONFIG if defaults is None else defaults
    cfg = dict(defaults)
    issues = []
    if not isinstance(raw, dict):
        return cfg, ["配置文件顶层不是对象，已整体回退默认配置"]

    for key, value in raw.items():
        default = defaults.get(key, _SHARED_SENTINEL)
        if default is _SHARED_SENTINEL:
            cfg[key] = value                               # 未知键原样保留
            continue
        if not _check_type(key, default, value):
            issues.append(
                "%s 类型错误（期望 %s，实际 %r），已回退默认 %r"
                % (key, type(default).__name__, value, default))
            continue
        choices = _CONFIG_ENUMS.get(key)
        if choices is not None and value not in choices:
            issues.append(
                "%s 取值 %r 非法（可选：%s），已回退默认 %r"
                % (key, value, "/".join(choices), default))
            continue
        bounds = _CONFIG_RANGES.get(key)
        if bounds is not None and not (bounds[0] <= value <= bounds[1]):
            issues.append(
                "%s 取值 %r 超出范围 %s~%s，已回退默认 %r"
                % (key, value, bounds[0], bounds[1], default))
            continue
        cfg[key] = value
    return cfg, issues

_SHARED_SENTINEL = object()             # 「默认表里没有这个键」的哨兵


def load_config():
    """读磁盘配置，经类型校验后与默认值合并（缺失项补默认，非法项回退默认）。"""
    try:
        with open(runtime.config_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return dict(DEFAULT_CONFIG)
    except OSError:
        return dict(DEFAULT_CONFIG)

    cfg, issues = validate_config(data)
    if issues:
        # 配置层刻意不依赖 Qt，这里不能 import applog（它 import PySide6）；
        # 用标准 logging 兜底，由 install_qt_log_filter 之外的渠道也能看到。
        import logging
        for msg in issues:
            logging.getLogger("kaomoji.config").warning("配置项已回退：%s", msg)
    return cfg


def enforce_test_feature_gate(config):
    """测试模式关闭时，把未毕业的测试功能归位为默认值（就地修改）。

    仅靠隐藏设置 UI 拦不住功能——已开启的测试项其配置位仍会被功能代码读取并生效。
    放在落盘前统一执行，可覆盖设置页保存、开发者页开关测试模式等所有写入路径。

    ⚠ 现已毕业的功能**不受此门控**：UIA 提权、插件系统、三种输入方式
    都是正式功能，关闭测试模式不会把它们关掉或回退。
    """
    if config.get("use_test_features", False):
        return
    for key, default in TEST_FEATURE_DEFAULTS.items():
        config[key] = default


def save_config(config):
    """把配置写入磁盘（先过测试功能门禁）。"""
    enforce_test_feature_gate(config)
    path = runtime.config_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)
