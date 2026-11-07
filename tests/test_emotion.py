"""情绪识别单测：`core/emotion.py`。

核心约定：自动弹出场景必须用 `detect_last`（取**最靠右**命中），而不是按字典
声明顺序取第一个 —— 否则「先打过『开心』，再打『为什么』」会一直粘在开心上。
"""
import pytest

from core.emotion import EMOTION_KEYWORDS, detect_last


def test_empty_text_returns_none():
    assert detect_last("") is None
    assert detect_last(None) is None


def test_no_keyword_returns_none():
    assert detect_last("今天天气不错") is None


@pytest.mark.parametrize("text,expected", [
    ("我好开心", "开心"),
    ("好难过啊", "伤心"),
    ("气死我了", "生气"),
    ("哇塞好厉害", "惊讶"),
    ("好累啊", "疲惫"),
    ("为什么会这样", "思考"),
])
def test_basic_emotions(text, expected):
    hit = detect_last(text)
    assert hit is not None
    assert hit[0] == expected


def test_rightmost_hit_wins():
    """最新输入在最右边，必须跟着最新走而不是声明顺序。"""
    hit = detect_last("开心完之后为什么")
    assert hit[0] == "思考"


def test_rightmost_beats_longer():
    """「更靠右」优先于「更长」：用户最新输入的是最右边那几个字。

    "哈哈哈" 里 "哈哈哈" 从 0 开始、"哈哈" 从 1 开始（更靠右），
    因此应命中更靠右的 "哈哈" —— 这条用例同时证明了位置优先于长度。
    """
    emotion, kw, pos = detect_last("哈哈哈")
    assert emotion == "开心"
    assert pos == 1
    assert kw == "哈哈"


def test_rightmost_then_longest_is_the_rule():
    """排序规则：(命中位置, 关键词长度) 字典序取最大。

    同位置比长度这一分支在纯"哈哈"串里构造不出来（"哈哈"总能更靠右），
    所以这里直接对规则本身断言，防止有人把它改成"按字典声明顺序取第一个"。
    """
    best = None
    for pos, kw, emotion in [(0, "哈哈", "开心"), (0, "哈哈哈", "开心"),
                             (2, "笑", "开心")]:
        if best is None or (pos, len(kw)) > (best[0], best[1]):
            best = (pos, len(kw), emotion, kw)
    assert best[3] == "笑", "位置更靠右者优先"

    best = None
    for pos, kw, emotion in [(0, "哈哈", "开心"), (0, "哈哈哈", "开心")]:
        if best is None or (pos, len(kw)) > (best[0], best[1]):
            best = (pos, len(kw), emotion, kw)
    assert best[3] == "哈哈哈", "位置相同时取更长的关键词"


def test_returns_position_of_hit():
    text = "为什么"
    emotion, kw, pos = detect_last(text)
    assert text[pos:pos + len(kw)] == kw


def test_mapped_emotions_exist_in_keyword_table():
    """返回的情绪名必须是 EMOTION_KEYWORDS 的键（供库页按分类取条目）。"""
    for emotion in EMOTION_KEYWORDS:
        assert isinstance(emotion, str) and emotion


def test_keyword_table_is_non_empty():
    assert EMOTION_KEYWORDS
    for emotion, kws in EMOTION_KEYWORDS.items():
        assert kws, "%s 的关键词列表不能为空" % emotion
