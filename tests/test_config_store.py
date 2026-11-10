"""配置层单测：类型校验 + 测试功能门禁。

覆盖 `core/config_store.py` 的两条不变式：
  1. 手改 config.json 写错类型/越界时回退默认值，绝不让错误类型流进业务代码
     （`int("三十")` 曾在启动期直接抛异常）；
  2. 测试模式关闭时把未毕业功能归位默认（防止"只隐藏 UI、功能仍在生效"的逃逸）。
"""
import json

import pytest

from core import config_store, runtime


# ---------------------------------------------------------------- 类型校验
def test_wrong_type_falls_back_to_default():
    cfg, issues = config_store.validate_config({"max_recent": "30"})
    assert cfg["max_recent"] == config_store.DEFAULT_CONFIG["max_recent"]
    assert any("max_recent" in m and "类型错误" in m for m in issues)


def test_bool_is_not_accepted_as_int():
    """Python 里 isinstance(True, int) 为真，必须特判数值项里的 bool 笔误。

    `"opacity": true` 是典型手误，不能因为 True==1 就被当成合法数值悄悄生效。
    """
    for key in ("opacity", "panel_alpha", "max_recent", "page_size"):
        cfg, issues = config_store.validate_config({key: True})
        assert cfg[key] == config_store.DEFAULT_CONFIG[key], \
            "%s 不应接受 True" % key
        assert any(key in m for m in issues)


def test_int_accepted_for_float_field():
    """手写 `"opacity": 1` 很常见，应视为合法数值而不是类型错误。"""
    cfg, issues = config_store.validate_config({"opacity": 1})
    assert cfg["opacity"] == 1
    assert issues == []


def test_bool_field_rejects_int():
    cfg, issues = config_store.validate_config({"auto_popup": 1})
    assert cfg["auto_popup"] is config_store.DEFAULT_CONFIG["auto_popup"]
    assert any("auto_popup" in m for m in issues)


@pytest.mark.parametrize("key,value", [
    ("theme", "neon"),
    ("input_method", "telepathy"),
])
def test_enum_out_of_range_falls_back(key, value):
    cfg, issues = config_store.validate_config({key: value})
    assert cfg[key] == config_store.DEFAULT_CONFIG[key]
    assert any(key in m and "非法" in m for m in issues)


@pytest.mark.parametrize("key,value", [
    ("opacity", 5),
    ("panel_alpha", 0.01),
    ("max_recent", 999),
    ("page_size", 0),
])
def test_numeric_out_of_range_falls_back(key, value):
    cfg, issues = config_store.validate_config({key: value})
    assert cfg[key] == config_store.DEFAULT_CONFIG[key]
    assert any(key in m and "超出范围" in m for m in issues)


def test_unknown_key_is_preserved():
    """向前兼容：未来版本或插件写入的键不能被清掉。"""
    cfg, issues = config_store.validate_config({"some_future_key": 42})
    assert cfg["some_future_key"] == 42
    assert issues == []


def test_missing_keys_filled_with_defaults():
    cfg, issues = config_store.validate_config({})
    assert cfg == config_store.DEFAULT_CONFIG
    assert issues == []


def test_non_dict_top_level_falls_back_entirely():
    cfg, issues = config_store.validate_config([1, 2, 3])
    assert cfg == config_store.DEFAULT_CONFIG
    assert issues


def test_valid_config_passes_through_untouched():
    raw = {
        "theme": "dark", "opacity": 0.5, "max_recent": 10,
        "input_method": "direct", "auto_popup": False,
        "plugins_enabled": {"demo": True},
    }
    cfg, issues = config_store.validate_config(raw)
    assert issues == []
    for k, v in raw.items():
        assert cfg[k] == v


# ---------------------------------------------------------------- 读取落盘
def test_load_config_merges_and_validates(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"theme": "dark", "max_recent": "坏值"}),
                    encoding="utf-8")
    monkeypatch.setattr(runtime, "config_path", lambda: str(path))

    cfg = config_store.load_config()
    assert cfg["theme"] == "dark"                       # 合法项保留
    assert cfg["max_recent"] == config_store.DEFAULT_CONFIG["max_recent"]


def test_load_config_handles_missing_file(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, "config_path",
                        lambda: str(tmp_path / "nope.json"))
    assert config_store.load_config() == config_store.DEFAULT_CONFIG


def test_load_config_handles_broken_json(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    path.write_text("{ not json", encoding="utf-8")
    monkeypatch.setattr(runtime, "config_path", lambda: str(path))
    assert config_store.load_config() == config_store.DEFAULT_CONFIG


def test_save_config_writes_and_roundtrips(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    monkeypatch.setattr(runtime, "config_path", lambda: str(path))
    cfg = dict(config_store.DEFAULT_CONFIG, theme="dark")
    config_store.save_config(cfg)
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk["theme"] == "dark"
    assert config_store.load_config()["theme"] == "dark"


# ---------------------------------------------------------------- 测试功能门禁
def test_gate_no_longer_resets_graduated_features():
    """已毕业的功能不受测试模式门控。

    `use_uia_elevation` 与三种输入方式都已成为正式功能：
    关闭测试模式**不应**把它们归位或回退。
    """
    cfg = dict(config_store.DEFAULT_CONFIG,
               use_test_features=False, use_uia_elevation=True,
               input_method="clipboard")
    config_store.enforce_test_feature_gate(cfg)
    assert cfg["use_uia_elevation"] is True, "UIA 提权已毕业，不该被归位"
    assert cfg["input_method"] == "clipboard", "输入方式已毕业，不该被回退"


def test_gate_still_works_for_remaining_test_features():
    """门控机制本身仍然有效：TEST_FEATURE_DEFAULTS 里的项仍会被归位。

    当前表是空的（没有未毕业功能），这里临时注入一项验证机制没坏。
    """
    import core.config_store as cs

    orig = dict(cs.TEST_FEATURE_DEFAULTS)
    try:
        cs.TEST_FEATURE_DEFAULTS["some_future_feature"] = False
        cfg = dict(cs.DEFAULT_CONFIG, use_test_features=False,
                   some_future_feature=True)
        cs.enforce_test_feature_gate(cfg)
        assert cfg["some_future_feature"] is False
    finally:
        cs.TEST_FEATURE_DEFAULTS.clear()
        cs.TEST_FEATURE_DEFAULTS.update(orig)


def test_gate_keeps_test_features_when_enabled():
    cfg = dict(config_store.DEFAULT_CONFIG,
               use_test_features=True, use_uia_elevation=True,
               input_method="type")
    config_store.enforce_test_feature_gate(cfg)
    assert cfg["use_uia_elevation"] is True
    assert cfg["input_method"] == "type"


def test_gate_applies_on_save(tmp_path, monkeypatch):
    """门禁挂在落盘路径上；对已毕业功能不做任何回退。"""
    path = tmp_path / "config.json"
    monkeypatch.setattr(runtime, "config_path", lambda: str(path))
    cfg = dict(config_store.DEFAULT_CONFIG,
               use_test_features=False, use_uia_elevation=True)
    config_store.save_config(cfg)
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk["use_uia_elevation"] is True


def test_all_input_methods_are_available():
    """三种输入方式全部毕业，都应可用。"""
    assert config_store.ALWAYS_AVAILABLE_INPUT_METHODS == frozenset(
        {"direct", "clipboard", "type"})
