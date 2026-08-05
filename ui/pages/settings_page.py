"""设置页：热键、主题、输入方式等配置表单（Win11 Settings 风格）。"""
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QComboBox,
    QSlider, QCheckBox, QSpinBox, QFrame, QSizePolicy,
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont

from core import win_utils
from core import autostart
from core.win_utils import MOD_CONTROL, MOD_ALT, MOD_SHIFT, MOD_WIN


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
    config_applied = Signal(dict)

    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.config = dict(config)
        self._captured = None
        self._capturing = False
        self._cap_mods = set()
        self._cap_keys = []
        self._listener = None
        self._pynput = None
        self._init_ui()
        self.refresh_from_config()

    def _init_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(36, 28, 36, 28)
        root.setSpacing(22)

        title = QLabel("设置")
        title.setObjectName("PageTitle")
        root.addWidget(title)

        # 全局热键
        root.addWidget(self._section_title("全局热键"))
        card = self._card()
        croot = QVBoxLayout(card)
        croot.setContentsMargins(16, 14, 16, 14)
        croot.setSpacing(10)
        hk_row = QHBoxLayout()
        hk_row.setContentsMargins(0, 8, 0, 8)
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
        hk_container.setMinimumHeight(34)
        croot.addWidget(hk_container)
        tip = QLabel("点「录制…」后按修饰键+单键（如 Ctrl+Shift+K）或多键序列（如 k+l）。录完点「完成」；Esc 取消。")
        tip.setObjectName("Caption")
        tip.setWordWrap(True)
        croot.addWidget(tip)
        root.addWidget(card)

        # 外观
        root.addWidget(self._section_title("外观"))
        card = self._card()
        croot = QVBoxLayout(card)
        croot.setContentsMargins(16, 14, 16, 14)
        croot.setSpacing(14)
        self._add_row(croot, "主题", self._theme_combo())
        self._add_row(croot, "不透明度", self._opacity_row())
        self._add_row(croot, "亚克力模糊", self._acrylic_check())
        root.addWidget(card)

        # 输入
        root.addWidget(self._section_title("输入"))
        card = self._card()
        croot = QVBoxLayout(card)
        croot.setContentsMargins(16, 14, 16, 14)
        croot.setSpacing(14)
        self._add_row(croot, "输入方式", self._method_combo())
        self._add_row(croot, "最大最近记录", self._recent_spin())
        self._add_row(croot, "每页候选数", self._page_spin())
        auto_row = QHBoxLayout()
        auto_row.setContentsMargins(0, 8, 0, 8)
        auto_row.setSpacing(12)
        auto_row.setAlignment(Qt.AlignVCenter)
        self.auto_check = QCheckBox("打字时自动弹出（识别情绪推荐颜文字）")
        auto_row.addWidget(self.auto_check)
        auto_row.addStretch(1)
        auto_container = QWidget()
        auto_container.setLayout(auto_row)
        auto_container.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        auto_container.setMinimumHeight(34)
        croot.addWidget(auto_container)
        root.addWidget(card)

        # 系统
        root.addWidget(self._section_title("系统"))
        card = self._card()
        croot = QVBoxLayout(card)
        croot.setContentsMargins(16, 14, 16, 14)
        croot.setSpacing(14)
        auto_row = QHBoxLayout()
        auto_row.setContentsMargins(0, 8, 0, 8)
        auto_row.setSpacing(12)
        auto_row.setAlignment(Qt.AlignVCenter)
        self.autostart_check = QCheckBox("开机自动启动")
        if not autostart.is_supported():
            self.autostart_check.setEnabled(False)
            self.autostart_check.setToolTip("仅打包后的 exe 版本支持")
        auto_row.addWidget(self.autostart_check)
        auto_row.addStretch(1)
        auto_container = QWidget()
        auto_container.setLayout(auto_row)
        auto_container.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        auto_container.setMinimumHeight(34)
        croot.addWidget(auto_container)
        root.addWidget(card)

        root.addStretch(1)

        # 底部应用按钮
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        ok = QPushButton("应用并保存")
        ok.setObjectName("AccentButton")
        ok.setDefault(True)
        ok.clicked.connect(self._on_apply)
        btn_row.addWidget(ok)
        root.addLayout(btn_row)

    # ---------- 子控件工厂 ----------
    def _section_title(self, text):
        lb = QLabel(text)
        lb.setObjectName("SectionTitle")
        return lb

    def _card(self):
        card = QFrame()
        card.setObjectName("Card")
        return card

    def _add_row(self, layout, label, widget_or_layout):
        """向卡片中添加一行设置项；用 QWidget 容器保证最小行高，防止被压扁。"""
        row = QHBoxLayout()
        row.setContentsMargins(0, 8, 0, 8)
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
        container.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        container.setMinimumHeight(34)
        layout.addWidget(container)

    def _theme_combo(self):
        self.theme_combo = QComboBox()
        self.theme_combo.addItem("浅色", "light")
        self.theme_combo.addItem("深色", "dark")
        self.theme_combo.setMinimumWidth(120)
        return self.theme_combo

    def _opacity_row(self):
        row = QHBoxLayout()
        self.opacity_slider = QSlider(Qt.Horizontal)
        self.opacity_slider.setRange(50, 100)
        self.opacity_slider.setTickInterval(5)
        self.opacity_value = QLabel("98%")
        self.opacity_value.setFixedWidth(44)
        self.opacity_value.setObjectName("BodyText")
        self.opacity_slider.valueChanged.connect(
            lambda v: self.opacity_value.setText("%d%%" % v)
        )
        row.addWidget(self.opacity_slider, 1)
        row.addWidget(self.opacity_value)
        return row

    def _acrylic_check(self):
        self.acrylic_check = QCheckBox()
        return self.acrylic_check

    def _method_combo(self):
        self.method_combo = QComboBox()
        self.method_combo.addItem("剪贴板粘贴", "clipboard")
        self.method_combo.addItem("直接字符投递", "direct")
        self.method_combo.addItem("模拟键入", "type")
        self.method_combo.setMinimumWidth(140)
        self.method_combo.setToolTip(
            "剪贴板粘贴（默认，推荐）：发 Ctrl+V，不被中文输入法拦截；"
            "直接字符投递：WM_CHAR 直送焦点控件；"
            "模拟键入在中文输入法下可能产生乱码"
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

    # ---------- 读取/写入 ----------
    def refresh_from_config(self):
        cfg = self.config
        self._captured = None
        self._update_hotkey_label(cfg.get("hotkey", "<ctrl>+<shift>+k"))
        theme = cfg.get("theme", "light")
        idx = self.theme_combo.findData(theme)
        self.theme_combo.setCurrentIndex(idx if idx >= 0 else 0)
        op = int(float(cfg.get("opacity", 0.98)) * 100)
        self.opacity_slider.setValue(op)
        self.acrylic_check.setChecked(bool(cfg.get("acrylic", True)))
        method = cfg.get("input_method", "clipboard")
        midx = self.method_combo.findData(method)
        self.method_combo.setCurrentIndex(midx if midx >= 0 else 0)
        self.recent_spin.setValue(int(cfg.get("max_recent", 30)))
        self.auto_check.setChecked(bool(cfg.get("auto_popup", True)))
        self.page_spin.setValue(int(cfg.get("page_size", 3)))
        self.autostart_check.setChecked(autostart.is_enabled())

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
    def _on_apply(self):
        new_cfg = dict(self.config)
        if self._captured is not None:
            new_cfg["hotkey"] = win_utils.format_hotkey(self._captured)
        new_cfg["theme"] = self.theme_combo.currentData()
        new_cfg["opacity"] = self.opacity_slider.value() / 100.0
        new_cfg["acrylic"] = self.acrylic_check.isChecked()
        new_cfg["input_method"] = self.method_combo.currentData()
        new_cfg["max_recent"] = self.recent_spin.value()
        new_cfg["auto_popup"] = self.auto_check.isChecked()
        new_cfg["page_size"] = self.page_spin.value()
        new_cfg["autostart"] = self.autostart_check.isChecked()
        self.config = new_cfg
        self.config_applied.emit(new_cfg)

    def hideEvent(self, e):
        self._stop_capture()
        super().hideEvent(e)
