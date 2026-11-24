"""主题 QSS 覆盖检查：防止「新控件忘了写样式」。

## 保护什么

Qt 的全局 QSS 是**匹配式**的：没匹配到规则的控件会用系统默认绘制，
而系统默认是浅色。深色主题下就变成"白底浅字"，内容几乎看不见。

这个坑本项目踩过两次：

* 「使用统计」对话框的排行框（`QPlainTextEdit` 没有规则）
* 「从剪贴板提取」对话框的列表（同上）
* 「使用统计」的标签页（`QTabBar` 没有规则，选中/未选中全是白底）

共同特征：**界面上大部分地方正常，只有打开某个对话框才发现**。
肉眼看代码很难发现——你看不到一个"不存在的规则"。

所以用测试把这件事自动化：扫描项目源码里用到的所有 Qt 控件类型，
逐个检查主题 QSS 里有没有对应规则。以后新增控件时测试会直接失败，
而不是等用户截图来反馈。
"""
import os
import re
import sys

import pytest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

#: 不需要样式的控件：它们是容器/纯逻辑，没有可见外观。
#:
#: 判定标准：加进这个名单前先想清楚"它在深色主题下会不会显示成白底"。
_NO_STYLE_NEEDED = {
    "QWidget",          # 容器基类，子控件各自有样式
    "QObject", "QApplication", "QLayout", "QHBoxLayout", "QVBoxLayout",
    "QGridLayout", "QFormLayout", "QStackedLayout", "QButtonGroup",
    "QSystemTrayIcon", "QAction", "QMenuBar", "QFileDialog",
    "QTimer", "QEvent", "QFont", "QIcon", "QPixmap", "QImage",
    "QPainter", "QColor", "QCursor", "QPoint", "QRect", "QSize",
    "QUrl", "QDesktopServices", "QMimeData", "QClipboard",
    "QGraphicsOpacityEffect", "QPropertyAnimation", "QVariantAnimation",
    "QParallelAnimationGroup", "QEasingCurve", "QAbstractAnimation",
    "QStandardPaths", "QThread", "QNetworkAccessManager",
    "QNetworkRequest", "QNetworkReply", "QGuiApplication", "QScreen",
    "QFontDatabase", "QFontMetrics", "QTransform", "QPainterPath",
    "QConicalGradient", "QLinearGradient", "QBrush", "QPen", "QPalette",
    "QStyleOption", "QStyle", "QApplication",
    # 非可视类型：数据项 / 布局策略 / 绘制选项，本身不绘制
    "QListWidgetItem", "QTableWidgetItem", "QTreeWidgetItem",
    "QSizePolicy", "QSpacerItem", "QStyleOptionButton",
    # QGraphics* 是离屏图形对象（背景模糊用），不参与界面渲染
    "QGraphicsBlurEffect", "QGraphicsPixmapItem", "QGraphicsScene",
    # 顶层容器：内部控件各自有样式
    "QMainWindow",
}


def _project_py_files():
    """项目源码里的 .py 文件（排除测试与工具）。"""
    out = []
    for base in ("ui", "core"):
        d = os.path.join(HERE, base)
        for root, _dirs, files in os.walk(d):
            for f in files:
                if f.endswith(".py"):
                    out.append(os.path.join(root, f))
    for f in ("app_runtime.py", "main.py"):
        p = os.path.join(HERE, f)
        if os.path.isfile(p):
            out.append(p)
    return out


def _widgets_used():
    """扫描源码，收集所有从 PySide6.QtWidgets 导入的控件类名。"""
    pat_import = re.compile(
        r"from\s+PySide6\.QtWidgets\s+import\s*\(([^)]*)\)", re.S)
    pat_import2 = re.compile(r"from\s+PySide6\.QtWidgets\s+import\s+(.+)")
    names = set()
    for path in _project_py_files():
        try:
            with open(path, encoding="utf-8") as f:
                src = f.read()
        except Exception:
            continue
        for m in pat_import.finditer(src):
            for part in m.group(1).split(","):
                part = part.strip().split(" as ")[0].strip()
                if part.startswith("Q"):
                    names.add(part)
        for m in pat_import2.finditer(src):
            for part in m.group(1).split(","):
                part = part.strip().split(" as ")[0].strip()
                if part.startswith("Q"):
                    names.add(part)
    return names


@pytest.fixture(scope="module")
def theme_qss(qapp):
    from ui.win11_theme import Theme

    return Theme("dark").style_sheet()


