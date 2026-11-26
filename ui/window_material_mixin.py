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


def load_surface_config(surface="dialog"):
    """给独立窗口取配置：优先读磁盘上的真实配置。

    对话框可能是被直接 new 出来的（测试 / 插件 / 更新检查），拿不到
    宿主的 config。这时从磁盘读一份——主题包本来就持久化在
    config.json 里，所以独立窗口也能"自动沿用主配置"。
    """
    try:
        from core.config_store import load_config

        cfg = load_config()
        from core.theme_profile import migrate_legacy

        return migrate_legacy(cfg)
    except Exception:
        return {}


def make_surface_composer(surface="dialog", config=None, theme_provider=None,
                          widget=None):
    """给任意独立窗口/对话框造一个 `MaterialComposer`。

    :param surface: 窗口类别（"dialog" / "log" / "plugin" / "picker"）
    :param config: 配置 dict；None 时从磁盘读
    :param theme_provider: 无参可调用，返回 Theme；默认从主题包构建
    :param widget: 目标控件（用于读取它自己的 _theme 属性）

    ## 为什么需要这个函数

    不是所有窗口都继承 `WindowMaterialMixin`（有些是 `QDialog`，
    有自己的绘制逻辑）。这个helper 让它们也能一行拿到主题包解析后的
    外观参数，不必新增任何配置键。
    """
    from ui.material_composer import MaterialComposer

    cfg = config if config is not None else load_surface_config(surface)

    if theme_provider is None:
        def theme_provider():
            # 控件自己的 _theme 优先（换肤时会被更新）
            t = getattr(widget, "_theme", None) if widget is not None else None
            if t is not None:
                return t
            try:
                from ui.theme_manager import build_theme
                from core.theme_profile import resolve

                p = resolve(cfg, surface)
                return build_theme({"theme_mode": p.get("theme_mode", "light"),
                                    "theme_preset": p.get("theme_preset", "classic")})
            except Exception:
                return None

    return MaterialComposer(cfg, theme_provider, surface=surface)


def surface_theme(surface="dialog", config=None):
    """给对话框/独立窗口解析出它该用的主题。

    ## 为什么需要

    这些窗口（更新提示/欢迎/组合/提取）原本各处写

        Theme(theme_name)          # 只有明暗，丢配色预设
        self.setStyleSheet(...)

    于是它们各自为政：用户换了配色/自定义主题，这些窗口还是经典灰，
    看起来"没有主题功能"。

    本函数统一从**主题包**取：默认沿用主配置，某界面有覆盖时用覆盖的。
    新窗口只要报一个 surface 名就能获得正确主题，无需新增配置键。
    """
    cfg = config
    if cfg is None:
        cfg = load_surface_config(surface)
    try:
        from core.theme_profile import resolve

        p = resolve(cfg, surface)
        mode = p.get("theme_mode", "light")
    except Exception:
        p, mode = {}, "light"
    try:
        from ui.theme_manager import build_theme

        return build_theme(cfg, mode_override=mode), p
    except Exception:
        from ui.win11_theme import Theme, resolve_theme_name

        return Theme(resolve_theme_name(mode)), p


class WindowMaterialMixin:
    """给独立窗口提供背景材质绘制能力。"""

    _material_composer = None
    _material_config = None

    def _init_material(self, config, theme_provider=None, surface="dialog"):
        """初始化材质支持。

        :param config: 配置 dict
        :param theme_provider: 无参可调用，返回当前 Theme；
                               默认用 `self._theme` 属性
        :param surface: 窗口类别（见 core.theme_profile.SURFACES）。
                        默认 "dialog"——**自动沿用主配置**，
                        不需要为该窗口新增任何配置键。
        """
        from ui.material_composer import MaterialComposer

        self._material_config = config
        if theme_provider is None:
            def theme_provider():
                return getattr(self, "_theme", None)

        self._material_composer = MaterialComposer(
            config, theme_provider, surface=surface)

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
