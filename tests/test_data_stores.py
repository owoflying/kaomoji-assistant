"""数据层单测：颜文字库、用户颜文字、快捷短语、用户状态。

这些都是"用户资产"，重点验证：
  * 脏数据不会让程序崩溃（读失败留空 + 记日志，不回写原文件）；
  * `UserState` 的白名单自净（最近/收藏只保留库里真实存在的条目）；
  * 内置库与插件数据源分离，`save()` 永不写插件数据。
"""
import json
import os

from core.kaomoji_data import KaomojiData
from core.user_kaomoji import UserKaomoji
from core.user_state import UserState
from core.user_triggers import UserTriggers


# ---------------------------------------------------------------- 内置库
def test_builtin_library_loads():
    data = KaomojiData()
    names = data.get_category_names()
    assert names, "内置颜文字库不能为空"
    assert all(data.get_items(n) for n in names)


def test_library_search_returns_matching_items():
    data = KaomojiData()
    all_items = data.get_items()
    target = all_items[0]
    assert target in data.search(target)


def test_library_broken_json_does_not_raise(tmp_path, quiet_log):
    """库文件损坏不能拖垮启动：读失败留空并记日志。"""
    path = tmp_path / "kaomoji.json"
    path.write_text("{ broken", encoding="utf-8")
    data = KaomojiData(path=str(path))
    assert data.get_items() == []


def test_external_categories_merge_without_persisting(tmp_path):
    """插件数据只进内存视图；save() 永远只写内置库。"""
    path = tmp_path / "kaomoji.json"
    path.write_text(json.dumps({"categories": [
        {"name": "开心", "items": ["(^_^)"]}]}), encoding="utf-8")
    data = KaomojiData(path=str(path))
    data.set_external_categories([{"name": "插件分类", "items": ["[p]"]}])

    assert "插件分类" in data.get_category_names()
    assert "[p]" in data.get_items()

    data.save()
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    names = [c["name"] for c in on_disk["categories"]]
    assert "插件分类" not in names, "插件数据不得污染随包分发的库文件"


# ---------------------------------------------------------------- 用户颜文字
def test_user_kaomoji_crud(isolated_data):
    uk = UserKaomoji(path=os.path.join(isolated_data, "uk.json"))
    uk.add_item("(^_^)", group="默认", tags=["笑脸"])
    assert "(^_^)" in uk.get_all()
    uk.update_item("(^_^)", "(^o^)", group="默认", tags=["改"])
    assert "(^o^)" in uk.get_all()
    assert "(^_^)" not in uk.get_all()
    uk.remove_item("(^o^)")
    assert "(^o^)" not in uk.get_all()


def test_user_kaomoji_groups_and_search(isolated_data):
    uk = UserKaomoji(path=os.path.join(isolated_data, "uk.json"))
    uk.add_item("(a)", group="甲")
    uk.add_item("(b)", group="乙")
    uk.add_group("丙")
    assert "丙" in uk.get_groups()
    assert "(a)" in uk.items_for_group("甲")
    assert uk.search("(a)")


def test_user_kaomoji_export_import(isolated_data):
    src = UserKaomoji(path=os.path.join(isolated_data, "src.json"))
    src.add_item("(x)", group="组", tags=["t"])
    payload = src.export_dict()

    dst = UserKaomoji(path=os.path.join(isolated_data, "dst.json"))
    dst.import_data(payload.get("groups", []), payload.get("items", []), merge=True)
    assert "(x)" in dst.get_all()


