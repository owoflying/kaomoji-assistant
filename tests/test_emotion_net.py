"""神经网络情绪分类器测试。

## 为什么要有网络（而不是只用关键词）

关键词匹配看的是"句子里有没有某个词"，看不到**词的组合与顺序**：

    今天一点都不开心   →  关键词看到"开心"，判成开心（人知道是不开心）
    我不开心           →  同上，关键词判反

网络把整句压成一个向量再判断，能学到「否定词 + 情绪词」这种组合模式。
这是"整体思考"而非"逐字匹配"——本文件钉住这个能力。

## 三层要测的东西

    1. `emotion_net`       模型本身：结构、推理、训练、序列化
    2. `emotion_classifier` 协作层：网络与关键词怎么配合
    3. 在线学习            用户的选择如何反过来调整模型
"""
import json
import os

import pytest

from core import emotion_net as en


# ======================================================================
# 模型本身
# ======================================================================
def test_model_is_tiny():
    """模型必须足够小：参数量与文件体积都在可接受范围。

    实测 4487 参数、92KB JSON。这个规模下推理 0.7ms，
    对候选条（本身几十毫秒）完全无感——"神经网络会很慢"在这里不成立。
    """
    net = en.EmotionNet()
    params = (net.n_in * net.n_hidden + net.n_hidden
              + net.n_hidden * net.n_out + net.n_out)
    assert params < 10000, "参数量应保持在万以内，否则推理会变慢"


def test_vectorize_is_fixed_length():
    """不同长度的句子都要编码成同样维度的向量。"""
    for text in ["", "哈", "今天一点都不开心", "很长" * 50]:
        v = en.vectorize(text)
        assert len(v) == en.INPUT_DIM


def test_vectorize_captures_negation_flag():
    """含否定词的句子，否定特征位必须被点亮。"""
    base = en.UNIGRAM_BUCKETS + en.BIGRAM_BUCKETS
    v_neg = en.vectorize("我不开心")
    v_pos = en.vectorize("我很开心")
    assert v_neg[base + 0] == 1.0, "否定特征未点亮"
    assert v_pos[base + 0] == 0.0


def test_vectorize_captures_question_mark():
    base = en.UNIGRAM_BUCKETS + en.BIGRAM_BUCKETS
    assert en.vectorize("为什么？")[base + 3] == 1.0
    assert en.vectorize("为什么")[base + 3] == 0.0


def test_vectorize_bigram_distinguishes_context():
    """"不开心" 与 "开心" 的特征向量必须不同（bigram 捕捉组合）。"""
    a = en.vectorize("不开心")
    b = en.vectorize("开心")
    assert a != b


def test_forward_returns_probabilities():
    net = en.EmotionNet()
    probs, hid = net.forward(en.vectorize("测试"))
    assert len(probs) == len(en.CLASSES)
    assert abs(sum(probs) - 1.0) < 1e-6, "softmax 输出应归一化"
    assert len(hid) == en.HIDDEN


def test_train_reduces_loss():
    """训练应当有效：同一批样本反复训练，损失下降、预测变准。"""
    net = en.EmotionNet()
    text, idx = "我真的很开心", en.class_index("开心")

    first_loss = net.train_one(text, idx, lr=0.1)
    for _ in range(30):
        last_loss = net.train_one(text, idx, lr=0.1)

    assert last_loss < first_loss, "训练没有让损失下降"
    got, _ = net.predict(text)
    assert got == "开心"


def test_train_moves_in_right_direction():
    """训练后目标类别的概率应上升。"""
    net = en.EmotionNet()
    x = en.vectorize("好难过啊")
    idx = en.class_index("伤心")
    before = net.forward(x)[0][idx]
    for _ in range(20):
        net.train_one("好难过啊", idx, lr=0.1)
    after = net.forward(x)[0][idx]
    assert after > before


# ======================================================================
# 序列化
# ======================================================================
def test_save_and_load_roundtrip(tmp_path):
    net = en.EmotionNet()
    for _ in range(10):
        net.train_one("好开心", en.class_index("开心"), lr=0.1)

    path = str(tmp_path / "m.json")
    assert net.save(path)

    loaded = en.EmotionNet.load(path)
    assert loaded is not None
    p1, _ = net.forward(en.vectorize("好开心"))
    p2, _ = loaded.forward(en.vectorize("好开心"))
    for a, b in zip(p1, p2):
        assert abs(a - b) < 1e-9


