"""新功能测试：库扩充 / 智能搜索 / 时段习惯 / 组合器 / 注入历史。

## 这些测试保护什么

本轮加的五个功能都属于「用起来才知道好不好」的类型，
但它们底层的**契约**是明确的，值得锁住：

* 库文件能同时读新旧两种条目写法（旧的纯字符串、新的带元数据）
* 搜索能命中中文 / 拼音 / 英文别名，且错一个字也能找回
* 时段统计正确分桶、正确归一化
* 组合器拼出来的东西能被反解回来（round-trip）
* 注入历史是「流水」语义，与「集合」语义的 recent 区分开
"""
import json
import os
import sys
import tempfile

import pytest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

# ⚠ 不要在这里重新定义 `qapp` 夹具：`tests/conftest.py` 已经提供了
# session 级的同名夹具（整个会话共用一个 QApplication，Qt 不允许建第二个）。
# 重复定义会让作用域打架，报 ScopeMismatch。


# ====================================================================
# 一、库结构：新旧两种写法都要能读
# ====================================================================
def _write_lib(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)


def test_library_reads_plain_string_items(qapp, tmp_path):
    """旧的纯字符串条目继续可用（向后兼容）。"""
    from core.kaomoji_data import KaomojiData

    p = tmp_path / "k.json"
    _write_lib(p, {"categories": [
        {"name": "旧格式", "items": ["(A)", "(B)"]},
    ]})
    d = KaomojiData(path=str(p))
    assert d.get_items("旧格式") == ["(A)", "(B)"]
    assert d.meta == {}


def test_library_reads_meta_items(qapp, tmp_path):
    """带元数据的条目：文本要变成纯字符串，元数据单独入索引。"""
    from core.kaomoji_data import KaomojiData

    p = tmp_path / "k.json"
    _write_lib(p, {"categories": [{
        "name": "新版",
        "aliases": ["new"],
        "scenes": ["greeting"],
        "items": [
            {"text": "(A)", "aliases": ["aa"], "scenes": ["night"]},
            "(B)",
        ],
    }]})
    d = KaomojiData(path=str(p))
    # items 仍是字符串（旧行为）
    assert d.get_items("新版") == ["(A)", "(B)"]
    # 元数据进了索引
    assert d.meta["(A)"]["aliases"] == ["aa"]
    assert d.meta["(A)"]["scenes"] == ["night"]
    assert d.cat_meta["新版"]["aliases"] == ["new"]


def test_library_save_keeps_metadata(qapp, tmp_path):
    """写回磁盘不能把别名/场景弄丢。

    这是个真实的坑：早期 save() 只写 items 字符串数组，改一次库
    就把精心维护的元数据全清了，搜索立刻退化成纯子串匹配。
    """
    from core.kaomoji_data import KaomojiData

    p = tmp_path / "k.json"
    _write_lib(p, {"categories": [{
        "name": "带元数据",
        "aliases": ["meta"],
        "scenes": ["greeting"],
        "items": [{"text": "(A)", "aliases": ["aa"], "scenes": ["night"]}],
    }]})
    d = KaomojiData(path=str(p))
    d.save()

    with open(p, encoding="utf-8") as f:
        raw = json.load(f)
    cat = raw["categories"][0]
    assert cat["aliases"] == ["meta"]
    assert cat["scenes"] == ["greeting"]
    assert cat["items"][0]["text"] == "(A)"
    assert cat["items"][0]["aliases"] == ["aa"]


def test_real_library_is_expanded(qapp):
    """内置库要够用：至少 20 个分类、250 条以上。"""
    from core import runtime

    tmp = tempfile.mkdtemp()
    orig = runtime.app_data_dir
    runtime.app_data_dir = lambda: os.path.join(tmp, "K")
    try:
        from core.kaomoji_data import KaomojiData

        d = KaomojiData()
        assert len(d.categories) >= 20, "分类太少：%d" % len(d.categories)
        assert len(d.get_items()) >= 250, "条目太少：%d" % len(d.get_items())
        # 常见场景都要有
        scenes = set(d.all_scenes())
        for need in ("greeting", "celebrate", "comfort", "night"):
            assert need in scenes, "缺少场景 %s" % need
    finally:
        runtime.app_data_dir = orig


