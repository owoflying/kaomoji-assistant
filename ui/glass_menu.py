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

        # 2. 半透明白/黑：菜单要保证文字可读，比窗口更"实"一些
        veil = QColor(255, 255, 255, 200) if not dark else QColor(32, 32, 32, 205)
        p.fillRect(0, 0, w, h, veil)

        # 3. 釉面高光：顶部亮、往下衰减
        g = QLinearGradient(0, 0, 0, h)
        if dark:
            g.setColorAt(0.0, QColor(255, 255, 255, 22))
            g.setColorAt(0.5, QColor(255, 255, 255, 8))
            g.setColorAt(1.0, QColor(255, 255, 255, 0))
        else:
            g.setColorAt(0.0, QColor(255, 255, 255, 120))
            g.setColorAt(0.5, QColor(255, 255, 255, 40))
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
