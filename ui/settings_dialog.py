"""可视化配置面板：改热键、改主题等都不用手编 JSON。

热键录制用 pynput 做一次性的临时监听（仅录制时挂起，结束后立即移除），
因此不会像常驻钩子那样造成系统卡顿。
"""
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QComboBox,
    QSlider, QCheckBox, QSpinBox, QFrame, QSizePolicy,
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont

from core import win_utils
from core import autostart
from core.win_utils import MOD_CONTROL, MOD_ALT, MOD_SHIFT, MOD_WIN

# pynput 仅在「热键录制」时按需导入，缺失该依赖只禁用录制、不拖垮整个程序
_pynput_keyboard = None


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


class SettingsDialog(QDialog):
    config_applied = Signal(dict)

    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.config = dict(config)
        self._captured = None          # dict: {type:'simple'|'sequence', ...}
        self._capturing = False
        self._cap_mods = set()
        self._cap_keys = []            # [(vk, char), ...] 录制到的普通键序列
        self._listener = None
        self._pynput = None
        self.setWindowTitle("设置 · 颜文字输入辅助器")
        self.setMinimumWidth(420)
        self._init_ui()
        self.refresh_from_config()

    # ---------- UI ----------
    def _init_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(14)
        title = QLabel("设置")
        title.setFont(QFont("Segoe UI", 16, QFont.Weight.Bold))
        root.addWidget(title)

        # 热键
        root.addWidget(self._section_label("全局热键"))
        hk_row = QHBoxLayout()
        self.hotkey_btn = QPushButton("录制…")
        self.hotkey_btn.setMinimumWidth(110)
        self.hotkey_btn.clicked.connect(self._start_capture)
        self.finish_btn = QPushButton("完成")
        self.finish_btn.setMinimumWidth(80)
        self.finish_btn.hide()
        self.finish_btn.clicked.connect(self._finish_capture)
        self.hotkey_label = QLabel("")
        self.hotkey_label.setStyleSheet("color:#5b5b5b;font-size:13px;")
        hk_row.addWidget(self.hotkey_btn)
        hk_row.addWidget(self.finish_btn)
        hk_row.addWidget(self.hotkey_label, 1)
        root.addLayout(hk_row)
        tip = QLabel("点「录制…」后：按 修饰键+单键（如 Ctrl+Shift+K，零卡顿）"
                     "或 多键序列（如 k+l、-+=）。录完点「完成」；Esc 取消。\n"
                     "注意：多键序列会挂一个轻量按键监听，普通输入时若恰好连按到该序列也可能触发。")
        tip.setStyleSheet("color:#8a8a8a;font-size:11px;")
        tip.setWordWrap(True)
        root.addWidget(tip)

        root.addWidget(self._divider())

        # 外观
        root.addWidget(self._section_label("外观"))
        theme_row = QHBoxLayout()
        theme_row.addWidget(QLabel("主题"))
        theme_row.addStretch(1)
        self.theme_combo = QComboBox()
        self.theme_combo.addItem("浅色", "light")
        self.theme_combo.addItem("深色", "dark")
        self.theme_combo.setMinimumWidth(120)
        theme_row.addWidget(self.theme_combo)
        root.addLayout(theme_row)

        op_row = QHBoxLayout()
        op_row.addWidget(QLabel("不透明度"))
        self.opacity_slider = QSlider(Qt.Horizontal)
        self.opacity_slider.setRange(50, 100)
        self.opacity_slider.setTickInterval(5)
        self.opacity_value = QLabel("98%")
        self.opacity_value.setFixedWidth(44)
        self.opacity_slider.valueChanged.connect(
            lambda v: self.opacity_value.setText("%d%%" % v)
        )
        op_row.addWidget(self.opacity_slider, 1)
        op_row.addWidget(self.opacity_value)
        root.addLayout(op_row)

        acrylic_row = QHBoxLayout()
        acrylic_row.addWidget(QLabel("亚克力模糊（Win11 材质）"))
        acrylic_row.addStretch(1)
        self.acrylic_check = QCheckBox()
        acrylic_row.addWidget(self.acrylic_check)
        root.addLayout(acrylic_row)

        root.addWidget(self._divider())

        # 输入
        root.addWidget(self._section_label("输入"))
        method_row = QHBoxLayout()
        method_row.addWidget(QLabel("输入方式"))
        method_row.addStretch(1)
        self.method_combo = QComboBox()
        self.method_combo.addItem("剪贴板粘贴", "clipboard")
        self.method_combo.addItem("直接字符投递", "direct")
        self.method_combo.addItem("模拟键入", "type")
        self.method_combo.setMinimumWidth(140)
        self.method_combo.setToolTip(
            "剪贴板粘贴（默认，推荐）：发 Ctrl+V，不被中文输入法拦截，兼容性最好；"
            "直接字符投递：WM_CHAR 直送焦点控件，绕过输入法、无乱码、不污染剪贴板；"
            "模拟键入在微软拼音等中文输入法下可能产生乱码"
        )
        method_row.addWidget(self.method_combo)
        root.addLayout(method_row)

        recent_row = QHBoxLayout()
        recent_row.addWidget(QLabel("最大最近记录"))
        recent_row.addStretch(1)
        self.recent_spin = QSpinBox()
        self.recent_spin.setRange(5, 100)
        recent_row.addWidget(self.recent_spin)
        root.addLayout(recent_row)

        auto_row = QHBoxLayout()
        self.auto_check = QCheckBox("打字时自动弹出（识别情绪推荐颜文字）")
        auto_row.addWidget(self.auto_check)
        root.addLayout(auto_row)

        page_row = QHBoxLayout()
        page_row.addWidget(QLabel("每页候选数"))
        page_row.addStretch(1)
        self.page_spin = QSpinBox()
        self.page_spin.setRange(1, 9)
        page_row.addWidget(self.page_spin)
        root.addLayout(page_row)

        root.addWidget(self._divider())

        # 系统
        root.addWidget(self._section_label("系统"))
        autostart_row = QHBoxLayout()
        self.autostart_check = QCheckBox("开机自动启动")
        if not autostart.is_supported():
            self.autostart_check.setEnabled(False)
            self.autostart_check.setToolTip("仅打包后的 exe 版本支持")
        autostart_row.addWidget(self.autostart_check)
        root.addLayout(autostart_row)

        root.addStretch(1)

        # 底部按钮
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        cancel = QPushButton("取消")
        cancel.clicked.connect(self.reject)
        ok = QPushButton("应用并保存")
        ok.setDefault(True)
        ok.clicked.connect(self._on_apply)
        btn_row.addWidget(cancel)
        btn_row.addWidget(ok)
        root.addLayout(btn_row)

    def _section_label(self, text):
        lb = QLabel(text)
        lb.setStyleSheet("font-weight:600;font-size:13px;color:#0a84ff;")
        return lb

    def _divider(self):
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setFrameShadow(QFrame.Sunken)
        line.setStyleSheet("color:rgba(0,0,0,0.08);")
        return line

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
            self.hotkey_label.setText("未安装 pynput，无法录制（可手动编辑 config.json 改热键）")
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
            # 既不是多键序列，也不是“修饰键+单键” -> 无效，保留原设置
            self.hotkey_label.setText("至少需 2 个按键，或加修饰键；已保留原设置")
            self._update_hotkey_label()
            return
        self._update_hotkey_label()

    def _on_cap_press(self, key):
        if not self._capturing:
            return
        kb = self._pynput
        if key == kb.Key.esc:
            # 取消录制
            self._stop_capture()
            self._update_hotkey_label(self.config.get("hotkey"))
            return
        # 修饰键只记录为“修饰”，不计入序列
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
        # 普通键：追加到序列
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
        self.accept()

    def closeEvent(self, e):
        self._stop_capture()
        super().closeEvent(e)
