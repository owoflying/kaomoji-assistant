"""配置读写与「测试功能」门禁。

配置是运行时本地 JSON（`%APPDATA%/KaomojiAssistant/config.json` 或源码根目录），
不随包分发。这里集中三件事：

1. 默认值表——新增配置项只要往 `DEFAULT_CONFIG` 加一行，旧配置文件缺字段也不会报错；
2. 落盘前的门禁归位——`use_test_features` 关闭时把未毕业功能强制恢复默认值，
   防止「只隐藏了 UI、功能却仍在生效」的逃逸；
3. 磁盘读写——唯一的配置读入口与写入口。

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
    "page_size": 3,
    "autostart": False,
    "auto_hide_on_blur": True,
    "developer_mode": False,
    "use_uia_elevation": False,
    "use_test_features": False,
    # 插件启用状态表：{"<plugin_id>": True/False}；未出现的插件视为停用
    "plugins_enabled": {},
}

# 处于测试模式的功能项（默认归属测试分组，确认上线后移出）；值为「关闭态默认」。
TEST_FEATURE_DEFAULTS = {
    "use_uia_elevation": False,
}

# 输入方式中始终可用的默认项；其余（clipboard/type）属于测试功能。
ALWAYS_AVAILABLE_INPUT_METHODS = frozenset({"direct"})


def load_config():
    """读磁盘配置并与默认值合并（缺失项自动补默认）。"""
    cfg = dict(DEFAULT_CONFIG)
    try:
        with open(runtime.config_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            cfg.update(data)
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    except OSError:
        pass
    return cfg


def enforce_test_feature_gate(config):
    """测试模式关闭时，把未毕业的测试功能归位为默认值（就地修改）。

    仅靠隐藏设置 UI 拦不住功能——已开启的测试项其配置位仍会被功能代码读取并生效。
    放在落盘前统一执行，可覆盖设置页保存、开发者页开关测试模式等所有写入路径。
    """
    if config.get("use_test_features", False):
        return
    for key, default in TEST_FEATURE_DEFAULTS.items():
        config[key] = default
    if config.get("input_method") not in ALWAYS_AVAILABLE_INPUT_METHODS:
        config["input_method"] = "direct"


def save_config(config):
    """把配置写入磁盘（先过测试功能门禁）。"""
    enforce_test_feature_gate(config)
    path = runtime.config_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)
