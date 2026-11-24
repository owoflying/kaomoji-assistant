"""主题的唯一权威源。

## 为什么需要这个类

重构前，主题状态散落在**五处**，各自维护一份副本：

    app._applied_style      全局 QSS 字符串（幂等判据）
    app.theme_obj           当前 Theme 对象
    app._kao_theme_obj      给 tooltip 的 Theme 副本
    unified.theme           主窗口自己的 Theme
    unified._theme_signature 主窗口的指纹
    picker._theme_obj       候选条自己的 Theme 缓存

而且有**两条不同的应用路径**：

    正式：apply_settings → _apply_theme_with_fade → _apply_config_now
    预览：_preview_theme_palette → 手动改 unified.theme / picker._theme_obj

预览路径绕过了主窗口的 `_apply_config_now`，于是必然出现状态不同步。
用户看到的现象就是"切换自定义主题有概率生效"——取决于某次
`_apply_app_theme` 是否因为 QSS 串相同而被幂等短路。

## 设计

    ThemeController
      state: config + preview_palette     ← 唯一的真相
      current() -> Theme                  ← 所有人都从这里取
      set_config(cfg)                     ← 正式变更
      set_preview(palette)                ← 预览（不落盘）
      clear_preview()                     ← 退出预览
      subscribe(cb)                       ← 主题变化时通知

所有窗口订阅 `on_change`，不再各自缓存 Theme。预览和正式走同一条
`_notify()` 路径，只是数据来源不同——从根本上消除两条路径的分叉。

## 线程

只在 GUI 主线程使用。`_notify()` 直接同步调用订阅者，
因为换肤本身必须在主线程做（Qt 控件约束）。
"""
from PySide6.QtCore import QObject, Signal


class ThemeController(QObject):
    """主题的唯一权威源。

    :param config: 初始配置 dict（会被复制，不直接持有调用方的对象）

    ## 信号

    `changed(dict)`：主题发生了变化，参数是最新的完整色板（含派生色）。
    订阅者据此重绘，不需要自己去 build_theme。
    """

    #: 主题变化。参数是完整色板 dict（含 dark/mode/preset/name）。
    changed = Signal(dict)

    def __init__(self, config=None, parent=None):
        super().__init__(parent)
        self._config = dict(config or {})
        #: 预览色板；None 表示"不在预览态"
        self._preview = None
        #: 最近一次构建出的色板，供 current() 快速返回
        self._palette = None
        #: 防止 notify 期间再次触发（订阅者回调里改 config 会递归）
        self._notifying = False
        self.rebuild()

    # ---------------------------------------------------------------- 状态
    def config(self):
        """当前配置的**副本**（防止外部误改内部状态）。"""
        return dict(self._config)

    @property
    def previewing(self):
        """是否处于预览态（编辑器打开着）。"""
        return self._preview is not None

    def current(self):
        """当前生效的 Theme 对象。所有人从这里取，不要自己 build。"""
        from ui.win11_theme import Theme

        if self._palette is None:
            self.rebuild()
        return Theme(self._palette.get("mode", "light"),
                     palette=self._palette)

    def current_palette(self):
        """当前生效的完整色板 dict。"""
        if self._palette is None:
            self.rebuild()
        return dict(self._palette)

    # ---------------------------------------------------------------- 变更
    def set_config(self, config, notify=True):
        """更新配置（正式应用）。退出预览态。"""
        self._config = dict(config or {})
        self._preview = None
        self.rebuild(notify=notify)

    def update_config(self, **kwargs):
        """原地更新若干配置键（不退出预览，用于明暗切换等）。"""
        self._config.update(kwargs)
        self.rebuild()

    def set_preview(self, palette):
        """进入/更新预览态。

        :param palette: `{"name","light","dark"}` 或 None（= 退出预览）

        预览不改 `config`，只改"取色板时的数据来源"。这样
        `current()` 在预览和正式两种状态下走的是**同一段代码**，
        只是输入不同——这正是重构要消除的分叉。
        """
        self._preview = dict(palette) if palette else None
        self.rebuild()

    def clear_preview(self):
        """退出预览，回到已保存的主题。"""
        if self._preview is None:
            return
        self._preview = None
        self.rebuild()

    # ---------------------------------------------------------------- 内部
    def rebuild(self, notify=True):
        """按当前 state 重新构建色板，并按需广播。"""
        from ui.theme_manager import build_theme

        cfg = dict(self._config)
        # 预览态：色板来自编辑器，不走磁盘
        preview = None
        if self._preview is not None:
            cfg["theme_preset"] = "custom"
            preview = self._preview

        theme = build_theme(cfg, preview_palette=preview)
        self._palette = dict(theme._palette) if hasattr(theme, "_palette") \
            else self._palette_from(theme)

        if notify:
            self._notify()

    @staticmethod
    def _palette_from(theme):
        """从 Theme 对象反推色板（兼容没有 _palette 的实现）。"""
        from core.theme_presets import ALL_COLOR_KEYS

        p = {k: getattr(theme, k, "") for k in ALL_COLOR_KEYS}
        p["dark"] = theme.dark
        p["mode"] = theme.name
        p["preset"] = theme.preset
        p["name"] = getattr(theme, "preset_name", theme.preset)
        return p

    def _notify(self):
        """广播变化。带递归保护——订阅者回调里不应该再改主题。"""
        if self._notifying:
            return
        self._notifying = True
        try:
            self.changed.emit(self.current_palette())
        finally:
            self._notifying = False

    # ---------------------------------------------------------------- 订阅
    def subscribe(self, callback):
        """订阅主题变化。返回一个可调用的取消订阅函数。

        :param callback: `callback(palette_dict)`

        用"返回值就是取消函数"而不是 `unsubscribe(cb)`：后者在
        lambda 场景下没法精确移除（每次都是新对象）。
        """
        self.changed.connect(callback)

        def _off():
            try:
                self.changed.disconnect(callback)
            except (RuntimeError, TypeError):
                pass        # 已经断开/对象已销毁

        return _off
