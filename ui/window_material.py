"""窗口背景材质：自定义背景图 + 毛玻璃/磨砂/雾化。

## 为什么要自绘，而不是用系统亚克力

原来调 Windows 的 `DwmSetWindowAttribute(DWMWA_SYSTEMBACKDROP_TYPE)` 开
系统亚克力。但该属性**只在 Win11 (build 22000+) 存在**，Win10 上返回
`0x80070057`（参数错误）——完全无效。实测本机（Win10 19045）：

    DwmSetWindowAttribute(backdrop) HRESULT = 0x80070057

而且旧代码用 `except: pass` 吞掉了失败，看起来"开了亚克力"其实没有。

更重要的是：系统亚克力只能模糊**窗口后面的桌面**，无法模糊我们自己的
背景图。要做"自定义背景 + 毛玻璃"，只能自己把图模糊了再画。

所以这里全部走 Qt 自绘，跨平台稳定，也能和背景图配合。

## 四种材质

    none     —— 不处理，背景图/底色原样绘制
    blur     —— 毛玻璃：高斯模糊（半径较大）
    frosted  —— 磨砂玻璃：轻模糊 + 细噪点颗粒
    fog      —— 雾化：不模糊，用半透明白/黑叠层压低对比

## 性能

模糊整窗背景（约 1000x700）单次 30~60ms，**不能每帧算**。
所以结果缓存起来：只有"背景图变了 / 尺寸变了 / 材质参数变了"
才重算。窗口重绘时直接用缓存的 QPixmap。
"""
import os

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QImage, QLinearGradient, QPainter, QPixmap

#: 材质类型 → (显示名, 说明)
MATERIALS = {
    "none": ("无", "背景图/底色原样显示"),
    "blur": ("毛玻璃", "高斯模糊，背景隐约可见"),
    "frosted": ("磨砂玻璃", "轻微模糊 + 细颗粒噪点"),
    "fog": ("雾化", "半透明叠层压低对比，画面变柔和"),
}

DEFAULT_MATERIAL = "none"

#: 各种材质的默认强度（0~100）
DEFAULT_STRENGTH = 55


def material_choices():
    """给设置页用：[(id, 显示名), ...]"""
    return [(k, v[0]) for k, v in MATERIALS.items()]


def normalize_material(name):
    """把配置里的材质名归一到合法值。"""
    n = str(name or "").lower()
    return n if n in MATERIALS else DEFAULT_MATERIAL


# ====================================================================
# 背景图加载
# ====================================================================

#: 支持的图片后缀
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp")


def is_image_file(path):
    return bool(path) and os.path.splitext(str(path))[1].lower() in IMAGE_SUFFIXES


def copy_background(source):
    """把用户选的背景图复制到数据目录，返回 (成功, 目标路径或错误)。

    ## 为什么要复制

    用户可能从桌面/下载目录选一张图，那个文件随时可能被删被移动。
    直接引用原路径，下次启动就找不到背景了。

    复制到 `%APPDATA%/KaomojiAssistant/background.<ext>`，
    删掉旧的同名文件避免堆积。
    """
    if not source or not os.path.isfile(source):
        return False, "文件不存在"
    if not is_image_file(source):
        return False, "不支持的图片格式（支持 png/jpg/bmp/gif/webp）"
    try:
        from core.runtime import app_data_dir

        data_dir = app_data_dir()
        os.makedirs(data_dir, exist_ok=True)
        ext = os.path.splitext(source)[1].lower()
        # 清掉其它后缀的旧背景，避免残留
        for old in IMAGE_SUFFIXES:
            p = os.path.join(data_dir, "background" + old)
            if os.path.isfile(p):
                try:
                    os.remove(p)
                except Exception:
                    pass
        dst = os.path.join(data_dir, "background" + ext)
        # 用二进制读写，GIF 也能原样复制
        with open(source, "rb") as f:
            data = f.read()
        if len(data) > 32 * 1024 * 1024:
            return False, "图片太大（超过 32MB）"
        with open(dst, "wb") as f:
            f.write(data)
        return True, dst
    except Exception as e:
        return False, "复制失败：%r" % (e,)


def background_path():
    """当前背景图路径；没设置时返回 None。"""
    try:
        from core.runtime import app_data_dir

        data_dir = app_data_dir()
        for ext in IMAGE_SUFFIXES:
            p = os.path.join(data_dir, "background" + ext)
            if os.path.isfile(p):
                return p
    except Exception:
        pass
    return None


def clear_background():
    """删除已设置的背景图。返回是否删掉了。"""
    p = background_path()
    if not p:
        return False
    try:
        os.remove(p)
        return True
    except Exception:
        return False


# ====================================================================
# 材质渲染
# ====================================================================