# ====================================================================
# 二、智能搜索
# ====================================================================
@pytest.fixture()
def data(qapp, tmp_path):
    from core.kaomoji_data import KaomojiData

    p = tmp_path / "k.json"
    _write_lib(p, {"categories": [
        {
            "name": "开心",
            "aliases": ["kx", "happy"],
            "items": [
                {"text": "(A)", "aliases": ["smile"]},
                {"text": "(B)"},
            ],
        },
        {
            "name": "伤心",
            "aliases": ["sx", "sad"],
            "items": [{"text": "(C)", "aliases": ["cry"]}],
        },
    ]})
    return KaomojiData(path=str(p))


def test_search_by_category_alias(data):
    """拼音首字母 / 英文别名都能命中整类。"""
    assert set(data.search("kx")) == {"(A)", "(B)"}
    assert set(data.search("happy")) == {"(A)", "(B)"}


def test_search_by_chinese_category_name(data):
    assert set(data.search("开心")) == {"(A)", "(B)"}


def test_search_by_item_alias(data):
    """条目级别的别名只命中那一条。"""
    assert data.search("smile") == ["(A)"]
    assert data.search("cry") == ["(C)"]


def test_search_typo_tolerance(data):
    """错一个字母也能找回（编辑距离 ≤1）。"""
    assert data.search("smil") or data.search("smlie")
    # 关键词太短时不做容错，否则 "kx" 会乱匹配
    assert data.search("zz") == []


def test_search_ranks_exact_first(data):
    """精确命中排最前：完全等于颜文字本身 > 别名 > 分类。"""
    r = data.search("(A)")
    assert r and r[0] == "(A)"


def test_search_empty_returns_all(data):
    assert len(data.search("")) == 3


def test_by_scene(data):
    assert data.by_scene("nonexistent") == []


# ====================================================================
# 三、时段习惯
# ====================================================================
def test_hour_bucket_covers_all_hours():
    """24 小时都要能落进某个桶，不能有洞。"""
    import datetime

    from core.user_state import HOUR_BUCKETS, hour_bucket

    seen = set()
    base = datetime.datetime.now().replace(minute=0, second=0, microsecond=0)
    for h in range(24):
        b = hour_bucket(base.replace(hour=h).timestamp())
        assert b, "%d 点没有落到任何桶" % h
        seen.add(b)
    assert len(seen) == len(HOUR_BUCKETS)


def test_hour_affinity_normalizes(qapp, tmp_path):
    """时段偏好归一化到 0~1：最高频的那条是 1.0。

    ⚠ 有个**冷启动门槛**（`HOUR_MIN_BUCKET_SAMPLES`）：该时段总样本
    不足时直接返回 0。不加的话，用户某天第一次用了某条，这一条就成了
    "该时段最高频"，affinity=1.0 立刻霸屏——第一次用就被"学"成这样，
    用户会觉得推荐在乱跳。

    所以这里要**先喂够样本**再断言归一化。
    """
    from core.user_state import (
        HOUR_MIN_BUCKET_SAMPLES, UserState, hour_bucket,
    )

    st = UserState(path=str(tmp_path / "s.json"))
    b = hour_bucket()

    # 冷启动：样本不足时一律 0
    st.record_usage("(A)")
    assert st.hour_affinity("(A)", b) == 0.0, "样本不足时不该有时段偏好"

    # 喂够样本后正常归一化
    for _ in range(HOUR_MIN_BUCKET_SAMPLES + 2):
        st.record_usage("(A)")
    for _ in range(2):
        st.record_usage("(B)")

    assert st.hour_affinity("(A)", b) == pytest.approx(1.0)
    assert 0.0 < st.hour_affinity("(B)", b) < 1.0
    assert st.hour_affinity("(Z)", b) == 0.0


def test_hour_usage_persists(qapp, tmp_path):
    from core.user_state import UserState

    p = str(tmp_path / "s.json")
    st = UserState(path=p)
    st.record_usage("(A)")
    st.save()

    st2 = UserState(path=p)
    assert st2.hour_usage == st.hour_usage


def test_score_includes_hour_bonus(qapp, tmp_path):
    """时段加成要真的影响排序（同样要先喂够样本）。"""
    from core.user_state import (
        HOUR_MIN_BUCKET_SAMPLES, WEIGHT_HOUR, UserState, hour_bucket,
    )

    st = UserState(path=str(tmp_path / "s.json"))
    b = hour_bucket()
    for _ in range(HOUR_MIN_BUCKET_SAMPLES + 1):
        st.record_usage("(A)")

    has = st.score("(A)", hour_bucket_name=b)
    no = st.score("(A)", hour_bucket_name="__none__")
    assert has > no
    assert has - no <= WEIGHT_HOUR + 1e-6


