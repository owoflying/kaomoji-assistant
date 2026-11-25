"""窗口背景材质合成器 —— 供**所有**顶层窗口共用。

## 为什么抽成模块

`_render_composed` 这套逻辑（背景图 → 材质模糊 → 调色 → 整窗透明度）
最初写死在 `UnifiedSettingsWindow` 里，于是只有主窗口吃到了材质：

    主窗口        有材质
    日志窗口      纯色        ← 用户反馈"没吃上"
    候选条        纯色
    插件弹窗      纯色

而这三者的绘制代码是各自独立的 `paintEvent`，不可能各自复制一份合成逻辑。

现在把「合成」抽到这里，任何窗口只要提供 `config` / `theme` / 面板尺寸
就能拿到一张合成好的位图，贴上去即可。

## 一个窗口需要什么

    synth = MaterialComposer(config, theme_provider)
    pixmap = synth.compose(panel_size)

`config` 是配置 dict（读 material_* 各项），`theme_provider` 是
无参函数，返回当前 Theme（读 bg / dark）。

## 缓存

合成一次要 25~90ms（1000x700），**不能每帧算**。缓存键包含所有
影响结果的输入：面板尺寸、主题明暗与底色、材质全部参数、背景图
路径与 mtime、面板不透明度。
"""
import os


def _background_path():
    """当前背景图路径；没设置返回 None。"""
    try:
        from ui.window_material import background_path

        return background_path()
    except Exception:
        return None


