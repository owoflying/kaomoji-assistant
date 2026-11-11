"""设置页：热键、主题、输入方式等配置表单（Win11 Settings 风格，可滚动）。

内容包裹在 QScrollArea 内：当窗口较短、内容高于视口时不会被压扁，
而是出现滚动条，卡片/行保持自然高度（修复此前“被压成细线”的显示异常）。
布尔项改用 WinUI 3 风格 ToggleSwitch，更贴近系统设置观感。
"""
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QSlider, QSpinBox, QFrame, QSizePolicy, QScrollArea,
)
from PySide6.QtCore import Qt, QTimer, Signal

from core import autostart, uia_elevation, win_utils
from core.win_utils import MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN
from ui.fluent_combobox import FluentComboBox
from ui.pages._scaffold import card as make_card
from ui.pages._scaffold import page_layout, page_title, section_title
from ui.toggle_switch import ToggleSwitch
from ui.win11_theme import Theme, resolve_theme_name

# pynput 的 Key 枚举名 -> RegisterHotKey 的 fsModifiers 位。
# 左右修饰键（ctrl_l / ctrl_r …）与通用名（ctrl）必须一并收录：pynput 在 Windows
# 上实际投递的是带左右后缀的那个成员。
_MOD_KEY_BITS = (
    ("ctrl", MOD_CONTROL), ("ctrl_l", MOD_CONTROL), ("ctrl_r", MOD_CONTROL),
    ("shift", MOD_SHIFT), ("shift_l", MOD_SHIFT), ("shift_r", MOD_SHIFT),
    ("alt", MOD_ALT), ("alt_l", MOD_ALT), ("alt_r", MOD_ALT),
    ("cmd", MOD_WIN), ("cmd_l", MOD_WIN), ("cmd_r", MOD_WIN),
)


def _mod_bit_of(key, kb):
    """pynput 按键对象若是修饰键，返回对应的 fsModifiers 位；否则 None。"""
    for attr, bit in _MOD_KEY_BITS:
        if key == getattr(kb.Key, attr, None):
            return bit
    return None


def _roundtrips(vk):
    """该 VK 能否写进配置字符串、再被原样解析回来。

    `_key_token()` 对没有名字的键（PrintScreen、NumLock 等）会生成 `<key44>` 这种
    占位 token，而 `parse_hotkey()` 只认「有名字的键」和单个字符，`<key44>` 会被
    整段丢弃 —— 于是热键配置变成一条永远解析失败的死字符串，用户只看到「录进去了
    但按了没反应」，重启后照样没反应。不如录制当场就拒绝。
    """
    token = win_utils._key_token(vk)
    return win_utils.parse_hotkey(token).get("type") != "none"


def _resolve_capture_key(key):
    """把 pynput 按键对象解析成 (vk, 展示标签)；无法识别时返回 (None, "")。

    展示标签一律由 VK 反推，**不直接用 `key.char`**：
      * 按住 Ctrl 时 pynput 给出的 char 是控制字符（Ctrl+K -> '\\x0b'），塞进
        QLabel 就是一段看不见的内容——用户看到的现象正是「按了键但不显示」；
      * 小写字母在标签里也不好看，VK 反推能稳定得到大写。
    """
    vk = getattr(key, "vk", None)
    char = getattr(key, "char", None)
    if char is not None and (len(char) != 1 or not char.isprintable()):
        char = None                  # 控制字符：既不可显示，也不能用来反查 VK
    if not vk and char:
        vk = win_utils._vk_from_char(char)
    if not vk or not _roundtrips(vk):
        return None, ""
    return vk, win_utils._vk_label(vk)