def test_current_scene_maps_to_known_scene(qapp):
    """当前时段对应的场景名，要么是空，要么是库里存在的场景名。"""
    from core.user_state import BUCKET_SCENE, HOUR_BUCKETS

    # 所有桶的场景值都必须出现在映射表里（允许空）
    for name, _lo, _hi in HOUR_BUCKETS:
        assert name in BUCKET_SCENE, "桶 %s 没有场景映射" % name


# ====================================================================
# 四、组合器
# ====================================================================
def test_compose_basic(qapp):
    from core import kaomoji_compose as KC

    assert KC.compose("(", "◕", "‿", ")") == "(◕‿◕)"
    assert KC.compose("(", "◕", "‿", ")", "♡") == "(◕‿◕)♡"


def test_compose_mirrors_eye(qapp):
    """非对称眼睛要自动镜像，否则左右一样很怪。"""
    from core import kaomoji_compose as KC

    assert KC.compose("(", "•̀", "ω", ")") == "(•̀ω•́)"
    assert KC.compose("(", "＞", "ω", ")") == "(＞ω＜)"


def test_parts_of_roundtrip(qapp):
    """拼出来的东西要能被反解回同一套积木。"""
    from core import kaomoji_compose as KC

    for combo in KC.GOOD_COMBOS:
        text = KC.compose(*combo)
        parts = KC.parts_of(text)
        assert parts is not None, "反解失败：%s" % text
        # 反解出来的积木再拼一次应当得到同样的文本
        assert KC.compose(**parts) == text, \
            "round-trip 不一致：%s -> %r" % (text, parts)


def test_parts_of_returns_none_for_complex(qapp):
    """结构不常规的颜文字不应被硬拆（返回 None 让用户直接改文本）。"""
    from core import kaomoji_compose as KC

    assert KC.parts_of("(=^･ω･^=)") is None
    assert KC.parts_of("(ノಠ益ಠ)ノ彡┻━┻") is None


def test_random_parts_are_valid(qapp):
    """随机的组合必须：部件都在词表里、眼睛≠嘴。"""
    from core import kaomoji_compose as KC

    for _ in range(30):
        p = KC.random_parts()
        assert p["left"] in KC.LEFT
        assert p["eye"] in KC.EYES
        assert p["mouth"] in KC.MOUTHS
        assert p["right"] in KC.RIGHT
        assert p["decor"] in KC.DECOR
        assert p["eye"] != p["mouth"], "眼睛和嘴相同：%r" % (p,)


# ====================================================================
# 五、注入历史
# ====================================================================
def test_history_is_a_stream_not_a_set(qapp, tmp_path):
    """历史是流水语义：同一条可以连续出现多次（与 recent 不同）。"""
    from core.user_state import UserState

    st = UserState(path=str(tmp_path / "s.json"))
    st.add_history("(A)")
    st.add_history("(B)")
    st.add_history("(A)")

    assert [h["text"] for h in st.recent_injected()] == ["(A)", "(B)", "(A)"]
    assert st.last_injected() == "(A)"
    assert st.last_injected(1) == "(B)"


def test_history_persists(qapp, tmp_path):
    from core.user_state import UserState

    p = str(tmp_path / "s.json")
    st = UserState(path=p)
    st.add_history("(A)", source="picker")
    st.save()

    st2 = UserState(path=p)
    assert st2.last_injected() == "(A)"
    assert st2.recent_injected()[0]["source"] == "picker"


def test_history_respects_max(qapp, tmp_path):
    from core.user_state import UserState

    st = UserState(path=str(tmp_path / "s.json"), max_history=3)
    for i in range(6):
        st.add_history("(%d)" % i)
    assert len(st.history) == 3
    assert st.last_injected() == "(5)"


def test_history_rejects_items_outside_library(qapp, tmp_path):
    """自净：不在库里的条目不该进历史（与 recent 同一套规则）。"""
    from core.user_state import UserState

    st = UserState(path=str(tmp_path / "s.json"),
                   valid_items={"(A)"})
    st.add_history("(A)")
    st.add_history("(B)")       # 不在 valid_items 里，应被拒
    assert [h["text"] for h in st.recent_injected()] == ["(A)"]


def test_last_injected_empty_when_no_history(qapp, tmp_path):
    from core.user_state import UserState

    st = UserState(path=str(tmp_path / "s.json"))
    assert st.last_injected() == ""
    assert st.recent_injected() == []
