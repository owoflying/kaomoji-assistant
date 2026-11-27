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

    :param config: 配置 dict
    :param theme_provider: 无参可调用，返回当前 Theme
    :param surface: 这个合成器服务于哪类窗口（"main"/"picker"/"log"/...）。
                    参数通过 `core.theme_profile.resolve()` 算出：
                    **默认沿用主配置**，该窗口有独立设置时才覆盖。
    """

    def __init__(self, config, theme_provider, surface="main"):
        self._config = config
        self._theme_provider = theme_provider
        self._surface = surface or "main"
        self._cache = None
        self._key = None

    # ---------------------------------------------------------- 读配置
    def _params(self):
        """这个表面实际生效的完整参数（主配置 + 该表面的覆盖）。"""
        try:
            from core.theme_profile import resolve

            return resolve(self._config, self._surface)
        except Exception:
            try:
                from core.theme_profile import normalize

                return normalize({})
            except Exception:
                return {}

    def _get(self, name, default):
        try:
            return self._params().get(name, default)
        except Exception:
            return default

    def _surface_config(self):
        """把生效参数包成一个 dict，供 `render_background` 使用。

        `render_background` 等的键名是 `material_type` / `strength` 等，
        而主题包里已经就是这些名字，所以直接透传即可。
        """
        return self._params()

    def _image_path(self):
        """当前该用哪张背景图。

        ## 取图规则

        1. **所有窗口默认沿用主窗口那张**（`background`）——
           这是"主题包"的核心：主配置一改，所有界面跟着变
        2. 只有候选条显式设了独立图，才用 `picker_background`
        """
        try:
            from core.theme_profile import effective_image_slot
            from ui.window_material import background_path

            slot = effective_image_slot(self._config, self._surface)
            return background_path(slot)
        except Exception:
            return _background_path()

    def _crop(self):
        """裁切参数 (x, y, w, h)。"""
        try:
            return (float(self._get("crop_x", 0.0)),
                    float(self._get("crop_y", 0.0)),
                    float(self._get("crop_w", 0.0)),
                    float(self._get("crop_h", 0.0)))
        except Exception:
            return None

    def _image_opacity(self):
        """背景图不透明度 0~1。"""
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
            return max(0.0, min(1.0, float(self._get("panel_alpha", 0.92))))
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
        #
        # ⚠ 这里曾经 import 了 `ui.theme_presets`——那个模块**不存在**
        # （实际是 `core.theme_presets`）。ImportError 被下面的
        # `except Exception: pass` 吃掉，于是实色面板从来没画过。
        #
        # 后果：没设背景图时 `compose()` 返回**全透明图**。
        # 主窗口有自己的底色兜底所以看不出来，但托盘菜单依赖这张图，
        # 结果就是"菜单是透明的/白的"，而且怎么调主题都不对。
        #
        # 教训：这种 `except: pass` 会把"导入写错"伪装成"渲染没效果"，
        # 排查时完全看不到线索。所以现在失败会**写日志**。
        if not drew_image:
            try:
                from core.theme_presets import parse_color

                bg_spec = getattr(theme, "bg", None) or ""
                r, g, b, a = parse_color(bg_spec)
                p.fillRect(0, 0, w, h,
                           QColor(r, g, b, int(max(0.0, min(1.0, a)) * 255)))
            except Exception as e:
                # 主题都拿不到时不填色：留透明，让窗口自己的兜底绘制负责。
                # 但必须留下痕迹——静默吞掉会让上面那种导入错误隐身。
                try:
                    from core.applog import append_log

                    append_log("warn", "material",
                               "实色面板填充失败（合成图将为透明）：%r" % (e,))
                except Exception:
                    pass

        # ---- 3. 整窗透明度 ----
        if alpha < 0.999:
            p.setCompositionMode(QPainter.CompositionMode_DestinationIn)
            p.fillRect(0, 0, w, h, QColor(0, 0, 0, int(round(alpha * 255))))
            p.setCompositionMode(QPainter.CompositionMode_SourceOver)

        p.end()
        return QPixmap.fromImage(canvas)
