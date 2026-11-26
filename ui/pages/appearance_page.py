"""外观页：主题、透明度、背景材质的统一入口。

## 为什么要独立成页

原来这些散在「设置」页的「外观」和「背景材质」两个分组里，和热键、
输入、推荐、系统等十几项挤在一起，一屏放不下。加了背景材质之后：

* 用户找不到「面板不透明度」了（被材质那 6 行挤到屏幕外）；
* 外观相关的改动互相冲突（详见 `ui/appearance.py` 的说明）。

现在外观独立成页，结构清晰：

    主题        明暗 / 配色方案 / 编辑自定义主题
    透明度      面板 / 候选条
    背景材质    开关 / 效果 / 强度 / 背景图 / 图片透明度 / 暗化

## 唯一入口

所有读写都走 `AppearanceController`（见 ui/appearance.py），
不直接碰 config。这样"改 A 把 B 带歪"从机制上被杜绝。
"""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QPushButton, QSlider, QWidget,
)

from ui.fluent_combobox import FluentComboBox
from ui.pages._scaffold import (
    card, card_layout, page_layout, page_title, section_title,
)
from ui.toggle_switch import ToggleSwitch


class AppearancePage(QWidget):
    """外观设置页。

    :param controller: `AppearanceController`（唯一入口）
    :param theme: 当前主题（用于控件配色）
    :param on_apply: 无参回调——外观变了、需要落盘 + 重绘时调用
    """

    def __init__(self, controller, theme, on_apply=None, parent=None):
        super().__init__(parent)
        self._ctrl = controller
        self._theme = theme
        self._on_apply = on_apply
        self._loading = False
        self._build()

    # ------------------------------------------------------------ 构建
    # ------------------------------------------------------------ 构建
    def _build(self):
        root = page_layout(self)
        root.addWidget(page_title("外观"))

        # ---- 作用于哪个界面 ----
        #
        # ⚠ 这里**合并了原来的「外观」页和「主题包」页**。
        #
        # 那两页曾同时存在，且都能改材质开关/材质效果/材质强度/整窗不透明度
        # ——同一份配置两个入口，纯属重复。现在只有一个页面，顶部选界面，
        # 下面所有控件都作用于选中的那个。
        root.addWidget(section_title("应用于"))
        c = card()
        lay = card_layout(c)
        self._add_row(lay, "界面", self._surface_row())
        self._add_row(lay, "独立配置", self._follow_check_row())
        self._hint(lay, "所有界面默认沿用「主窗口」的配置。"
                        "打开「独立配置」后，该界面才会有自己的设置，"
                        "不再跟随主窗口。")
        root.addWidget(c)

        # ---- 主题 ----
        root.addWidget(section_title("主题"))
        c = card()
        lay = card_layout(c)
        self._add_row(lay, "主题", self._theme_combo())
        self._add_row(lay, "配色方案", self._preset_row())
        self._hint(lay, "「自定义」配色可以用右侧的「编辑…」调整颜色；"
                        "强调色也在编辑器里设置。")
        root.addWidget(c)

        # ---- 透明度 ----
        root.addWidget(section_title("透明度"))
        c = card()
        lay = card_layout(c)
        self._add_row(lay, "面板不透明度", self._panel_alpha_row())
        self._hint(lay, "整窗透明度：拉到 100% 完全不透明，"
                        "拉低会透出桌面和背景图。")
        root.addWidget(c)

        # ---- 背景材质 ----
        root.addWidget(section_title("背景材质"))
        c = card()
        lay = card_layout(c)
        self._add_row(lay, "启用背景材质", self._material_toggle())
        self._add_row(lay, "材质效果", self._material_combo())
        self._add_row(lay, "材质强度", self._material_strength_row())
        self._add_row(lay, "玻璃饱和度", self._material_saturate_row())
        self._add_row(lay, "玻璃亮度", self._material_brightness_row())
        self._add_row(lay, "背景图", self._material_image_row())
        self._add_row(lay, "显示图片的一部分", self._main_crop_row())
        self._add_row(lay, "背景暗化/提亮", self._material_dim_row())
        self._hint(lay, "设置了背景图时，面板会自动让位给背景图（始终看得见）；"
                        "没设背景图时显示主题实色面板。")
        root.addWidget(c)
        root.addStretch(1)

    # ---------- 界面切换 ----------
    def _surface_row(self):
        """选择当前要编辑哪个界面。"""
        from core.theme_profile import SURFACES

        self._surface_combo = FluentComboBox(self._theme)
        for key, label in SURFACES.items():
            self._surface_combo.addItem(label, key)
        self._surface_combo.setMinimumWidth(150)
        self._surface_combo.currentIndexChanged.connect(
            lambda *_a: self._on_surface_changed())
        return self._surface_combo

    def _follow_check_row(self):
        """是否给当前界面单独配置（关闭 = 沿用主窗口）。"""
        self._follow_check = ToggleSwitch(self._theme)
        self._follow_check.setToolTip(
            "关闭 = 该界面完整沿用主窗口的外观；\n"
            "打开 = 为该界面单独设置。")
        self._follow_check.toggled.connect(self._on_follow_toggled)
        return self._follow_check

    def _current_surface(self):
        try:
            return self._surface_combo.currentData() or "main"
        except Exception:
            return "main"

    def _on_surface_changed(self):
        if self._loading:
            return
        self.refresh_from_controller()

    def _on_follow_toggled(self, follow):
        """切「独立配置」：开 = 把当前生效值落成一份覆盖；关 = 删掉覆盖。"""
        if self._loading:
            return
        from core.theme_profile import clear_override, resolve, set_override

        surface = self._current_surface()
        if surface == "main":
            return
        cfg = self._ctrl.config()
        if follow:
            p = resolve(cfg, surface)
            set_override(cfg, surface, {
                "material_enabled": p.get("material_enabled"),
                "material_type": p.get("material_type"),
                "material_strength": p.get("material_strength"),
                "material_saturate": p.get("material_saturate"),
                "material_brightness": p.get("material_brightness"),
                "material_dim": p.get("material_dim"),
                "panel_alpha": p.get("panel_alpha"),
                "image_opacity": p.get("image_opacity"),
            })
        else:
            clear_override(cfg, surface)
        if self._on_apply is not None:
            try:
                self._on_apply(dict(cfg))
            except Exception:
                pass
        self.refresh_from_controller()

    # ---------- 候选条外观 ----------
    def _main_crop_row(self):
        """主窗口背景图的「只显示一部分」——用可视化裁切对话框。"""
        row = QHBoxLayout()
        self.main_crop_btn = QPushButton("选择显示区域…")
        self.main_crop_btn.setToolTip("打开裁切窗口，拖拽选图里的一块")
        self.main_crop_btn.clicked.connect(self._edit_main_crop)
        row.addWidget(self.main_crop_btn)
        self.main_crop_reset = QPushButton("整张")
        self.main_crop_reset.clicked.connect(self._reset_main_crop)
        row.addWidget(self.main_crop_reset)
        self.main_crop_label = QLabel("")
        self.main_crop_label.setObjectName("Caption")
        row.addWidget(self.main_crop_label)
        row.addStretch(1)
        return row

    def _edit_main_crop(self):
        """弹出可视化裁切框（拖拽选区，不是填数字）。"""
        from ui.crop_dialog import edit_crop
        from ui.window_material import SLOT_MAIN, background_path

        path = background_path(SLOT_MAIN)
        if not path:
            from PySide6.QtWidgets import QMessageBox

            QMessageBox.information(self, "裁切", "请先选择一张背景图。")
            return
        st = self._ctrl.state()
        crop = edit_crop(path, (st.crop_x, st.crop_y,
                                st.crop_w, st.crop_h),
                         parent=self, title="主窗口背景图 - 选择显示区域")
        if crop is None:
            return
        self._push(crop_x=crop[0], crop_y=crop[1],
                   crop_w=crop[2], crop_h=crop[3])
        self._refresh_main_crop_label()

    def _reset_main_crop(self):
        self._push(crop_x=0.0, crop_y=0.0, crop_w=0.0, crop_h=0.0)
        self._refresh_main_crop_label()

    def _refresh_main_crop_label(self):
        st = self._ctrl.state()
        try:
            if st.crop_w <= 0 or st.crop_h <= 0:
                self.main_crop_label.setText("整张")
            else:
                self.main_crop_label.setText(
                    "%d%% × %d%% @ (%d%%, %d%%)" % (
                        round(st.crop_w * 100), round(st.crop_h * 100),
                        round(st.crop_x * 100), round(st.crop_y * 100)))
        except Exception:
            pass

    def _add_row(self, layout, label, widget_or_layout):
        """一行设置项：左标签 + 右控件（与设置页同样的视觉规格）。"""
        row = QHBoxLayout()
        row.setContentsMargins(0, 6, 0, 6)
        row.setSpacing(12)
        row.setAlignment(Qt.AlignVCenter)
        lb = QLabel(label)
        lb.setObjectName("BodyText")
        row.addWidget(lb)
        row.addStretch(1)
        if isinstance(widget_or_layout, QWidget):
            row.addWidget(widget_or_layout)
        else:
            row.addLayout(widget_or_layout)
        container = QWidget()
        container.setLayout(row)
        container.setMinimumHeight(36)
        layout.addWidget(container)

    def _hint(self, layout, text):
        """分组底部的说明文字。"""
        lb = QLabel(text)
        lb.setObjectName("Caption")
        lb.setWordWrap(True)
        layout.addWidget(lb)

    # ------------------------------------------------------------ 控件
    def _theme_combo(self):
        self.theme_combo = FluentComboBox(self._theme)
        self.theme_combo.addItem("浅色", "light")
        self.theme_combo.addItem("深色", "dark")
        self.theme_combo.addItem("跟随系统", "system")
        self.theme_combo.currentIndexChanged.connect(
            lambda *_a: self._push(theme_mode=self.theme_combo.currentData()))
        self.theme_combo.setMinimumWidth(120)
        return self.theme_combo

    def _preset_row(self):
        from core.theme_presets import preset_choices

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        self.preset_combo = FluentComboBox(self._theme)
        for pid, name in preset_choices():
            self.preset_combo.addItem(name, pid)
        self.preset_combo.addItem("自定义", "custom")
        self.preset_combo.currentIndexChanged.connect(
            lambda *_a: self._on_preset_changed())
        self.preset_combo.setMinimumWidth(110)
        row.addWidget(self.preset_combo)

        self.edit_preset_btn = QPushButton("编辑…")
        self.edit_preset_btn.setToolTip("调整自定义主题的颜色")
        self.edit_preset_btn.clicked.connect(self._open_theme_editor)
        row.addWidget(self.edit_preset_btn)
        row.addStretch(1)
        return row

    def _slider_row(self, attr, config_key, lo, hi, fmt="%d%%",
                    on_change=None):
        """0~100 通用滑块行。"""
        row = QHBoxLayout()
        s = QSlider(Qt.Horizontal)
        s.setRange(lo, hi)
        s.setFixedWidth(140)
        val = QLabel("")
        val.setFixedWidth(44)
        s.valueChanged.connect(lambda v: val.setText(fmt % v))
        s.sliderReleased.connect(
            lambda *_a: (on_change or self._push_from_slider)(config_key, s))
        row.addWidget(s)
        row.addWidget(val)
        row.addStretch(1)
        setattr(self, attr, s)
        setattr(self, attr + "_value", val)
        return row

    def _panel_alpha_row(self):
        # 面板不透明度：0~100 映射到 0.0~1.0
        return self._slider_row(
            "panel_alpha_slider", "panel_alpha", 0, 100,
            on_change=lambda k, s: self._push(panel_alpha=s.value() / 100.0))

    def _material_toggle(self):
        self.material_check = ToggleSwitch(self._theme)
        self.material_check.setToolTip(
            "开启后可用自定义背景图和玻璃效果。\n"
            "关闭则使用纯色背景，最省资源。")
        self.material_check.toggled.connect(
            lambda *_a: self._push(material_enabled=self.material_check.isChecked()))
        return self.material_check

    def _material_combo(self):
        from ui.window_material import material_choices

        self.material_combo = FluentComboBox(self._theme)
        for mid, name in material_choices():
            self.material_combo.addItem(name, mid)
        self.material_combo.setToolTip(
            "毛玻璃：高斯模糊，背景隐约可见\n"
            "磨砂玻璃：轻微模糊 + 细颗粒\n"
            "雾化：半透明叠层，画面柔和")
        self.material_combo.currentIndexChanged.connect(
            lambda *_a: self._push(
                material_type=self.material_combo.currentData()))
        self.material_combo.setMinimumWidth(120)
        return self.material_combo

    def _material_strength_row(self):
        return self._slider_row(
            "material_strength_slider", "material_strength", 0, 100,
            on_change=lambda k, s: self._push(material_strength=s.value()))

    def _material_saturate_row(self):
        """玻璃饱和度 ×10（10 = 1.0x，30 = 3.0x）。

        这是"玻璃精致感"最关键的参数——纯模糊只会糊，提饱和才有通透感。
        """
        row = QHBoxLayout()
        s = QSlider(Qt.Horizontal)
        s.setRange(10, 30)
        s.setFixedWidth(140)
        val = QLabel("")
        val.setFixedWidth(44)
        s.valueChanged.connect(lambda v: val.setText("%.1fx" % (v / 10.0)))
        s.sliderReleased.connect(
            lambda *_a: self._push(material_saturate=s.value()))
        row.addWidget(s)
        row.addWidget(val)
        row.addStretch(1)
        self.material_saturate_slider = s
        self.material_saturate_slider_value = val
        return row

    def _material_brightness_row(self):
        """玻璃亮度 ×100（80 = 0.8x，130 = 1.3x）。"""
        row = QHBoxLayout()
        s = QSlider(Qt.Horizontal)
        s.setRange(80, 130)
        s.setFixedWidth(140)
        val = QLabel("")
        val.setFixedWidth(44)
        s.valueChanged.connect(lambda v: val.setText("%.2fx" % (v / 100.0)))
        s.sliderReleased.connect(
            lambda *_a: self._push(material_brightness=s.value()))
        row.addWidget(s)
        row.addWidget(val)
        row.addStretch(1)
        self.material_brightness_slider = s
        self.material_brightness_slider_value = val
        return row

    def _material_dim_row(self):
        return self._slider_row(
            "material_dim_slider", "material_dim", 0, 100,
            on_change=lambda k, s: self._push(material_dim=s.value()))

    def _material_image_row(self):
        row = QHBoxLayout()
        self.material_image_btn = QPushButton("选择图片…")
        self.material_image_btn.clicked.connect(self._pick_image)
        row.addWidget(self.material_image_btn)
        self.material_image_clear = QPushButton("清除")
        self.material_image_clear.clicked.connect(self._clear_image)
        row.addWidget(self.material_image_clear)
        self.material_image_label = QLabel("")
        self.material_image_label.setObjectName("Caption")
        row.addWidget(self.material_image_label)
        row.addStretch(1)
        return row

    # ------------------------------------------------------------ 交互
    def _push(self, **changes):
        """把改动应用到**当前选中的界面**。

        ## 两个去向

        * 选的是「主窗口」→ 写进主配置（theme_profile），
          所有**沿用主配置**的界面会一起跟着变
        * 选的是某个独立配置的界面 → 只写该界面的覆盖层，
          主窗口和其它界面不受影响

        ## 为什么不用 controller

        `AppearanceController` 是"主配置"的编辑器，只认识平铺的旧键。
        多界面覆盖是 `core.theme_profile` 的职责，两者语义不同，
        所以这里直接写主题包，再交给宿主落盘。
        """
        if self._loading:
            return
        from core.theme_profile import (
            get_profile, has_override, set_override, set_profile,
        )

        surface = self._current_surface()
        cfg = self._ctrl.config()

        # 旧键名 → 主题包字段名
        alias = {
            "material_strength": "material_strength",
            "material_saturate": "material_saturate",
            "material_brightness": "material_brightness",
            "material_dim": "material_dim",
            "material_type": "material_type",
            "material_enabled": "material_enabled",
            "panel_alpha": "panel_alpha",
        }
        patch = {}
        for key, val in changes.items():
            if key in alias:
                patch[alias[key]] = val
            elif key in ("opacity",):
                continue        # 候选条透明度已合并到 panel_alpha

        if not patch:
            return

        if surface == "main":
            prof = get_profile(cfg)
            prof.update(patch)
            set_profile(cfg, prof)
        else:
            if not has_override(cfg, surface):
                return          # 未开独立配置时控件本就置灰，不该走到这
            set_override(cfg, surface, patch)

        self._sync_enabled()
        if self._on_apply is None:
            return
        target = dict(cfg)
        # 换材质种类时走过渡动画（明显视觉切换，需要遮掩重算停顿）
        if "material_type" in patch:
            animated = getattr(self, "_animated_apply", None)
            if animated is not None:
                animated(lambda: self._on_apply(target))
                return
        self._on_apply(target)

    def _push_from_slider(self, _key, _slider):
        # 默认路径不会走到（各滑块都自带 on_change）
        pass

    def _on_preset_changed(self):
        if self._loading:
            return
        self._sync_preset_btn()
        self._push(theme_preset=self.preset_combo.currentData() or "classic")

    def _sync_preset_btn(self):
        try:
            self.edit_preset_btn.setEnabled(
                self.preset_combo.currentData() == "custom")
        except Exception:
            pass

    def _sync_enabled(self):
        """按「当前界面 + 是否开独立配置 + 材质是否开启」联动置灰。"""
        from core.theme_profile import has_override, resolve

        try:
            cfg = self._ctrl.config()
            surface = self._current_surface()
            own = surface == "main" or has_override(cfg, surface)
            params = resolve(cfg, surface)
            on = own and bool(params.get("material_enabled", False))

            # 材质相关：关着就置灰
            for w in (self.material_combo, self.material_strength_slider,
                      self.material_saturate_slider,
                      self.material_brightness_slider,
                      self.material_image_btn,
                      self.main_crop_btn, self.main_crop_reset,
                      self.material_dim_slider):
                w.setEnabled(on)

            from ui.window_material import SLOT_MAIN, background_path

            self.material_image_clear.setEnabled(
                on and bool(background_path(SLOT_MAIN)))

            # 主题相关：只有开了独立配置（或就是主窗口）才能改
            for w in (self.theme_combo, self.preset_combo,
                      self.edit_preset_btn, self.panel_alpha_slider):
                w.setEnabled(own)
        except Exception:
            pass

    def _pick_image(self):
        from PySide6.QtWidgets import QFileDialog, QMessageBox

        from ui.window_material import IMAGE_SUFFIXES, copy_background

        patterns = " ".join("*" + e for e in IMAGE_SUFFIXES)
        path, _ = QFileDialog.getOpenFileName(
            self, "选择背景图", "", "图片 (%s);;所有文件 (*.*)" % patterns)
        if not path:
            return
        ok, result = copy_background(path)
        if not ok:
            QMessageBox.warning(self, "背景图", result)
            return
        self._refresh_image_label()
        self._sync_enabled()
        # 图片本身不在配置里（存在数据目录），仍要通知重绘
        if self._on_apply is not None:
            self._on_apply()

    def _clear_image(self):
        from ui.window_material import clear_background

        if clear_background():
            self._refresh_image_label()
            self._sync_enabled()
            if self._on_apply is not None:
                self._on_apply()

    def _refresh_image_label(self):
        import os

        from ui.window_material import background_path

        p = background_path()
        try:
            if p:
                self.material_image_label.setText(os.path.basename(p))
            else:
                self.material_image_label.setText("未设置（使用纯色）")
        except Exception:
            pass

    def _open_theme_editor(self):
        """打开主题编辑器（自定义配色）。"""
        from ui.theme_editor import ThemeEditorDialog

        parent = self.window()
        cb = getattr(self, "_theme_preview_cb", None)

        def _preview(cfg, palette=None):
            if cb is not None:
                cb(cfg, palette)

        before = self._ctrl.state().theme_preset
        dlg = ThemeEditorDialog(self._ctrl.config(), on_preview=_preview,
                                parent=parent)
        if dlg.exec() != dlg.DialogCode.Accepted:
            if cb is not None:
                cb(self._ctrl.config(), None)
            # 取消：恢复原来的预设选择
            self._loading = True
            try:
                idx = self.preset_combo.findData(before)
                self.preset_combo.setCurrentIndex(idx if idx >= 0 else 0)
            finally:
                self._loading = False
            self._sync_preset_btn()
            return

        from core.theme_presets import save_user_theme

        ok, msg = save_user_theme(dlg.result_data())
        if not ok:
            return
        if cb is not None:
            cb(self._ctrl.config(), None)
        self._loading = True
        try:
            idx = self.preset_combo.findData("custom")
            self.preset_combo.setCurrentIndex(idx if idx >= 0 else 0)
        finally:
            self._loading = False
        self._sync_preset_btn()
        self._push(theme_preset="custom")

    # ------------------------------------------------------------ 刷新
    def refresh_from_controller(self):
        """按**当前选中的界面**回填所有控件（不触发保存）。

        参数来自 `core.theme_profile.resolve(cfg, surface)`——
        主窗口读主配置，其它界面读"主配置 + 该界面覆盖"的合并结果。
        """
        from core.theme_profile import has_override, resolve

        cfg = self._ctrl.config()
        surface = self._current_surface()
        params = resolve(cfg, surface)
        is_main = surface == "main"
        own = is_main or has_override(cfg, surface)

        self._loading = True
        try:
            # 界面选择器与「独立配置」开关
            idx = self._surface_combo.findData(surface)
            if idx >= 0 and self._surface_combo.currentIndex() != idx:
                self._surface_combo.setCurrentIndex(idx)
            self._follow_check.setEnabled(not is_main)
            self._follow_check.setChecked(own)

            # 主题
            for combo, val in (
                    (self.theme_combo, params.get("theme_mode", "light")),
                    (self.preset_combo, params.get("theme_preset", "classic")),
                    (self.material_combo, params.get("material_type", "none"))):
                i2 = combo.findData(val)
                combo.setCurrentIndex(i2 if i2 >= 0 else 0)

            self.material_check.setChecked(bool(
                params.get("material_enabled", False)))

            # 滑块（单位不同，分别格式化）
            for attr, val, fmt in (
                    ("panel_alpha_slider",
                     int(round(float(params.get("panel_alpha", 0.92)) * 100)),
                     lambda v: "%d%%" % v),
                    ("material_strength_slider",
                     int(params.get("material_strength", 55)),
                     lambda v: "%d%%" % v),
                    ("material_saturate_slider",
                     int(params.get("material_saturate", 18)),
                     lambda v: "%.1fx" % (v / 10.0)),
                    ("material_brightness_slider",
                     int(params.get("material_brightness", 104)),
                     lambda v: "%.2fx" % (v / 100.0)),
                    ("material_dim_slider",
                     int(params.get("material_dim", 0)),
                     lambda v: "%d%%" % v)):
                s = getattr(self, attr, None)
                if s is not None:
                    s.setValue(max(s.minimum(), min(s.maximum(), val)))
                    lbl = getattr(self, attr + "_value", None)
                    if lbl is not None:
                        lbl.setText(fmt(s.value()))

            self._refresh_image_label()
            self._refresh_main_crop_label()
        finally:
            self._loading = False
        self._sync_preset_btn()
        self._sync_enabled()

    def set_theme(self, theme_obj):
        """换肤时同步控件配色。"""
        self._theme = theme_obj
        from ui.fluent_combobox import FluentComboBox as _CB

        for c in self.findChildren(_CB):
            try:
                c.set_theme(theme_obj)
            except Exception:
                pass
        for t in self.findChildren(ToggleSwitch):
            try:
                t.update_theme(theme_obj)
            except Exception:
                pass
        self.update()
