"""`--diag` 自检：把图标字体与颜文字字体链的加载情况写到 diag.txt。

为什么放在 ui/ 而不是 core/：判定「字形是否真的可用」只能靠**真画一遍比对像素**
（`QRawFont.supportsCharacter` 在 Windows 上对印度语系字符会返回假阳性），
因此这里必然依赖 QPainter。主程序打包成窗口程序后没有控制台，
自检结果必须落盘，用户才能拿 diag.txt 反馈问题。
"""
import os
import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import (
    QColor, QFont, QFontDatabase, QFontInfo, QImage, QPainter,
)

from core import runtime
from core.applog import MUTED_LOG_COUNT
from ui.fluent_icons import FONT_FILE, GLYPHS, char, font_status
from ui.win11_theme import kaomoji_font


def _render_glyph(font, ch, size):
    """把一个字符画到白底图片上，返回 (像素字节, 墨迹像素数)。

    豆腐块/缺字形的表现有两种：一片空白，或所有字符画出来一模一样。
    比 supportsCharacter 可靠。
    """
    img = QImage(size, size, QImage.Format_ARGB32)
    img.fill(QColor(255, 255, 255))
    p = QPainter(img)
    p.setFont(font)
    p.setPen(QColor(0, 0, 0))
    p.drawText(img.rect(), Qt.AlignCenter, ch)
    p.end()
    ink = sum(1 for y in range(size) for x in range(size)
              if img.pixelColor(x, y).lightness() < 200)
    return img.constBits().tobytes(), ink


def write_diag():
    """执行自检并将结果写入 diag.txt（打包态写 exe 同目录，源码态写项目根）。"""
    family, err = font_status()
    path = runtime.resource_path("ui", "fonts", FONT_FILE)
    lines = [
        "frozen        = %s" % runtime.is_frozen(),
        "font path     = %s" % path,
        "  isfile      = %s" % os.path.isfile(path),
        "  isdir       = %s" % os.path.isdir(path),
        "registered    = %r" % family,
        "error         = %r" % err,
    ]

    icon_font = QFont(family)
    icon_font.setPixelSize(24)
    lines.append("resolved      = %r" % QFontInfo(icon_font).family())

    # 逐个图标真画一遍：缺字形时要么空白，要么全部长一样
    shapes, blank = {}, []
    for name in GLYPHS:
        data, ink = _render_glyph(icon_font, char(name), 32)
        if ink < 6:
            blank.append(name)
        shapes.setdefault(data, []).append(name)
    identical = [v for v in shapes.values() if len(v) > 1]

    lines.append("blank glyphs  = %s" % (blank or "none"))
    lines.append("identical sets= %s" % (identical or "none"))

    # 颜文字字体链：库里混着印度语系字符，实测它们能不能真的画出字形
    kao_font = kaomoji_font(14)
    kao_font.setPixelSize(24)
    installed = set(QFontDatabase.families())
    lines.append("")
    lines.append("kaomoji font chain:")
    # 无头/offscreen 环境枚举不到系统字体，此时该项无意义，跳过判定。
    # 注意不能用 len(installed)==0 判断：ensure_icon_font() 已注册内置图标字体，
    # 字体库永远至少有 1 个。改判「回退链里一个系统字体都没装上」。
    degenerate = not any(fam in installed for fam in kao_font.families())
    if degenerate:
        lines.append("  (枚举不到任何系统字体——offscreen/无头环境，跳过此项检查)")
    lines.append("  families    = %s" % ", ".join(kao_font.families()))

    missing_kao = []
    if not degenerate:
        tofu, _ = _render_glyph(kao_font, "\uffff", 48)   # 一定是 .notdef 豆腐块，作参照
        # 标签只用 ASCII：诊断结果要往 stdout 打，Windows 控制台是 GBK 代码页，
        # 直接把 ಠ/દ 写进去会 UnicodeEncodeError。
        for label, ch in (("Kannada  U+0CA0", "\u0ca0"),
                          ("Gujarati U+0AA6", "\u0aa6")):
            data, ink = _render_glyph(kao_font, ch, 48)
            if data == tofu:
                state = "TOFU 豆腐块"
            elif ink < 6:
                state = "BLANK 空白"
            else:
                state = "OK"
            lines.append("  %-16s ink=%-5d %s" % (label, ink, state))
            if state != "OK":
                missing_kao.append(label)

    lines.append("  噪声告警过滤    = 已拦下 %d 条 qt.text.font.db"
                 % MUTED_LOG_COUNT["n"])

    ok = (not blank) and (not identical) and not err and not missing_kao
    lines.append("")
    lines.append("RESULT        = %s" % ("OK" if ok else "FAIL"))

    base = (os.path.dirname(sys.executable) if runtime.is_frozen()
            else runtime.source_base_dir())
    out = os.path.join(base, "diag.txt")
    text = "\n".join(lines) + "\n"
    try:
        with open(out, "w", encoding="utf-8") as fp:
            fp.write(text)
    except OSError:
        pass
    # 控制台可能是 GBK 代码页，写不进去的字符降级处理，别让自检自己崩掉
    try:
        sys.stdout.write(text)
    except UnicodeEncodeError:
        enc = getattr(sys.stdout, "encoding", None) or "utf-8"
        sys.stdout.write(text.encode(enc, "replace").decode(enc, "replace"))
