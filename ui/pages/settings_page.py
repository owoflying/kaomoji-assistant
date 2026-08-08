"""设置页：热键、主题、输入方式等配置表单（Win11 Settings 风格，可滚动）。

内容包裹在 QScrollArea 内：当窗口较短、内容高于视口时不会被压扁，
而是出现滚动条，卡片/行保持自然高度（修复此前“被压成细线”的显示异常）。
布尔项改用 WinUI 3 风格 ToggleSwitch，更贴近系统设置观感。
"""
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QSlider, QSpinBox, QFrame, QSizePolicy, QScrollArea,
)
from ui.fluent_combobox import FluentComboBox
from PySide6.QtCore import Qt, Signal

from core import win_utils
from core import autostart
from core import uia_elevation
from core.win_utils import MOD_CONTROL, MOD_ALT, MOD_SHIFT, MOD_WIN
from ui.win11_theme import Theme
from ui.toggle_switch import ToggleSwitch


def _build_hotkey_from_pynput(key, mods_set):
    """根据 pynput 的按键对象与主键，返回 (modifiers:int, vk:int, char)。"""
    from pynput import keyboard
    vk = None
    char = None
    if isinstance(key, keyboard.KeyCode):
        if key.vk:
            vk = key.vk
        if key.char:
            char = key.char
    elif hasattr(key, "vk") and key.vk:
        vk = key.vk
    if vk is None and char:
        vk = win_utils._vk_from_char(char)
    mods = 0
    if MOD_CONTROL in mods_set:
        mods |= MOD_CONTROL
    if MOD_ALT in mods_set:
        mods |= MOD_ALT
    if MOD_SHIFT in mods_set:
        mods |= MOD_SHIFT
    if MOD_WIN in mods_set:
        mods |= MOD_WIN
    return mods, vk, char


