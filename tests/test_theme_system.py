"""主题系统专项测试：颜色算法、配置迁移、预设、自定义主题。

## 这一层要防什么

主题系统重构后，有四个容易出错、且**出错后很难察觉**的地方：

1. **颜色算法**：`parse_color` 解析错 → 静默回退兜底色 → 整块界面配色失灵
2. **配置迁移**：老 `theme` 键没迁过来 → 用户设置被重置成浅色
3. **派生色**：对比度算错 → 某套预设下文字看不清
4. **自定义主题**：文件损坏/缺失 → 程序起不来

共同特征：**不会抛异常，只是难看**。所以必须显式测。
"""
import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)


# ====================================================================
# 一、颜色算法
# ====================================================================
@pytest.mark.parametrize("spec,expect", [
    ("#ff0000", (255, 0, 0, 1.0)),
    ("#00FF00", (0, 255, 0, 1.0)),
    ("rgba(10,20,30,0.5)", (10, 20, 30, 0.5)),
    ("rgb(1,2,3)", (1, 2, 3, 1.0)),
])
def test_parse_color(spec, expect):
    from core.theme_presets import parse_color

    got = parse_color(spec)
    assert got is not None, "解析失败：%r" % spec
    for a, b in zip(got, expect):
        assert abs(a - b) < 0.01


@pytest.mark.parametrize("bad", ["", None, "red", "#fff", "rgb()", "#gggggg"])
def test_parse_color_rejects_garbage(bad):
    """解析不了要返回 None，不能抛异常——主题文件是用户可改的数据。"""
    from core.theme_presets import parse_color

    assert parse_color(bad) is None


def test_mix_and_luminance():
    from core.theme_presets import luminance, mix

    # 黑白按 0.5 混 -> 中灰
    mid = mix("#000000", "#ffffff", 0.5)
    assert 120 < mid[0] < 135
    # 亮度：白 > 灰 > 黑
    assert luminance("#ffffff") > luminance("#808080") > luminance("#000000")


# ====================================================================
# 二、派生色
# ====================================================================
def test_derive_fills_all_keys():
    from core.theme_presets import ALL_COLOR_KEYS, derive, _fallback_core

    for dark in (False, True):
        full = derive(_fallback_core(dark), dark)
        missing = [k for k in ALL_COLOR_KEYS if not full.get(k)]
        assert not missing, "dark=%s 缺派生字段：%r" % (dark, missing)


def test_derive_contrast_mode_no_transparency():
    """高对比度预设不能有任何半透明——低视力场景下等于看不见。"""
    from core.theme_presets import derive, _fallback_core

    core = dict(_fallback_core(True))
    core["_contrast"] = True
    full = derive(core, True)
    for key in ("nav_hover", "nav_selected", "input_bg", "input_border",
                "divider", "window_border"):
        val = str(full.get(key, ""))
        assert "rgba" not in val, \
            "高对比度下 %s 仍是半透明：%r" % (key, val)


def test_text_readable_in_all_presets(qapp):
    """每套预设下，正文与背景的亮度差要够大。"""
    from core.theme_presets import luminance, resolve_palette

    for preset in ("classic", "midnight", "warm", "contrast"):
        for mode in ("light", "dark"):
            p = resolve_palette(preset, mode)
            diff = abs(luminance(p["text"]) - luminance(p["bg"]))
            assert diff > 0.5, (
                "%s/%s 正文与背景对比不足：text=%s bg=%s (diff=%.2f)"
                % (preset, mode, p["text"], p["bg"], diff))


# ====================================================================
# 三、配置迁移
# ====================================================================
@pytest.mark.parametrize("old,expect", [
    ("light", "light"),
    ("dark", "dark"),
    ("system", "system"),
])
def test_migrate_old_theme_key(old, expect):
    from core.config_store import migrate_theme_config

    cfg = migrate_theme_config({"theme": old})
    assert cfg["theme_mode"] == expect
    assert cfg["theme_preset"] == "classic"
    assert cfg["accent_mode"] == "preset"


def test_migrate_is_idempotent():
    """已有新键时不覆盖——否则升级后再降级会丢设置。"""
    from core.config_store import migrate_theme_config

    cfg = {"theme": "dark", "theme_mode": "light",
           "theme_preset": "warm", "accent_mode": "custom"}
    out = migrate_theme_config(dict(cfg))
    assert out["theme_mode"] == "light", "旧 theme 不该覆盖新 theme_mode"
    assert out["theme_preset"] == "warm"


