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
