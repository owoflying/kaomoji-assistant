# -*- coding: utf-8 -*-
"""回归测试：「我的颜文字」组合按钮 + 标签参与情绪推荐。

两个真实 bug：
1. `CustomKaomojiPage` 没有 `_theme` 属性，点「组合一个」抛 AttributeError，
   PySide6 把槽函数里的异常吞掉，用户只看到"按钮按了没反应"；
2. 带情绪标签的自定义颜文字被追加到内置分类（20~30 条）后面，
   `rank()` 稳定排序后沉到第 11 页，默认每页 3 条永远看不到。
"""
import json
import os

import pytest

CUSTOM = "(◕‿◕)♡"
COMPOSED = "(◕‿◕)"


def _user_kao(tmp_path, items=()):
    from core.user_kaomoji import UserKaomoji
    uk = UserKaomoji(path=os.path.join(str(tmp_path), "uk.json"))
    for text, tags in items:
        uk.add_item(text, "默认", tags)
    return uk


@pytest.fixture
def fake_compose(monkeypatch):
    """把组合器换成不弹模态框的假对话框，并记录收到的主题。"""
    from PySide6.QtWidgets import QDialog
    import ui.compose_dialog as cd

    class FakeCompose:
        themes = []
        texts = []

        def __init__(self, theme=None, initial="", parent=None):
            FakeCompose.themes.append(theme)
            FakeCompose.texts.append(initial)

        def exec(self):
            return QDialog.Accepted

        def text(self):
            return COMPOSED

    FakeCompose.themes = []
    FakeCompose.texts = []
    monkeypatch.setattr(cd, "ComposeDialog", FakeCompose)
    return FakeCompose


@pytest.fixture
def classifier(monkeypatch):
    """给标签归类一个干净的分类器实例，不依赖全局单例被谁改过。"""
    from core import emotion_classifier as ec

    clf = ec.EmotionClassifier()
    monkeypatch.setattr(ec, "_shared", clf)
    ec.clear_tag_emotion_cache()
    yield clf
    ec.clear_tag_emotion_cache()


# ---------------------------------------------------------------- 组合按钮
def test_compose_button_adds_item(qapp, tmp_path, fake_compose):
    """主按钮：不抛异常，且拼出来的颜文字真的进了当前分组。"""
    from ui.pages.custom_kaomoji_page import CustomKaomojiPage

    uk = _user_kao(tmp_path)
    page = CustomKaomojiPage(uk)
    try:
        page._compose_and_add()          # 等价于点「组合一个」
        assert COMPOSED in uk.get_all()
        assert len(fake_compose.themes) == 1
    finally:
        page.deleteLater()


def test_compose_theme_survives_theme_broadcast(qapp, tmp_path, fake_compose):
    """换肤广播之后组合器仍能拿到主题，不会退回缺属性崩溃。"""
    from ui.pages.custom_kaomoji_page import CustomKaomojiPage
    from ui.win11_theme import Theme

    uk = _user_kao(tmp_path)
    page = CustomKaomojiPage(uk)
    try:
        page.set_theme(Theme("dark"))
        page._compose_and_add()
        assert getattr(fake_compose.themes[-1], "name", None) == "dark"
        assert page.group_combo._theme.name == "dark"
        assert page.tag_combo._theme.name == "dark"
        assert COMPOSED in uk.get_all()
    finally:
        page.deleteLater()


def test_edit_dialog_compose_entry(qapp, fake_compose):
    """新增/编辑对话框里的「组合…」入口同样能回填预览。"""
    from ui.pages.custom_kaomoji_page import _ItemEditDialog

    dlg = _ItemEditDialog(["默认"], text="(^_^)", theme="light")
    try:
        dlg._open_composer()
        assert dlg.text_edit.text() == COMPOSED
        assert fake_compose.texts[-1] == "(^_^)"
    finally:
        dlg.deleteLater()


# ---------------------------------------------------------------- 标签推荐
def test_tagged_item_is_on_first_page_of_emotion_candidates(qapp, tmp_path):
    """情绪推荐第一页必须能看到打了该情绪标签的自定义颜文字。"""
    from core.kaomoji_data import KaomojiData
    from core.user_state import UserState
    from ui.picker_window import PickerWindow

    uk = _user_kao(tmp_path, [(CUSTOM, "开心")])
    data = KaomojiData()
    state = UserState(path=os.path.join(str(tmp_path), "st.json"))
    w = PickerWindow(data, {"page_size": 3}, state, uk)
    captured = {}
    w._present = lambda items, **kw: captured.update(items=list(items))
    try:
        w.show_for_emotion("开心", "今天好开心")
        items = captured["items"]
        assert items.index(CUSTOM) < 3, "标签项应出现在第一页，而不是沉在内置库后面"
        # 内置分类的候选不能被挤掉
        assert set(data.get_items("开心")).issubset(set(items))
    finally:
        w.shutdown()
        w.deleteLater()


def test_untagged_items_are_not_forced_into_emotion_candidates(qapp, tmp_path,
                                                               classifier):
    """没打情绪标签的自定义颜文字不该混进情绪推荐（只保留内置分类）。"""
    from core.kaomoji_data import KaomojiData
    from core.user_state import UserState
    from ui.picker_window import PickerWindow

    uk = _user_kao(tmp_path, [("(*´∀`*)", "加油")])
    data = KaomojiData()
    state = UserState(path=os.path.join(str(tmp_path), "st.json"))
    w = PickerWindow(data, {"page_size": 3}, state, uk)
    captured = {}
    w._present = lambda items, **kw: captured.update(items=list(items))
    try:
        w.show_for_emotion("开心", "好开心")
        assert "(*´∀`*)" not in captured["items"]
    finally:
        w.shutdown()
        w.deleteLater()