def test_every_used_widget_has_style(theme_qss):
    """项目里用到的每个 Qt 控件，主题里都要有对应规则。

    失败信息会列出缺哪些——照着加到 `ui/win11_theme.py` 即可。
    """
    used = _widgets_used()
    missing = []
    for name in sorted(used):
        if name in _NO_STYLE_NEEDED:
            continue
        # QSS 里出现 "QTabBar" 或 "QTabBar::xxx" 都算覆盖
        if re.search(r"(?<![\w])%s\b" % re.escape(name), theme_qss):
            continue
        missing.append(name)

    assert not missing, (
        "以下控件在项目里用到了，但主题 QSS 没有对应规则——"
        "深色主题下它们会退回系统浅色默认样式，显示成白底浅字：\n  "
        + "\n  ".join(missing)
        + "\n\n请在 ui/win11_theme.py 的 style_sheet() 里补上，"
          "或（若确实不需要样式）加到本测试的 _NO_STYLE_NEEDED 里。"
    )


def test_text_inputs_are_styled(theme_qss):
    """文本框必须有明确的前景/背景色。

    这是踩过两次的具体场景，单独再验一遍（比通用检查更直白）。
    """
    for w in ("QPlainTextEdit", "QTextEdit"):
        assert w in theme_qss, "%s 没有样式，深色主题下会白底浅字" % w
    # 背景色也必须是主题色，而不是留空继承
    m = re.search(r"QPlainTextEdit[^{]*\{([^}]*)\}", theme_qss)
    assert m, "QPlainTextEdit 规则格式异常"
    body = m.group(1)
    assert "background" in body, "QPlainTextEdit 缺少 background"
    assert "color" in body, "QPlainTextEdit 缺少 color"


def test_tab_bar_is_styled(theme_qss):
    """选项卡必须有样式——否则选中态和未选中态都是白底。"""
    assert "QTabBar::tab" in theme_qss, "QTabBar::tab 没有样式"
    assert "QTabBar::tab:selected" in theme_qss, \
        "QTabBar 缺少选中态样式"


def test_dark_theme_colors_are_dark(qapp):
    """深色主题的背景必须是暗色。

    防止将来改成浅色背景却忘了改对应的文字色——
    那会变成"暗底暗字"，同样看不见。
    """
    from ui.win11_theme import Theme

    t = Theme("dark")
    assert t.dark is True
    for name in ("bg", "card"):
        val = getattr(t, name, "")
        m = re.match(r"#([0-9a-fA-F]{6})", val or "")
        if m:
            r, g, b = (int(m.group(1)[i:i + 2], 16) for i in (0, 2, 4))
            assert (r + g + b) / 3 < 120, \
                "深色主题的 %s 太亮：%s" % (name, val)


def test_light_theme_colors_are_light(qapp):
    from ui.win11_theme import Theme

    t = Theme("light")
    assert t.dark is False
    val = t.bg
    m = re.match(r"#([0-9a-fA-F]{6})", val or "")
    if m:
        r, g, b = (int(m.group(1)[i:i + 2], 16) for i in (0, 2, 4))
        assert (r + g + b) / 3 > 150, "浅色主题的 bg 太暗：%s" % val


def test_stylesheet_is_valid_qss(theme_qss):
    """大括号要配平——QSS 写错一个括号，Qt 会静默丢弃整条规则。"""
    assert theme_qss.count("{") == theme_qss.count("}"), \
        "QSS 大括号不配平（%d 个 { vs %d 个 }）" % (
            theme_qss.count("{"), theme_qss.count("}"))
    assert theme_qss.strip(), "主题 QSS 是空的"


# ====================================================================
# 二、硬编码颜色防复发
# ====================================================================
#: 允许保留硬编码颜色的位置（语义固定，不该跟主题走）。
#:
#: 判定标准：换个配色预设，这个颜色**依然应该**是这个值。
_ALLOWED_HARDCODED = {
    # 语义色：错误/危险/成功在任何主题下都该是同一个颜色语义
    ("ui/win11_theme.py", "shape"),          # 文件内注释与文档
    ("ui/dev/dev_page.py", "danger_button"),  # 危险操作按钮的规范红
    ("ui/toggle_switch.py", "thumb"),         # 开关滑块永远是白的
    ("ui/fluent_checkbox.py", "check_mark"),  # 对勾永远是白的
    ("ui/fluent_icons.py", "icon_default"),   # 图标默认灰
}


def _ui_sources():
    """ui/ 下所有 .py 源码路径（相对项目根）。"""
    out = []
    d = os.path.join(HERE, "ui")
    for root, _dirs, files in os.walk(d):
        for f in files:
            if f.endswith(".py"):
                rel = os.path.relpath(os.path.join(root, f), HERE)
                out.append(rel.replace("\\", "/"))
    return out


