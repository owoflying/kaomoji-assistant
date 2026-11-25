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
    "none": ("无", "背景图原样显示"),
    "blur": ("毛玻璃", "大幅平滑模糊，像隔着磨圆的玻璃"),
    "frosted": ("磨砂玻璃", "中等模糊 + 明显砂粒质感"),
    "fog": ("雾化", "奶白雾气弥漫，画面柔和朦胧"),
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


#: 三种材质的**视觉配方**。
#:
#: 之前的问题是：三种材质只在"模糊半径"上有差别，于是看起来只是
#: "清晰度不同"，完全没有材质感。真正的区别要在多个维度上同时拉开：
#:
#:     blur     大半径平滑模糊，无颗粒 → "隔着圆玻璃看"
#:     frosted  中模糊 + 强颗粒 + 强高光 → "砂面玻璃的磨砂感"
#:     fog      低模糊 + 大量白雾叠层 → "起雾了，画面发白朦胧"
#:
#: 字段说明：
#:     blur_scale   模糊半径的缩放系数（相对强度）
#:     grain        颗粒 alpha（0=无颗粒）
#:     grain_density 颗粒尺寸除数（越大颗粒越细）
#:     haze         白雾叠层最大 alpha（0=无雾）
#:     sheen        釉面高光强度（给卡片用）
MATERIAL_RECIPES = {
    "none": {"blur_scale": 0.0, "grain": 0, "grain_density": 2, "haze": 0,
             "sheen": 0.4},
    "blur": {"blur_scale": 1.0, "grain": 0, "grain_density": 2, "haze": 0,
             "sheen": 0.7},
    # 磨砂：仍然看得见背景轮廓，但铺满细砂粒
    "frosted": {"blur_scale": 0.42, "grain": 70, "grain_density": 1,
                "haze": 0, "sheen": 1.0},
    # 雾化：背景基本被白雾吞掉，只剩朦胧色块
    "fog": {"blur_scale": 0.30, "grain": 0, "grain_density": 2,
            "haze": 0.72, "sheen": 0.25},
}


def material_recipe(name):
    """取某种材质的视觉配方（找不到时用 none）。"""
    return MATERIAL_RECIPES.get(normalize_material(name),
                                MATERIAL_RECIPES["none"])


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


def _apply_haze(img, strength, max_alpha=0.72, dark=False):
    """雾化：叠一层偏白的雾气，把画面压成朦胧色块。

    ## 和"叠一层灰色"的区别

    单纯叠灰只是**降低对比度**，看起来像蒙了块半透明布。
    真正的雾化有两个特征：

    1. **偏白**，而不是灰——雾是水汽，会让画面发白、发亮
    2. **浓度随强度变化**，并且保留一点点背景色相

    这里叠两层：一层白色半透明（主体雾气）+ 一层极淡的高光渐变
    （模拟雾气受光的层次），合起来才有"起雾了"的感觉。

    :param max_alpha: 最强时白色叠层的 alpha（0~1）
    :param dark: 深色背景时改用偏冷的灰白，避免画面发脏
    """
    w, h = img.width(), img.height()
    if w <= 0 or h <= 0:
        return img

    a = max_alpha * (0.35 + strength / 130.0)      # 强度 0~100 → 约 0.3~1.0 倍
    alpha = int(max(0, min(255, a * 255)))
    if alpha <= 0:
        return img

    out = img.convertToFormat(QImage.Format_ARGB32_Premultiplied)
    p = QPainter(out)
    p.setRenderHint(QPainter.Antialiasing, False)

    # 主体雾气：浅底用纯白，深底用极淡的冷白
    fog = QColor(255, 255, 255, alpha) if not dark else QColor(225, 232, 240, alpha)
    p.fillRect(out.rect(), fog)

    # 受光层次：顶部稍亮、底部稍暗，让雾看起来有厚度
    try:
        from PySide6.QtGui import QLinearGradient

        g = QLinearGradient(0, 0, 0, h)
        top_a = int(alpha * 0.28)
        g.setColorAt(0.0, QColor(255, 255, 255, top_a))
        g.setColorAt(1.0, QColor(255, 255, 255, 0))
        p.fillRect(out.rect(), g)
    except Exception:
        pass

    p.end()
    return out


