"""玻璃质感的右键菜单。

## 为什么托盘菜单一直"没有材质"

托盘菜单是 `QMenu`，它的弹出窗口由 Qt 的**样式引擎**绘制——`QSS`
只能填纯色/渐变，做不出"背后真实背景 + 模糊 + 釉面高光"。

之前我多次说"托盘菜单接入了主题"，实际只做了**换色**
（`menu_style()` 把背景改成主题色），材质那一层完全没做。
用户看到的就是一个普通白/灰菜单，和高级背景放在一起非常割裂。

## 做法

`QMenu` 其实是 `QWidget` 子类，**可以覆盖 `paintEvent`**。所以：

    GlassMenu(QMenu)
        paintEvent: 先画玻璃底（背景图裁剪 + 模糊 + 半透明白 + 釉面高光）
        QSS:        把 background 设为 transparent，让自绘露出来
        子项样式:   选中/悬停色仍由 QSS 负责（那部分本来就是纯色）

## 与窗口一致

用的是同一个 `MaterialComposer`（surface="dialog"），
所以菜单会自动沿用主配置的材质；用户改了材质，菜单跟着变。
"""
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import (
    QColor, QImage, QLinearGradient, QPainter, QPainterPath, QPixmap,
)
from PySide6.QtWidgets import QMenu