class MaterialComposer:
    """把「背景图 + 材质 + 面板底色 + 整窗透明度」合成成一张位图。

    :param config: 配置 dict（读 material_*）
    :param theme_provider: 无参可调用，返回当前 Theme
    :param key_prefix: 配置键前缀。默认 ""（主窗口读 `material_*`）；
                       候选条传 `"picker_"`（读 `picker_material_*`）
    :param slot: 背景图命名空间。默认主窗口的 `background`；
                 候选条用 `picker_background`
    :param own_image_only: True 时**只用** `slot` 里的图，不回退到主窗口那张。
                           候选条勾了「独立背景图」才为 True。
    """

    def __init__(self, config, theme_provider, key_prefix="",
                 slot=None, own_image_only=False):
        self._config = config
        self._theme_provider = theme_provider
        self._prefix = key_prefix or ""
        self._slot = slot
        self._own_only = own_image_only
        self._cache = None
        self._key = None

    # ---------------------------------------------------------- 读配置
    def _get(self, name, default):
        """读带前缀的配置项。

        候选条用 `picker_` 前缀，读 `picker_material_enabled`；
        主窗口前缀为空，直接读 `material_enabled`。
        """
        key = self._prefix + name
        try:
            return self._config.get(key, default)
        except Exception:
            return default

    def _image_path(self):
        """当前该用哪张背景图。

        ## 候选条的取图规则

        1. 勾了「独立背景图」→ **只用**候选条自己的图，没有就当作无背景
           （不能回退到主窗口那张，否则"独立"就不成立了）
        2. 没勾 → 优先用候选条自己的（若存在），否则沿用主窗口那张
        """
        if self._slot is None:
            return _background_path()
        try:
            from ui.window_material import background_path

            own = background_path(self._slot)
        except Exception:
            own = None
        if own:
            return own
        if self._own_only:
            return None
        if self._slot == "background":
            return own
        # 没勾独立 → 回退到主窗口的背景图
        return _background_path()

    def _crop(self):
        """裁切参数 (x, y, w, h)。只有候选条支持。"""
        if self._prefix != "picker_":
            return None
        try:
            return (float(self._get("crop_x", 0.0)),
                    float(self._get("crop_y", 0.0)),
                    float(self._get("crop_w", 0.0)),
                    float(self._get("crop_h", 0.0)))
        except Exception:
            return None

    def _image_opacity(self):
        """背景图不透明度（只有候选条读 `picker_image_opacity`）。"""
        if self._prefix != "picker_":
            return 1.0
        try:
            return max(0.0, min(1.0, float(
                self._get("image_opacity", 100)) / 100.0))
        except Exception:
            return 1.0

    # ---------------------------------------------------------- 公开
    def enabled(self):
        """材质是否启用（总开关 + 有实际内容）。"""
        try:
            if not bool(self._get("material_enabled", False)):
                return False
            mat = str(self._get("material_type", "none") or "none").lower()
            if mat in ("blur", "frosted", "fog"):
                return True
            return bool(self._image_path())
        except Exception:
            return False

    def alpha(self):
        """整窗不透明度 0~1。"""
        try:
            return max(0.0, min(1.0, float(
                self._config.get("panel_alpha", 0.92))))
        except Exception:
            return 0.92

    def invalidate(self):
        """配置或主题变了，丢掉缓存。"""
        self._cache = None
        self._key = None

    def compose(self, size, panel_alpha=None):
        """合成面板位图。

        :param size: QSize，面板尺寸（已扣除窗口阴影留白）
        :param panel_alpha: 覆盖整窗透明度；None 时用配置里的
        :return: QPixmap（永不返回 None，失败时给透明图）
        """
        from PySide6.QtGui import QPixmap

        w = max(1, int(size.width()))
        h = max(1, int(size.height()))
        alpha = self.alpha() if panel_alpha is None else panel_alpha
        key = self._cache_key(w, h, alpha)
        if self._cache is not None and self._key == key:
            return self._cache
        try:
            pm = self._render(w, h, alpha)
        except Exception:
            pm = QPixmap(w, h)
            pm.fill()
        self._cache = pm
        self._key = key
        return pm

    # ---------------------------------------------------------- 内部
    def _cache_key(self, w, h, alpha):
        """任何影响结果的输入都要进键。"""
        try:
            theme = self._theme_provider()
            dark = bool(getattr(theme, "dark", False))
            bg = getattr(theme, "bg", "")
        except Exception:
            dark, bg = False, ""
        bp = self._image_path()
        mtime = 0.0
        if bp:
            try:
                mtime = os.path.getmtime(bp)
            except Exception:
                mtime = 0.0
        return (
            w, h, dark, bg, round(float(alpha), 4),
            bool(self._get("material_enabled", False)),
            str(self._get("material_type", "none")),
            int(self._get("material_strength", 55)),
            int(self._get("material_saturate", 18)),
            int(self._get("material_brightness", 104)),
            int(self._get("material_dim", 0)),
            # 裁切与图片不透明度也必须进键，否则改了不重算
            tuple(self._crop() or ()), round(self._image_opacity(), 3),
            bp, mtime,
        )

    def _render(self, w, h, alpha):
        """真正合成：材质 → 底色 → 整窗透明度。"""
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QColor, QImage, QPainter, QPixmap

        theme = self._theme_provider()
        dark = bool(getattr(theme, "dark", False))
        canvas = QImage(w, h, QImage.Format_ARGB32_Premultiplied)
        canvas.fill(Qt.transparent)
        p = QPainter(canvas)
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)

        # ---- 1. 背景图（有就不画底色，见 frameless_shell 的规则说明）----
        drew_image = False
        if self.enabled():
            bp = self._image_path()
            if bp:
                from PySide6.QtCore import QSize

                from ui.window_material import (
                    normalize_material, render_background,
                )

                mat = normalize_material(self._get("material_type", "none"))
                strength = int(self._get("material_strength", 55))
                sat = max(10, min(30, int(
                    self._get("material_saturate", 18)))) / 10.0
                bri = max(80, min(130, int(
                    self._get("material_brightness", 104)))) / 100.0
                layer = render_background(
                    QSize(w, h), material=mat, strength=strength,
                    image_path=bp, saturate=sat, brightness=bri,
                    contrast=1.01, dark=dark, crop=self._crop())
                dim = int(self._get("material_dim", 0))
                if dim:
                    ll = QPainter(layer)
                    a = min(255, int(dim * 1.6))
                    ll.fillRect(layer.rect(),
                                QColor(0, 0, 0, a) if dark
                                else QColor(255, 255, 255, a))
                    ll.end()
                # 图片不透明度（候选条可单独调）
                iop = self._image_opacity()
                if iop < 0.999:
                    p.setOpacity(iop)
                p.drawPixmap(0, 0, layer)
                p.setOpacity(1.0)
                drew_image = True

        # ---- 2. 没有背景图 → 实色面板 ----
        if not drew_image:
            try:
                from ui.theme_presets import parse_color

                bg_spec = getattr(theme, "bg", None) or "" 
                r, g, b, a = parse_color(bg_spec)
                p.fillRect(0, 0, w, h,
                           QColor(r, g, b, int(max(0.0, min(1.0, a)) * 255)))
            except Exception:
                # 主题都拿不到时不填色：留透明，让窗口自己的兜底绘制负责
                pass

        # ---- 3. 整窗透明度 ----
        if alpha < 0.999:
            p.setCompositionMode(QPainter.CompositionMode_DestinationIn)
            p.fillRect(0, 0, w, h, QColor(0, 0, 0, int(round(alpha * 255))))
            p.setCompositionMode(QPainter.CompositionMode_SourceOver)

        p.end()
        return QPixmap.fromImage(canvas)
