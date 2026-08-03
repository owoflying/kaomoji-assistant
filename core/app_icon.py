"""应用图标：系统托盘与 exe 文件共用同一张图。

用纯代码绘制（白底圆角方块 + 颜文字），不依赖任何外部图片文件；
save_ico() 直接拼出多分辨率 .ico，保证 exe 图标与托盘图标「同款」。
"""
import os
import struct
import sys

from PySide6.QtGui import QPixmap, QPainter, QColor, QFont, QImage, QIcon
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication


def draw_icon_pixmap(size=64):
    """绘制一张指定尺寸的图标位图（与系统托盘同款样式）。"""
    pix = QPixmap(size, size)
    pix.fill(QColor(0, 0, 0, 0))
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QColor(255, 255, 255))
    p.setPen(QColor(0, 0, 0, 0))
    r = int(size * 0.22)
    p.drawRoundedRect(4, 4, size - 8, size - 8, r, r)
    p.setPen(QColor(40, 40, 40))
    p.setFont(QFont("Segoe UI", int(size * 0.44)))
    p.drawText(pix.rect(), Qt.AlignCenter, "(◕‿◕)")
    p.end()
    return pix


def make_icon():
    """返回 QIcon（系统托盘 / 窗口图标使用）。"""
    return QIcon(draw_icon_pixmap(64))


def save_ico(path, sizes=(16, 32, 48, 64, 128, 256)):
    """把多分辨率图标写入 .ico 文件（BGRA + 全透明 AND 掩码）。"""
    # 绘制 / 读取像素需要 QApplication；若当前没有（如打包脚本里单独调用），
    # 临时建一个 offscreen 的即可，避免 Qt 在 QPainter 时报致命错误退出。
    app = QApplication.instance()
    if app is None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        app = QApplication(sys.argv)

    images = []
    for s in sizes:
        img = draw_icon_pixmap(s).toImage().convertToFormat(QImage.Format_ARGB32)
        h = img.height()
        bpl = img.bytesPerLine()
        buf = img.constBits()
        # 兼容不同 PySide6 版本：constBits() 可能返回 sip.voidptr 或 memoryview
        if hasattr(buf, "asstring"):
            raw = buf.asstring(img.sizeInBytes())
        else:
            raw = bytes(buf)[:img.sizeInBytes()]
        # QImage 行序自上而下，BMP/ICO 要求自下而上，需翻转
        rows = [raw[y * bpl:(y + 1) * bpl] for y in range(h)]
        rows.reverse()
        xor_data = b"".join(rows)
        and_stride = ((img.width() + 31) // 32) * 4
        and_data = b"\x00" * (and_stride * h)
        bih = struct.pack(
            "<IiiHHIIiiII", 40, img.width(), h * 2, 1, 32, 0,
            len(xor_data) + len(and_data), 0, 0, 0, 0,
        )
        images.append(bih + xor_data + and_data)

    count = len(images)
    icon_dir = struct.pack("<HHH", 0, 1, count)
    entries = b""
    offset = 6 + count * 16
    for im in images:
        w = struct.unpack_from("<i", im, 4)[0]
        h = struct.unpack_from("<i", im, 8)[0] // 2
        entries += struct.pack(
            "<BBBBHHII", w % 256, h % 256, 0, 0, 1, 32, len(im), offset
        )
        offset += len(im)
    data = icon_dir + entries + b"".join(images)
    with open(path, "wb") as f:
        f.write(data)
