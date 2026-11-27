"""主题数据流守卫：防止「改动被静默丢弃」这类 bug 再次出现。

## 为什么需要它

同一个 bug 在这套主题系统里出现了**至少四次**，表现各不相同：

    * 「选择显示区域」点击无反应     —— 裁切字段没进 alias 表
    * 换主题后界面停在原主题         —— theme_mode 没进 alias 表
    * 主题编辑器改强调色点确定没反应  —— 色板已写盘，diff 恒为空
    * 深浅色下拉变了但界面不变        —— 先改 config 再拷快照，diff 恒为空

它们其实是**同一个根因的两次不同表现**：

    1. 「字段白名单」是手写的 → 新增字段必然漏
    2. 「改动判定」依赖值比对 → 值提前写入后就永远判定为"没变"

两者共同的结果都是：**用户操作了，但系统静默什么都不做**。
这才是它反复恶心人的原因——不报错、不写日志，只能靠人肉复现。

本文件把两条不变量固化成测试：

    不变量 A：外观页能发出的每个字段，都必须存在于主题包字段表
    不变量 B：_push 的字段白名单必须由字段表派生，不能手写
"""
import io
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def _read(rel):
    with io.open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


def _appearance_pushed_keys():
    """从外观页源码里抽出所有 `_push(...)` 会被用到的键名。

    三种来源：
        * `self._push(xxx=...)` 直接写死的键
        * `lambda *_a: self._push(...)` 里的键
        * `_push_from_slider` 之流的间接调用（这里靠滑块 key 约定）
    """
    src = _read("ui/pages/appearance_page.py")
    keys = set()
    # 抓所有 _push( 之后的 xxx= 形式
    for m in re.finditer(r"_push\(([^)]*)\)", src):
        args = m.group(1)
        for km in re.finditer(r"(\w+)\s*=", args):
            keys.add(km.group(1))
    return keys


def test_appearance_push_keys_are_all_known_fields():
    """**不变量 A**：外观页发出的每个字段都必须在主题包字段表里。

    漏一个的后果不是报错，而是那个选项**静默失效**——
    用户点了没反应，日志里也什么都没有。

    如果这条测试红了：说明外观页加了一个新控件，但没把对应字段
    加进 `core.theme_profile.DEFAULT_PROFILE`。
    """
    from core.theme_profile import DEFAULT_PROFILE

    pushed = _appearance_pushed_keys()
    # 这些是控件私有参数，不是主题包字段
    allowed_extra = {
        "self", "changes", "key", "s", "cfg", "crop", "patch",
    }
    unknown = sorted(pushed - set(DEFAULT_PROFILE) - allowed_extra)
    assert not unknown, (
        "外观页会 _push 这些字段，但它们不在 DEFAULT_PROFILE 里：%r\n"
        "后果：用户改了这项会**静默不生效**（没有报错、没有日志）。\n"
        "修法：把字段加进 core/theme_profile.py 的 DEFAULT_PROFILE。"
        % (unknown,))


def test_push_whitelist_is_derived_not_handwritten():
    """**不变量 B**：`_push` 的白名单必须由字段表派生。

    历史上这里是手写的 alias 字典，每加一个主题字段都可能漏写，
    而且漏了完全没有提示。现在必须写成
    `{key: key for key in DEFAULT_PROFILE}` 这种派生形式。

    这条测试的意义：**禁止有人把派生改回手写**。
    """
    src = _read("ui/pages/appearance_page.py")
    assert "for key in DEFAULT_PROFILE" in src, (
        "_push 的字段白名单必须从 DEFAULT_PROFILE 派生，"
        "不要改回手写字典——手写必然漏，漏了就静默失效。")


def test_push_logs_when_field_dropped():
    """丢弃字段时必须写日志。

    静默丢弃是这个主题系统里最贵的 bug 类型：
    排查一次要人肉复现 + 读源码，前后发生过四次。
    留一行 warn，至少让日志里能看见。
    """
    src = _read("ui/pages/appearance_page.py")
    assert "丢弃了未登记的字段" in src, (
        "_push 在丢弃未知字段时必须写 warn 日志，"
        "否则这类问题只能靠人肉复现。")


def test_theme_editor_forces_reload():
    """主题编辑器保存后必须**强制重载**，不能依赖 diff。

    自定义主题保存时色板已经写到磁盘，但配置里的 `theme_preset`
    可能本来就是 `custom`——这时 `apply_settings` 的 diff 为空，
    强调色改了也看不见（实测 apply_settings 被调用 0 次）。

    正确做法是调一个显式的强制重载入口。
    """
    src = _read("ui/pages/appearance_page.py")
    assert "_force_reload_cb" in src, (
        "主题编辑器保存后必须走强制重载（_force_reload_cb），"
        "否则改了强调色但 preset 没变，界面不会刷新。")

    win = _read("ui/unified_window.py")
    assert "_force_reload_theme" in win and "_force_reload_cb" in win, (
        "UnifiedSettingsWindow 必须实现 _force_reload_theme 并注入给外观页。")


def _strip_comments(text):
    """去掉 `#` 开头的注释行，避免注释里举例的代码造成误报。"""
    out = []
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("#"):
            continue
        out.append(line)
    return "\n".join(out)


def test_snapshot_taken_before_mutating_config():
    """`_push` 必须先拷快照再改配置。

    宿主的 `apply_settings(new_cfg)` 靠 `old` / `new` 比对来判断变化。
    如果先改宿主 config（`cfg` 与宿主是同一个 dict 对象）再拷快照，
    那 `old` 拿到的已经是新值，diff 恒为空 → 换肤分支永不执行。

    实测症状：`config["theme_mode"]` 已经是 dark，但
    `theme_ctrl.current()` 仍是 light，界面完全不变。
    """
    src = _read("ui/pages/appearance_page.py")
    # 找到 _push 函数体
    i = src.find("def _push(self")
    j = src.find("\n    def ", i + 10)
    body = _strip_comments(src[i:j])
    pos_snapshot = body.find("target = dict(cfg)")
    pos_mutate = body.find("set_profile(target")
    assert pos_snapshot > 0, "_push 里必须有 target = dict(cfg) 拷快照"
    assert pos_mutate > 0, "_push 里必须把改动应用到快照上"
    assert pos_snapshot < pos_mutate, (
        "必须先 `target = dict(cfg)` 再改 target——\n"
        "若先改宿主 config 再拷快照，宿主 diff 恒为空，换肤不会生效。")
    # 不能出现直接改宿主 cfg 的调用
    assert "set_profile(cfg," not in body, (
        "不能直接 set_profile(cfg, ...) 改宿主配置，"
        "必须改 target 快照，让宿主自己去落盘+广播。")
