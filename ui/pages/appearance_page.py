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
        self._add_row(lay, "背景图", self._material_image_row())
        self._add_row(lay, "图片不透明度", self._material_image_opacity_row())
        self._add_row(lay, "背景暗化/提亮", self._material_dim_row())
        self._hint(lay, "材质只作用于主窗口；候选条和弹窗保持纯色"
                        "（它们很小，背景图反而看不清内容）。")
        root.addWidget(c)
        root.addStretch(1)

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

    def _material_image_opacity_row(self):
        return self._slider_row(
            "material_image_opacity_slider", "material_image_opacity", 0, 100,
            on_change=lambda k, s: self._push(
                material_image_opacity=s.value()))

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

        只传"用户刚改的那几项"，其余由 controller 保持不变。
        这是"改 A 不会带歪 B"的机制保证。
        """
        if self._loading:
            return
        changed = self._ctrl.update(**changes)
        self._sync_enabled()
        if changed and self._on_apply is not None:
            self._on_apply()

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
                      self.material_image_btn,
                      self.material_image_opacity_slider,
                      self.material_dim_slider):
                w.setEnabled(on)
            from ui.window_material import background_path

            self.material_image_clear.setEnabled(on and bool(background_path()))
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
            for attr, val in (
                    ("panel_alpha_slider", int(round(st.panel_alpha * 100))),
                    ("opacity_slider", int(round(st.opacity * 100))),
                    ("material_strength_slider", int(st.material_strength)),
                    ("material_image_opacity_slider",
                     int(st.material_image_opacity)),
                    ("material_dim_slider", int(st.material_dim))):
                s = getattr(self, attr, None)
                if s is not None:
                    s.setValue(max(s.minimum(), min(s.maximum(), val)))
                    lbl = getattr(self, attr + "_value", None)
                    if lbl is not None:
                        lbl.setText("%d%%" % s.value())
            self._refresh_image_label()
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