def test_migrate_unknown_value_falls_back():
    from core.config_store import migrate_theme_config

    cfg = migrate_theme_config({"theme": "紫色"})
    assert cfg["theme_mode"] == "light"


def test_new_keys_survive_validation():
    """validate_config 不能把新键判非法回退掉。"""
    from core.config_store import validate_config

    cfg, issues = validate_config({
        "theme_mode": "dark", "theme_preset": "midnight",
        "accent_mode": "custom", "accent_custom": "#ff0000"})
    assert cfg["theme_mode"] == "dark"
    assert cfg["theme_preset"] == "midnight"
    assert cfg["accent_mode"] == "custom"
    assert not issues, "不该有校验问题：%r" % issues


def test_load_config_migrates_old_file(qapp, tmp_path, monkeypatch):
    """完整链路：磁盘上的老配置 -> load_config 后是新结构。"""
    from core import config_store, runtime

    p = tmp_path / "config.json"
    p.write_text(json.dumps({"theme": "dark", "hotkey": "<ctrl>+k"}),
                 encoding="utf-8")
    monkeypatch.setattr(runtime, "config_path", lambda: str(p))

    cfg = config_store.load_config()
    assert cfg["theme_mode"] == "dark", "老配置的 dark 没迁过来"
    assert cfg["theme_preset"] == "classic"
    assert cfg["hotkey"] == "<ctrl>+k", "其它配置不该被动"


# ====================================================================
# 四、预设与统一入口
# ====================================================================
def test_all_presets_loadable():
    from core.theme_presets import load_presets

    ps = load_presets()
    assert "classic" in ps, "至少要有一套内置预设兜底"
    for pid, data in ps.items():
        assert data.get("light"), "%s 缺 light 色板" % pid
        assert data.get("dark"), "%s 缺 dark 色板" % pid


def test_resolve_palette_unknown_preset_falls_back():
    from core.theme_presets import resolve_palette

    p = resolve_palette("不存在的预设", "dark")
    assert p["preset"] == "classic"
    assert p["bg"]


def test_build_theme_all_combinations(qapp):
    """4 套预设 × 2 种明暗 × 3 种强调色模式，全部能构建。"""
    from ui.theme_manager import build_theme

    for preset in ("classic", "midnight", "warm", "contrast", "custom"):
        for mode in ("light", "dark"):
            for accent in ("preset", "system", "custom"):
                t = build_theme({"theme_mode": mode,
                                 "theme_preset": preset,
                                 "accent_mode": accent,
                                 "accent_custom": "#ff3366"})
                assert t.name == mode
                assert t.bg, "%s/%s/%s 没有 bg" % (preset, mode, accent)
                assert t.style_sheet()


def test_build_theme_accent_override(qapp):
    from ui.theme_manager import build_theme

    t = build_theme({"theme_mode": "light", "accent_mode": "custom",
                     "accent_custom": "#ff3366"})
    assert t.accent == "#ff3366"


def test_theme_signature_changes_with_config():
    from ui.theme_manager import theme_signature

    a = theme_signature({"theme_mode": "light", "theme_preset": "classic"})
    b = theme_signature({"theme_mode": "dark", "theme_preset": "classic"})
    c = theme_signature({"theme_mode": "light", "theme_preset": "warm"})
    assert a != b and a != c


# ====================================================================
# 五、自定义主题
# ====================================================================
def test_custom_theme_roundtrip(qapp, tmp_path, monkeypatch):
    from core import theme_presets as TP
    from ui.theme_manager import build_theme

    monkeypatch.setattr(TP, "user_theme_path",
                        lambda: str(tmp_path / "user_theme.json"))
    data = {"name": "测试", "light": {"bg": "#faf5ff", "accent": "#8b5cf6"},
            "dark": {"bg": "#1a1425", "accent": "#a78bfa"}}
    ok, _msg = TP.save_user_theme(data)
    assert ok

    for mode, want_bg in (("light", "#faf5ff"), ("dark", "#1a1425")):
        t = build_theme({"theme_mode": mode, "theme_preset": "custom"})
        assert t.bg == want_bg, "自定义主题未生效：%s" % mode
        assert t.preset == "custom"


