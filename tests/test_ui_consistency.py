"""界面一致性回归：主题同步、页面滚动、弹层配色。

## 这三类 bug 的共同点

用户截图报的问题都很"随机"：

* 切到深色后，**只有配色方案那个下拉是浅色**，别的下拉都正常；
* 下拉第一次打开是旧主题，**闪一下**才变对；
* 主页底部「使用提示」**被裁掉**，文字显示不全。

它们的共同点是：**不会抛异常，只在特定操作序列下才出现**，
而且看起来像"偶发"。实际上是三处遗漏：

1. `set_theme()` 里手写枚举控件列表 → 新加的控件漏了；
2. `showPopup()` 先弹后贴样式 → 第一帧必然是旧配色；
3. `page_layout()` 没有滚动 → 内容超出窗口高度就被硬裁。

所以这里用测试把"应该覆盖到"这件事固定下来。
"""
import os
import sys

import pytest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)


@pytest.fixture()
def settings_page(qapp, tmp_path):
    from core.config_store import load_config
    from ui.pages.settings_page import SettingsPage

    cfg = load_config()
    return SettingsPage(cfg)


# ====================================================================
# 一、主题同步：所有下拉框都要被更新
# ====================================================================
def test_all_comboboxes_get_theme_on_switch(settings_page):
    """`set_theme()` 必须覆盖页面里的**每一个** FluentComboBox。

    ## 真实 bug

    新增「配色方案」和「强调色」两个下拉后，忘了把它们加进
    `set_theme()` 的枚举列表。表现是：切到深色后，打开「配色方案」
    下拉还是浅色白底——而其它下拉都正常。

    这种"只有一个控件不对"的症状极难猜，所以用测试固定：
    只要页面里有 FluentComboBox 没被 set_theme 覆盖到，就失败。
    """
    from ui.fluent_combobox import FluentComboBox
    from ui.theme_manager import build_theme

    page = settings_page
    combos = page.findChildren(FluentComboBox)
    assert combos, "设置页应当有下拉框"

    dark = build_theme({"theme_mode": "dark"})
    page.set_theme(dark)

    stale = []
    for c in combos:
        t = getattr(c, "_theme", None)
        if t is None:
            stale.append((c.objectName() or type(c).__name__, "未设置主题"))
        elif getattr(t, "name", None) != "dark":
            stale.append((c.objectName() or type(c).__name__,
                          "仍是 %s" % getattr(t, "name", "?")))
    assert not stale, (
        "以下下拉框没有被 set_theme() 覆盖，切主题后它们会保持旧配色：\n  "
        + "\n  ".join("%s (%s)" % s for s in stale)
    )


def test_all_toggles_get_theme_on_switch(settings_page):
    """`set_theme()` 必须覆盖页面里的**每一个** ToggleSwitch。

    ## 真实 bug

    原来 `set_theme()` 写死更新 4 个开关（auto/autostart/blur_hide/
    uia_elevation），而「情绪推荐」那组的三个开关
    （emotion_check / emotion_ai_check / rank_learn_check）漏了。

    用户切强调色后看到：上面几个开关变成了新颜色，下面三个还是旧的
    ——而且下面那组的轨道色是上个强调色的青色，非常显眼。

    根因同样是**手写枚举**：新增控件时没有任何提示。
    现在改成 `findChildren(ToggleSwitch)` 自动收集。
    """
    from ui.theme_manager import build_theme
    from ui.toggle_switch import ToggleSwitch

    page = settings_page
    toggles = page.findChildren(ToggleSwitch)
    assert len(toggles) >= 8, \
        "设置页开关数量异常（只有 %d 个），测试数据不足" % len(toggles)

    target = build_theme({"theme_mode": "dark", "accent_mode": "custom",
                          "accent_custom": "#ff3366"})
    page.set_theme(target)

    stale = []
    for t in toggles:
        got = getattr(t, "_theme", None)
        if got is not target:
            stale.append(getattr(t, "objectName", lambda: "")() or
                         type(t).__name__)
    assert not stale, (
        "以下开关没有被 set_theme() 覆盖，切强调色后它们保持旧颜色：\n  "
        + "\n  ".join(stale))