class SettingsPage(QWidget):
    config_applied = Signal(dict)    # 任何设置改动后自动保存/应用
    opacity_preview = Signal(float)  # 候选条不透明度实时预览（拖动中），仅作视觉反馈，不落盘

    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.config = dict(config)
        self._captured = None
        self._capturing = False
        self._cap_mods = set()
        self._cap_keys = []
        self._listener = None
        self._pynput = None
        # 程序化加载（构造 / apply_config）期间屏蔽自动保存：setValue/setCurrentIndex/setChecked
        # 会触发 currentIndexChanged/toggled/valueChanged -> _on_apply -> config_applied，
        # 导致把 self.config 换成中间副本、切断与关于页共享的字典引用，造成
        # “开发者模式”等状态在不同页之间读不一致。用户交互时此标志为 False，自动保存照常触发。
        self._loading = False
        # 测试功能分组：处于测试模式的设置项在 use_test_features 关闭时整体隐藏。
        # 新功能默认归入此分组，确认上线后才移出。
        self._test_widgets = []
        self._test_section_title = None
        self._theme = Theme(config.get("theme", "light"))
        self._init_ui()
        self.refresh_from_config()

    def _init_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # 可滚动内容区：内容高于视口时滚动而非压缩
        self.scroll = QScrollArea()
        self.scroll.setObjectName("SettingsScroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.scroll.setStyleSheet("background:transparent;border:none;")
        root.addWidget(self.scroll, 1)

        body = QWidget()
        body.setObjectName("SettingsBody")
        body.setStyleSheet("background:transparent;")
        v = QVBoxLayout(body)
        v.setContentsMargins(36, 28, 36, 28)
        v.setSpacing(22)

        title = QLabel("设置")
        title.setObjectName("PageTitle")
        v.addWidget(title)

        # 全局热键
        v.addWidget(self._section_title("全局热键"))
        card = self._card()
        croot = QVBoxLayout(card)
        croot.setContentsMargins(16, 14, 16, 14)
        croot.setSpacing(10)
        hk_row = QHBoxLayout()
        hk_row.setContentsMargins(0, 6, 0, 6)
        hk_row.setSpacing(12)
        hk_row.setAlignment(Qt.AlignVCenter)
        self.hotkey_btn = QPushButton("录制…")
        self.hotkey_btn.setMinimumWidth(110)
        self.hotkey_btn.clicked.connect(self._start_capture)
        self.finish_btn = QPushButton("完成")
        self.finish_btn.setMinimumWidth(80)
        self.finish_btn.hide()
        self.finish_btn.clicked.connect(self._finish_capture)
        self.hotkey_label = QLabel("")
        self.hotkey_label.setObjectName("BodyText")
        hk_row.addWidget(self.hotkey_btn)
        hk_row.addWidget(self.finish_btn)
        hk_row.addWidget(self.hotkey_label, 1)
        hk_container = QWidget()
        hk_container.setLayout(hk_row)
        hk_container.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        hk_container.setMinimumHeight(36)
        croot.addWidget(hk_container)
        tip = QLabel("点「录制…」后按修饰键+单键（如 Ctrl+Shift+K）或多键序列（如 k+l）。录完点「完成」；Esc 取消。")
        tip.setObjectName("Caption")
        tip.setWordWrap(True)
        croot.addWidget(tip)
        v.addWidget(card)

        # 外观
        v.addWidget(self._section_title("外观"))
        card = self._card()
        croot = QVBoxLayout(card)
        croot.setContentsMargins(16, 14, 16, 14)
        croot.setSpacing(12)
        self._add_row(croot, "主题", self._theme_combo())
        self._add_row(croot, "面板不透明度", self._panel_alpha_row())
        self._add_row(croot, "候选条不透明度", self._opacity_row())
        v.addWidget(card)

        # 输入
        v.addWidget(self._section_title("输入"))
        card = self._card()
        croot = QVBoxLayout(card)
        croot.setContentsMargins(16, 14, 16, 14)
        croot.setSpacing(12)
        self._add_row(croot, "输入方式", self._method_combo())
        self._add_row(croot, "最大最近记录", self._recent_spin())
        self._add_row(croot, "每页候选数", self._page_spin())
        self._add_row(croot, "打字时自动弹出", self._auto_toggle())
        self._add_row(croot, "失焦自动隐藏", self._blur_hide_toggle())
        v.addWidget(card)

        # 系统
        v.addWidget(self._section_title("系统"))
        card = self._card()
        croot = QVBoxLayout(card)
        croot.setContentsMargins(16, 14, 16, 14)
        croot.setSpacing(12)
        self._add_row(croot, "开机自动启动", self._autostart_toggle())
        v.addWidget(card)

        # 高级（测试功能分组：use_test_features 关闭时整体隐藏，见 _apply_test_feature_visibility）
        adv_title = self._section_title("高级")
        v.addWidget(adv_title)
        card = self._card()
        croot = QVBoxLayout(card)
        croot.setContentsMargins(16, 14, 16, 14)
        croot.setSpacing(12)
        uia_row = self._add_row(croot, "使用 UIA 提权", self._uia_elevation_toggle(),
                               test_feature=True)
        tip = QLabel("开启后程序会尝试为自身进程启用 uiAccess，使候选栏成为 UIAccess 顶级窗口，"
                     "从而能与屏幕键盘(osk)等系统窗口处于同一 Z 序层级、互相覆盖。"
                     "该操作需要本程序本身也以管理员身份启动，否则不会生效；默认关闭，不影响其他功能。")
        tip.setObjectName("Caption")
        tip.setWordWrap(True)
        croot.addWidget(tip)
        # 真实状态行：反映“实际是否已拿到 UIAccess”，避免“配置开着但未生效”的假象。
        self.uia_status = QLabel("")
        self.uia_status.setObjectName("Caption")
        self.uia_status.setWordWrap(True)
        croot.addWidget(self.uia_status)
        # 亚克力模糊：与 UIA 提权一并归入测试功能分组（use_test_features 关闭时整段隐藏并归位为关闭）。
        self._add_row(croot, "亚克力模糊", self._acrylic_toggle(), test_feature=True)
        ca_tip = QLabel("开启后窗口与候选栏使用 Windows 亚克力模糊背景（需系统支持 DWM 亚克力）。"
                        "该效果属于实验性外观，仅测试模式可用。")
        ca_tip.setObjectName("Caption")
        ca_tip.setWordWrap(True)
        croot.addWidget(ca_tip)
        v.addWidget(card)
        # 登记：use_test_features 关闭时隐藏整段（标题 + 卡片 + 行 + 说明）。
        # uia_row / 亚克力行 已由 _add_row(test_feature=True) 登记，此处仅补登记卡片与说明文字，
        # 避免重复登记同一控件。
        self._test_section_title = adv_title
        self._test_widgets.extend([card, tip, self.uia_status, ca_tip])

        v.addStretch(1)
        self.scroll.setWidget(body)

        # 所有设置项改动后自动保存：所有控件（含上方各节所建）都已存在，此处统一接线。
        # 外观项不再走 preview（不落盘）机制；滑块拖动时实时更新标签与候选条预览，
        # 释放后再统一保存，避免高频写盘/重绘。
        self.theme_combo.currentIndexChanged.connect(lambda *_a: self._on_apply())
        self.acrylic_check.toggled.connect(lambda *_a: self._on_apply())
        self.panel_alpha_slider.sliderReleased.connect(lambda *_a: self._on_apply())
        self.opacity_slider.sliderReleased.connect(lambda *_a: self._on_apply())
        # 非外观项改动立即保存
        self.method_combo.currentIndexChanged.connect(lambda *_a: self._on_apply())
        self.recent_spin.valueChanged.connect(lambda *_a: self._on_apply())
        self.page_spin.valueChanged.connect(lambda *_a: self._on_apply())
        self.auto_check.toggled.connect(lambda *_a: self._on_apply())
        self.blur_hide_check.toggled.connect(lambda *_a: self._on_apply())
        self.autostart_check.toggled.connect(lambda *_a: self._on_apply())
        self.uia_elevation_check.toggled.connect(lambda *_a: self._on_apply())

    # ---------- 子控件工厂 ----------
    def _section_title(self, text):
        lb = QLabel(text)
        lb.setObjectName("SectionTitle")
        return lb

    def _card(self):
        card = QFrame()
        card.setObjectName("Card")
        return card

    def _add_row(self, layout, label, widget_or_layout, test_feature=False):
        """向卡片中添加一行设置项；用 QWidget 容器保证最小行高，防止被压扁。

        test_feature=True 时，该行会被登记到测试功能分组，在 use_test_features
        关闭时自动隐藏（新功能默认归此分组）。
        """
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
        container.setObjectName("SettingsRow")
        container.setLayout(row)
        container.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        container.setMinimumHeight(36)
        container.setAutoFillBackground(False)
        container.setAttribute(Qt.WA_StyledBackground, False)
        layout.addWidget(container)
        if test_feature:
            self._test_widgets.append(container)
        return container

    def _theme_combo(self):
        self.theme_combo = FluentComboBox(self._theme)
        self.theme_combo.addItem("浅色", "light")
        self.theme_combo.addItem("深色", "dark")
        self.theme_combo.setMinimumWidth(120)
        return self.theme_combo

    def _panel_alpha_row(self):
        row = QHBoxLayout()
        self.panel_alpha_slider = QSlider(Qt.Horizontal)
        self.panel_alpha_slider.setRange(50, 100)
        self.panel_alpha_slider.setTickInterval(5)
        self.panel_alpha_value = QLabel("")
        self.panel_alpha_value.setFixedWidth(44)
        self.panel_alpha_value.setObjectName("BodyText")
        self.panel_alpha_slider.valueChanged.connect(
            lambda v: self.panel_alpha_value.setText("%d%%" % v)
        )
        row.addWidget(self.panel_alpha_slider, 1)
        row.addWidget(self.panel_alpha_value)
        return row

    def _opacity_row(self):
        row = QHBoxLayout()
        self.opacity_slider = QSlider(Qt.Horizontal)
        self.opacity_slider.setRange(50, 100)
        self.opacity_slider.setTickInterval(5)
        self.opacity_value = QLabel("")
        self.opacity_value.setFixedWidth(44)
        self.opacity_value.setObjectName("BodyText")
        self.opacity_slider.valueChanged.connect(self._on_opacity_input)
        row.addWidget(self.opacity_slider, 1)
        row.addWidget(self.opacity_value)
        return row

    def _acrylic_toggle(self):
        self.acrylic_check = ToggleSwitch(self._theme)
        return self.acrylic_check

    def _method_combo(self):
        self.method_combo = FluentComboBox(self._theme)
        self.method_combo.addItem("剪贴板粘贴", "clipboard")
        self.method_combo.addItem("直接字符投递", "direct")
        self.method_combo.addItem("模拟键入", "type")
        self.method_combo.setMinimumWidth(140)
        self.method_combo.setToolTip(
            "直接字符投递（默认）：WM_CHAR 直送焦点控件，不被中文输入法拦截；"
            "剪贴板粘贴 / 模拟键入为测试功能，仅在「测试模式」下可选"
        )
        return self.method_combo

    def _recent_spin(self):
        self.recent_spin = QSpinBox()
        self.recent_spin.setRange(5, 100)
        return self.recent_spin

    def _page_spin(self):
        self.page_spin = QSpinBox()
        self.page_spin.setRange(1, 9)
        return self.page_spin

    def _auto_toggle(self):
        self.auto_check = ToggleSwitch(self._theme)
        return self.auto_check

    def _blur_hide_toggle(self):
        self.blur_hide_check = ToggleSwitch(self._theme)
        self.blur_hide_check.setToolTip(
            "候选条在窗口失焦或焦点离开输入框时自动收起；关闭则常驻直到手动关闭"
        )
        return self.blur_hide_check

    def _autostart_toggle(self):
        self.autostart_check = ToggleSwitch(self._theme)
        if not autostart.is_supported():
            self.autostart_check.setEnabled(False)
            self.autostart_check.setToolTip("仅打包后的 exe 版本支持")
        return self.autostart_check

    def _uia_elevation_toggle(self):
        self.uia_elevation_check = ToggleSwitch(self._theme)
        self.uia_elevation_check.setToolTip(
            "开启后尝试为进程启用 uiAccess，使候选栏成为顶级窗口、能与屏幕键盘互相覆盖；"
            "需本程序也以管理员身份运行才生效，否则无效果"
        )
        # 管理员权限检查：未通过则禁用开关（满足“检查通过才能使用”的约定），
        # 避免用户在无权提权的进程上白白开启却毫无反应。
        if not uia_elevation.is_user_admin():
            self.uia_elevation_check.setEnabled(False)
            self.uia_elevation_check.setToolTip(
                "需要以管理员身份运行本程序，UIA 提权才能使用（当前非管理员）。")
        return self.uia_elevation_check

    def _refresh_uia_status(self):
        """根据真实状态刷新 UIA 行下方的状态文案，杜绝“配置开着但没生效”的假象。"""
        if not uia_elevation.is_user_admin():
            self.uia_status.setText("⚠ 需以管理员身份运行本程序，UIA 提权才会生效。")
            self.uia_status.setStyleSheet("color:#c0392b;")
            return
        if not self.config.get("use_uia_elevation", False):
            self.uia_status.setText("")
            self.uia_status.setStyleSheet("")
            return
        if uia_elevation.has_uiaccess():
            self.uia_status.setText("✓ 已生效：候选栏已成为 UIAccess 顶级窗口，可与屏幕键盘互相覆盖。")
            self.uia_status.setStyleSheet("color:#107c10;")
        else:
            self.uia_status.setText("⚠ 已开启但本次未生效，请以管理员身份重启本程序。")
            self.uia_status.setStyleSheet("color:#c0392b;")

    # ---------- 读取/写入 ----------
    def set_theme(self, theme_obj):
        """主题切换时同步开关/下拉框配色，并强制刷新样式表。"""
        self._theme = theme_obj
        for t in (self.acrylic_check, self.auto_check, self.autostart_check,
                  self.blur_hide_check, self.uia_elevation_check):
            t.update_theme(theme_obj)
        for c in (self.theme_combo, self.method_combo):
            if c is not None:
                c.set_theme(theme_obj)
        # 强制 Qt 重新评估该分支的样式，解决部分控件换主题后未刷新外观的问题
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()

    def refresh_from_config(self):
        cfg = self.config
        # 程序化加载：先把各控件值刷回去，但屏蔽由此引发的预览信号（见 _loading 注释）。
        # 用 try/finally 确保即使某行抛错也恢复正常，避免后续用户交互的预览被永久屏蔽。
        self._loading = True
        try:
            self._captured = None
            self._update_hotkey_label(cfg.get("hotkey", "<ctrl>+<shift>+k"))
            theme = cfg.get("theme", "light")
            idx = self.theme_combo.findData(theme)
            self.theme_combo.setCurrentIndex(idx if idx >= 0 else 0)
            pa = int(float(cfg.get("panel_alpha", 0.92)) * 100)
            self.panel_alpha_slider.setValue(pa)
            self.panel_alpha_value.setText("%d%%" % pa)
            op = int(float(cfg.get("opacity", 0.98)) * 100)
            self.opacity_slider.setValue(op)
            self.opacity_value.setText("%d%%" % op)
            self.acrylic_check.setChecked(bool(cfg.get("acrylic", True)))
            method = cfg.get("input_method", "direct")
            midx = self.method_combo.findData(method)
            self.method_combo.setCurrentIndex(midx if midx >= 0 else 0)
            self.recent_spin.setValue(int(cfg.get("max_recent", 30)))
            self.auto_check.setChecked(bool(cfg.get("auto_popup", True)))
            self.blur_hide_check.setChecked(bool(cfg.get("auto_hide_on_blur", True)))
            self.page_spin.setValue(int(cfg.get("page_size", 3)))
            self.autostart_check.setChecked(autostart.is_enabled())
            self.uia_elevation_check.setChecked(bool(cfg.get("use_uia_elevation", False)))
        finally:
            self._loading = False
        # 测试功能分组：根据 use_test_features 决定高级分区(UIA 提权等)是否可见
        self.apply_test_feature_visibility()
        # 刷新 UIA 真实状态文案（避免“配置开着但未生效”的假象）
        self._refresh_uia_status()

    def apply_test_feature_visibility(self):
        """按 use_test_features 显隐测试功能分组。

        正式环境默认关闭，处于测试模式的新功能不显示；开发者模式里开启
        「测试功能」后才会显示并可用。新功能默认归入此分组，确认上线后
        再从此处移出并改为无条件显示。
        """
        visible = bool(self.config.get("use_test_features", False))
        for w in self._test_widgets:
            if w is not None:
                w.setVisible(visible)
        if self._test_section_title is not None:
            self._test_section_title.setVisible(visible)
        # 输入方式里属于测试功能的选项（剪贴板粘贴/模拟键入）随测试模式同步禁用/启用
        self._apply_input_method_availability()

    def _apply_input_method_availability(self):
        """测试模式关闭时，禁用「输入方式」里属于测试功能的选项（剪贴板粘贴/模拟键入），
        仅保留默认且始终可用的「直接字符投递」；开启测试模式时全部可选。

        这样正常情况下其他输入模式不可选择，符合「默认=直接字符输入」的产品定位。
        若当前选中了被禁用的项（例如先开测试模式选了剪贴板、再关测试模式），
        自动回退到默认 direct，使显示与「保存时归位」后的配置保持一致。
        """
        test = bool(self.config.get("use_test_features", False))
        gated = {"clipboard", "type"}
        model = self.method_combo.model()
        for i in range(self.method_combo.count()):
            data = self.method_combo.itemData(i)
            if data in gated:
                item = model.item(i) if model is not None else None
                if item is not None:
                    item.setEnabled(test)
        if not test and self.method_combo.currentData() in gated:
            idx = self.method_combo.findData("direct")
            if idx >= 0:
                # 临时屏蔽 _on_apply 信号，避免强制改选中项时触发隐式应用/保存
                was_loading = self._loading
                self._loading = True
                try:
                    self.method_combo.setCurrentIndex(idx)
                finally:
                    self._loading = was_loading

    def _update_hotkey_label(self, hotkey_str=None):
        if self._captured is not None:
            self.hotkey_label.setText(win_utils.label_from_parsed(self._captured))
            self.hotkey_btn.setText("重新录制")
            return
        if hotkey_str:
            try:
                self.hotkey_label.setText(win_utils.hotkey_label(hotkey_str))
            except Exception:
                self.hotkey_label.setText(hotkey_str)
        self.hotkey_btn.setText("录制…")

    # ---------- 热键录制 ----------
    def _start_capture(self):
        if self._capturing:
            return
        self._capturing = True
        self._cap_mods = set()
        self._cap_keys = []
        self._captured = None
        self.hotkey_btn.setText("录制中…")
        self.hotkey_btn.setDisabled(True)
        self.finish_btn.show()
        self.finish_btn.setFocus()
        self.hotkey_label.setText("依次按下想要的按键…")
        try:
            from pynput import keyboard as pynput_kb
        except ImportError:
            self.hotkey_label.setText("未安装 pynput，无法录制")
            self.finish_btn.hide()
            self.hotkey_btn.setDisabled(False)
            self.hotkey_btn.setText("录制…")
            self._capturing = False
            return
        self._pynput = pynput_kb
        self._listener = pynput_kb.Listener(
            on_press=self._on_cap_press, on_release=self._on_cap_release
        )
        self._listener.start()

    def _stop_capture(self):
        self._capturing = False
        if self._listener is not None:
            try:
                self._listener.stop()
            except Exception:
                pass
            self._listener = None
        self.finish_btn.hide()
        self.hotkey_btn.setDisabled(False)
        self.hotkey_btn.setText("录制…")

    def _update_capture_preview(self):
        parts = []
        for vk, char in self._cap_keys:
            parts.append(char if char else win_utils._vk_label(vk))
        self.hotkey_label.setText("已录制: " + " + ".join(parts) + "  （点「完成」确认）")

    def _finish_capture(self):
        if not self._capturing:
            return
        self._stop_capture()
        if len(self._cap_keys) >= 2:
            self._captured = {
                "type": "sequence",
                "keys": [v for v, _ in self._cap_keys],
            }
        elif len(self._cap_keys) == 1 and self._cap_mods:
            vk = self._cap_keys[0][0]
            m = 0
            if MOD_CONTROL in self._cap_mods:
                m |= MOD_CONTROL
            if MOD_ALT in self._cap_mods:
                m |= MOD_ALT
            if MOD_SHIFT in self._cap_mods:
                m |= MOD_SHIFT
            if MOD_WIN in self._cap_mods:
                m |= MOD_WIN
            self._captured = {"type": "simple", "mods": m, "vk": vk}
        else:
            self.hotkey_label.setText("至少需 2 个按键，或加修饰键；已保留原设置")
            self._update_hotkey_label()
            return
        self._update_hotkey_label()
        # 热键录制完成后自动保存
        self._on_apply()

    def _on_cap_press(self, key):
        if not self._capturing:
            return
        kb = self._pynput
        if key == kb.Key.esc:
            self._stop_capture()
            self._update_hotkey_label(self.config.get("hotkey"))
            return
        if key in (kb.Key.ctrl, kb.Key.ctrl_l, kb.Key.ctrl_r):
            self._cap_mods.add(MOD_CONTROL)
            return
        if key in (kb.Key.shift, kb.Key.shift_l, kb.Key.shift_r):
            self._cap_mods.add(MOD_SHIFT)
            return
        if key in (kb.Key.alt, kb.Key.alt_l, kb.Key.alt_r):
            self._cap_mods.add(MOD_ALT)
            return
        if key in (kb.Key.cmd, kb.Key.cmd_l, kb.Key.cmd_r):
            self._cap_mods.add(MOD_WIN)
            return
        mods, vk, char = _build_hotkey_from_pynput(key, self._cap_mods)
        if vk:
            self._cap_keys.append((vk, char))
            self._update_capture_preview()

    def _on_cap_release(self, key):
        pass

    # ---------- 应用 ----------
    def _on_opacity_input(self, value):
        """候选条不透明度滑块拖动中：实时更新标签与候选条预览（不落盘）。

        程序化加载（refresh_from_config 的 setValue）期间 _loading 为 True，此时仅让
        label 随值刷新，不向外发预览信号，避免与 _on_apply 的自保存逻辑互相干扰。
        """
        if getattr(self, "_loading", False):
            return
        self.opacity_value.setText("%d%%" % value)
        self.opacity_preview.emit(value / 100.0)

    def _on_apply(self):
        if getattr(self, "_loading", False):
            return
        new_cfg = dict(self.config)
        if self._captured is not None:
            new_cfg["hotkey"] = win_utils.format_hotkey(self._captured)
            self._captured = None
        new_cfg["theme"] = self.theme_combo.currentData()
        new_cfg["panel_alpha"] = self.panel_alpha_slider.value() / 100.0
        new_cfg["opacity"] = self.opacity_slider.value() / 100.0
        new_cfg["acrylic"] = self.acrylic_check.isChecked()
        new_cfg["input_method"] = self.method_combo.currentData()
        new_cfg["max_recent"] = self.recent_spin.value()
        new_cfg["auto_popup"] = self.auto_check.isChecked()
        new_cfg["auto_hide_on_blur"] = self.blur_hide_check.isChecked()
        new_cfg["page_size"] = self.page_spin.value()
        new_cfg["autostart"] = self.autostart_check.isChecked()
        new_cfg["use_uia_elevation"] = self.uia_elevation_check.isChecked()
        self.config = new_cfg
        self.config_applied.emit(new_cfg)

    def showEvent(self, e):
        # 每次打开设置页都重新评估 UIA 真实状态（例如以管理员重启后刚生效）
        self._refresh_uia_status()
        super().showEvent(e)

    def hideEvent(self, e):
        self._stop_capture()
        super().hideEvent(e)