def test_load_rejects_wrong_version(tmp_path):
    """结构变了（版本不符）时必须拒绝加载，让调用方回退，而不是崩溃。"""
    path = str(tmp_path / "bad.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"version": 999, "classes": list(en.CLASSES)}, f)
    assert en.EmotionNet.load(path) is None


def test_load_rejects_corrupt_file(tmp_path):
    path = str(tmp_path / "corrupt.json")
    with open(path, "w", encoding="utf-8") as f:
        f.write("not json at all")
    assert en.EmotionNet.load(path) is None


def test_load_missing_file_returns_none(tmp_path):
    assert en.EmotionNet.load(str(tmp_path / "nope.json")) is None


# ======================================================================
# 协作层：网络 + 关键词
# ======================================================================
@pytest.fixture
def clf(monkeypatch, tmp_path):
    """用随包模型构造分类器（不读用户模型，保证测试可重复）。"""
    from core import emotion_classifier as ec

    monkeypatch.setattr(ec, "user_model_path",
                        lambda: str(tmp_path / "user.json"))
    c = ec.EmotionClassifier()
    return c


def test_classifier_loads_bundled_model(clf):
    assert clf.has_net, "随包模型应能加载"


@pytest.mark.parametrize("text,expected", [
    # 否定句：关键词必然判错，网络必须救回来
    ("今天一点都不开心", "伤心"),
    ("我不开心", "伤心"),
    ("我一点也不高兴", "伤心"),
    # 正常句
    ("我很开心", "开心"),
    ("好难过啊", "伤心"),
    ("气死我了", "生气"),
    ("哈哈哈哈哈", "开心"),
    # 转折：取后半句
    ("虽然很累但是很开心", "开心"),
])
def test_negation_and_turn(clf, text, expected):
    r = clf.detect(text)
    assert r is not None, "「%s」应命中" % text
    assert r[0] == expected


@pytest.mark.parametrize("text", [
    "吃饭了吗",
    "你在干什么",
    "今天天气不错",
    "今天心情不错",
])
def test_chitchat_does_not_trigger(clf, text):
    """闲聊不该弹窗。

    "不错"里的"不"不是否定（固定搭配），不能当作否定句处理，
    否则会走网络的否定分支乱判。
    """
    assert clf.detect(text) is None, "「%s」是闲聊，不该触发" % text


def test_false_negation_words_are_excluded(clf):
    """固定搭配里的"不"不算否定。"""
    assert clf._has_negation("今天天气不错") is False
    assert clf._has_negation("不好意思") is False
    assert clf._has_negation("我不开心") is True


def test_classifier_falls_back_without_net(monkeypatch, tmp_path):
    """模型加载失败时，关键词层必须顶上（不能让功能整个失效）。"""
    from core import emotion_classifier as ec

    monkeypatch.setattr(ec.en.EmotionNet, "load",
                        classmethod(lambda c, p: None))
    monkeypatch.setattr(ec, "user_model_path",
                        lambda: str(tmp_path / "user.json"))
    c = ec.EmotionClassifier()
    assert not c.has_net
    # 关键词仍能工作
    r = c.detect("哈哈哈哈哈")
    assert r is not None and r[0] == "开心"
    assert c.last_source == "keyword"


def test_detect_returns_detect_last_compatible_tuple(clf):
    """返回结构必须与 detect_last 一致（emotion, kw, pos），兼容既有调用方。"""
    r = clf.detect("哈哈哈哈哈")
    assert isinstance(r, tuple) and len(r) == 3
    assert isinstance(r[0], str)


# ======================================================================
# 在线学习
# ======================================================================
def test_online_learning_updates_weights(clf):
    """用户的选择会真的改变模型（持续适应用户习惯）。"""
    text = "测试用的句子"
    before, _ = clf.net.forward(en.vectorize(text))

    for _ in range(15):
        clf.learn_from_choice(text, "生气")

    after, _ = clf.net.forward(en.vectorize(text))
    idx = en.class_index("生气")
    assert after[idx] > before[idx], "在线学习没有提升目标类别的概率"


def test_online_learning_ignores_unknown_emotion(clf):
    assert clf.learn_from_choice("随便", "不存在的情绪") is False


def test_online_learning_ignores_empty_text(clf):
    assert clf.learn_from_choice("", "开心") is False


def test_user_model_persists(tmp_path, monkeypatch):
    """学到的权重能存下来，下次启动继续用。"""
    from core import emotion_classifier as ec

    upath = str(tmp_path / "user.json")
    monkeypatch.setattr(ec, "user_model_path", lambda: upath)

    c = ec.EmotionClassifier()
    c.learn_from_choice("独特表达", "惊讶")
    assert c.save_user_model()
    assert os.path.exists(upath)

    # 新实例应优先加载用户模型
    c2 = ec.EmotionClassifier()
    assert c2.has_net
    assert c2.net.trained_samples == c.net.trained_samples


def test_save_without_learning_is_noop(tmp_path, monkeypatch):
    """没学过就不该写文件，避免每次退出都无谓地写盘。"""
    from core import emotion_classifier as ec

    upath = str(tmp_path / "never.json")
    monkeypatch.setattr(ec, "user_model_path", lambda: upath)

    c = ec.EmotionClassifier()
    assert c.save_user_model() is False
    assert not os.path.exists(upath)


# ======================================================================
# 性能
# ======================================================================
def test_inference_is_fast(clf):
    """推理必须足够快（结构断言，不做绝对时间比较以免 CI 抖动）。"""
    import time

    texts = ["今天一点都不开心", "好累啊", "哈哈哈"]
    for t in texts:
        clf.detect(t)          # 预热

    t0 = time.perf_counter()
    for _ in range(200):
        for t in texts:
            clf.detect(t)
    per_call = (time.perf_counter() - t0) / (200 * len(texts))

    # 阈值放到 5ms：远宽于实测 0.7ms，只为挡住"突然慢了 10 倍"这类回归
    assert per_call < 0.005, "单次判定耗时 %.3f ms，过慢" % (per_call * 1000)


def test_train_one_is_fast(clf):
    """在线学习必须够快，否则选字瞬间会卡。"""
    import time

    for _ in range(20):
        clf.learn_from_choice("预热", "开心")

    t0 = time.perf_counter()
    for _ in range(100):
        clf.learn_from_choice("测试句子", "伤心")
    per_call = (time.perf_counter() - t0) / 100

    assert per_call < 0.01, "单次在线学习耗时 %.3f ms" % (per_call * 1000)
