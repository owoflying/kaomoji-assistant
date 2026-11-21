"""「从聊天记录提取颜文字」的测试。

## 核心风险

这个功能的成败**全在识别准确率**上：

* 漏抽 → 用户觉得"这功能没用"
* 抽错 → 用户收藏了一堆「（真的吗）」这种垃圾，比漏抽更招人烦

所以测试的重点是**负面样本**：普通中文括号内容、英文括号、
价格标注都不能被当成颜文字。这类样本比正面样本更能暴露问题。
"""
import os
import sys

import pytest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)


# ====================================================================
# 一、识别器：正面样本
# ====================================================================
@pytest.mark.parametrize("text,expect", [
    ("今天好累啊 (´-ω-`) 想睡觉", "(´-ω-`)"),
    ("哈哈哈哈 ヽ(≧∇≦)ノ 笑死我了", "ヽ(≧∇≦)ノ"),
    ("嗯…(￣▽￣; ) 你说的对", "(￣▽￣; )"),
    ("(๑•̀ㅂ•́)و✧ 加油！", "(๑•̀ㅂ•́)و✧"),
    ("(๑˃ᴗ˂)ﻭ 给你", "(๑˃ᴗ˂)ﻭ"),
    ("谢谢 (｡◕‿◕｡)❤", "(｡◕‿◕｡)❤"),
    ("¯\\_(ツ)_/¯ 不知道", None),        # 只要求能抽到，不要求精确相等
])
def test_extracts_kaomoji(text, expect):
    from core.kaomoji_extract import extract

    got = extract(text)
    assert got, "没能从 %r 里抽出颜文字" % text
    if expect is not None:
        assert got[0] == expect, "抽出的是 %r，期望 %r" % (got[0], expect)


def test_extracts_multiple():
    from core.kaomoji_extract import extract

    got = extract("今天好累 (´-ω-`) 明天见 ヽ(≧∇≦)ノ 还有 (◕‿◕)")
    assert len(got) == 3
    assert "(´-ω-`)" in got and "(◕‿◕)" in got


def test_extracts_from_long_message():
    """长消息里也要能抽到（只看前几条，不要求全部）。"""
    from core.kaomoji_extract import extract

    text = ("哈哈哈" * 100) + " (◕‿◕) " + ("啊啊啊" * 100)
    assert "(◕‿◕)" in extract(text)


# ====================================================================
# 二、识别器：负面样本（更重要）
# ====================================================================
@pytest.mark.parametrize("text", [
    "（真的吗）",            # 中文括号内容
    "（笑）",                # 单字
    "好的(明天见)",           # 中文短语
    "价格(100元)",           # 数字+量词
    "(hello world)",         # 纯英文
    "(café)",                # 拉丁扩展字母
    "这是普通的一段中文，没有任何表情。",
    "第一点(1)第二点(2)",     # 编号
    "（注：以上内容仅供参考）",
])
def test_does_not_extract_normal_text(text):
    from core.kaomoji_extract import extract

    got = extract(text)
    assert got == [], "把普通内容当颜文字了：%r -> %r" % (text, got)


def test_empty_input():
    from core.kaomoji_extract import extract

    assert extract("") == []
    assert extract(None) == []
    assert extract("   \n\t ") == []


def test_ignores_very_long_segments():
    """超长的一定不是颜文字，不该被收进来。"""
    from core.kaomoji_extract import extract

    long_inner = "◕" * 200
    assert extract("(%s)" % long_inner) == []


def test_returns_at_most_max_items():
    from core.kaomoji_extract import extract

    text = " ".join("(◕%d◕)" % i for i in range(50))
    assert len(extract(text, max_items=5)) <= 5


def test_deduplicates():
    from core.kaomoji_extract import extract

    got = extract("(◕‿◕) 和 (◕‿◕) 是同一个")
    assert len(got) == 1


# ====================================================================
# 三、弹窗与剪贴板
# ====================================================================
def test_read_clipboard_items(qapp):
    from ui.extract_dialog import read_clipboard_items

    qapp.clipboard().setText("今天好累 (´-ω-`) 明天见")
    items, raw = read_clipboard_items()
    assert items == ["(´-ω-`)"]
    assert "今天好累" in raw


def test_read_clipboard_empty(qapp):
    from ui.extract_dialog import read_clipboard_items

    qapp.clipboard().setText("")
    items, raw = read_clipboard_items()
    assert items == []
    assert raw == ""


def test_dialog_preexisting_unchecked(qapp):
    """已在库中的条目不默认勾选，避免重复收藏。"""
    from ui.extract_dialog import ExtractDialog

    items = ["(A样)", "(B样)"]
    dlg = ExtractDialog(items, preexisting={"(A样)"})
    assert "(A样)" not in dlg.selected()
    assert "(B样)" in dlg.selected()


def test_dialog_select_all_skips_preexisting(qapp):
    from ui.extract_dialog import ExtractDialog

    dlg = ExtractDialog(["(A样)", "(B样)"], preexisting={"(A样)"})
    dlg._set_all(True)
    assert dlg.selected() == ["(B样)"]


def test_dialog_select_none(qapp):
    from ui.extract_dialog import ExtractDialog

    dlg = ExtractDialog(["(A样)", "(B样)"])
    dlg._set_all(False)
    assert dlg.selected() == []


# ====================================================================
# 四、与去重工具
# ====================================================================
def test_dedupe_against():
    from core.kaomoji_extract import dedupe_against

    got = dedupe_against(["(A)", "(B)", "(C)"], ["(A)", "(C)"])
    assert got == ["(B)"]


def test_dedupe_against_empty():
    from core.kaomoji_extract import dedupe_against

    assert dedupe_against(["(A)"], []) == ["(A)"]
    assert dedupe_against([], ["(A)"]) == []
