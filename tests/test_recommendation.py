"""推荐算法测试：排序评分 + 情绪识别。

## 这轮改了什么（2026-11）

用户反馈"算法不够服从使用习惯"。实测定位到三个问题，本文件逐个钉住：

### A. 排序只看「最近」，不看「最常用」

真实数据里用过 4 次的 `(｡･ω･｡)` 排在第 5 位，只用过 2 次的却排第 1。
→ 改成 `UserState.score()` 加权评分（频率 + 新近度 + 收藏）。

### B. 情绪推荐每次都 `random.shuffle`

顺序随机意味着用户永远无法靠位置记住"我常用的在第几个"。
→ 改用 `UserState.rank()`，顺序稳定。

### C. 疑问词被当情绪，疯狂误弹

「吃饭了吗」「你在干什么」这类普通提问全被判成「思考」。
→ 把关键词分成强/弱信号：弱信号（吗/什么/怎么）只在句尾是问号时生效。
"""
import json
import os

import pytest

from core.emotion import (
    EMOTION_KEYWORDS, WEAK_KEYWORDS, detect_last,
)


# ======================================================================
# 情绪识别：强弱信号
# ======================================================================
@pytest.mark.parametrize("text,expected", [
    # 强信号照常命中
    ("今天真是太开心了", "开心"),
    ("哈哈哈哈笑死我了", "开心"),
    ("这个太好笑了", "开心"),
    ("好难过啊", "伤心"),
    ("呜呜呜想哭", "伤心"),
    ("我破防了", "伤心"),
    ("气死我了", "生气"),
    ("真的很无语", "生气"),
    ("哇塞这也太厉害了吧", "惊讶"),
    ("我的天哪", "惊讶"),
    ("好喜欢这个", "喜欢"),
    ("awsl", "喜欢"),
    ("为什么会这样", "思考"),
    ("今天好累啊", "疲惫"),
    ("困死了要睡了", "疲惫"),
])
def test_strong_signals_still_hit(text, expected):
    hit = detect_last(text)
    assert hit is not None, "「%s」应命中 %s" % (text, expected)
    assert hit[0] == expected


@pytest.mark.parametrize("text", [
    "吃饭了吗",
    "你在干什么",
    "这个怎么用",
    "好吗",
    "明天去不去",
    "今天天气不错",
    "我买了个新手机",
])
def test_plain_questions_do_not_trigger(text):
    """普通提问不该被当成情绪弹窗。

    这是本轮最重要的修复：原先"吗/什么/怎么"是普通关键词，
    任何提问都会触发「思考」情绪，误报极其频繁。
    """
    assert detect_last(text) is None, \
        "「%s」是普通提问，不该触发情绪" % text


def test_weak_signal_works_with_question_mark():
    """弱信号在句尾有问号时仍然生效（保住真正的疑问情绪识别）。"""
    hit = detect_last("这样真的好吗？")
    assert hit is not None
    assert hit[0] == "思考"


def test_strong_signal_beats_weak_regardless_of_position():
    """强信号优先于弱信号，即使弱信号更靠右。

    反例「为什么会这样」里「什么」比「为什么」更靠右；
    若按纯位置优先，会选中弱信号然后因为没问号被丢弃，
    连本来命中的强信号一起丢了。
    """
    hit = detect_last("为什么会这样")
    assert hit is not None
    assert hit[0] == "思考"
    assert hit[1] == "为什么"


def test_no_overly_broad_single_chars():
    """过于宽泛的单字不该作为强信号。"""
    broad = {"乐", "棒", "亲", "爱", "牛", "怒", "乏"}
    all_strong = {k for kws in EMOTION_KEYWORDS.values() for k in kws}
    overlap = broad & all_strong
    assert not overlap, "这些单字误报率太高，应移除：%s" % overlap


def test_weak_keywords_are_separate():
    """弱信号必须放在 WEAK_KEYWORDS 而不是 EMOTION_KEYWORDS。"""
    assert "思考" in WEAK_KEYWORDS
    strong_thinking = EMOTION_KEYWORDS.get("思考", [])
    for w in WEAK_KEYWORDS["思考"]:
        assert w not in strong_thinking, "%r 不该同时出现在强信号里" % w


def test_rightmost_wins_among_strong():
    """强信号之间仍按「最靠右」优先。"""
    hit = detect_last("开心之后有点难过")
    assert hit[0] == "伤心"


# ======================================================================
# 排序评分
# ======================================================================
@pytest.fixture
def state(tmp_path, qapp):
    from core.user_state import UserState

    path = str(tmp_path / "state.json")
    st = UserState(path=path)
    return st


