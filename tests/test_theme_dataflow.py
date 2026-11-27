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


def test_animation_switches_are_all_wired():
    """每个动画开关都必须有实际生效的检查点。

    ## 为什么需要这条

    四个动画开关（页面切换 / 按钮悬停 / 候选条 / 开关控件 / 换肤）
    分散在不同文件里，各自检查一处。漏掉任何一处，用户关掉开关后
    那个动画照样播——而**不会报错**，只能靠人肉发现。

    实测踩到过：`page_switch` 只挡住了整体淡入，
    `reveal_page()` 的页内逐块浮现没挡，用户看到的仍然是
    "元素一个个冒出来"，于是反馈"界面切换开关无效"。
    """
    from core.anim_settings import ANIMATIONS

    sources = {
        "page_switch": "ui/unified_nav.py",
        "hover": "ui/hover_feedback.py",
        "picker": "ui/picker_window.py",
        "theme": "ui/theme_fade.py",
        "toggle": "ui/toggle_switch.py",
    }
    missing = []
    for name, _key, label, _desc in ANIMATIONS:
        rel = sources.get(name)
        if rel is None:
            missing.append("%s（没有登记检查点文件）" % label)
            continue
        src = _read(rel)
        if ('_anim_on("%s")' % name) not in src and \
                ("_anim_on('%s')" % name) not in src:
            missing.append("%s（%s 里找不到 enabled(%r) 检查）"
                           % (label, rel, name))
    assert not missing, "动画开关没有生效检查点：\n  " + "\n  ".join(missing)


def test_page_switch_also_gates_intro_reveal():
    """「页面切换」开关必须同时管住页内逐块浮现。

    这是实测踩过的坑：只关整体淡入、没关 `reveal_page()`，
    用户会觉得开关完全无效。

    实现上要保证 `reveal_page(...)` 出现在一个 `if animate:` 分支里
    （`animate` 在 set_page 开头已按开关算好）。
    """
    src = _read("ui/unified_nav.py")
    i = src.find("def set_page")
    j = src.find("\n    def ", i + 10)
    body = src[i:j]

    assert "_anim_on(\"page_switch\")" in body or \
        "_anim_on('page_switch')" in body, (
        "set_page 必须检查 anim_settings.enabled('page_switch')——\n"
        "否则关掉开关后仍有动画。")

    lines = body.splitlines()
    reveal_at = None
    for idx, line in enumerate(lines):
        if "reveal_page(" in line:
            reveal_at = idx
            break
    assert reveal_at is not None, "set_page 里应该调用 reveal_page 做页内浮现"

    # 往上找最近的 if 语句，必须是以 animate 为条件的
    guard = ""
    for k in range(reveal_at, max(-1, reveal_at - 12), -1):
        s = lines[k].strip()
        if s.startswith("if "):
            guard = s
            break
    assert "animate" in guard, (
        "reveal_page 必须包在 `if animate:` 里，当前最近的 if 是：%r\n"
        "否则用户关掉「页面切换」后，页内元素仍会逐个浮现。" % (guard,))


def test_theme_controller_reads_profile_not_flat_key():
    """ThemeController 必须从**主题包**取明暗，不能只读平铺键。

    ## 实测踩到的现象

    用户 config.json 里同时有：

        theme_mode    = light
        theme_profile = {theme_mode: dark, ...}

    主题包是唯一真相，所以 `resolve()` 和外观页下拉框都是 dark，
    但 `ThemeController` 还在读平铺的 `theme_mode`（light）——
    结果**主窗口和托盘停在浅色，必须重启才恢复**。

    重启能好，是因为重启后走的是另一条初始化路径。
    """
    src = _read("ui/theme_controller.py")
    i = src.find("def rebuild")
    j = src.find("\n    def ", i + 10)
    body = src[i:j]
    assert "get_profile" in body, (
        "ThemeController.rebuild 必须用 get_profile() 取明暗/配色——\n"
        "否则会和主题包（唯一真相）不一致，表现为换肤后界面不变。")


def test_glass_menu_cache_key_includes_dark():
    """托盘菜单的玻璃缓存 key 必须包含主题明暗。

    ## 实测踩到的现象

    缓存 key 原本是 `(w, h, id(composer))`。切换深浅色时尺寸不变、
    composer 对象也不换，于是永远命中旧缓存——菜单玻璃底停在上一次的
    颜色，用户以为"换了深浅色托盘不变，必须重启"。

    重启能好，是因为重启后缓存是空的。
    """
    src = _read("ui/glass_menu.py")
    i = src.find("def _render_glass")
    j = src.find("\n    def ", i + 10)
    body = src[i:j]
    assert "dark" in body.split("key = ")[-1].split("\n")[0] or \
        "theme_key" in body, (
        "glass_menu._render_glass 的缓存 key 必须包含主题明暗，\n"
        "否则切换深浅色后菜单玻璃底不会重算（缓存命中旧颜色）。")


def test_override_snapshot_is_complete():
    """打开「独立配置」时，覆盖层必须包含主题包的**全部字段**。

    ## 实测踩到的现象

    `_on_follow_toggled()` 原来手写 8 个字段建覆盖层，漏了 8 个，
    其中包含 **theme_mode / theme_preset**（明暗与配色）。

    后果：打开独立配置 → 在这个界面改深浅色 → 表面上 `_push`
    写进了覆盖层，但因为快照里压根没有这个键，`resolve()` 合并时
    又从主配置回落，于是"改了没反应"，且没有任何报错。

    这与 `_push` 的 alias 手写漏字段是**同一类错误**，
    修法也一致：从 DEFAULT_PROFILE 派生，不要手写。
    """
    src = _read("ui/pages/appearance_page.py")
    i = src.find("def _on_follow_toggled")
    assert i > 0
    j = src.find("\n    def ", i + 10)
    body = _strip_comments(src[i:j])

    assert "DEFAULT_PROFILE" in body, (
        "创建覆盖层时必须遍历 DEFAULT_PROFILE 全量快照——\n"
        "手写字段清单必然漏，漏了就是静默失效（改深浅色没反应）。")
    assert "for key in DEFAULT_PROFILE" in body, (
        "_on_follow_toggled 里应当 `for key in DEFAULT_PROFILE` 逐项拷贝。")


def test_every_profile_field_is_snapshotted():
    """反过来验证：DEFAULT_PROFILE 的每个键都能被 resolve 出来。

    覆盖层是按 resolve 的结果拷的，如果某个键 resolve 不出来，
    快照里就会缺它。这条防止将来加字段时 resolve 没跟上。
    """
    from core.theme_profile import DEFAULT_PROFILE, resolve

    cfg = {}
    resolved = resolve(cfg, "picker")
    missing = sorted(set(DEFAULT_PROFILE) - set(resolved))
    assert not missing, (
        "这些主题包字段 resolve() 没有返回，覆盖层快照会漏掉它们：%r"
        % (missing,))