def test_no_stray_hardcoded_colors():
    """ui/ 里不允许有"忘了收敛到主题"的硬编码颜色。

    ## 为什么这条重要

    主题系统的完整度取决于**所有**颜色都跟着主题走。
    只要有一处写死，换配色预设那里就不变——表现为"某个控件没变色"，
    而且这类遗漏肉眼极难发现（和 QSS 漏样式是同一类问题）。

    `ui/win11_theme.py` 豁免：它就是主题色的定义处。
    语义色（危险红、开关白滑块）也在白名单里，它们**本来就不该**跟着变。
    """
    import re

    #: 允许豁免的文件（主题定义处 + 有明确语义的固定色）
    exempt_files = {"ui/win11_theme.py", "ui/theme_manager.py"}
    #: 允许豁免的具体行特征（危险按钮 / 滑块 / 对勾 / 图标默认灰）
    exempt_markers = (
        "#d13438", "#b62a2e",   # 危险按钮规范红
        "#ffffff",              # 白滑块/白对勾/危险按钮文字
        "#000000",              # 纯黑兜底
        "#888888",              # 图标默认灰
        "#0067c0", "#60cdff",   # picker 的兜底色板（极端情况才用）
        "#1f1f1f", "#f0f0f0",
        "#8a8a8e", "#a0a0a6", "#6b6b70", "#b8b8be",
        "255,255,255", "43,43,43",
    )
    pat = re.compile(r"#[0-9a-fA-F]{6}\b")
    offenders = []
    for rel in _ui_sources():
        if rel in exempt_files:
            continue
        path = os.path.join(HERE, rel)
        try:
            with open(path, encoding="utf-8") as f:
                for lineno, line in enumerate(f, 1):
                    s = line.strip()
                    if s.startswith("#"):        # 纯注释行
                        continue
                    for m in pat.finditer(line):
                        col = m.group(0).lower()
                        if col in exempt_markers:
                            continue
                        offenders.append("%s:%d %s" % (rel, lineno, col))
        except Exception:
            continue

    assert not offenders, (
        "以下硬编码颜色没有收敛到主题（换配色预设时它们不会变）：\n  "
        + "\n  ".join(offenders)
        + "\n\n应该改成 theme.xxx；若确实是语义固定色，"
          "把它加到本测试的 exempt_markers 里并说明理由。"
    )


def test_picker_palette_follows_theme(qapp):
    """候选条配色必须跟随主题预设，不能再用静态色板。

    这是实测踩过的坑：主窗口换成午夜蓝，候选条还是经典灰，
    看起来像两个程序拼在一起。
    """
    from core.theme_presets import resolve_palette
    from ui.picker_window import _palette_from_theme
    from ui.win11_theme import Theme

    for preset in ("classic", "midnight", "warm"):
        palette = resolve_palette(preset, "dark")
        theme = Theme("dark", palette=palette)
        pal = _palette_from_theme(theme)
        # 候选条背景必须与主题 card 一致（RGB 数值相同）
        from core.theme_presets import parse_color

        want = parse_color(theme.card)
        got = [int(x) for x in pal["bg"].split(",")]
        assert got == list(want[:3]), (
            "预设 %s 的候选条背景 %r 与主题 card %r 不一致"
            % (preset, got, want[:3]))


def test_semantic_colors_differ_by_mode(qapp):
    """语义色必须随明暗切换——警告橙在深色背景上看不见。"""
    from core.theme_presets import semantic

    light = semantic(False)
    dark = semantic(True)
    for key in ("warning", "success", "danger", "info"):
        assert key in light and key in dark
        assert light[key] != dark[key], \
            "%s 在深浅主题下用了同一个值，总有一边看不清" % key


def test_on_accent_contrasts(qapp):
    """强调色上的文字要能看清。

    高对比度深色预设的强调色是亮黄 `#ffff00`——白字在上面完全看不见。
    所以 `on_accent` 必须按强调色亮度自动选黑或白。
    """
    from core.theme_presets import luminance, resolve_palette
    from ui.win11_theme import Theme

    for preset in ("classic", "midnight", "warm", "contrast"):
        for mode in ("light", "dark"):
            palette = resolve_palette(preset, mode)
            theme = Theme(mode, palette=palette)
            accent_lum = luminance(theme.accent)
            on_lum = luminance(theme.on_accent)
            assert abs(accent_lum - on_lum) > 0.35, (
                "%s/%s 的强调色 %s 与文字色 %s 对比不足"
                % (preset, mode, theme.accent, theme.on_accent))