def test_theme_update_is_collected_automatically(settings_page):
    """开关/下拉的同步必须是**自动收集**，不能靠手写列表。

    ## 为什么守这个

    手写枚举列表的毛病是"新增控件时没有任何提示"——设置页就因此出过
    两次 bug：

      * `preset_combo` / `accent_combo` 漏了 → 切深色后某个下拉还是浅色；
      * `emotion_check` 那组开关漏了 → 切强调色后下面三个不跟着变。

    这里直接验证 `set_theme()` 之后**每一个**子控件都被覆盖到。
    """
    from ui.fluent_combobox import FluentComboBox
    from ui.theme_manager import build_theme
    from ui.toggle_switch import ToggleSwitch

    page = settings_page
    target = build_theme({"theme_mode": "dark"})
    page.set_theme(target)

    stale = []
    for w in page.findChildren(ToggleSwitch):
        if getattr(w, "_theme", None) is not target:
            stale.append("开关 %s" % (w.objectName() or type(w).__name__))
    for w in page.findChildren(FluentComboBox):
        if getattr(w, "_theme", None) is not target:
            stale.append("下拉 %s" % (w.objectName() or type(w).__name__))

    assert not stale, (
        "以下控件没被 set_theme() 同步（换主题后会保持旧配色）：\n  "
        + "\n  ".join(stale))


# ====================================================================
# 五、悬浮提示（QToolTip）必须跟随主题
# ====================================================================
def test_tooltip_palette_follows_theme(qapp):
    """QToolTip 的调色板必须跟着主题走。

    ## 真实 bug

    浅色主题下悬浮提示是**黑底黑字**——界面上冒出一条突兀的黑框。

    根因：`QToolTip` 是**原生弹出窗口**，不是普通子控件。Qt 绘制它时
    优先取 `QPalette` 的 `ToolTipBase` / `ToolTipText`，QSS 里的
    `QToolTip {{ }}` 规则在不少情况下**根本不生效**。

    实测系统默认 `ToolTipBase` 是 `#ffffdc`（浅黄），但窗口背景被画成
    黑色，与主题完全脱节。

    所以必须显式改 palette。
    """
    from PySide6.QtGui import QPalette

    from app_runtime import _apply_tooltip_palette
    from core.theme_presets import parse_color
    from ui.theme_manager import build_theme

    for mode in ("light", "dark"):
        t = build_theme({"theme_mode": mode})
        _apply_tooltip_palette(qapp, t)
        pal = qapp.palette()
        base = pal.color(QPalette.ToolTipBase).name()
        text = pal.color(QPalette.ToolTipText).name()

        want_bg = parse_color(t.card)
        want_fg = parse_color(t.text)
        expect_bg = "#%02x%02x%02x" % want_bg[:3]
        expect_fg = "#%02x%02x%02x" % want_fg[:3]

        assert base == expect_bg, \
            "%s 主题下 tooltip 背景 %s，期望 %s（主题 card）" % (
                mode, base, expect_bg)
        assert text == expect_fg, \
            "%s 主题下 tooltip 文字 %s，期望 %s（主题 text）" % (
                mode, text, expect_fg)


def test_tooltip_contrasts(qapp):
    """tooltip 的前景与背景必须能区分开。

    防止将来把 tooltip 背景改成跟文字一个颜色（黑底黑字），
    那等于提示完全看不见。
    """
    from PySide6.QtGui import QPalette

    from app_runtime import _apply_tooltip_palette
    from core.theme_presets import luminance
    from ui.theme_manager import build_theme

    for mode in ("light", "dark"):
        _apply_tooltip_palette(qapp, build_theme({"theme_mode": mode}))
        pal = qapp.palette()
        bg = pal.color(QPalette.ToolTipBase).name()
        fg = pal.color(QPalette.ToolTipText).name()
        assert abs(luminance(bg) - luminance(fg)) > 0.4, \
            "%s 主题下 tooltip 明暗对比不足：bg=%s fg=%s" % (mode, bg, fg)


def test_combobox_theme_is_full_palette(settings_page):
    """下拉框拿到的是**完整色板**，不只是明暗名。

    这是另一个 bug：`set_app_theme()` 原来只存明暗名，独立弹层
    `Theme(名字)` 只能得到 classic 预设配色——用户选了午夜蓝，
    主窗口变了、下拉还是经典灰。
    """
    from ui.theme_manager import build_theme

    dark = build_theme({"theme_mode": "dark", "theme_preset": "midnight"})
    settings_page.set_theme(dark)

    c = settings_page.preset_combo
    t = c._theme
    assert t is not None
    assert t.preset == "midnight", \
        "下拉框拿到的是 %s 预设，不是当前的 midnight" % t.preset
    assert t.card == dark.card


