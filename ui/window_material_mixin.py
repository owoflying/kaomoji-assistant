"""让独立窗口（日志/候选条/插件弹窗）也能吃到背景材质的 mixin。

## 为什么要 mixin

`UnifiedSettingsWindow` 继承 `FramelessShell`，材质在基类里就处理好了。
但日志窗口、候选条、插件弹窗是各自独立的 `QWidget`，各有自己的
`paintEvent`，不可能都去继承那个主窗口基类。

于是它们画的一直是**纯色面板**——用户反馈
"高级材质托盘和日志查看等独立窗口怎么没吃上"。

## 用法

    class LogViewer(WindowMaterialMixin, QWidget):
        def __init__(...):
            self._init_material(config_provider)

        def paintEvent(self, e):
            if self._paint_material(self, _PAD, _RADIUS):
                return          # 已经画好材质面板
            ...原有纯色绘制...

`_paint_material()` 返回 True 表示"材质已生效、不用再画纯色"；
返回 False 表示材质关闭（或没有背景图），调用方照旧画实色面板。
"""
from PySide6.QtCore import QRect


class WindowMaterialMixin:
    """给独立窗口提供背景材质绘制能力。"""

    _material_composer = None
    _material_config = None

    def _init_material(self, config, theme_provider=None):
        """初始化材质支持。

        :param config: 配置 dict（读 material_* 各项）
        :param theme_provider: 无参可调用，返回当前 Theme；
                               默认用 `self._theme` 属性
        """
        from ui.material_composer import MaterialComposer

        self._material_config = config
        if theme_provider is None:
            def theme_provider():
                return getattr(self, "_theme", None)

        self._material_composer = MaterialComposer(config, theme_provider)

    def _material_enabled(self):
        """材质是否真的会画东西。"""
        c = self._material_composer
        return bool(c is not None and c.enabled())

    def _paint_material(self, painter, pad=0, radius=10, shadow_color=None):
        """画材质面板（含外阴影 + 圆角裁切）。

        :return: True = 已画好，调用方不必再画纯色；False = 材质没启用
        """
        if not self._material_enabled():
            return False
        try:
            from PySide6.QtCore import Qt
            from PySide6.QtCore import QRectF
            from PySide6.QtGui import QPainterPath

            from ui.frameless_panel import paint_shadow

            w = self.width() - 2 * pad
            h = self.height() - 2 * pad
            if w <= 0 or h <= 0:
                return False
            panel = QRect(pad, pad, w, h)

            # 外阴影（画在窗口留白里；随整窗透明度缩放，避免全透时留幽灵阴影）
            alpha = self._material_composer.alpha()
            if shadow_color is not None and pad > 0 and alpha > 0:
                try:
                    from ui.frameless_shell import _scale_alpha

                    paint_shadow(painter, panel,
                                 _scale_alpha(shadow_color, alpha),
                                 pad, radius, spread=0.35, growth=0.5)
                except Exception:
                    pass

            pm = self._material_composer.compose(panel.size())
            if pm is None or pm.isNull():
                return False
            painter.save()
            path = QPainterPath()
            path.addRoundedRect(QRectF(panel), radius, radius)
            painter.setClipPath(path)
            painter.drawPixmap(panel.topLeft(), pm)
            painter.restore()

            # 1px 边框（材质是半透明的，没边框会和背景糊在一起）
            try:
                from ui.frameless_panel import _as_color

                theme = getattr(self, "_theme", None)
                border = getattr(theme, "window_border", None)
                if border:
                    from PySide6.QtGui import QPen

                    edge = QPainterPath()
                    edge.addRoundedRect(
                        QRectF(panel).adjusted(0.5, 0.5, -0.5, -0.5),
                        radius, radius)
                    pen = QPen(_as_color(border))
                    pen.setWidth(1)
                    painter.setPen(pen)
                    painter.setBrush(Qt.NoBrush)
                    painter.strokePath(edge, pen)
            except Exception:
                pass
            return True
        except Exception:
            return False

    def _invalidate_material(self):
        """配置/主题变了，清材质缓存。"""
        c = self._material_composer
        if c is not None:
            c.invalidate()
