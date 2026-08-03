"""应用图标：系统托盘与 exe 文件共用同一张图。

用纯代码绘制（白底圆角方块 + 颜文字风小圆脸），不依赖任何外部图片文件；
save_ico() 把各尺寸以 PNG 编码写入 .ico，保证 exe 图标与托盘图标「同款」且清晰。
"""
import os
import struct
import sys

from PySide6.QtGui import QPixmap, QPainter, QColor, QImage, QIcon
from PySide6.QtCore import Qt, QByteArray, QBuffer, QIODevice
from PySide6.QtWidgets import QApplication


def draw_icon_pixmap(size=64):
    """绘制一张指定尺寸的图标位图（与系统托盘同款样式）。

    不用文本绘制，避免大字号 / 不同系统字体回退导致颜文字变成方块或 tofu。
    """
    pix = QPixmap(size, size)
    pix.fill(QColor(0, 0, 0, 0))
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)

    # 白底圆角方块
    margin = max(2, int(round(size * 0.06)))
    radius = int(round(size * 0.22))
    p.setBrush(QColor(255, 255, 255))
    p.setPen(Qt.NoPen)
    p.drawRoundedRect(
        margin, margin,
        size - 2 * margin, size - 2 * margin,
        radius, radius,
    )

    dark = QColor(40, 40, 40)
    p.setPen(Qt.NoPen)

    # 两只眼睛（◕ 用实心圆 + 左上小高光模拟）
    eye_r = max(2, int(round(size * 0.10)))
    eye_y = int(round(size * 0.40))
    left_x = int(round(size * 0.36))
    right_x = int(round(size * 0.64))
    p.setBrush(dark)
    p.drawEllipse(left_x - eye_r, eye_y - eye_r, 2 * eye_r, 2 * eye_r)
    p.drawEllipse(right_x - eye_r, eye_y - eye_r, 2 * eye_r, 2 * eye_r)

    # 眼睛高光（让眼睛看起来像 ◕ 而不是纯黑点）
    hl_r = max(1, int(round(eye_r * 0.35)))
    hl_offset = int(round(eye_r * 0.35))
    p.setBrush(QColor(255, 255, 255))
    p.drawEllipse(
        left_x - hl_offset - hl_r, eye_y - hl_offset - hl_r,
        2 * hl_r, 2 * hl_r,
    )
    p.drawEllipse(
        right_x - hl_offset - hl_r, eye_y - hl_offset - hl_r,
        2 * hl_r, 2 * hl_r,
    )

    # 嘴巴（‿ 用底部小椭圆模拟）
    mouth_w = max(2, int(round(size * 0.18)))
    mouth_h = max(2, int(round(size * 0.10)))
    mouth_x = size // 2
    mouth_y = int(round(size * 0.63))
    p.setBrush(dark)
    p.drawEllipse(
        mouth_x - mouth_w // 2, mouth_y - mouth_h // 2,
        mouth_w, mouth_h,
    )

    p.end()
    return pix


def make_icon():
    """返回 QIcon（系统托盘 / 窗口图标使用）。"""
    return QIcon(draw_icon_pixmap(64))


def save_ico(path, sizes=(16, 32, 48, 64, 128, 256)):
    """把多分辨率图标以 PNG 编码写入 .ico 文件。

    现代 Windows（Vista+）与 PyInstaller 均支持 ICO 内嵌 PNG，
    比手写 BMP 掩码体积更小、兼容性更好。
    """
    # 绘制 / 读取像素需要 QApplication；若当前没有（如打包脚本里单独调用），
    # 临时建一个 offscreen 的即可，避免 Qt 在 QPainter 时报致命错误退出。
    app = QApplication.instance()
    if app is None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        app = QApplication(sys.argv)

    images = []
    for s in sizes:
        img = draw_icon_pixmap(s).toImage().convertToFormat(QImage.Format_ARGB32)
        ba = QByteArray()
        buf = QBuffer(ba)
        buf.open(QIODevice.WriteOnly)
        # PNG 编码，小体积且支持透明
        img.save(buf, "PNG")
        images.append(bytes(ba))

    count = len(images)
    icon_dir = struct.pack("<HHH", 0, 1, count)
    entries = b""
    offset = 6 + count * 16
    for i, im in enumerate(images):
        # ICO 规范：宽高为 0 表示 256
        w = sizes[i] % 256
        h = sizes[i] % 256
        # PNG 数据仍按 32bpp 标记，保持兼容性
        entries += struct.pack(
            "<BBBBHHII", w, h, 0, 0, 1, 32, len(im), offset
        )
        offset += len(im)
    data = icon_dir + entries + b"".join(images)
    with open(path, "wb") as f:
        f.write(data)
