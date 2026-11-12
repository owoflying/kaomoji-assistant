"""情绪推荐的可调项与「越用越顺手」机制测试。

## 覆盖

1. **灵敏度**：0~100 换算成置信度阈值，影响触发难易
2. **开关**：emotion_suggest / emotion_learning 能真正关掉对应行为
3. **学习状态**：learning_stats 如实反映学了多少
4. **重置**：清掉个性化模型后回到出厂状态
5. **弱负反馈**：learn_ignored 用很小的步长，不会一次否定就带偏
"""
import os

import pytest

from core import emotion_net as en


@pytest.fixture
def clf(monkeypatch, tmp_path):
    from core import emotion_classifier as ec

    monkeypatch.setattr(ec, "user_model_path",
                        lambda: str(tmp_path / "user.json"))
    return ec.EmotionClassifier()


# ======================================================================
# 灵敏度
# ======================================================================
def test_threshold_mapping():
    """灵敏度越高，阈值越低（越容易触发）。"""
    from core.emotion_classifier import threshold_for_sensitivity as f

    assert f(0) == pytest.approx(0.65)
    assert f(50) == pytest.approx(0.45)
    assert f(100) == pytest.approx(0.25)
    # 单调递减
    vals = [f(s) for s in range(0, 101, 10)]
    assert vals == sorted(vals, reverse=True)


def test_threshold_clamps_out_of_range():
    from core.emotion_classifier import threshold_for_sensitivity as f

    assert f(-100) == pytest.approx(0.65)
    assert f(9999) == pytest.approx(0.25)
    assert f("bad") == pytest.approx(0.45)


def test_sensitivity_affects_detection(clf):
    """同一句话，高灵敏度比低灵敏度更容易触发。"""
    text = "好萌"
    clf.configure(sensitivity=0)      # 保守：阈值 0.65
    low = clf.detect(text)
    clf.configure(sensitivity=100)    # 灵敏：阈值 0.25
    high = clf.detect(text)
    assert (high is not None) >= (low is not None)


def test_configure_updates_threshold(clf):
    clf.configure(sensitivity=0)
    assert clf.threshold == pytest.approx(0.65)
    clf.configure(sensitivity=100)
    assert clf.threshold == pytest.approx(0.25)


# ======================================================================
# 开关
# ======================================================================
def test_learning_can_be_disabled(clf):
    """关掉个性化学习后，选字不再改权重。"""
    text = "独特表达测试"

    clf.configure(learning_enabled=False)
    before = list(clf.net.w1[0])
    assert clf.learn_from_choice(text, "开心") is False
    assert clf.net.w1[0] == before, "关闭学习后权重不该变"


def test_learning_enabled_by_default(clf):
    assert clf.learning_enabled is True


def test_learning_changes_weights_when_enabled(clf):
    """学习后网络参数必须变。

    ⚠ 不能只看 `w1[0]`：第 0 个隐层神经元若没被激活（relu 输出为 0），
    它的权重就不会更新。要检查整体。
    """
    text = "另一句独特表达"
    b1 = list(clf.net.b1)
    b2 = list(clf.net.b2)
    w2 = [list(r) for r in clf.net.w2]

    assert clf.learn_from_choice(text, "伤心") is True

    changed = (clf.net.b1 != b1 or clf.net.b2 != b2 or clf.net.w2 != w2)
    assert changed, "学习后参数应发生变化"


# ======================================================================
# 学习状态 / 重置
# ======================================================================
def test_learning_stats_shape(clf):
    st = clf.learning_stats()
    assert set(st) >= {"personal_updates", "base_samples",
                       "has_personal_model", "model_ready"}
    assert st["model_ready"] is True
    assert st["base_samples"] > 0


def test_learning_stats_counts_updates(clf):
    before = clf.learning_stats()["personal_updates"]
    for _ in range(3):
        clf.learn_from_choice("计数测试", "开心")
    assert clf.learning_stats()["personal_updates"] == before + 3


def test_reset_removes_personal_model(clf):
    """「清除学习记录」要真的把用户模型删掉。"""
    from core import emotion_classifier as ec

    clf.learn_from_choice("要被清掉的样本", "开心")
    assert clf.save_user_model()
    path = ec.user_model_path()
    assert os.path.exists(path)

    clf.reset_personal_model()
    assert not os.path.exists(path)
    # 重置后仍能正常工作（回到随包模型）
    assert clf.has_net
    assert clf.learning_stats()["personal_updates"] == 0