def test_user_kaomoji_broken_file_does_not_raise(isolated_data):
    path = os.path.join(isolated_data, "uk.json")
    os.makedirs(isolated_data, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("{ broken")
    uk = UserKaomoji(path=path)
    assert uk.get_all() == []


def test_user_kaomoji_flush_persists(isolated_data):
    path = os.path.join(isolated_data, "uk.json")
    uk = UserKaomoji(path=path)
    uk.add_item("(p)", group="默认")
    uk.flush()
    assert os.path.isfile(path)
    assert "(p)" in UserKaomoji(path=path).get_all()


# ---------------------------------------------------------------- 快捷短语
def test_user_triggers_crud_and_match(isolated_data):
    tr = UserTriggers(path=os.path.join(isolated_data, "tr.json"))
    tr.add("(bk)", "(￣▽￣)", delete_trigger=True)
    # get_all() 返回条目 dict 列表（trigger/output/delete_trigger）
    assert any(it["trigger"] == "(bk)" for it in tr.get_all())
    # 注意：match 是「触发词 in 文本」的子串匹配（取最长命中），不是仅匹配尾部
    m = tr.match("随便打的(bk)")
    assert m is not None
    assert m["output"] == "(￣▽￣)"
    assert m["delete_trigger"] is True
    tr.remove("(bk)")
    assert tr.match("(bk)") is None


def test_user_triggers_match_prefers_longest(isolated_data):
    """长短触发词都能命中时取最长，避免「k」抢先于「kk」。"""
    tr = UserTriggers(path=os.path.join(isolated_data, "tr.json"))
    tr.add("k", "(短)")
    tr.add("kk", "(长)")
    assert tr.match("输入kk")["output"] == "(长)"


def test_user_triggers_outputs_pool(isolated_data):
    tr = UserTriggers(path=os.path.join(isolated_data, "tr.json"))
    tr.add("aa", "(aa)")
    assert "(aa)" in tr.outputs()


def test_user_triggers_no_match_returns_none(isolated_data):
    tr = UserTriggers(path=os.path.join(isolated_data, "tr.json"))
    tr.add("aa", "(aa)")
    assert tr.match("完全无关的内容") is None


# ---------------------------------------------------------------- 用户状态
def test_user_state_recent_and_favorite(isolated_data):
    path = os.path.join(isolated_data, "st.json")
    valid = {"(a)", "(b)"}
    st = UserState(path=path, max_recent=5, valid_items=valid)
    st.add_recent("(a)")
    st.toggle_favorite("(b)")
    assert "(a)" in st.recent
    assert st.is_favorite("(b)")


def test_user_state_rejects_items_outside_whitelist(isolated_data):
    """白名单自净：库中已不存在的条目（测试残留/手改 JSON）应被丢弃。"""
    path = os.path.join(isolated_data, "st.json")
    os.makedirs(isolated_data, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"recent": ["(ghost)", "(real)"], "favorites": ["(ghost)"],
                   "usage": {"(ghost)": 3}}, f)
    st = UserState(path=path, max_recent=10, valid_items={"(real)"})
    assert "(ghost)" not in st.recent
    assert "(real)" in st.recent
    assert not st.is_favorite("(ghost)")


def test_user_state_max_recent_enforced(isolated_data):
    path = os.path.join(isolated_data, "st.json")
    st = UserState(path=path, max_recent=2,
                   valid_items={"(1)", "(2)", "(3)"})
    for item in ("(1)", "(2)", "(3)"):
        st.add_recent(item)
    assert len(st.recent) <= 2


def test_user_state_usage_stats(isolated_data):
    path = os.path.join(isolated_data, "st.json")
    st = UserState(path=path, valid_items={"(a)"})
    st.record_usage("(a)")
    st.record_usage("(a)")
    assert st.total_inserts() >= 2
    assert st.top_usage(1)


def test_user_state_flush_persists(isolated_data):
    path = os.path.join(isolated_data, "st.json")
    st = UserState(path=path, valid_items={"(a)"})
    st.add_recent("(a)")
    st.flush()
    assert os.path.isfile(path)
    reloaded = UserState(path=path, valid_items={"(a)"})
    assert "(a)" in reloaded.recent


def test_user_state_broken_file_does_not_raise(isolated_data):
    path = os.path.join(isolated_data, "st.json")
    os.makedirs(isolated_data, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("not json")
    st = UserState(path=path, valid_items=set())
    assert st.recent == []