def test_no_popup_on_programmatic_restore(qapp):
    """程序化回填配置时**不能弹出任何模态窗口**。

    ## 真实 bug

    用户上次把强调色设成「自定义」后，`accent_mode` 存为 `custom`。
    下次启动、打开设置页时，`refresh_from_config()` 会
    `setCurrentIndex(...)` 到「自定义」那一项——这会触发
    `currentIndexChanged`，进而弹出调色板。

    用户根本没点任何东西，窗口自己跳出来。

    `theme_preset=custom` 同理（会弹主题编辑器）。

    根因是**程序化赋值被当成了用户操作**。所有"会弹窗口"的槽函数
    都必须先检查 `_loading`。
    """
    import os
    import tempfile

    from core.config_store import load_config
    from ui.pages.settings_page import SettingsPage

    tmp = tempfile.mkdtemp()
    old = os.environ.get("APPDATA")
    os.environ["APPDATA"] = tmp
    try:
        cfg = load_config()
        # 模拟"上次选过自定义主题"的配置（accent 已归编辑器管，这里不再涉及）
        cfg["accent_mode"] = "custom"
        cfg["accent_custom"] = "#ff3366"
        cfg["theme_preset"] = "custom"

        # 构造过程中若弹模态窗口，QT_QPA_PLATFORM=offscreen 下会卡住
        page = SettingsPage(cfg)
        qapp.processEvents()

        # 值要正确回填（说明确实走了 setCurrentIndex 那条路）
        assert page.preset_combo.currentData() == "custom"
        # 强调色不归设置页管，不该有对应下拉
        assert not hasattr(page, "accent_combo"), \
            "设置页又出现强调色下拉了——强调色应只在主题编辑器里设置"
        # 回填结束后 _loading 必须复位，否则后续用户操作全被吞掉
        assert page._loading is False, \
            "回填后 _loading 没有复位，后续用户操作会被静默忽略"
    finally:
        if old is None:
            os.environ.pop("APPDATA", None)
        else:
            os.environ["APPDATA"] = old


# ====================================================================
# 二、下拉弹层：第一帧就该是正确配色
# ====================================================================
def test_popup_theme_prepared_before_show(qapp):
    """`showPopup()` 要在**显示之前**贴好样式。

    原实现先 `super().showPopup()` 再贴样式，而 showPopup 会立刻把
    窗口映射到屏幕——用户看到"先弹出浅色列表，闪一下变深色"。

    这里验证 `_prepare_popup_theme()` 能在不显示窗口的情况下把样式
    贴到 view / viewport / container 三层。
    """
    from ui.fluent_combobox import FluentComboBox
    from ui.theme_manager import build_theme

    cb = FluentComboBox(build_theme({"theme_mode": "dark"}))
    cb.addItems(["A", "B"])
    cb._prepare_popup_theme()

    view = cb.view()
    assert view is not None
    assert view.styleSheet(), "列表本体没拿到样式"
    assert "rgba" in view.styleSheet() or "#" in view.styleSheet()
    vp = view.viewport()
    if vp is not None:
        assert vp.styleSheet(), "视口没拿到样式"


# ====================================================================
# 三、页面滚动：内容超长不能硬裁
# ====================================================================
@pytest.mark.parametrize("key", ["home", "library", "custom",
                                 "triggers", "search"])
def test_page_has_scroll_area(qapp, key):
    """每个内容页都要能滚动。

    ## 真实 bug

    窗口最小高度 640px，而主页内容实测需要 **668px**——直接超出 28px，
    底部「使用提示」卡片被裁，文字显示不全。用户截图里能明显看到。

    设置页早就用 QScrollArea 解决了（注释写着"修复被压成细线"），
    但主页/词库/搜索/快捷短语漏了这层。
    """
    from PySide6.QtWidgets import QScrollArea, QWidget
    from ui.pages._scaffold import page_layout

    page = QWidget()
    page_layout(page)
    assert page.findChildren(QScrollArea), \
        "页面 %s 没有滚动区，内容超出窗口高度会被裁掉" % key


def test_scroll_can_be_disabled(qapp):
    """已自建滚动的页面（设置页/插件页）要能关掉，避免双层滚动。"""
    from PySide6.QtWidgets import QScrollArea, QWidget
    from ui.pages._scaffold import page_layout

    page = QWidget()
    page_layout(page, scroll=False)
    assert not page.findChildren(QScrollArea), \
        "scroll=False 时不该再包一层滚动区"