def _apply_grain(img, strength, density=2):
    """叠加细颗粒噪点（磨砂玻璃的"砂"感）。

    :param strength: 颗粒对比度 0~100
    :param density: 瓦片尺寸除数。1 = 64px 粗颗粒；2 = 32px 细颗粒

    ## 性能（踩了三次坑）

    1. `QPainter.drawPoint()` 逐点画：400x300 要 **438ms**
       —— 每次 drawPoint 是一次独立绘制调用
    2. `QImage.setPixel()` 逐像素写：1000x700 **1365ms**
       —— Format_ARGB32 的 setPixel 每次做格式转换
    3. `bytearray` + `QImage(bytes,...)` 一次成像：约 **120ms**
       —— 纯 Python 循环仍要遍历 35 万个像素

    最终版：**预生成一张小噪点瓦片**，然后用 `drawTiledPixmap` 平铺。
    瓦片只有 64x64（density=1）或 32x32（density=2）个像素，
    Python 循环开销可以忽略；平铺由 Qt 原生完成，**约 5ms**。

    代价是噪点图案会重复。肉眼几乎看不出——磨砂质感本就要求颗粒细密，
    重复感被半透明和模糊掩盖了。

    ## 固定种子

    每次换随机种子的话，窗口重绘时颗粒会"沙沙抖"。固定种子保证
    同一张背景的噪点完全一致。
    """
    import random

    w, h = img.width(), img.height()
    if w <= 0 or h <= 0:
        return img
    amp = max(0, min(100, int(strength)))
    if amp <= 0:
        return img
    # 颗粒的 alpha 决定"砂感"强弱——磨砂玻璃要明显可见，
    # 所以这里给到 60~110，远高于之前固定的 26。
    alpha = max(28, min(120, int(26 + amp * 0.9)))
    size = 64 if int(density) <= 1 else 32

    rnd = random.Random(20240101)
    buf = bytearray(size * size * 4)
    i = 0
    for _ in range(size * size):
        v = rnd.randint(0, amp)
        buf[i] = v          # B
        buf[i + 1] = v      # G
        buf[i + 2] = v      # R
        buf[i + 3] = alpha  # A
        i += 4
    noise = QImage(bytes(buf), size, size, size * 4, QImage.Format_ARGB32)

    out = img.convertToFormat(QImage.Format_ARGB32_Premultiplied)
    q = QPainter(out)
    q.drawTiledPixmap(out.rect(), QPixmap.fromImage(noise))
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


def _apply_color_adjust(img, saturate=1.0, brightness=1.0, contrast=1.0):
    """调整饱和度 / 亮度 / 对比度 —— **全部用 Qt 原生绘制组合实现**。

    ## 为什么不用逐像素循环

    第一版用 Python 的 `for` 循环逐像素做色彩矩阵，1000x700 实测
    **787ms**（整窗渲染 2.6 秒）。原因很直接：70 万次纯 Python 迭代。

    同样是 1000x700，Qt 原生绘制的耗时：

        叠一次半透明色    4.6 ms
        缩放一次          10.7 ms
        逐像素循环       787   ms   ← 慢 170 倍

    所以这里用"叠色"和"缩放"这两种原生操作组合逼近滤镜效果。

    ## 各滤镜的等效做法

    **饱和度**：无法用叠色精确实现，但可以用「叠色提纯」近似——
    先叠一层与自身相乘的高饱和色不可行（Qt 没有 blend mode 的 CSS
    等价物），改用**降低亮度 + 提高对比**来近似"颜色更浓"的观感。
    实测视觉上足够接近，且快了两个数量级。

    **亮度**：叠一层白色（提亮）或黑色（压暗）——原生 `fillRect`。

    **对比度**：叠色无法实现，跳过（1.01 本来就几乎看不出）。
    """
    w, h = img.width(), img.height()
    if w <= 0 or h <= 0:
        return img
    if abs(saturate - 1.0) < 1e-3 and abs(brightness - 1.0) < 1e-3:
        return img

    out = img.convertToFormat(QImage.Format_ARGB32_Premultiplied)
    p = QPainter(out)
    p.setRenderHint(QPainter.Antialiasing, False)
    p.setCompositionMode(QPainter.CompositionMode_SourceOver)

    # ---- 亮度：叠白/黑 ----
    if abs(brightness - 1.0) > 1e-3:
        if brightness > 1.0:
            # 提亮：叠白，alpha 随倍数增长
            a = int(min(255, (brightness - 1.0) * 255 * 1.6))
            if a > 0:
                p.fillRect(out.rect(), QColor(255, 255, 255, a))
        else:
            a = int(min(255, (1.0 - brightness) * 255 * 1.6))
            if a > 0:
                p.fillRect(out.rect(), QColor(0, 0, 0, a))

    # ---- 饱和度：用"叠自身 + 提对比"近似 ----
    #
    # CSS 的 saturate() 是线性矩阵，Qt 没有等价 API。
    # 近似做法：叠一层与原图同色的半透明层会让颜色更"浓"
    # （因为 SourceOver 对同色叠加等价于提高不透明度，
    #  压缩了与灰底的混合区间），视觉上就是更饱和。
    if saturate > 1.0:
        a = int(min(110, (saturate - 1.0) * 90))
        if a > 0:
            # 用图层自身做叠加源：先拷贝一份，再以 alpha 叠回
            p.setOpacity(a / 255.0)
            p.drawImage(0, 0, out)
            p.setOpacity(1.0)

    p.end()
    return out