def test_frequency_beats_recency(state):
    """用得多的排前面，即使它「最近」用得更早。

    这是本轮的核心修复：原先只看 recent 顺序，
    最常用的反而被挤到后面。
    """
    state.usage = {"A": 10, "B": 1}
    state.recent = ["B", "A"]          # B 更新，但 A 更常用
    ranked = state.rank(["B", "A"])
    assert ranked[0] == "A", "最常用的应排第一"


def test_recent_breaks_ties(state):
    """频率相同时，更近使用的排前。"""
    state.usage = {"A": 3, "B": 3}
    state.recent = ["B", "A"]
    ranked = state.rank(["A", "B"])
    assert ranked[0] == "B"


def test_favorite_gets_boost(state):
    """收藏的项有额外加成。"""
    state.usage = {"A": 5, "B": 1}
    state.recent = ["A", "B"]
    state.favorites = ["B"]
    ranked = state.rank(["A", "B"])
    assert ranked[0] == "B", "收藏项应被优先推荐"


def test_rank_is_stable_for_equal_scores(state):
    """同分项保持原顺序（稳定排序），避免顺序抖动。"""
    state.usage = {}
    state.recent = []
    items = ["X", "Y", "Z"]
    assert state.rank(items) == items
    # 多次调用结果一致
    assert state.rank(items) == state.rank(items)


def test_score_is_zero_for_unknown(state):
    assert state.score("从未用过的") == 0.0


def test_log_compression_prevents_dominance(state):
    """对数压缩：用过 200 次的项不该把其它全压成 0。"""
    state.usage = {"hot": 200, "cold": 1}
    sh = state.score("hot")
    sc = state.score("cold")
    assert sh > sc
    # 差距存在但不悬殊（线性的话会是 200 倍）
    assert sh < sc * 10


# ======================================================================
# 与候选条集成
# ======================================================================
def test_picker_manual_items_uses_rank(qapp, tmp_path):
    """手动唤起的候选顺序必须走 rank()，不再只按 recent。"""
    from core.kaomoji_data import KaomojiData
    from core.user_state import UserState
    from ui.picker_window import PickerWindow

    d = str(tmp_path / "d")
    os.makedirs(d, exist_ok=True)
    st = UserState(path=os.path.join(d, "s.json"))

    data = KaomojiData()
    all_items = data.get_items()
    assert len(all_items) >= 3
    a, b = all_items[0], all_items[1]

    # A 用过很多次但最近一次更早；B 只用过 1 次但最近
    st.usage = {a: 20, b: 1}
    st.recent = [b, a]

    w = PickerWindow(data, {}, st, None)
    try:
        items = w._manual_items()
        assert items[0] == a, "最常用的应排在第一"
    finally:
        w.deleteLater()


def test_picker_emotion_order_is_deterministic(qapp, tmp_path):
    """情绪推荐顺序必须稳定（不再 random.shuffle）。"""
    from core.kaomoji_data import KaomojiData
    from core.user_state import UserState
    from ui.picker_window import PickerWindow

    d = str(tmp_path / "d2")
    os.makedirs(d, exist_ok=True)
    st = UserState(path=os.path.join(d, "s.json"))
    data = KaomojiData()

    w = PickerWindow(data, {}, st, None)
    try:
        orders = []
        for _ in range(5):
            # 直接调用排序部分（避免依赖 UI 显示）
            items = data.get_items("开心")
            orders.append(st.rank(items))
        for o in orders[1:]:
            assert o == orders[0], "情绪推荐顺序应稳定，不能每次不同"
    finally:
        w.deleteLater()


def test_user_state_persists_last_used(tmp_path, qapp):
    """last_used 要能落盘并重新读回（为将来的时间衰减留数据）。"""
    from core.user_state import UserState

    path = str(tmp_path / "s2.json")
    st = UserState(path=path)
    st.record_usage("(^_^)")
    st.flush()

    raw = json.load(open(path, encoding="utf-8"))
    assert "last_used" in raw
    assert "(^_^)" in raw["last_used"]

    st2 = UserState(path=path)
    assert "(^_^)" in st2.last_used


def test_old_state_file_without_last_used_loads(tmp_path, qapp):
    """旧版本没有 last_used 字段，加载不能崩。"""
    from core.user_state import UserState

    path = str(tmp_path / "old.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"recent": ["(^_^)"], "favorites": [], "usage": {"(^_^)": 2}}, f)

    st = UserState(path=path)
    assert st.recent == ["(^_^)"]
    assert st.usage == {"(^_^)": 2}
    assert st.last_used == {}