class SettingsPage(QWidget):
    config_applied = Signal(dict)    # 任何设置改动后自动保存/应用
    opacity_preview = Signal(float)  # 候选条不透明度实时预览（拖动中），仅作视觉反馈，不落盘
    # True = 进入热键录制（宿主应临时停用全局热键，否则刚录的组合键会当场把候选条弹出来）
    # False = 录制结束（宿主按当前配置恢复）
    hotkey_capture_changed = Signal(bool)
    # pynput 监听线程 -> GUI 线程的事件桥（"mod" / "key" / "esc" + 载荷）
    capture_event = Signal(str, object)

    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.config = dict(config)
        self._captured = None
        self._capturing = False
        self._cap_mods = set()
        self._cap_keys = []              # [(vk, 展示标签), ...]
        self._listener = None
        self._pynput = None
        # pynput 的回调跑在它自己的监听线程里，直接 setText 是未定义行为（重绘丢失、
        # 偶发崩溃）。所有录制事件先过信号，由 Qt 排队回 GUI 线程再动控件。
        self.capture_event.connect(self._handle_capture_event)
        # 程序化加载（构造 / apply_config）期间屏蔽自动保存：setValue/setCurrentIndex/setChecked
        # 会触发 currentIndexChanged/toggled/valueChanged -> _on_apply -> config_applied，
        # 导致把 self.config 换成中间副本、切断与关于页共享的字典引用，造成
        # “开发者模式”等状态在不同页之间读不一致。用户交互时此标志为 False，自动保存照常触发。
        self._loading = False
        # 测试功能分组：处于测试模式的设置项在 use_test_features 关闭时整体隐藏。
        # 新功能默认归入此分组，确认上线后才移出。
        self._test_widgets = []
        self._test_section_title = None
        self._theme = Theme(resolve_theme_name(config.get("theme", "light")))
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
        v = page_layout(body)

        v.addWidget(page_title("设置"))

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
        tip = QLabel("点「录制」后按组合键（如 Ctrl+Shift+K），也可以连着按几个键做序列（如 k+l）。")
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

        # 情绪推荐
        v.addWidget(self._section_title("情绪推荐"))
        card = self._card()
        croot = QVBoxLayout(card)
        croot.setContentsMargins(16, 14, 16, 14)
        croot.setSpacing(12)
        self._add_row(croot, "按情绪推荐颜文字", self._emotion_toggle())
        tip = QLabel("打出「好累」「破防了」这类话时，自动推荐对应表情。"
                     "它会在你选择时学习你的表达习惯，越用越准。")
        tip.setObjectName("Caption")
        tip.setWordWrap(True)
        croot.addWidget(tip)
        self._add_row(croot, "推荐灵敏度", self._emotion_sensitivity())
        sens_tip = QLabel("调低更保守（少打扰），调高更容易触发推荐。")
        sens_tip.setObjectName("Caption")
        sens_tip.setWordWrap(True)
        croot.addWidget(sens_tip)
        self._add_row(croot, "学习我的用词习惯", self._emotion_learn_toggle())
        self._emotion_stats = QLabel("")
        self._emotion_stats.setObjectName("Caption")
        self._emotion_stats.setWordWrap(True)
        croot.addWidget(self._emotion_stats)
        self._add_row(croot, "", self._emotion_reset_row())
        v.addWidget(card)

        # 系统
        v.addWidget(self._section_title("系统"))
        card = self._card()
        croot = QVBoxLayout(card)
        croot.setContentsMargins(16, 14, 16, 14)
        croot.setSpacing(12)
        self._add_row(croot, "开机自动启动", self._autostart_toggle())
        v.addWidget(card)

        # 高级（**正式功能**：UIA 提权已毕业，不再受 use_test_features 门控。
        # 首次使用时会由欢迎页给出风险说明并要求勾选确认，见 ui/welcome_dialog.py）
        adv_title = self._section_title("高级")
        v.addWidget(adv_title)
        card = self._card()
        croot = QVBoxLayout(card)
        croot.setContentsMargins(16, 14, 16, 14)
        croot.setSpacing(12)
        self._add_row(croot, "使用 UIA 提权", self._uia_elevation_toggle())
        tip = QLabel("开启后程序会尝试为自身进程启用 uiAccess，使候选栏成为 UIAccess 顶级窗口，"
                     "从而能与屏幕键盘(osk)等系统窗口处于同一 Z 序层级、互相覆盖。"
                     "开启时会弹窗申请管理员权限；默认关闭，不影响其他功能。")
        tip.setObjectName("Caption")
        tip.setWordWrap(True)
        croot.addWidget(tip)
        # 真实状态行：反映"实际是否已拿到 UIAccess"，避免"配置开着但未生效"的假象。
        self.uia_status = QLabel("")
        self.uia_status.setObjectName("Caption")
        self.uia_status.setWordWrap(True)
        croot.addWidget(self.uia_status)
        v.addWidget(card)

        v.addStretch(1)
        self.scroll.setWidget(body)

        # 所有设置项改动后自动保存：所有控件（含上方各节所建）都已存在，此处统一接线。
        # 外观项不再走 preview（不落盘）机制；滑块拖动时实时更新标签与候选条预览，
        # 释放后再统一保存，避免高频写盘/重绘。
        self.theme_combo.currentIndexChanged.connect(lambda *_a: self._on_apply())
        self.panel_alpha_slider.sliderReleased.connect(lambda *_a: self._on_apply())
        self.opacity_slider.sliderReleased.connect(lambda *_a: self._on_apply())
        # 非外观项改动立即保存
        self.method_combo.currentIndexChanged.connect(lambda *_a: self._on_apply())
        self.recent_spin.valueChanged.connect(lambda *_a: self._on_apply())
        self.page_spin.valueChanged.connect(lambda *_a: self._on_apply())
        self.auto_check.toggled.connect(lambda *_a: self._on_apply())
        self.blur_hide_check.toggled.connect(lambda *_a: self._on_apply())
        self.autostart_check.toggled.connect(lambda *_a: self._on_apply())
        # 情绪推荐：灵敏度释放后才保存（拖动中只改标签，避免高频写盘）
        self.emotion_check.toggled.connect(self._on_emotion_toggled)
        self.emotion_sens_slider.sliderReleased.connect(
            lambda *_a: self._on_apply())
        self.emotion_learn_check.toggled.connect(lambda *_a: self._on_apply())
        # UIA 提权涉及 UAC 提权 + 计划任务，属于敏感操作：先二次确认再落配置。
        self.uia_elevation_check.toggled.connect(self._on_uia_toggled)

    def _on_emotion_toggled(self, checked):
        """情绪推荐开关：联动灵敏度控件的可用性，并立即保存。"""
        if getattr(self, "_loading", False):
            return
        for w in (getattr(self, "emotion_sens_slider", None),
                  getattr(self, "emotion_learn_check", None)):
            if w is not None:
                w.setEnabled(bool(checked))
        self._refresh_emotion_stats()
        self._on_apply()

    # ---------- 子控件工厂 ----------
    def _section_title(self, text):
        return section_title(text)

    def _card(self):
        return make_card()

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
        self.theme_combo.addItem("跟随系统", "system")
        self.theme_combo.setMinimumWidth(140)
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

    # ---------- 情绪推荐 ----------
    def _emotion_toggle(self):
        self.emotion_check = ToggleSwitch(self._theme)
        self.emotion_check.setToolTip(
            "关闭后打字不再按情绪自动推荐，热键唤起等其它功能不受影响")
        return self.emotion_check

    def _emotion_sensitivity(self):
        """灵敏度：映射到 conf 阈值。高灵敏度 = 阈值低 = 更容易触发。"""
        row = QHBoxLayout()
        self.emotion_sens_slider = QSlider(Qt.Horizontal)
        # 0..100 表示"灵敏度"，内部换算成阈值 0.65..0.25
        self.emotion_sens_slider.setRange(0, 100)
        self.emotion_sens_slider.setTickInterval(10)
        self.emotion_sens_slider.setToolTip(
            "往左更保守，往右更容易触发推荐")
        self.emotion_sens_value = QLabel("")
        self.emotion_sens_value.setFixedWidth(52)
        self.emotion_sens_value.setObjectName("BodyText")
        self.emotion_sens_slider.valueChanged.connect(self._on_sens_changed)
        row.addWidget(self.emotion_sens_slider, 1)
        row.addWidget(self.emotion_sens_value)
        return row

    @staticmethod
    def _sens_to_label(v):
        if v < 34:
            return "保守"
        if v < 67:
            return "适中"
        return "灵敏"

    def _on_sens_changed(self, v):
        self.emotion_sens_value.setText(self._sens_to_label(v))

    def _emotion_learn_toggle(self):
        self.emotion_learn_check = ToggleSwitch(self._theme)
        self.emotion_learn_check.setToolTip(
            "根据你实际选择的颜文字，逐渐贴合你的说话方式。"
            "关闭后不再学习，已学到的保持不变")
        return self.emotion_learn_check

    def _emotion_reset_row(self):
        from PySide6.QtWidgets import QPushButton

        b = QPushButton("清除学习记录")
        b.setCursor(Qt.PointingHandCursor)
        b.setToolTip("把个性化学习结果清空，恢复到出厂模型")
        b.clicked.connect(self._on_reset_emotion_learning)
        return b

    def _on_reset_emotion_learning(self):
        """清除个性化模型。不弹确认框——它是可再生的，重学即可。"""
        try:
            from core import emotion_classifier
            emotion_classifier.shared().reset_personal_model()
            self._refresh_emotion_stats("已清除学习记录，已恢复到出厂模型")
        except Exception as e:
            self._refresh_emotion_stats("清除失败：%r" % (e,))

    def _refresh_emotion_stats(self, note=""):
        """刷新「学习到多少」的展示。

        让用户看到"越用越顺手"是**有据可查**的，而不是一句宣传。
        """
        lbl = getattr(self, "_emotion_stats", None)
        if lbl is None:
            return
        if note:
            lbl.setText(note)
            return
        try:
            from core import emotion_classifier
            st = emotion_classifier.shared().learning_stats()
            if not st["model_ready"]:
                lbl.setText("情绪模型未加载，当前使用关键词匹配")
                return
            parts = ["出厂模型学过 %d 句" % st["base_samples"]]
            if st["has_personal_model"]:
                parts.append("已保存你的个性化学习结果")
            if st["personal_updates"]:
                parts.append("本次已学习 %d 次" % st["personal_updates"])
            lbl.setText("　·　".join(parts))
        except Exception:
            lbl.setText("")

    def _autostart_toggle(self):
        self.autostart_check = ToggleSwitch(self._theme)
        if not autostart.is_supported():
            self.autostart_check.setEnabled(False)
            self.autostart_check.setToolTip("仅打包后的 exe 版本支持")
        return self.autostart_check

    def _uia_elevation_toggle(self):
        self.uia_elevation_check = ToggleSwitch(self._theme)
        from core import elevate as _elevate

        # 源码态（python main.py）直接禁用：提权重启依赖 sys.executable
        # 指向本程序，而源码态它是 python.exe，runas 只会打开空解释器。
        # 这一步必须在"能不能弹 UAC"之前判断，否则用户开了开关也毫无反应。
        if not _elevate.is_supported():
            self.uia_elevation_check.setEnabled(False)
            self.uia_elevation_check.setChecked(False)
            self.uia_elevation_check.setToolTip(_elevate.unsupported_reason())
            return self.uia_elevation_check

        # 注意：**不因为"非管理员"而禁用开关**。
        # 打包态下开启时会弹 UAC 申请管理员权限（见 core/elevate.py），
        # 用户不必自己去"右键 → 以管理员身份运行"。
        if uia_elevation.is_user_admin():
            self.uia_elevation_check.setToolTip(
                "开启后尝试为进程启用 uiAccess，使候选栏成为顶级窗口、"
                "能与屏幕键盘互相覆盖。")
        else:
            self.uia_elevation_check.setToolTip(
                "开启后将弹窗申请管理员权限并重启本程序；\n"
                "若同时开启「开机自动启动」，会改用计划任务实现"
                "（登录时自动以管理员权限运行，不再弹 UAC）。")
        return self.uia_elevation_check

    def _refresh_uia_status(self):
        """根据真实状态刷新 UIA 行下方的状态文案，杜绝"配置开着但没生效"的假象。"""
        from core import elevate as _elevate

        # 源码态：明确说明不支持，而不是显示"需要管理员"
        if not _elevate.is_supported():
            self.uia_status.setText("⚠ " + _elevate.unsupported_reason())
            self.uia_status.setStyleSheet("color:#a06000;")
            return

        if not uia_elevation.is_user_admin():
            if self.config.get("use_uia_elevation", False):
                self.uia_status.setText(
                    "⚠ 当前非管理员：开启时会弹窗申请管理员权限并重启程序。")
            else:
                self.uia_status.setText(
                    "开启后将弹窗申请管理员权限（无需手动以管理员身份运行）。")
            self.uia_status.setStyleSheet("color:#a06000;")
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
        for t in (self.auto_check, self.autostart_check,
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
            method = cfg.get("input_method", "direct")
            midx = self.method_combo.findData(method)
            self.method_combo.setCurrentIndex(midx if midx >= 0 else 0)
            self.recent_spin.setValue(int(cfg.get("max_recent", 30)))
            self.auto_check.setChecked(bool(cfg.get("auto_popup", True)))
            self.blur_hide_check.setChecked(bool(cfg.get("auto_hide_on_blur", True)))
            # 情绪推荐
            _emo_on = bool(cfg.get("emotion_suggest", True))
            self.emotion_check.setChecked(_emo_on)
            _sens = int(cfg.get("emotion_sensitivity", 50))
            self.emotion_sens_slider.setValue(max(0, min(100, _sens)))
            self.emotion_sens_value.setText(self._sens_to_label(_sens))
            self.emotion_learn_check.setChecked(
                bool(cfg.get("emotion_learning", True)))
            self.emotion_sens_slider.setEnabled(_emo_on)
            self.emotion_learn_check.setEnabled(_emo_on)
            self._refresh_emotion_stats()
            self.page_spin.setValue(int(cfg.get("page_size", 3)))
            self.autostart_check.setChecked(autostart.is_enabled())
            # 源码态强制显示为关闭：开关本身已禁用，若还显示"开"会
            # 让用户以为功能生效了（实际配置值可能是打包态遗留的 true）。
            from core import elevate as _elevate
            _uia_wanted = bool(cfg.get("use_uia_elevation", False))
            if not _elevate.is_supported():
                _uia_wanted = False
            self.uia_elevation_check.setChecked(_uia_wanted)
        finally:
            self._loading = False
        # 测试功能分组：根据 use_test_features 决定高级分区(UIA 提权等)是否可见
        self.apply_test_feature_visibility()
        # 刷新 UIA 真实状态文案（避免“配置开着但未生效”的假象）
        self._refresh_uia_status()

    def apply_test_feature_visibility(self):
        """按 use_test_features 显隐测试功能分组。

        ⚠ **当前没有"测试功能分组"了**：UIA 提权、插件系统、三种输入方式
        都已毕业为正式功能，不再受此开关影响（见 core/config_store.py 的
        TEST_FEATURE_DEFAULTS 注释）。

        这个入口保留是因为开发者页的「测试功能」开关仍会调它；将来往
        TEST_FEATURE_DEFAULTS 里加新功能时，登记到 _test_widgets 即可生效。
        """
        visible = bool(self.config.get("use_test_features", False))
        for w in self._test_widgets:
            if w is not None:
                w.setVisible(visible)
        if self._test_section_title is not None:
            self._test_section_title.setVisible(visible)

    def _apply_input_method_availability(self):
        """三种输入方式现在都是正式功能，一律可选。

        历史：剪贴板粘贴/模拟键入曾属测试功能，关闭测试模式时会被禁用并
        回退到 direct。现已毕业——保留这个方法只是为了兼容既有调用点。
        """
        model = self.method_combo.model()
        for i in range(self.method_combo.count()):
            item = model.item(i) if model is not None else None
            if item is not None and not item.isEnabled():
                item.setEnabled(True)

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
        # 全局热键必须在录制期间停用：热键走 RegisterHotKey，录制时按下的正是它，
        # 不停的话每按一次都会顺手把候选条弹出来盖住设置页。
        self.hotkey_capture_changed.emit(True)

    def _stop_capture(self):
        was_capturing = self._capturing
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
        if was_capturing:
            self.hotkey_capture_changed.emit(False)

    def _update_capture_preview(self):
        """实时预览：已按下的修饰键 + 主键，逐段拼出来。"""
        mods = 0
        for bit in self._cap_mods:
            mods |= bit
        parts = win_utils.mod_labels(mods)
        parts += [label for _, label in self._cap_keys]
        if not parts:
            self.hotkey_label.setText("依次按下想要的按键…")
            return
        self.hotkey_label.setText(
            "已录制: %s  （点「完成」确认，Esc 取消）" % " + ".join(parts))

    def _handle_capture_event(self, kind, payload):
        """GUI 线程侧处理录制事件（由 pynput 线程通过信号排队过来）。"""
        if not self._capturing:
            return
        if kind == "esc":
            self._stop_capture()
            self._update_hotkey_label(self.config.get("hotkey"))
            return
        if kind == "mod":
            self._cap_mods.add(payload)
            self._update_capture_preview()   # 按下修饰键也要有反馈，否则像卡住了
            return
        vk, label = payload
        if vk is None:
            self.hotkey_label.setText("该按键暂不支持，请换字母 / 数字 / 功能键")
            return
        if any(v == vk for v, _ in self._cap_keys):
            return                            # 长按重复触发，不重复追加
        self._cap_keys.append((vk, label))
        self._update_capture_preview()

    def _commit_capture(self):
        """把已录内容整理成配置结构；不满足条件返回 None。"""
        if len(self._cap_keys) >= 2:
            return {"type": "sequence", "keys": [v for v, _ in self._cap_keys]}
        if len(self._cap_keys) == 1 and self._cap_mods:
            mods = 0
            for bit in self._cap_mods:
                mods |= bit
            return {"type": "simple", "mods": mods, "vk": self._cap_keys[0][0]}
        return None

    def _finish_capture(self):
        if not self._capturing:
            return
        parsed = self._commit_capture()
        self._stop_capture()
        if parsed is None:
            self.hotkey_label.setText("至少需「修饰键 + 主键」或连按 2 个键；已保留原设置")
            self._update_hotkey_label()
            return
        self._captured = parsed
        self._update_hotkey_label()
        # 保存/应用放到事件循环下一轮：_on_apply 会走完整条 apply_settings（写盘、
        # 主题重铺、热键重注册），同步跑在点击回调里会让「完成」按钮僵住近一秒。
        QTimer.singleShot(0, self._on_apply)

    def _on_cap_press(self, key):
        """pynput 监听线程回调：只做解析，绝不碰控件（见 capture_event 注释）。"""
        if not self._capturing:
            return
        try:
            kb = self._pynput
            if kb is None:
                return
            if key == kb.Key.esc:
                self.capture_event.emit("esc", None)
                return
            bit = _mod_bit_of(key, kb)
            if bit is not None:
                self.capture_event.emit("mod", bit)
                return
            vk, label = _resolve_capture_key(key)
            if vk is not None:
                self.capture_event.emit("key", (vk, label))
        except Exception:
            # 监听线程里抛异常会让 pynput 直接停掉监听器，之后整场录制再无反应；
            # 这里吞掉并留一条日志，宁可漏一个键也别让录制整体失效。
            from core.applog import append_log
            append_log("warn", "hotkey", "录制热键时解析按键失败")

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

    def _on_uia_toggled(self, checked):
        """UIA 提权开关：**第一次开启时**给出风险说明，之后不再打扰。"""
        if getattr(self, "_loading", False):
            return
        if not checked:
            self._on_apply()
            return

        # 首次开启：说明会发生什么（弹 UAC、以管理员重启、获得系统级窗口权限）
        try:
            from ui import risk_dialog
            risk_dialog.maybe_show_uia_risk(
                parent=self.window(),
                theme_name=resolve_theme_name(self.config.get("theme", "light")))
        except Exception:
            pass
        self._on_apply()

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
        new_cfg["input_method"] = self.method_combo.currentData()
        new_cfg["max_recent"] = self.recent_spin.value()
        new_cfg["auto_popup"] = self.auto_check.isChecked()
        new_cfg["auto_hide_on_blur"] = self.blur_hide_check.isChecked()
        new_cfg["emotion_suggest"] = self.emotion_check.isChecked()
        new_cfg["emotion_sensitivity"] = int(self.emotion_sens_slider.value())
        new_cfg["emotion_learning"] = self.emotion_learn_check.isChecked()
        new_cfg["page_size"] = self.page_spin.value()
        new_cfg["autostart"] = self.autostart_check.isChecked()
        # 源码态开关是禁用的、恒显示 False。此时**不能**把 False 写回配置，
        # 否则用户在打包态开过 UIA 提权、临时用源码跑一次改个主题，
        # 就会把那个 true 抹掉（下次打包运行还得重新开）。
        # 只在"该形态支持 UIA 提权"时才由开关决定取值。
        from core import elevate as _elevate
        if _elevate.is_supported():
            new_cfg["use_uia_elevation"] = self.uia_elevation_check.isChecked()
        else:
            new_cfg["use_uia_elevation"] = bool(
                self.config.get("use_uia_elevation", False))
        self.config = new_cfg
        self.config_applied.emit(new_cfg)

    def showEvent(self, e):
        # 每次打开设置页都重新评估 UIA 真实状态（例如以管理员重启后刚生效）
        self._refresh_uia_status()
        super().showEvent(e)

    def hideEvent(self, e):
        self._stop_capture()
        super().hideEvent(e)
