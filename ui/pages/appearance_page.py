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
    def _build(self):
        root = page_layout(self)
        root.addWidget(page_title("外观"))

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
        self._add_row(lay, "候选条不透明度", self._opacity_row())
        self._hint(lay, "面板不透明度影响主窗口底板；"
                        "拉到 100% 完全不透明，拉低会透出桌面和背景图。")
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
        self._add_row(lay, "背景暗化/提亮", self._material_dim_row())
        self._hint(lay, "设置了背景图时，面板会自动让位给背景图（始终看得见）；"
                        "没设背景图时显示主题实色面板。"
                        "「面板不透明度」是整窗透明度，拉低会透出桌面。")
        root.addWidget(c)

        # ---- 候选条外观（独立于主窗口）----
        #
        # 候选条弹在光标附近，跟主窗口的视觉需求完全不同：它要"看得清字"，
        # 主窗口要"背景好看"。所以给一套独立配置。
        root.addWidget(section_title("候选条外观"))
        c = card()
        lay = card_layout(c)
        self._add_row(lay, "启用材质", self._picker_material_toggle())
        self._add_row(lay, "材质效果", self._picker_material_combo())
        self._add_row(lay, "材质强度", self._picker_strength_row())
        self._add_row(lay, "独立背景图", self._picker_own_image_toggle())
        self._add_row(lay, "选择背景图", self._picker_image_row())
        self._add_row(lay, "图片不透明度", self._picker_image_opacity_row())
        self._add_row(lay, "显示图片的一部分", self._picker_crop_row())
        self._hint(lay, "不勾选「独立背景图」时沿用主窗口那张；"
                        "「显示图片的一部分」可以只取图片的某个区域，"
                        "适合从整张壁纸里截一块好看的。")
        root.addWidget(c)
        root.addStretch(1)

    # ---------- 候选条外观 ----------
    def _picker_material_toggle(self):
        """候选条是否使用材质（与主窗口的开关互不影响）。"""
        self.picker_material_check = ToggleSwitch(self._theme)
        self.picker_material_check.setToolTip(
            "候选条是否套用背景图/模糊效果。\n"
            "候选条很小，材质过强会影响看字，建议低强度。")
        self.picker_material_check.toggled.connect(
            lambda *_a: self._push(picker_material_enabled=(
                self.picker_material_check.isChecked())))
        return self.picker_material_check

    def _picker_material_combo(self):
        from ui.window_material import material_choices

        self.picker_material_combo = FluentComboBox(self._theme)
        for mid, name in material_choices():
            self.picker_material_combo.addItem(name, mid)
        self.picker_material_combo.currentIndexChanged.connect(
            lambda *_a: self._push(picker_material_type=(
                self.picker_material_combo.currentData())))
        self.picker_material_combo.setMinimumWidth(120)
        return self.picker_material_combo

    def _picker_strength_row(self):
        return self._slider_row(
            "picker_strength_slider", "picker_material_strength", 0, 100,
            on_change=lambda k, s: self._push(
                picker_material_strength=s.value()))

    def _picker_own_image_toggle(self):
        """是否用候选条自己的背景图（否则沿用主窗口的）。"""
        self.picker_own_image_check = ToggleSwitch(self._theme)
        self.picker_own_image_check.setToolTip(
            "勾选后候选条用自己的背景图；不勾选沿用主窗口那张。")
        self.picker_own_image_check.toggled.connect(
            lambda *_a: self._push(picker_use_own_image=(
                self.picker_own_image_check.isChecked())))
        return self.picker_own_image_check

    def _picker_image_row(self):
        row = QHBoxLayout()
        self.picker_image_btn = QPushButton("选择图片…")
        self.picker_image_btn.clicked.connect(self._pick_picker_image)
        row.addWidget(self.picker_image_btn)
        self.picker_image_clear = QPushButton("清除")
        self.picker_image_clear.clicked.connect(self._clear_picker_image)
        row.addWidget(self.picker_image_clear)
        self.picker_image_label = QLabel("")
        self.picker_image_label.setObjectName("Caption")
        row.addWidget(self.picker_image_label)
        row.addStretch(1)
        return row

    def _picker_image_opacity_row(self):
        return self._slider_row(
            "picker_image_opacity_slider", "picker_image_opacity", 0, 100,
            on_change=lambda k, s: self._push(
                picker_image_opacity=s.value()))

    def _picker_crop_row(self):
        """「显示图片的一部分」：四个 0~100 的输入，对应 x/y/w/h 百分比。

        用比例而不是像素：候选条尺寸会随候选数量变化，存像素会导致
        窗口一变裁切就错位。
        """
        from PySide6.QtWidgets import QSpinBox

        row = QHBoxLayout()
        self._picker_crop_boxes = {}
        for key, label in (("x", "左"), ("y", "上"),
                           ("w", "宽"), ("h", "高")):
            row.addWidget(QLabel(label))
            sb = QSpinBox()
            sb.setRange(0, 100)
            sb.setFixedWidth(62)
            sb.setSuffix("%")
            if key in ("w", "h"):
                sb.setValue(100)
            sb.editingFinished.connect(self._on_picker_crop_changed)
            row.addWidget(sb)
            self._picker_crop_boxes[key] = sb
        btn = QPushButton("重置")
        btn.setToolTip("恢复为显示整张图片")
        btn.clicked.connect(self._reset_picker_crop)
        row.addWidget(btn)
        row.addStretch(1)
        return row

    def _current_picker_crop(self):
        """从四个输入框读出 (x, y, w, h) 相对比例。"""
        try:
            b = self._picker_crop_boxes
            return (b["x"].value() / 100.0, b["y"].value() / 100.0,
                    b["w"].value() / 100.0, b["h"].value() / 100.0)
        except Exception:
            return (0.0, 0.0, 0.0, 0.0)

    def _on_picker_crop_changed(self):
        if self._loading:
            return
        x, y, w, h = self._current_picker_crop()
        self._push(picker_crop_x=x, picker_crop_y=y,
                   picker_crop_w=w, picker_crop_h=h)

    def _reset_picker_crop(self):
        """恢复成"显示整张"（全 0 表示不裁切）。"""
        self._loading = True
        try:
            for key, sb in self._picker_crop_boxes.items():
                sb.setValue(0 if key in ("x", "y") else 100)
        finally:
            self._loading = False
        self._push(picker_crop_x=0.0, picker_crop_y=0.0,
                   picker_crop_w=0.0, picker_crop_h=0.0)

    def _pick_picker_image(self):
        """选候选条背景图（存到独立命名空间，不覆盖主窗口那张）。"""
        from PySide6.QtWidgets import QFileDialog, QMessageBox

        from ui.window_material import (
            IMAGE_SUFFIXES, SLOT_PICKER, copy_background,
        )

        patterns = " ".join("*" + e for e in IMAGE_SUFFIXES)
        path, _ = QFileDialog.getOpenFileName(
            self, "选择候选条背景图", "",
            "图片 (%s);;所有文件 (*.*)" % patterns)
        if not path:
            return
        ok, result = copy_background(path, slot=SLOT_PICKER)
        if not ok:
            QMessageBox.warning(self, "候选条背景图", result)
            return
        self._refresh_picker_image_label()
        self._sync_enabled()
        self._push(picker_use_own_image=True)

    def _clear_picker_image(self):
        from ui.window_material import SLOT_PICKER, clear_background

        if clear_background(slot=SLOT_PICKER):
            self._refresh_picker_image_label()
            self._sync_enabled()
            if self._on_apply is not None:
                self._on_apply(self._ctrl.changes_for())

    def _refresh_picker_image_label(self):
        import os

        from ui.window_material import SLOT_PICKER, background_path

        try:
            p = background_path(SLOT_PICKER)
            if p:
                self.picker_image_label.setText(os.path.basename(p))
            else:
                self.picker_image_label.setText("未设置")
        except Exception:
            pass

    # ---------- 候选条外观 ----------
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

    def _opacity_row(self):
        return self._slider_row(
            "opacity_slider", "opacity", 0, 100,
            on_change=lambda k, s: self._push(opacity=s.value() / 100.0))

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
        """把改动交给唯一入口。

        ## 关键

        1. 只传"用户刚改的那几项"，其余由 controller 保持不变。
        2. 用 `changes_for()` 算出**目标配置快照**，交给宿主的 `on_apply(cfg)`。
           controller 自己**不改** config——否则宿主的 `apply_settings`
           比对不出差异，切主题会静默失效（实测过：config 变了、界面没变）。

        ## 动画策略

        `material_type` 变化（换材质种类）时走完整过渡——那是一次明显的
        视觉切换，需要动画遮掩重算停顿；其它参数（强度/饱和度滑块）
        只重绘，不做动画，否则拖一次滑块就要看一遍淡入淡出，很拖沓。
        """
        if self._loading:
            return
        target_cfg = self._ctrl.changes_for(**changes)
        changed = self._ctrl.update(**changes)
        self._sync_enabled()
        if not changed or self._on_apply is None:
            return
        kind_changed = "material_type" in changes
        animated = getattr(self, "_animated_apply", None)
        if kind_changed and animated is not None:
            animated(lambda: self._on_apply(target_cfg))
        else:
            self._on_apply(target_cfg)

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
        """材质关着时，下方参数控件置灰。"""
        try:
            st = self._ctrl.state()
            on = bool(st.material_enabled)
            for w in (self.material_combo, self.material_strength_slider,
                      self.material_saturate_slider,
                      self.material_brightness_slider,
                      self.material_image_btn,
                      self.material_dim_slider):
                w.setEnabled(on)
            from ui.window_material import background_path

            self.material_image_clear.setEnabled(on and bool(background_path()))

            # 候选条：材质关时下方控件置灰
            pon = bool(st.picker_material_enabled)
            for w in (self.picker_material_combo,
                      self.picker_strength_slider,
                      self.picker_own_image_check,
                      self.picker_image_btn,
                      self.picker_image_opacity_slider, *self._picker_crop_boxes.values()):
                w.setEnabled(pon)
            from ui.window_material import SLOT_PICKER, background_path as _bp

            self.picker_image_clear.setEnabled(
                pon and bool(_bp(SLOT_PICKER)))
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
        """按 controller 的当前状态回填所有控件（不触发保存）。"""
        st = self._ctrl.state()
        self._loading = True
        try:
            for combo, val in ((self.theme_combo, st.theme_mode),
                               (self.preset_combo, st.theme_preset),
                               (self.material_combo, st.material_type)):
                idx = combo.findData(val)
                combo.setCurrentIndex(idx if idx >= 0 else 0)
            self.material_check.setChecked(bool(st.material_enabled))
            # (控件, 目标值, 标签格式化) —— 三类滑块单位不同，必须分别格式化
            for attr, val, fmt in (
                    ("panel_alpha_slider", int(round(st.panel_alpha * 100)),
                     lambda v: "%d%%" % v),
                    ("opacity_slider", int(round(st.opacity * 100)),
                     lambda v: "%d%%" % v),
                    ("material_strength_slider", int(st.material_strength),
                     lambda v: "%d%%" % v),
                    ("material_saturate_slider", int(st.material_saturate),
                     lambda v: "%.1fx" % (v / 10.0)),
                    ("material_brightness_slider", int(st.material_brightness),
                     lambda v: "%.2fx" % (v / 100.0)),
                    ("material_dim_slider", int(st.material_dim),
                     lambda v: "%d%%" % v)):
                s = getattr(self, attr, None)
                if s is not None:
                    s.setValue(max(s.minimum(), min(s.maximum(), val)))
                    lbl = getattr(self, attr + "_value", None)
                    if lbl is not None:
                        lbl.setText(fmt(s.value()))
            self._refresh_image_label()
            # 候选条外观
            self.picker_material_check.setChecked(
                bool(st.picker_material_enabled))
            self.picker_own_image_check.setChecked(
                bool(st.picker_use_own_image))
            idx = self.picker_material_combo.findData(
                st.picker_material_type)
            self.picker_material_combo.setCurrentIndex(idx if idx >= 0 else 0)
            for attr, val in (
                    ("picker_strength_slider",
                     int(st.picker_material_strength)),
                    ("picker_image_opacity_slider",
                     int(st.picker_image_opacity))):
                sl = getattr(self, attr, None)
                if sl is not None:
                    sl.setValue(max(sl.minimum(), min(sl.maximum(), val)))
                    lbl = getattr(self, attr + "_value", None)
                    if lbl is not None:
                        lbl.setText("%d%%" % sl.value())
            # 裁切：相对比例 → 百分比。
            # ⚠ 存的全 0 表示"显示整张"，回填时要摆成 左0/上0/宽100/高100，
            # 否则用户看到 0% 会以为图片被裁没了。
            cx, cy = float(st.picker_crop_x), float(st.picker_crop_y)
            cw, ch = float(st.picker_crop_w), float(st.picker_crop_h)
            if cw <= 0 or ch <= 0:
                cx, cy, cw, ch = 0.0, 0.0, 1.0, 1.0
            for key, val in (("x", cx), ("y", cy),
                             ("w", cw), ("h", ch)):
                sb = self._picker_crop_boxes.get(key)
                if sb is not None:
                    sb.setValue(int(round(val * 100)))
            self._refresh_picker_image_label()
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
