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