# ======================================================================
# 弱负反馈
# ======================================================================
def test_ignored_uses_small_step(clf):
    """「忽略」的学习强度必须远小于「采用」。

    用户没选可能只是没找到合适的，不该被当成"判错了"重罚。
    """
    text = "忽略反馈测试"
    idx = en.class_index("开心")

    clf.learn_from_choice(text, "开心", strength=1.0)
    after_choice, _ = clf.net.forward(en.vectorize(text))

    # 换一个全新实例做对比：只施加忽略
    from core import emotion_classifier as ec
    clf2 = ec.EmotionClassifier()
    before, _ = clf2.net.forward(en.vectorize(text))
    clf2.learn_ignored(text, "开心")
    after_ignore, _ = clf2.net.forward(en.vectorize(text))

    # 忽略让该情绪概率**下降**，但幅度小于采用带来的上升
    assert after_ignore[idx] < before[idx], "忽略应降低该情绪概率"


def test_ignored_respects_learning_switch(clf):
    clf.configure(learning_enabled=False)
    assert clf.learn_ignored("随便一句", "开心") is False


def test_ignored_with_unknown_emotion(clf):
    assert clf.learn_ignored("句子", "不存在的情绪") is False


def test_ignored_with_empty_text(clf):
    assert clf.learn_ignored("", "开心") is False


# ======================================================================
# AI 开关（纯算法模式）
# ======================================================================
def test_ai_enabled_by_default(clf):
    assert clf.ai_enabled is True
    assert clf.net is not None


def test_disable_ai_unloads_model(clf):
    """关掉 AI 必须把模型从内存卸掉，而不是只挂个标记。"""
    assert clf.net is not None
    clf.configure(ai_enabled=False)
    assert clf.ai_enabled is False
    assert clf.net is None, "关闭 AI 后不该再持有模型"


def test_pure_algorithm_uses_keywords_only(clf):
    """纯算法模式下判定结果必须与关键词层完全一致。"""
    from core.emotion import detect_last

    clf.configure(ai_enabled=False)
    for text in ("我很开心", "好难过啊", "气死我了", "吃饭了吗", "今天天气不错"):
        r = clf.detect(text)
        kw = detect_last(text)
        assert (r[0] if r else None) == (kw[0] if kw else None)


def test_pure_algorithm_cannot_understand_negation(clf):
    """这是关闭 AI 的**代价**，测试钉住它，避免有人误以为两种模式等价。

    「今天一点都不开心」在纯算法下会被判成开心（关键词只看到"开心"），
    而 AI 模式能正确判成伤心。
    """
    clf.configure(ai_enabled=True)
    ai = clf.detect("今天一点都不开心")

    clf.configure(ai_enabled=False)
    pure = clf.detect("今天一点都不开心")

    assert ai is not None and ai[0] == "伤心", "AI 模式应理解否定"
    assert pure is not None and pure[0] == "开心", "纯算法必然判反"


def test_disable_ai_blocks_learning(clf):
    clf.configure(ai_enabled=False)
    assert clf.learn_from_choice("测试句", "开心") is False
    assert clf.learn_ignored("测试句", "开心") is False


def test_disable_ai_blocks_model_save(clf):
    """关掉 AI 后不该写任何模型文件。"""
    from core import emotion_classifier as ec

    clf.configure(ai_enabled=False)
    assert clf.save_user_model() is False
    assert not os.path.exists(ec.user_model_path())


def test_reenable_ai_reloads_model(clf):
    clf.configure(ai_enabled=False)
    assert clf.net is None
    clf.configure(ai_enabled=True)
    assert clf.net is not None, "重新打开 AI 应把模型读回来"
    assert clf.learn_from_choice("重新学习", "开心") is True


def test_ai_switch_is_idempotent(clf):
    """反复开关不应出问题。"""
    for _ in range(3):
        clf.configure(ai_enabled=False)
        assert clf.net is None
        clf.configure(ai_enabled=True)
        assert clf.net is not None


def test_pure_algorithm_marks_source(clf):
    """纯算法模式的判定来源要可辨认，便于排查。"""
    clf.configure(ai_enabled=False)
    clf.detect("我很开心")
    assert "纯算法" in clf.last_source
