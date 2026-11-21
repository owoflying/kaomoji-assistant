"""推荐网络与打磨项的测试。

## 保护什么

### 推荐网络（`core/rank_net.py`）

它是个**会随使用而改变排序**的组件，风险比纯规则代码高：
学歪了用户会觉得"推荐坏了"却不知道原因。所以要锁住：

* 冷启动时**完全不碰**排序（blend=0），新用户感受不到它
* 学得**温和**：一次点击不该立刻推翻排序
* 换口味时能**扳回来**，不是单向锁死
* 权重能持久化，也能一键重置

### 打磨项

* 搜索不因反向包含误命中（`happy` 不该捞 `unhappy` 的分类）
* 否定查询有兜底（`不开心` 不该返回 0 条）
* 单字符查询不泛滥
* 时段偏好有冷启动门槛
* Ctrl+R 没有可重复内容时不吞键（不然会打断浏览器刷新）
"""
import os
import sys

import pytest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)


# ====================================================================
# 一、推荐网络
# ====================================================================
def test_net_starts_neutral(qapp):
    """未训练时输出接近 0.5，blend 权重为 0——完全不影响排序。"""
    from core.rank_net import RankNet, features
    from core.user_state import UserState
    import tempfile

    net = RankNet()
    st = UserState(path=os.path.join(tempfile.mkdtemp(), "s.json"))
    fv = features("(A)", state=st)

    assert 0.3 < net.predict(fv) < 0.7, "初始输出应接近 0.5"
    assert net.blend_weight() == 0.0, "冷启动时不该影响排序"


def test_net_learns_preference(qapp):
    """反复选同一条后，该条分数显著高于未选过的。"""
    from core.rank_net import RankNet, features
    from core.user_state import UserState
    import tempfile

    net = RankNet()
    st = UserState(path=os.path.join(tempfile.mkdtemp(), "s.json"))
    st.attach_rank_net(net)
    shown = ["(A)", "(B)", "(C)"]
    for _ in range(40):
        st.record_usage("(A)")
        st.learn_selection("(A)", shown)

    a = net.predict(features("(A)", state=st))
    b = net.predict(features("(B)", state=st))
    assert a > b + 0.2, "该条分数应明显更高（%.3f vs %.3f）" % (a, b)


def test_net_learning_is_gentle(qapp):
    """一次点击不该把分数推到极端——偏好要多次一致才形成。"""
    from core.rank_net import RankNet, features
    from core.user_state import UserState
    import tempfile

    net = RankNet()
    st = UserState(path=os.path.join(tempfile.mkdtemp(), "s.json"))
    st.attach_rank_net(net)
    st.record_usage("(A)")
    st.learn_selection("(A)", ["(A)", "(B)", "(C)"])

    score = net.predict(features("(A)", state=st))
    assert score < 0.85, \
        "只学了一次就冲到 %.3f，太激进了（用户手滑一次就毁掉推荐）" % score


def test_net_can_change_mind(qapp):
    """换口味能扳回来：改用 B 若干次后，B 不再被 A 压死。"""
    from core.rank_net import RankNet, features
    from core.user_state import UserState
    import tempfile

    net = RankNet()
    st = UserState(path=os.path.join(tempfile.mkdtemp(), "s.json"))
    st.attach_rank_net(net)
    shown = ["(A)", "(B)"]
    for _ in range(30):
        st.record_usage("(A)")
        st.learn_selection("(A)", shown)
    a0 = net.predict(features("(A)", state=st))

    for _ in range(30):
        st.record_usage("(B)")
        st.learn_selection("(B)", shown)

    a1 = net.predict(features("(A)", state=st))
    b1 = net.predict(features("(B)", state=st))
    assert a1 < a0, "换口味后 A 的分数该降下来"
    assert b1 > a1, "B 该超过 A"


def test_net_blend_grows_with_samples(qapp):
    """样本越多，网络话语权越大，但有上限。"""
    from core.rank_net import RankNet, WARMUP, features
    from core.user_state import UserState
    import tempfile

    net = RankNet()
    st = UserState(path=os.path.join(tempfile.mkdtemp(), "s.json"))
    fv = features("(A)", state=st)

    w0 = net.blend_weight()
    for _ in range(WARMUP):
        net.train_one(fv, 1.0)
    w1 = net.blend_weight()
    for _ in range(WARMUP * 10):
        net.train_one(fv, 1.0)
    w2 = net.blend_weight()

    assert w0 == 0.0
    assert w0 < w1 < w2
    assert w2 <= 0.8, "网络不该完全接管排序"


def test_net_persists(qapp, tmp_path):
    from core.rank_net import RankNet

    net = RankNet()
    net.samples = 42
    net.save(str(tmp_path / "n.json"))

    loaded = RankNet.load(str(tmp_path / "n.json"))
    assert loaded.samples == 42


def test_net_load_missing_returns_fresh(qapp, tmp_path):
    """文件不存在时给一个可用网络，不能返回 None 或崩。"""
    from core.rank_net import RankNet

    net = RankNet.load(str(tmp_path / "nope.json"))
    assert net is not None
    assert net.samples == 0


def test_net_load_corrupt_returns_fresh(qapp, tmp_path):
    """权重文件损坏时也要能起来（用户可能手改坏了）。"""
    from core.rank_net import RankNet

    p = tmp_path / "bad.json"
    p.write_text("{not valid json", encoding="utf-8")
    net = RankNet.load(str(p))
    assert net is not None
    assert net.samples == 0


