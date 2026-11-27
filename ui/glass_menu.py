"""托盘菜单的玻璃质感 —— **纯 QSS 渐变方案**。

## 为什么不用自绘（踩过的大坑）

`QMenu` 虽然是 `QWidget` 子类，但它的 `paintEvent` 会重画整个背景。
我们试过覆盖 `paintEvent` 自绘玻璃底：

    * 自绘后再 `super().paintEvent()` → 背景被 super 重画覆盖，
      两种主题下菜单都是纯白（用户说"深色比浅色还亮"）
    * 不调 super、手动画菜单项 → 菜单全透明点不到，
      而且每帧重算材质图**极卡**（用户说"响应特别慢"）

两次都把功能弄坏了。

## 现在：用 QSS 渐变模拟玻璃

实测 `QMenu` 上 `qlineargradient` **完全有效**：

    background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
        stop:0 <亮色>, stop:1 <暗色>);

顶部亮、底部暗，加上顶部高光色，观感就是一层"釉面玻璃"：
比纯色有层次，又不牺牲性能和可靠性。

这不是真正的 `backdrop-filter`（背后内容实时模糊），
但 QMenu 是系统弹出窗口，Qt 没有可靠的方式对它做背板模糊。
稳定、好看、不卡，比"理论上完美但把功能弄坏"重要得多。

## 明暗两套配色由 `ui.win11_theme.glass_menu_style()` 提供：
##     light 顶部浅、底部略深；dark 顶部深灰、底部更深

选中项用主题强调色，文字用主题前景色——都从 Theme 取，
所以换主题 / 托盘菜单单独配置都能正确生效。
"""
from PySide6.QtWidgets import QMenu




class GlassMenu(QMenu):
    """用 QSS 渐变实现玻璃质感的托盘菜单。

    这里**不覆盖 paintEvent**——保留 QMenu 原生绘制，保证菜单项
    能正常响应、不透明、不卡顿。质感全靠 `apply_glass_style()`。

    :param theme: 当前 Theme（可为 None，用默认配色）
    """

    def __init__(self, parent=None, theme=None):
        super().__init__(parent)
        self._theme = theme
        self.apply_glass_style(theme)

    def set_theme(self, theme):
        """换主题时重刷配色。"""
        self._theme = theme
        self.apply_glass_style(theme)

    # ------------------------------------------------------------ 兼容接口
    #
    # AppRuntime 里还调用着下面几个方法（历史遗留）。保留成空实现，
    # 避免改调用点；现在材质靠 QSS，不需要 composer / 半透明属性。

    def material_enabled(self):
        """保留兼容接口：QSS 方案始终"有材质"。"""
        return True

    def refresh_material(self, config=None):
        """保留兼容接口：有主题变化时直接重刷样式。"""
        try:
            self.apply_glass_style(self._theme)
        except Exception:
            pass

    # ------------------------------------------------------------ 样式
    def apply_glass_style(self, theme=None):
        """按主题生成渐变 + 高光的 QSS。

        配色由 `ui.win11_theme.glass_menu_style(t)` 统一提供——
        颜色属于主题定义，集中管理，也避免样式散落。
        """
        t = theme if theme is not None else self._theme
        if t is None:
            return
        try:
            from ui.win11_theme import glass_menu_style

            self.setStyleSheet(glass_menu_style(t))
        except Exception:
            pass