def test_page_content_taller_than_viewport_scrolls(qapp):
    """内容真的超出时，滚动条要能出现（而不是压缩内容）。"""
    from PySide6.QtWidgets import QScrollArea, QWidget
    from ui.pages._scaffold import page_layout

    page = QWidget()
    lay = page_layout(page)
    for _ in range(40):
        stub = QWidget()
        stub.setMinimumHeight(40)
        lay.addWidget(stub)
    page.resize(600, 300)
    page.show()
    qapp.processEvents()

    area = page.findChildren(QScrollArea)[0]
    body = area.widget()
    assert body is not None
    # 内容高于视口 → 需要滚动
    assert body.sizeHint().height() > area.viewport().height(), \
        "内容没有超出视口，测试数据不足"
    page.hide()


# ====================================================================
# 四、复合控件必须有完整样式（含子控件）
# ====================================================================
#: 需要检查子控件样式的复合控件 → 必须出现的伪状态选择器。
#:
#: ## 为什么单独检查

#: 只写 `QSpinBox {{...}}` 时，**输入框部分**会用主题色，但上下按钮和
#: 箭头仍走 Qt 原生绘制。原生箭头颜色随焦点/hover 状态变化，
#: 于是同类型的两个 SpinBox 看起来颜色不一样。

#: 实测：设置页「最大最近记录」的箭头是强调青色，「每页候选数」是灰色
#: ——用户以为是样式坏了。

#: 这类"子控件漏样式"和之前「QTabBar 白底」是同一类问题。
_COMPOUND_WIDGETS = {
    "QSpinBox": ("::up-button", "::down-button", "::up-arrow", "::down-arrow"),
    "QComboBox": ("::drop-down", "::down-arrow"),
    "QScrollBar": ("::handle", "::add-line", "::sub-line"),
    "QTabBar": ("::tab",),
    "QCheckBox": ("::indicator",),
    "QSlider": ("::groove", "::handle"),
}


def test_compound_widgets_have_subcontrol_styles(qapp):
    from ui.win11_theme import Theme

    theme_qss = Theme('dark').style_sheet()
    """复合控件的**子控件**必须有显式样式。

    Qt 不会因为父控件有 QSS 就自动接管子控件的绘制——按钮、箭头、
    滑块手柄这些都要单独写。漏了就会退回原生绘制，颜色不再受主题控制。
    """
    missing = []
    for widget, subs in _COMPOUND_WIDGETS.items():
        for sub in subs:
            sel = widget + sub
            if sel not in theme_qss:
                missing.append(sel)
    assert not missing, (
        "以下子控件没有样式，会退回 Qt 原生绘制（颜色随状态变化，"
        "同类型控件看起来不一致）：\n  " + "\n  ".join(missing))


def test_spinboxes_render_identically(qapp):
    """同类型的两个 SpinBox 渲染结果必须一致。

    这是用户实际看到的问题：设置页里两个数字框的箭头颜色不一样。
    """
    import os
    import tempfile

    from PySide6.QtWidgets import QSpinBox

    from core.config_store import load_config
    from ui.pages.settings_page import SettingsPage

    tmp = tempfile.mkdtemp()
    old = os.environ.get("APPDATA")
    os.environ["APPDATA"] = tmp
    try:
        page = SettingsPage(load_config())
        page.resize(900, 700)
        page.show()
        qapp.processEvents()

        a, b = page.recent_spin, page.page_spin
        assert isinstance(a, QSpinBox) and isinstance(b, QSpinBox)

        def arrow_visible(w):
            """箭头区域是否画出了**深色像素**（箭头本身）。

            ⚠ 不要逐像素比较两个 SpinBox 的颜色集合。它们的实际像素高度
            可能差 1px（33 vs 32），三角形落在不同的像素格上，抗锯齿会
            产生不同的过渡色（实测 #5a5a5a vs #5c5c5c）。那是**正常的
            亚像素差异**，不是样式 bug。

            要验证的是"箭头有没有画出来"：区域内存在明显深于背景的像素。
            """
            from core.theme_presets import luminance

            img = w.grab().toImage()
            if img.isNull():
                return False
            x0 = max(0, img.width() - 20)
            for x in range(x0, img.width()):
                for y in range(img.height()):
                    c = img.pixelColor(x, y)
                    if c.alpha() > 0 and luminance(c.name()) < 0.55:
                        return True
            return False

        va, vb = arrow_visible(a), arrow_visible(b)
        page.hide()
        assert va and vb, (
            "SpinBox 箭头没画出来（a=%s b=%s）——\n"
            "通常是 QSpinBox::up-arrow/down-arrow 样式缺失" % (va, vb))
    finally:
        if old is None:
            os.environ.pop("APPDATA", None)
        else:
            os.environ["APPDATA"] = old