def _apply_blur(img, radius):
    """高斯模糊。用 QGraphicsBlurEffect 离屏渲染——Qt 没有直接的
    图像模糊 API，这是标准做法。"""
    if radius <= 0:
        return img
    try:
        from PySide6.QtGui import QPainter as _P
        from PySide6.QtWidgets import (
            QGraphicsBlurEffect, QGraphicsPixmapItem, QGraphicsScene,
        )

        scene = QGraphicsScene()
        item = QGraphicsPixmapItem(QPixmap.fromImage(img))
        effect = QGraphicsBlurEffect()
        effect.setBlurRadius(radius)
        effect.setBlurHints(QGraphicsBlurEffect.QualityHint)
        item.setGraphicsEffect(effect)
        scene.addItem(item)

        out = QImage(img.size(), QImage.Format_ARGB32_Premultiplied)
        out.fill(Qt.transparent)
        p = _P(out)
        scene.render(p, source=scene.sceneRect() if not scene.sceneRect().isEmpty()
                     else QRect(0, 0, img.width(), img.height()))
        p.end()
        return out
    except Exception:
        # 模糊失败就用原图，不能让窗口画不出来
        return img


def _apply_grain(img, strength):
    """叠加细颗粒噪点（磨砂玻璃的"砂"感）。

    ## 性能（踩了两次坑）

    第一版用 `QPainter.drawPoint()` 逐点画：400x300 就要 **438ms**
    —— 每次 drawPoint 是一次独立绘制调用。

    第二版改用 `QImage.setPixel()`：1000x700 反而涨到 **1365ms**
    —— `Format_ARGB32` 的 setPixel 每次都要做格式转换。

    最终版直接构造 `bytearray` 再 `QImage(bytes, ...)` 一次性成像：
    纯 Python 循环写内存，没有逐像素的 Qt 调用开销，**降到 ~120ms**。

    ## 固定种子

    每次换随机种子的话，窗口重绘时颗粒会"沙沙抖"。固定种子保证同一张
    背景的噪点完全一致。
    """
    import random

    w, h = img.width(), img.height()
    if w <= 0 or h <= 0:
        return img
    sw, sh = max(1, w // 2), max(1, h // 2)
    amp = max(0, min(100, int(strength)))
    alpha = 26

    # 直接拼 ARGB32 字节流（内存序 BGRA）。
    # 透明像素的 RGB 也一起给值：放大时用最近邻以外的方式插值也不会发黑。
    rnd = random.Random(20240101)
    buf = bytearray(sw * sh * 4)
    i = 0
    for _ in range(sw * sh):
        v = rnd.randint(0, amp)
        buf[i] = v          # B
        buf[i + 1] = v      # G
        buf[i + 2] = v      # R
        buf[i + 3] = alpha  # A
        i += 4

    noise = QImage(bytes(buf), sw, sh, sw * 4, QImage.Format_ARGB32)

    out = img.convertToFormat(QImage.Format_ARGB32_Premultiplied)
    q = QPainter(out)
    q.setRenderHint(QPainter.SmoothPixmapTransform, False)
    q.drawImage(QRect(0, 0, w, h), noise)
    q.end()
    return out


def _apply_fog(img, strength):
    """雾化：叠一层半透明的白/黑，把画面压柔和。"""
    out = img.copy()
    p = QPainter(out)
    # 用当前明暗决定雾的颜色：浅色叠白、深色叠黑，都往"低对比"走
    from core.theme_presets import luminance

    c = img.pixelColor(img.width() // 2, img.height() // 2)
    light_bg = luminance(c.name()) > 0.5
    a = int(max(0, min(100, strength)) * 1.4)     # 强度映射到 alpha
    p.fillRect(out.rect(),
               QColor(255, 255, 255, a) if light_bg else QColor(0, 0, 0, a))
    p.end()
    return out


def render_background(size, material="none", strength=DEFAULT_STRENGTH,
                      image_path=None, fallback_color=None):
    """渲染一张"窗口背景"图，供 paintEvent 直接 drawPixmap。

    :param size: QSize，窗口客户区尺寸
    :param material: none / blur / frosted / fog
    :param strength: 0~100，材质强度
    :param image_path: 背景图路径；None 时用 fallback_color 纯色
    :param fallback_color: 没有背景图时的底色（通常是主题 bg）
    :return: QPixmap（永远非空）

    ## 绘制顺序

        1. 铺底：背景图（按窗口等比裁切填满）或纯色
        2. 按材质处理整张图
        3. 返回结果

    结果由调用方缓存——本函数较慢（模糊整窗 30~60ms），不应每帧调用。
    """
    w = max(1, int(size.width()))
    h = max(1, int(size.height()))
    mat = normalize_material(material)
    strength = max(0, min(100, int(strength)))

    # ---- 1. 铺底 ----
    canvas = QImage(w, h, QImage.Format_ARGB32_Premultiplied)
    canvas.fill(Qt.transparent)

    loaded = None
    if image_path and os.path.isfile(image_path):
        loaded = QImage(image_path)
        if loaded.isNull():
            loaded = None

    p = QPainter(canvas)
    p.setRenderHint(QPainter.SmoothPixmapTransform, True)
    if loaded is not None:
        # 等比缩放并居中裁切（cover）：铺满窗口且不变形
        iw, ih = loaded.width(), loaded.height()
        if iw > 0 and ih > 0:
            scale = max(w / iw, h / ih)
            nw, nh = int(iw * scale + 0.5), int(ih * scale + 0.5)
            src_x = max(0, (nw - w) // 2)
            src_y = max(0, (nh - h) // 2)
            scaled = loaded.scaled(nw, nh, Qt.IgnoreAspectRatio,
                                   Qt.SmoothTransformation)
            p.drawImage(QRect(0, 0, w, h),
                        scaled, QRect(src_x, src_y, w, h))
    else:
        p.fillRect(0, 0, w, h, QColor(fallback_color or "#ffffff"))
    p.end()

    # ---- 2. 材质 ----
    if mat == "blur":
        # 强度 0~100 映射到模糊半径 2~28
        radius = 2 + strength * 0.26
        canvas = _apply_blur(canvas, radius)
    elif mat == "frosted":
        canvas = _apply_blur(canvas, 1 + strength * 0.10)
        canvas = _apply_grain(canvas, 12 + strength * 0.3)
    elif mat == "fog":
        canvas = _apply_fog(canvas, strength)

    out = QPixmap.fromImage(canvas)
    return out if not out.isNull() else QPixmap(w, h)


def soft_light_overlay(size, dark=False, alpha=36):
    """给背景叠一层很淡的斜向高光，增加"玻璃"质感。

    :param alpha: 高光最亮处的透明度（0~255）
    """
    layer = QPixmap(max(1, size.width()), max(1, size.height()))
    layer.fill(Qt.transparent)
    p = QPainter(layer)
    p.setRenderHint(QPainter.Antialiasing, True)
    w, h = layer.width(), layer.height()
    grad = QLinearGradient(0, 0, w * 0.6, h)
    if dark:
        grad.setColorAt(0, QColor(255, 255, 255, max(6, alpha // 3)))
        grad.setColorAt(1, QColor(0, 0, 0, 0))
    else:
        grad.setColorAt(0, QColor(255, 255, 255, alpha))
        grad.setColorAt(1, QColor(255, 255, 255, 0))
    p.fillRect(0, 0, w, h, grad)
    p.end()
    return layer


# ====================================================================
# 内部元素（卡片/输入框）的材质
# ====================================================================

def render_inner_surface(size, material="none", strength=DEFAULT_STRENGTH,
                         base_color="#ffffff"):
    """渲染"卡片/输入框"这类**内部小面板**的材质底。

    ## 与窗口背景的区别

    窗口背景是一整张图（或模糊后的图），内部元素只是它上面的**半透明
    小面板**。所以这里不做"从背景图裁一块再模糊"——那样每张卡片都要
    独立裁剪+模糊，页面上一二十张卡片会卡死。

    实际做法是：内部元素只做**材质感的表面处理**（磨砂颗粒 / 雾化
    叠层 / 轻微模糊带来的柔化），叠在窗口背景之上。

    这样视觉上能看出"卡片也是玻璃的"，但成本只有一次小尺寸渲染，
    而且尺寸很小（几百像素），单张 2~5ms。

    :param base_color: 卡片本身的基础色（主题 card 色），作为叠层基准
    """
    w = max(1, int(size.width()))
    h = max(1, int(size.height()))
    mat = normalize_material(material)
    strength = max(0, min(100, int(strength)))

    canvas = QImage(w, h, QImage.Format_ARGB32_Premultiplied)
    # 内部面板保持半透明：既能透出窗口背景，又有自己的"玻璃层"
    # 材质越强、越"厚"，不透明度越高
    base_alpha = int(120 + strength * 0.9)      # 120~210
    base_alpha = max(0, min(255, base_alpha))
    c = QColor(base_color)
    c.setAlpha(base_alpha)
    canvas.fill(c)

    if mat == "frosted":
        # 磨砂：叠细颗粒，模拟毛砂表面的漫反射
        canvas = _apply_grain(canvas, 10 + strength * 0.25)
    elif mat == "fog":
        # 雾化：再叠一层更白的雾，让面板更"朦胧"
        canvas = _apply_fog(canvas, int(strength * 0.5))
    elif mat == "blur":
        # 毛玻璃：内部面板没有可模糊的自身内容，用极轻的柔化 +
        # 一层微亮叠加来暗示"玻璃厚度"
        out = QImage(w, h, QImage.Format_ARGB32_Premultiplied)
        out.fill(Qt.transparent)
        p = QPainter(out)
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)
        p.drawImage(0, 0, canvas)
        p.fillRect(out.rect(),
                   QColor(255, 255, 255, int(strength * 0.25)))
        p.end()
        canvas = out

    pm = QPixmap.fromImage(canvas)
    return pm if not pm.isNull() else QPixmap(w, h)