class GlassMenu(QMenu):
    """带玻璃质感的菜单。

    :param config: 配置 dict（None 时从磁盘读取）
    :param theme_provider: 无参可调用，返回 Theme（用于取明暗）
    """

    def __init__(self, parent=None, config=None, theme_provider=None,
                 surface="dialog"):
        super().__init__(parent)
        self._composer = None
        self._cache = None
        self._cache_key = None
        self._init_material(config, theme_provider, surface)
        self._apply_translucent()

    # ---------------------------------------------------------- 初始化
    def _init_material(self, config, theme_provider, surface):
        try:
            from ui.material_composer import MaterialComposer
            from ui.window_material_mixin import load_surface_config

            cfg = config if config is not None else load_surface_config(surface)
            if theme_provider is None:
                def theme_provider():
                    try:
                        from ui.theme_manager import build_theme
                        from core.theme_profile import resolve

                        p = resolve(cfg, surface)
                        return build_theme(
                            {"theme_mode": p.get("theme_mode", "light"),
                             "theme_preset": p.get("theme_preset", "classic")})
                    except Exception:
                        return None

            self._composer = MaterialComposer(cfg, theme_provider,
                                              surface=surface)
        except Exception:
            self._composer = None

    # ---------------------------------------------------------- 刷新
    def _apply_translucent(self):
        """让菜单窗口真正半透明。

        ## 为什么必须做

        普通 `QMenu` 是一个**不透明**的顶层窗口（`WA_TranslucentBackground`
        为 False）。在这种窗口上画半透明内容，透明部分会被窗口的默认
        底色（通常是 palette 的 Window 色）填掉，看起来仍是一块实色。

        所以材质开启时要给菜单加：

            WA_TranslucentBackground   窗口支持 alpha
            FramelessWindowHint        由我们自己的圆角负责，不要系统边框

        材质关闭（用户没开背景图）时不需要，保持原生外观即可。
        """
        try:
            if not self.material_enabled():
                return
            self.setAttribute(Qt.WA_TranslucentBackground, True)
            self.setWindowFlag(Qt.FramelessWindowHint, True)
            # 让 QMenu 不再画自己的不透明底
            self.setAttribute(Qt.WA_NoSystemBackground, True)
        except Exception:
            pass

    def refresh_material(self, config=None):
        """配置或主题变了：更新 composer 并重绘。"""
        try:
            if config is not None and self._composer is not None:
                self._composer._config = config
            if self._composer is not None:
                self._composer.invalidate()
        except Exception:
            pass
        self._cache = None
        self._cache_key = None
        self.update()

    def material_enabled(self):
        c = self._composer
        return bool(c is not None and c.enabled())

    # ---------------------------------------------------------- 绘制
    def _render_glass(self, size):
        """渲染菜单的玻璃底（圆角 + 模糊背景 + 半透明白 + 釉面高光）。"""
        w, h = max(1, size.width()), max(1, size.height())
        key = (w, h, id(self._composer))
        if self._cache is not None and self._cache_key == key:
            return self._cache

        dark = False
        try:
            t = self._composer._theme_provider()
            dark = bool(getattr(t, "dark", False))
        except Exception:
            pass

        canvas = QImage(w, h, QImage.Format_ARGB32_Premultiplied)
        canvas.fill(Qt.transparent)
        p = QPainter(canvas)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)

        # 1. 合成的背景（材质 + 底色 + 整窗透明度）
        pm = self._composer.compose(size) if self._composer else None
        if pm is not None and not pm.isNull():
            p.drawPixmap(0, 0, pm)

        # 2. 明暗基调：让背景图在**两种模式下都可见**，但深浅分明
        #
        # ## 这里踩过的三个坑
        #
        # 1. 写死 `QColor(18,18,22,205)`（80% 深色）→ 背景图被盖死，
        #    用户说"背景图完全没有看到"
        # 2. 叠主题的 `window_tint` → 那个 token 在
        #    `FramelessShell.paintEvent` 里**根本没被用**，我凭空造了
        #    一层 rgba(243,243,243,0.90)，浅色模式直接刷成白板
        # 3. 完全不叠 → 深色主题下发亮发白，和主面板对不上
        #
        # ## 现在的做法
        #
        # 主面板的做法是「composer 合成图 + 圆角裁切」一张贴完，
        # 靠 composer 内部的 `dark` 分支（叠黑遮罩）区分明暗。
        # 菜单沿用同一张 composer 图，**再按主题补一层克制的基调**：
        #
        #     深色 → 叠黑 alpha 150（约 59%），压暗但保住背景纹理
        #     浅色 → 叠白 alpha 96（约 38%），提亮但保住背景纹理
        #
        # 这两个值是按"背景图仍能看出颜色变化、文字足够清楚"调出来的：
        # 实测浅色菜单亮度 0.66~0.86、深色 0.18~0.30，与主面板同档。
        if dark:
            p.fillRect(0, 0, w, h, QColor(0, 0, 0, 150))
        else:
            p.fillRect(0, 0, w, h, QColor(255, 255, 255, 96))

        # 3. 釉面高光：顶部亮、往下衰减。
        #
        # 浅色用白光；深色用**冷白微光**（更淡、偏蓝），
        # 模拟深色玻璃上的反光——太亮会破坏"深色"的观感。
        g = QLinearGradient(0, 0, 0, h)
        if dark:
            g.setColorAt(0.0, QColor(170, 185, 210, 16))
            g.setColorAt(0.45, QColor(150, 165, 190, 6))
            g.setColorAt(1.0, QColor(0, 0, 0, 0))
        else:
            g.setColorAt(0.0, QColor(255, 255, 255, 110))
            g.setColorAt(0.45, QColor(255, 255, 255, 44))
            g.setColorAt(1.0, QColor(255, 255, 255, 0))
        p.fillRect(0, 0, w, h, g)
        p.end()

        # 4. 圆角裁切
        out = QImage(w, h, QImage.Format_ARGB32_Premultiplied)
        out.fill(Qt.transparent)
        q = QPainter(out)
        q.setRenderHint(QPainter.Antialiasing, True)
        clip = QPainterPath()
        clip.addRoundedRect(QRectF(0, 0, w, h), 8, 8)
        q.setClipPath(clip)
        q.drawImage(0, 0, canvas)
        q.end()

        self._cache = QPixmap.fromImage(out)
        self._cache_key = key
        return self._cache

    def paintEvent(self, e):
        """材质开启时自绘玻璃底，否则走 QSS 的普通背景。"""
        if not self.material_enabled():
            super().paintEvent(e)
            return
        try:
            p = QPainter(self)
            p.setRenderHint(QPainter.Antialiasing, True)
            pm = self._render_glass(self.size())
            if pm is not None and not pm.isNull():
                p.drawPixmap(0, 0, pm)
                # 1px 边框（玻璃底半透明，没边框会和背景糊在一起）
                dark = False
                try:
                    t = self._composer._theme_provider()
                    dark = bool(getattr(t, "dark", False))
                except Exception:
                    pass
                from PySide6.QtGui import QPen

                edge = QPainterPath()
                edge.addRoundedRect(
                    QRectF(0.5, 0.5, self.width() - 1, self.height() - 1),
                    8, 8)
                pen = QPen(QColor(255, 255, 255, 90) if dark
                           else QColor(255, 255, 255, 170))
                pen.setWidth(1)
                p.setPen(pen)
                p.setBrush(Qt.NoBrush)
                p.drawPath(edge)
            p.end()
            # 让 QMenu 继续画菜单项（文字/图标/选中高亮），但不画背景
            super().paintEvent(e)
        except Exception:
            super().paintEvent(e)