def test_state_rank_works_without_net(qapp, tmp_path):
    """没装网络时 rank() 行为与原来一致（纯线性公式）。"""
    from core.user_state import UserState

    st = UserState(path=str(tmp_path / "s.json"))
    assert st.rank_net is None
    st.record_usage("(A)")
    r = st.rank(["(A)", "(B)"])
    assert r[0] == "(A)"


def test_state_learn_selection_noop_without_net(qapp, tmp_path):
    from core.user_state import UserState

    st = UserState(path=str(tmp_path / "s.json"))
    assert st.learn_selection("(A)", ["(A)", "(B)"]) == 0


def test_state_reset_rank_net(qapp, tmp_path):
    """重置后网络回到未训练状态，且磁盘权重被删掉。"""
    from core.rank_net import RankNet
    from core.user_state import UserState

    st = UserState(path=str(tmp_path / "s.json"))
    net = RankNet()
    net.samples = 99
    st.attach_rank_net(net)
    st.save()
    assert os.path.isfile(st.rank_net_path())

    st.reset_rank_net()
    assert st.rank_net.samples == 0
    assert not os.path.isfile(st.rank_net_path()), "磁盘权重该被删掉"


def test_features_never_raises(qapp, tmp_path):
    """特征工程出任何问题都不能让候选条崩，最差返回全零。"""
    from core.rank_net import INPUT_DIM, features
    from core.user_state import UserState

    st = UserState(path=str(tmp_path / "s.json"))
    # 各种奇怪输入
    for bad in ("", None, "正常", "🙂", "a" * 500):
        fv = features(bad, state=st)
        assert len(fv) == INPUT_DIM
        assert all(isinstance(x, float) for x in fv)


# ====================================================================
# 二、搜索打磨
# ====================================================================
@pytest.fixture()
def search_lib(qapp, tmp_path):
    import json

    from core.kaomoji_data import KaomojiData

    p = tmp_path / "k.json"
    data = {"categories": [
        {"name": "开心", "aliases": ["kx", "happy"],
         "items": [{"text": "(笑)", "aliases": ["smile"]}, "(乐)"]},
        {"name": "伤心", "aliases": ["sx", "sad", "unhappy"],
         "items": [{"text": "(哭)", "aliases": ["cry"]}]},
    ]}
    with open(p, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    return KaomojiData(path=str(p))


def test_reverse_containment_not_matched(search_lib):
    """`happy` 不该把 `unhappy`（伤心类）捞进来。

    这是真实踩过的坑：反向包含（a in q）会让 "happy" 命中
    "伤心" 的英文别名 "unhappy"，搜开心结果里一半是难过的。
    """
    got = search_lib.search("happy")
    cats = set()
    for t in got:
        cats.update(search_lib.categories_of(t))
    assert "伤心" not in cats, "happy 不该命中伤心类，实际：%s" % cats


def test_negation_returns_opposite(search_lib):
    """`不开心` 不该返回空，应给出伤心类。"""
    got = search_lib.search("不开心")
    assert got, "否定查询不该是空结果"
    cats = set()
    for t in got[:5]:
        cats.update(search_lib.categories_of(t))
    assert "伤心" in cats, "搜'不开心'应优先给伤心类，实际：%s" % cats


def test_single_char_does_not_flood(search_lib):
    """单字符查询不该把整库扫进来。"""
    all_items = search_lib.get_items()
    got = search_lib.search("(")
    assert len(got) < len(all_items), \
        "单字符不该命中全部 %d 条" % len(all_items)


def test_search_does_not_recurse(search_lib):
    """否定兜底不能无限递归（踩过 RecursionError）。"""
    # 这个词剥掉否定词后是空串，会走到兜底分支
    assert search_lib.search("不") == []


# ====================================================================
# 三、时段冷启动
# ====================================================================
def test_hour_affinity_needs_min_samples(qapp, tmp_path):
    """时段偏好要有最小样本门槛，否则用一次就霸屏。"""
    from core.user_state import (
        HOUR_MIN_BUCKET_SAMPLES, UserState, hour_bucket,
    )

    st = UserState(path=str(tmp_path / "s.json"))
    st.record_usage("(A)")
    assert st.hour_affinity("(A)", hour_bucket()) == 0.0

    for _ in range(HOUR_MIN_BUCKET_SAMPLES):
        st.record_usage("(A)")
    assert st.hour_affinity("(A)", hour_bucket()) > 0.0


# ====================================================================
# 四、Ctrl+R 不误吞
# ====================================================================
def test_repeat_not_eaten_without_history(qapp):
    """没有可重复内容时 Ctrl+R 不该被吞——它在浏览器里是刷新。"""
    from core.global_keys import GlobalKeyInterceptor

    class Fake:
        _state = (5, False, True)
        _has_repeatable = False
        _engaged = False

    assert GlobalKeyInterceptor._allows(Fake(), "repeat") is False

    Fake._has_repeatable = True
    assert GlobalKeyInterceptor._allows(Fake(), "repeat") is True


def test_r_key_alone_is_not_a_combo(qapp):
    """单独按 R 是普通字母，不能被当成组合键。"""
    from core.global_keys import VK_R, _map_key, _map_combo

    assert _map_key(VK_R) is None, "R 不该被单键映射捕获"
    # 没按 Ctrl 时组合键映射应为 None
    assert _map_combo(VK_R) is None