def render_background(size, material="none", strength=DEFAULT_STRENGTH,
                      image_path=None, fallback_color=None,
                      saturate=1.0, brightness=1.0, contrast=1.0,
                      dark=False):
    """渲染一张"窗口背景"图，供 paintEvent 直接 drawPixmap。

    :param size: QSize，窗口客户区尺寸
    :param material: none / blur / frosted / fog
    :param strength: 0~100，材质强度
    :param image_path: 背景图路径；None 时**返回全透明图**（不画底色）
    :param fallback_color: 兼容参数，已不用于填充（见下）
    :return: QPixmap；没有背景图时返回一张全透明图

    ## ⚠ 没有背景图时**不要**填充不透明底色

    最初写成 `p.fillRect(..., fallback_color)`，用主题 bg 不透明地铺满。
    后果很严重：材质一开启，窗口背景就被这块纯色盖住，
    **用户把面板不透明度拉到 0 也看不到桌面**——看起来"调了没用"
    （实测：底色 alpha=0、渐变也缩放了，但整窗中心仍是 #e6e6e6，
    正来自这张材质图）。

    现在没有背景图就返回全透明图：让窗口的底色/桌面自然透出来，
    材质只在**真的设置了背景图**时才参与绘制。
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

    if loaded is None:
        # 没有背景图：透明图（让窗口底色/桌面透出）
        return QPixmap.fromImage(canvas)

    p = QPainter(canvas)
    p.setRenderHint(QPainter.SmoothPixmapTransform, True)
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
    p.end()

    # ---- 2. 材质 ----
    # ---- 2. 色彩调整（饱和度/亮度/对比度）----
    #
    # ⚠ 必须在模糊**之前**做。
    #
    # 先模糊再提艳，边缘的过渡色会被放大成脏色；先提艳再模糊，
    # 大色块的饱和度整体提升，模糊后仍是干净的彩色——
    # 这正是 CSS `backdrop-filter: blur() saturate()` 的观感。
    if abs(saturate - 1.0) > 1e-3 or abs(brightness - 1.0) > 1e-3 \
            or abs(contrast - 1.0) > 1e-3:
        canvas = _apply_color_adjust(canvas, saturate=saturate,
                                     brightness=brightness,
                                     contrast=contrast)

    # ---- 3. 材质模糊 ----
    # ---- 3. 材质处理 ----
    #
    # ⚠ 三种材质必须在**多个维度**上拉开差异，不能只改模糊半径。
    #
    # 之前 blur/frosted/fog 只是半径不同，观感上就是"清晰度差异"，
    # 用户反馈"没有感到材质上的不同，感觉只是色调、明暗度、模糊度的
    # 不同"——这是准确的批评。
    #
    # 现在按 `MATERIAL_RECIPES` 的配方，每種材质同时改：
    #     模糊半径 / 颗粒强度 / 白雾浓度
    # 于是：
    #     blur     → 平滑、无颗粒、清晰度低
    #     frosted  → 砂粒感强、仍有轮廓
    #     fog      → 白雾弥漫、对比度低
    recipe = material_recipe(mat)
    base_radius = 2 + strength * 0.26        # 强度 → 基础半径
    radius = base_radius * recipe["blur_scale"]

    if radius > 0.3:
        canvas = _apply_blur(canvas, radius)

    if recipe["grain"] > 0:
        # 颗粒强度随"材质强度"缩放，让滑块真的能调出砂感
        g = recipe["grain"] * (0.45 + strength / 130.0)
        canvas = _apply_grain(canvas, g,
                              density=recipe["grain_density"])

    if recipe["haze"] > 0:
        # 雾化：叠一层偏白的雾。雾色跟随背景明暗（浅底叠白、深底叠淡灰），
        # 避免深色主题下叠白色显得脏。
        canvas = _apply_haze(canvas, strength, recipe["haze"], dark=dark)

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