def test_custom_theme_missing_falls_back(qapp, tmp_path, monkeypatch):
    """用户主题文件被删掉时回退 classic，不能起不来。"""
    from core import theme_presets as TP
    from ui.theme_manager import build_theme

    monkeypatch.setattr(TP, "user_theme_path",
                        lambda: str(tmp_path / "nope.json"))
    t = build_theme({"theme_mode": "dark", "theme_preset": "custom"})
    assert t.preset == "classic"
    assert t.bg


def test_custom_theme_corrupt_falls_back(qapp, tmp_path, monkeypatch):
    from core import theme_presets as TP
    from ui.theme_manager import build_theme

    p = tmp_path / "bad.json"
    p.write_text("{oops", encoding="utf-8")
    monkeypatch.setattr(TP, "user_theme_path", lambda: str(p))
    t = build_theme({"theme_mode": "light", "theme_preset": "custom"})
    assert t.preset == "classic"


def test_validate_palette_reports_bad_colors():
    from core.theme_presets import validate_palette

    ok, errs = validate_palette({"bg": "#ffffff", "accent": "紫色"})
    assert not ok
    assert any("accent" in e for e in errs)

    ok, errs = validate_palette({"bg": "#ffffff"})
    assert ok and not errs


# ====================================================================
# 六、强调色只能有一个来源
# ====================================================================
def test_editor_does_not_expose_accent():
    """强调色只能有**一个**入口：主题编辑器。设置页不该再提供。

    ## 真实 bug

    曾经强调色同时存在于设置页下拉和编辑器色块里，两边互相覆盖：

        设置页选「自定义颜色」时，编辑器里设的 #8b5cf6 被静默覆盖成设置页的值
        设置页选「配色方案自带」时，编辑器的才生效

    同一个输入框生效与否取决于另一个下拉框选了什么——用户完全无法理解。

    ## 现在的设计

    强调色收敛到**主题编辑器**（它本来就是"调整个性化配色"的地方），
    提供三个来源：配色方案自带 / 跟随系统 / 自定义。设置页不再有这一项。
    """
    from ui.theme_editor import EDITABLE_KEYS, _ACCENT_MODES

    # 编辑器里有强调色（作为特殊行，不在普通色块里）
    assert "accent" not in EDITABLE_KEYS, \
        "强调色应该是编辑器里的特殊行，不该混进普通色块"
    modes = [m for m, _label in _ACCENT_MODES]
    assert modes == ["preset", "system", "custom"], \
        "编辑器应提供 自带/跟随系统/自定义 三个来源：%r" % (modes,)


def test_settings_page_has_no_accent_combo(qapp):
    """设置页**不能**再有强调色下拉。

    这是上一个 bug 的另一半：两个入口必然打架。现在只留编辑器。
    """
    from core.config_store import load_config
    from ui.pages.settings_page import SettingsPage

    page = SettingsPage(load_config())
    assert not hasattr(page, "accent_combo"), \
        "设置页又出现强调色下拉了，会和编辑器里的设置打架"


def test_accent_always_from_settings(qapp, tmp_path, monkeypatch):
    """自定义主题下，强调色也必须跟着设置页走。

    编辑器里即使塞了一个 accent（老文件可能还带着），
    渲染时也要被 accent_mode 覆盖——保证"改设置页立即生效"。
    """
    from core import theme_presets as TP
    from ui.theme_manager import build_theme

    monkeypatch.setattr(TP, "user_theme_path",
                        lambda: str(tmp_path / "ut.json"))
    # 模拟老版本留下的、自带 accent 的自定义主题
    TP.save_user_theme({
        "name": "旧主题",
        "light": {"bg": "#ffffff", "accent": "#8b5cf6"},
        "dark": {"bg": "#111111", "accent": "#8b5cf6"},
    })

    # 设置页选「自定义颜色」
    t = build_theme({"theme_mode": "light", "theme_preset": "custom",
                     "accent_mode": "custom", "accent_custom": "#ff3366"})
    assert t.accent == "#ff3366", \
        "设置页选的自定义强调色没生效（被主题文件覆盖了）：%s" % t.accent

    # 设置页选「配色方案自带」→ 此时才用文件里那个
    t2 = build_theme({"theme_mode": "light", "theme_preset": "custom",
                      "accent_mode": "preset"})
    assert t2.accent == "#8b5cf6", \
        "preset 模式下应当使用主题自带的强调色：%s" % t2.accent
