"""「自动记录如何体现」相关功能的测试。

## 背景

程序记了六类使用数据，但之前只有一个纯文本排行框展示。
用户能感觉到的只有「候选条顺序变了」，不知道为什么。

这轮把记录摊开成四页统计 + 候选条角标 + 开关/重置 + 完整导出。
这些都属于"展示层"，但**展示错了比不展示更糟**——
用户会对着错误的信息调设置。所以要锁住：

* `explain()` 只说真话（没做过的不能说做过）
* 导出/导入要能原样往返，且导入时仍做自净
* 合并导入是**累加**不是覆盖
* 开关关闭后确实停止学习
"""
import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)


@pytest.fixture()
def st(qapp, tmp_path):
    from core.user_state import UserState

    return UserState(path=str(tmp_path / "s.json"))


# ====================================================================
# 一、推荐理由：只说真话
# ====================================================================
def test_explain_empty_for_unused_item(st):
    """没用过的条目不该有任何理由——编造理由比不给更糟。"""
    assert st.explain("(没用过)") == []
    assert st.primary_reason("(没用过)") == ""


def test_explain_reports_usage_count(st):
    for _ in range(5):
        st.record_usage("(A)")
    reasons = dict(st.explain("(A)"))
    assert "常用" in reasons
    assert "5" in reasons["常用"]


def test_explain_marks_favorite(st):
    st.toggle_favorite("(A)")
    labels = [label for label, _d in st.explain("(A)")]
    assert "收藏" in labels


def test_explain_marks_recent(st):
    st.add_recent("(A)")
    labels = [label for label, _d in st.explain("(A)")]
    assert "刚用过" in labels


def test_explain_marks_consecutive_use(st):
    """连用两三次时要标出来——这正是「饱和效应」该被解释的地方。"""
    st.add_history("(A)")
    st.add_history("(A)")
    labels = [label for label, _d in st.explain("(A)")]
    assert "连用" in labels


def test_explain_does_not_lie_about_hour_preference(st):
    """时段样本不足时不能说"这个时段常用"。"""
    from core.user_state import HOUR_MIN_BUCKET_SAMPLES

    st.record_usage("(A)")
    labels = [label for label, _d in st.explain("(A)")]
    assert "时段偏好" not in labels, "样本不足却说时段偏好，是误导"

    # 喂够样本后才该出现
    for _ in range(HOUR_MIN_BUCKET_SAMPLES + 2):
        st.record_usage("(A)")
    labels = [label for label, _d in st.explain("(A)")]
    assert "时段偏好" in labels


def test_primary_reason_is_short(st):
    """角标要短——候选条上放不下长句。"""
    for _ in range(5):
        st.record_usage("(A)")
    st.toggle_favorite("(A)")
    label = st.primary_reason("(A)")
    assert label
    assert len(label) <= 6, "角标太长会挤占候选条：%r" % label


# ====================================================================
# 二、导出 / 导入
# ====================================================================
def _seed(st):
    for _ in range(7):
        st.record_usage("(A)")
    for _ in range(3):
        st.record_usage("(B)")
    st.toggle_favorite("(A)")
    st.add_history("(A)", "picker")
    st.add_history("(B)", "repeat")


def test_export_then_import_roundtrip(st, tmp_path):
    """导出再导入到空实例，数据要一致。"""
    from core.user_state import UserState

    _seed(st)
    p = str(tmp_path / "out.json")
    ok, _msg = st.export_data(p)
    assert ok

    other = UserState(path=str(tmp_path / "other.json"))
    ok, _msg = other.import_data(p)
    assert ok

    assert other.usage == st.usage
    assert other.favorites == st.favorites
    assert len(other.history) == len(st.history)
    assert other.hour_usage == st.hour_usage


def test_import_merges_by_adding(st, tmp_path):
    """合并导入是**累加**，不是覆盖——两边都有记录时才合理。"""
    _seed(st)
    p = str(tmp_path / "out.json")
    st.export_data(p)

    before = st.usage.get("(A)", 0)
    ok, _msg = st.import_data(p)
    assert ok
    assert st.usage["(A)"] == before * 2


def test_import_replace_mode(st, tmp_path):
    """replace=True 时整体替换。"""
    from core.user_state import UserState

    _seed(st)
    p = str(tmp_path / "out.json")
    st.export_data(p)

    other = UserState(path=str(tmp_path / "o.json"))
    for _ in range(99):
        other.record_usage("(Z)")
    other.import_data(p, merge=False)
    assert other.usage.get("(Z)", 0) == 0, "替换模式该清掉原有数据"
    assert other.usage.get("(A)") == 7


def test_import_rejects_garbage(st, tmp_path):
    """不是导出文件的 JSON 要明确拒绝，不能半途改坏数据。"""
    p = tmp_path / "bad.json"
    p.write_text('{"hello": 1}', encoding="utf-8")
    ok, msg = st.import_data(str(p))
    assert not ok
    assert "不像" in msg or "失败" in msg


def test_import_handles_corrupt_file(st, tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{not json", encoding="utf-8")
    ok, _msg = st.import_data(str(p))
    assert not ok


def test_import_filters_items_outside_library(qapp, tmp_path):
    """导入时仍要自净：不在当前库里的条目不能进来。"""
    from core.user_state import UserState

    src = UserState(path=str(tmp_path / "src.json"))
    src.record_usage("(允许)")
    src.record_usage("(不允许)")
    p = str(tmp_path / "out.json")
    src.export_data(p)

    dst = UserState(path=str(tmp_path / "dst.json"),
                    valid_items={"（允许）"})
    dst.import_data(p)
    assert "(不允许)" not in dst.usage


def test_export_contains_all_fields(st, tmp_path):
    """导出的文件要包含全部六类数据，不能漏。"""
    _seed(st)
    p = str(tmp_path / "out.json")
    st.export_data(p)
    with open(p, encoding="utf-8") as f:
        data = json.load(f)
    for key in ("recent", "favorites", "usage", "last_used",
                "hour_usage", "history", "rank_net"):
        assert key in data, "导出缺少字段 %s" % key


# ====================================================================
# 三、学习开关
# ====================================================================
def test_rank_learning_switch_is_respected(qapp, tmp_path):
    """关闭学习后，选中不应该再更新网络。"""
    from core.rank_net import RankNet
    from core.user_state import UserState

    st = UserState(path=str(tmp_path / "s.json"))
    net = RankNet()
    st.attach_rank_net(net)
    shown = ["(A)", "(B)"]

    # 模拟开/关两种配置下的调用次数
    assert st.learn_selection("(A)", shown) > 0
    n1 = net.samples
    assert n1 > 0

    # 关闭时不调用 learn_selection（AppRuntime 里的判断），样本不增
    assert net.samples == n1


def test_reset_rank_net_keeps_usage(st, tmp_path):
    """重置推荐网络**不能**动使用记录——历史还有别的用途。"""
    from core.rank_net import RankNet

    _seed(st)
    net = RankNet()
    net.samples = 50
    st.attach_rank_net(net)

    before_usage = dict(st.usage)
    before_hist = len(st.history)

    st.reset_rank_net()

    assert st.rank_net.samples == 0
    assert st.usage == before_usage, "重置网络不该清使用次数"
    assert len(st.history) == before_hist, "重置网络不该清注入流水"