def test_items_for_emotion_uses_wordlist_and_classifier(qapp, tmp_path,
                                                        classifier):
    """词表精确匹配 + 分类器修正，都不做子串匹配。"""
    uk = _user_kao(tmp_path, [
        ("(A)", "开心"),
        ("(B)", "happy"),
        ("(C)", "高兴"),
        ("(D)", "不开心"),     # 分类器修正为伤心，不是「开心」的子串命中
        ("(E)", "加油"),       # 自由标签，不触发任何情绪
        ("(F)", "好累"),
    ])
    assert set(uk.items_for_emotion("开心")) == {"(A)", "(B)", "(C)"}
    assert uk.items_for_emotion("伤心") == ["(D)"]
    assert uk.items_for_emotion("疲惫") == ["(F)"]
    assert uk.items_for_emotion("生气") == []
    assert "(E)" not in uk.items_for_emotion("喜欢")


def test_legacy_string_tags_are_normalized_on_load(qapp, tmp_path, classifier):
    """旧数据里 tags 是字符串时，界面显示与情绪匹配都不能碎成单字。"""
    from core.user_kaomoji import UserKaomoji

    path = tmp_path / "legacy.json"
    path.write_text(json.dumps({
        "groups": ["默认"],
        "items": [{"text": CUSTOM, "group": "默认", "tags": "开心, 加油"}],
    }, ensure_ascii=False), encoding="utf-8")
    uk = UserKaomoji(path=str(path))
    assert uk.tags_of(CUSTOM) == ["开心", "加油"]
    assert CUSTOM in uk.items_for_emotion("开心")


# ------------------------------------------- 复用情绪算法 / 神经网络解析标签
def test_tag_classifier_handles_negation_turn_and_implicit(classifier):
    """复用分类器最值钱的能力：否定 / 转折 / 隐式情绪。"""
    from core.emotion_classifier import emotion_for_tag

    assert emotion_for_tag("不开心") == "伤心"       # 关键词层必然判反
    assert emotion_for_tag("笑不出来") == "伤心"
    assert emotion_for_tag("虽然很累但是很开心") == "开心"
    assert emotion_for_tag("戳我") == "喜欢"         # 隐式情绪词
    assert emotion_for_tag("我没生气") is None       # 否认情绪，不该关联


def test_tag_classifier_rejects_unreliable_net_guesses(classifier):
    """短标签上的纯网络外推不能采纳：实测这些词会被高置信判错。"""
    from core.emotion_classifier import emotion_for_tag

    # 随包模型实测：加油→疲惫、打气→生气、猫猫→疲惫、早安→喜欢、
    # 摸鱼→伤心、摸摸头→思考、安慰→喜欢（置信 0.64~1.00，语义全不对）
    for tag in ("加油", "打气", "猫猫", "早安", "摸鱼", "摸摸头", "安慰", "随便"):
        assert emotion_for_tag(tag) is None, "%s 不该被硬塞进某个情绪" % tag


def test_tag_classifier_does_not_touch_shared_diagnostics(classifier):
    """标签归类不能污染打字判定的诊断字段与灵敏度阈值。"""
    from core import emotion_classifier as ec

    clf = classifier
    clf.last_source, clf.last_conf, clf.threshold = "SENTINEL", 0.123, 0.65
    assert ec.emotion_for_tag("不开心") == "伤心"
    assert (clf.last_source, clf.last_conf, clf.threshold) == (
        "SENTINEL", 0.123, 0.65)


def test_tag_classifier_pure_algorithm_when_ai_off(classifier):
    """关掉「用 AI 判断情绪」时退化为纯关键词层，不推理模型。"""
    from core import emotion_classifier as ec

    classifier.configure(ai_enabled=False)
    ec.clear_tag_emotion_cache()
    assert ec.emotion_for_tag("开心") == "开心"
    assert ec.emotion_for_tag("不开心") == "开心"   # 纯算法模式的已知行为
    assert ec.emotion_for_tag("加油") is None
    assert classifier.net is None


def test_tag_classifier_result_is_cached(classifier):
    """标签少且稳定，归类结果要缓存，自动弹出时不能每次都跑一遍推理。"""
    from core import emotion_classifier as ec

    ec.clear_tag_emotion_cache()
    ec.emotion_for_tag("笑死")
    ec.emotion_for_tag("笑死")
    assert ec._emotion_for_tag_cached.cache_info().hits >= 1


def test_negated_tag_item_leads_sad_candidates(qapp, tmp_path, classifier):
    """端到端：标签「不开心」的颜文字要出现在伤心候选第一页。"""
    from core.kaomoji_data import KaomojiData
    from core.user_state import UserState
    from ui.picker_window import PickerWindow

    uk = _user_kao(tmp_path, [(CUSTOM, "不开心")])
    data = KaomojiData()
    state = UserState(path=os.path.join(str(tmp_path), "st.json"))
    w = PickerWindow(data, {"page_size": 3}, state, uk)
    captured = {}
    w._present = lambda items, **kw: captured.update(items=list(items))
    try:
        w.show_for_emotion("伤心", "今天一点都不开心")
        assert captured["items"] and captured["items"][0] == CUSTOM
        # 同一个标签不该同时混进开心候选
        captured.clear()
        w.show_for_emotion("开心", "好开心")
        assert CUSTOM not in captured["items"]
    finally:
        w.shutdown()
        w.deleteLater()
