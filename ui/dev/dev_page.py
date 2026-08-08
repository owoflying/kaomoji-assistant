"""开发者模式标签页：集事件流、诊断、情绪可视化、模拟触发、焦点原始流、
输入方式对比、数据校验、热键冲突检测于一体。

仅在 developer_mode 启用后，由 UnifiedSettingsWindow 在导航栏显示。
所有对 Windows API 的读取都包在 try/except 中，且不在 __init__ 主动调用，
保证非 Windows / offscreen 环境下也能安全构造与运行。
"""
import os
import json
import time
import ctypes
from collections import Counter

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame, QPushButton,
    QPlainTextEdit, QLineEdit, QGridLayout, QScrollArea,
    QSizePolicy,
)
from ui.fluent_combobox import FluentComboBox
from PySide6.QtCore import Qt, Signal, QTimer, QElapsedTimer
from PySide6.QtGui import QFont, QFontDatabase

from ui.win11_theme import Theme
from ui.fluent_checkbox import FluentCheckBox
from core import win_utils


_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_KAOMOJI_PATH = os.path.join(_ROOT, "data", "kaomoji.json")


# FluentCheckBox 已抽离到 ui/fluent_checkbox.py，开发者设置与各处的勾选框统一复用。


# ---------------------------------------------------------------------------
# 内嵌日志面板（复用全局 LOG_BUFFER，不弹窗，直接嵌在标签页里）
# ---------------------------------------------------------------------------
class LogPanel(QWidget):
    def __init__(self, buffer, theme_name="light", parent=None):
        super().__init__(parent)
        self._buffer = buffer
        self._shown = 0
        self._theme = Theme(theme_name)
        self._init_ui()
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._refresh)
        self._refresh()

    def set_theme(self, theme):
        self._theme = theme
        if hasattr(self, "_auto"):
            self._auto.set_theme(theme)
        self._apply_style()

    def _init_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(6)
        bar = QHBoxLayout()
        self._auto = FluentCheckBox("自动刷新", self._theme)
        self._auto.setChecked(True)
        self._auto.stateChanged.connect(self._on_auto)
        self._btn_refresh = QPushButton("刷新")
        self._btn_refresh.clicked.connect(self._refresh)
        self._btn_clear = QPushButton("清屏")
        self._btn_clear.clicked.connect(self._clear_view)
        bar.addWidget(self._auto)
        bar.addStretch(1)
        bar.addWidget(self._btn_refresh)
        bar.addWidget(self._btn_clear)
        root.addLayout(bar)

        self._text = QPlainTextEdit()
        self._text.setReadOnly(True)
        self._text.setLineWrapMode(QPlainTextEdit.NoWrap)
        mono = "Consolas" if "Consolas" in QFontDatabase.families() else "Courier New"
        self._text.setFont(QFont(mono, 11))
        self._text.setMinimumHeight(110)
        self._apply_style()
        root.addWidget(self._text, 1)

    def _apply_style(self):
        t = self._theme
        self._text.setStyleSheet(
            "QPlainTextEdit{background:%s;border:1px solid %s;border-radius:8px;"
            "padding:8px 10px;color:%s;}"
            % (("#2a2a2a" if t.dark else "#ffffff"), t.card_border, t.text)
        )

    def _on_auto(self, state):
        if state == Qt.Checked:
            self._timer.start()
        else:
            self._timer.stop()

    def _refresh(self):
        n = len(self._buffer)
        if n == 0:
            if self._shown == 0:
                self._text.setPlainText("（暂无日志）")
            return
        if n == self._shown:
            return
        sb = self._text.verticalScrollBar()
        at_bottom = sb.value() >= sb.maximum() - 4
        if self._shown == 0:
            self._text.setPlainText("\n".join(self._buffer))
        else:
            self._text.appendPlainText("\n".join(self._buffer[self._shown:n]))
        self._shown = n
        if at_bottom:
            sb.setValue(sb.maximum())

    def _clear_view(self):
        self._shown = len(self._buffer)
        self._text.setPlainText("（已隐藏历史，新日志继续追加）")


# ---------------------------------------------------------------------------
# 事件流面板（订阅 DevEventBus）
# ---------------------------------------------------------------------------
class EventStream(QWidget):
    def __init__(self, bus, theme_name="light", parent=None):
        super().__init__(parent)
        self._bus = bus
        self._paused = False
        self._theme = Theme(theme_name)
        self._init_ui()
        if bus is not None:
            bus.event.connect(self.on_event)

    def set_theme(self, theme):
        self._theme = theme
        self._apply_style()

    def _init_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(6)
        bar = QHBoxLayout()
        self._btn_pause = QPushButton("暂停")
        self._btn_pause.clicked.connect(self._toggle_pause)
        self._btn_clear = QPushButton("清空")
        self._btn_clear.clicked.connect(self._clear)
        bar.addStretch(1)
        bar.addWidget(self._btn_pause)
        bar.addWidget(self._btn_clear)
        root.addLayout(bar)
        self._text = QPlainTextEdit()
        self._text.setReadOnly(True)
        self._text.setLineWrapMode(QPlainTextEdit.NoWrap)
        mono = "Consolas" if "Consolas" in QFontDatabase.families() else "Courier New"
        self._text.setFont(QFont(mono, 11))
        self._text.setMinimumHeight(110)
        self._apply_style()
        root.addWidget(self._text, 1)

    def _apply_style(self):
        t = self._theme
        self._text.setStyleSheet(
            "QPlainTextEdit{background:%s;border:1px solid %s;border-radius:8px;"
            "padding:8px 10px;color:%s;}"
            % (("#2a2a2a" if t.dark else "#ffffff"), t.card_border, t.text)
        )

    def _toggle_pause(self):
        self._paused = not self._paused
        self._btn_pause.setText("继续" if self._paused else "暂停")

    def _clear(self):
        self._text.clear()

    def on_event(self, level, source, message):
        if self._paused:
            return
        ts = time.strftime("%H:%M:%S")
        self._text.appendPlainText("[%s] %s · %s · %s" % (ts, level, source, message))
        sb = self._text.verticalScrollBar()
        sb.setValue(sb.maximum())


# ---------------------------------------------------------------------------
# 开发者标签页
# ---------------------------------------------------------------------------
class DevPage(QWidget):
    developer_mode_disabled = Signal()   # 请求主窗口关闭开发者模式
    test_features_changed = Signal()     # 测试功能总开关切换，请求设置页重新评估可见性

    def __init__(self, dev_refs, config=None, save_config=None,
                 theme_name="light", parent=None):
        super().__init__(parent)
        self._refs = dev_refs or {}
        self.config = config or {}
        self._save_config = save_config
        self._theme = Theme(theme_name)
        self._active = False
        self._init_ui()
        self._tick_timer = QTimer(self)
        self._tick_timer.setInterval(600)
        self._tick_timer.timeout.connect(self._tick)
        # 计时器默认不启动：由主窗口在页面「进入」时 set_active(True) 启动，
        # 「离开」（含退出动画期间）set_active(False) 停止，
        # 避免淡出动画过程中后台仍刷新文本导致「文字跳动/闪烁」。

    # ---------- UI ----------
    def _init_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # 滚动区：与设置页一致，关闭横向滚动条（避免窄窗口出现横向条/错位），
        # 背景透明以透出主窗口亚克力材质；body 用 SettingsBody 透明背景，
        # 由统一窗口的 _refresh_content_sheet 注入内容区表面底色。
        scroll = QScrollArea()
        scroll.setObjectName("SettingsScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        inner = QWidget()
        inner.setObjectName("SettingsBody")
        self._scroll = scroll
        self._inner = inner
        # 关键：给滚动区/内容体应用「完整」主题 QSS，而不是只设 background:transparent。
        # 仅设透明背景会让 Qt 切断从统一窗口根继承的全局 QSS 级联，导致其内部
        # AccentButton/复选框等子控件收不到样式（典型：浅色模式下 AccentButton
        # 白底白字不可见）。完整 QSS 已含 QScrollArea/SettingsBody 的透明规则，
        # 透出亚克力不受影响，同时子控件样式完整。
        self._apply_content_style()
        body = QVBoxLayout(inner)
        body.setContentsMargins(28, 22, 28, 28)
        body.setSpacing(16)
        scroll.setWidget(inner)
        root.addWidget(scroll, 1)

        title = QLabel("开发者模式")
        title.setObjectName("PageTitle")
        body.addWidget(title)
        sub = QLabel("实时事件流、诊断自检、情绪可视化与调试工具，便于排查自动弹出的触发与误触发。")
        sub.setObjectName("BodyText")
        sub.setWordWrap(True)
        body.addWidget(sub)

        # 把「开发者模式控制」「测试功能」放在最上方：危险/高频操作进入标签页就要看到，
        # 避免用户滚动到长页面底部才能找到关闭入口或测试开关。
        body.addWidget(self._build_control_card())
        body.addWidget(self._build_test_card())
        body.addWidget(self._build_event_card())
        body.addWidget(self._build_diag_card())
        body.addWidget(self._build_emotion_card())
        body.addWidget(self._build_sim_card())
        body.addWidget(self._build_focus_card())
        body.addWidget(self._build_inject_card())
        body.addWidget(self._build_data_card())
        body.addWidget(self._build_hotkey_card())
        body.addStretch(1)

    def _apply_content_style(self):
        """给滚动区/内容体应用「完整」主题 QSS（而非只设透明背景）。

        仅设 background:transparent 会让 Qt 切断从统一窗口根继承的全局 QSS 级联，
        导致其内部 AccentButton/复选框等子控件收不到样式（典型：浅色模式下
        AccentButton 白底白字不可见）。完整 QSS 已含 QScrollArea/SettingsBody
        的透明规则，透出亚克力不受影响，同时子控件样式完整。
        """
        if not hasattr(self, "_scroll") or not hasattr(self, "_inner"):
            return
        ss = self._theme.style_sheet()
        self._scroll.setStyleSheet(ss)
        self._inner.setStyleSheet(ss)

    def _card(self, title):
        card = QFrame()
        card.setObjectName("Card")
        card.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        v = QVBoxLayout(card)
        v.setContentsMargins(20, 18, 20, 18)
        v.setSpacing(12)
        t = QLabel(title)
        t.setFont(QFont("Microsoft YaHei UI", 15, QFont.Weight.Bold))
        v.addWidget(t)
        return card, v

    # ---- 1. 事件流 + 日志（可观测性） ----
    def _build_event_card(self):
        card, v = self._card("实时事件流 / 运行日志")
        self._stream = EventStream(self._refs.get("bus"), self._theme.name)
        self._stream.setMinimumHeight(150)
        self._stream.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        v.addWidget(self._stream)

        self._log_panel = LogPanel(self._refs.get("log_buffer") or [],
                                   self._theme.name)
        self._log_panel.setMinimumHeight(150)
        self._log_panel.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        v.addWidget(self._log_panel)
        return card

    # ---- 2. 诊断 / 自检 ----
    def _build_diag_card(self):
        card, v = self._card("诊断 / 自检")
        grid = QGridLayout()
        grid.setSpacing(8)
        labels = [
            ("热键配置", "_d_hotkey"), ("热键注册", "_d_hk_state"),
            ("热键模式", "_d_hk_mode"), ("监听线程", "_d_thread"),
            ("UIA 可用性", "_d_uia"), ("焦点控件类", "_d_focus_cls"),
            ("键盘布局", "_d_layout"), ("输入方式", "_d_method"),
            ("主题", "_d_theme"), ("自动弹出", "_d_auto"),
            ("失焦隐藏", "_d_blur"),
        ]
        self._diag_labels = {}
        r = 0
        for i, (name, attr) in enumerate(labels):
            row = i // 2
            col = (i % 2) * 2
            k = QLabel(name)
            k.setObjectName("Caption")
            val = QLabel("-")
            val.setObjectName("BodyText")
            grid.addWidget(k, row, col)
            grid.addWidget(val, row, col + 1)
            setattr(self, attr, val)
        v.addLayout(grid)
        btn = QPushButton("刷新诊断")
        btn.clicked.connect(self._refresh_diag)
        v.addWidget(btn)
        return card

    # ---- 3. 情绪可视化 ----
    def _build_emotion_card(self):
        card, v = self._card("情绪识别可视化")
        self._emo_text = QLabel("（无）")
        self._emo_emotion = QLabel("（无）")
        self._emo_trigger = QLabel("（无）")
        self._emo_suppress = QLabel("（无）")
        for lab in (self._emo_text, self._emo_emotion, self._emo_trigger, self._emo_suppress):
            lab.setWordWrap(True)
            lab.setObjectName("BodyText")
        rows = [
            ("最近焦点文本", self._emo_text),
            ("最近命中情绪", self._emo_emotion),
            ("最近触发短语", self._emo_trigger),
            ("最近防重拦截", self._emo_suppress),
        ]
        g = QGridLayout()
        for i, (name, val) in enumerate(rows):
            k = QLabel(name); k.setObjectName("Caption")
            g.addWidget(k, i, 0)
            g.addWidget(val, i, 1)
        v.addLayout(g)
        return card

    # ---- 4. 手动模拟触发 ----
    def _build_sim_card(self):
        card, v = self._card("手动模拟触发")
        tip = QLabel("无需真实打字即可验证候选条弹出与注入链路。")
        tip.setObjectName("BodyText")
        v.addWidget(tip)
        h = QHBoxLayout()
        b1 = QPushButton("模拟热键")
        b1.clicked.connect(self._sim_hotkey)
        h.addWidget(b1)
        for emo in ("开心", "伤心", "生气", "惊讶", "喜欢", "思考", "疲惫"):
            b = QPushButton("模拟·%s" % emo)
            b.clicked.connect(lambda _, e=emo: self._sim_emotion(e))
            h.addWidget(b)
        v.addLayout(h)
        b2 = QPushButton("模拟短语触发")
        b2.clicked.connect(self._sim_trigger)
        v.addWidget(b2)
        return card

    # ---- 5. 焦点 / 钩子原始流 ----
    def _build_focus_card(self):
        card, v = self._card("焦点 / 钩子原始流")
        self._focus_text = QLabel("（无焦点文本）")
        self._focus_text.setWordWrap(True)
        self._focus_text.setObjectName("BodyText")
        self._focus_class = QLabel("（无焦点控件）")
        self._focus_class.setObjectName("BodyText")
        v.addWidget(QLabel("焦点文本：")); v.addWidget(self._focus_text)
        v.addWidget(QLabel("焦点控件类：")); v.addWidget(self._focus_class)
        b = QPushButton("立即采样一次")
        b.clicked.connect(self._sample_focus)
        v.addWidget(b)
        return card

    # ---- 6. 三种输入方式 A/B 对比 ----
    def _build_inject_card(self):
        card, v = self._card("输入方式 A/B 对比")
        tip = QLabel("点击后在「当前前台窗口」实测三种注入方式并计时（仅在开发者模式主动操作）。")
        tip.setObjectName("BodyText")
        tip.setWordWrap(True)
        v.addWidget(tip)
        h = QHBoxLayout()
        self._inject_text = QLineEdit("(๑•̀ㅂ•́)و✧")
        self._method_combo = FluentComboBox(self._theme)
        self._method_combo.addItems(["clipboard", "direct", "type"])
        self._inject_result = QLabel("-")
        self._inject_result.setObjectName("BodyText")
        h.addWidget(QLabel("文本")); h.addWidget(self._inject_text, 1)
        h.addWidget(QLabel("方式")); h.addWidget(self._method_combo)
        v.addLayout(h)
        b = QPushButton("测试注入")
        b.clicked.connect(self._test_inject)
        v.addWidget(b)
        v.addWidget(self._inject_result)
        return card

    # ---- 7. 数据校验报告 ----
    def _build_data_card(self):
        card, v = self._card("数据校验报告")
        self._data_report = QLabel("（点击刷新查看）")
        self._data_report.setObjectName("BodyText")
        self._data_report.setWordWrap(True)
        v.addWidget(self._data_report)
        b = QPushButton("刷新校验")
        b.clicked.connect(self._refresh_data_report)
        v.addWidget(b)
        return card

    # ---- 9. 热键冲突检测 ----
    def _build_hotkey_card(self):
        card, v = self._card("热键冲突检测")
        self._hk_state = QLabel("（未检测）")
        self._hk_state.setObjectName("BodyText")
        v.addWidget(self._hk_state)
        h = QHBoxLayout()
        b1 = QPushButton("检测注册状态")
        b1.clicked.connect(self._detect_hotkey)
        b2 = QPushButton("强制重新注册")
        b2.clicked.connect(self._reregister_hotkey)
        h.addWidget(b1); h.addWidget(b2)
        v.addLayout(h)
        note = QLabel("若热键未注册成功，通常为被其他程序（输入法、游戏、快捷键工具）占用同一组合键。")
        note.setObjectName("Caption")
        note.setWordWrap(True)
        v.addWidget(note)
        return card

    # ---- 10. 开发者模式控制 ----
    def _build_control_card(self):
        card, v = self._card("开发者模式控制")
        tip = QLabel("关闭后「开发者」标签与导航项会立即移除，配置项 developer_mode 置为 false 并保存；"
                     "如需重新开启，回到「关于」页连点版本号 8 次即可。")
        tip.setObjectName("BodyText")
        tip.setWordWrap(True)
        v.addWidget(tip)
        h = QHBoxLayout()
        b = QPushButton("关闭开发者模式")
        b.setObjectName("DangerButton")
        # 内联兜底样式：确保即便主题 QSS 在滚动子树中未级联到本按钮，关闭入口也足够醒目、可点击。
        b.setStyleSheet(
            "QPushButton { background:#d13438; color:#ffffff; border:1px solid rgba(0,0,0,0.12); "
            "border-radius:6px; padding:8px 20px; font-weight:600; font-size:13px; }"
            "QPushButton:hover { background:#b62a2e; }"
            "QPushButton:pressed { background:#d13438; }"
        )
        b.setCursor(Qt.PointingHandCursor)
        b.setMinimumHeight(34)
        b.clicked.connect(self._on_disable)
        h.addWidget(b)
        h.addStretch(1)
        v.addLayout(h)
        return card

    # ---- 11. 测试功能总开关 ----
    def _build_test_card(self):
        card, v = self._card("测试功能")
        tip = QLabel("开启后，设置页中处于「测试模式」的新功能（如 UIA 提权）将显示并可用；"
                     "未开启时这些功能默认隐藏。新开发功能默认归入测试模式，"
                     "仅确认可正式上线后才移出测试模式并开放给正式环境。")
        tip.setObjectName("BodyText")
        tip.setWordWrap(True)
        v.addWidget(tip)
        b = QPushButton()
        b.setMinimumHeight(34)
        b.setCursor(Qt.PointingHandCursor)
        b.clicked.connect(self._on_toggle_test)
        self._test_btn = b
        v.addWidget(b)
        self._update_test_btn()
        return card

    def _update_test_btn(self):
        on = bool(self.config.get("use_test_features", False))
        self._test_btn.setText("关闭测试功能" if on else "开启测试功能")
        self._test_btn.setObjectName("DangerButton" if on else "AccentButton")
        # 切换 objectName 后强制刷新样式，使 AccentButton / DangerButton 配色生效
        self._test_btn.style().unpolish(self._test_btn)
        self._test_btn.style().polish(self._test_btn)

    def _on_toggle_test(self):
        self.config["use_test_features"] = not bool(self.config.get("use_test_features", False))
        if self._save_config:
            self._save_config(self.config)
        self._update_test_btn()
        self.test_features_changed.emit()

    # ---------- 行为 ----------
    def _on_disable(self):
        self.developer_mode_disabled.emit()
    def showEvent(self, e):
        super().showEvent(e)
        self._refresh_diag()
        self._refresh_data_report()
        self._detect_hotkey()

    def set_theme(self, theme):
        self._theme = theme
        self._apply_content_style()
        if hasattr(self, "_log_panel"):
            self._log_panel.set_theme(theme)
        if hasattr(self, "_stream"):
            self._stream.set_theme(theme)
        if hasattr(self, "_method_combo"):
            self._method_combo.set_theme(theme)

    def set_active(self, active):
        """由主窗口在页面进入/离开时调用。

        - 进入：启动后台计时器（实时事件流、诊断采样、日志刷新）；
        - 离开（含退出动画期间）：立即停止，避免在淡出动画过程中后台仍刷新文本，
          造成「文字跳动/闪烁」（这是此前退出动画闪烁的根因；其他页面无此类后台计时器）。
        """
        self._active = bool(active)
        if active:
            if not self._tick_timer.isActive():
                self._tick_timer.start()
            # 仅当用户勾选了「自动刷新」才重启日志轮询，尊重用户选择
            if hasattr(self, "_log_panel") and self._log_panel._auto.isChecked():
                self._log_panel._timer.start()
        else:
            self._tick_timer.stop()
            if hasattr(self, "_log_panel"):
                self._log_panel._timer.stop()

    def _tick(self):
        mon = self._refs.get("monitor")
        if mon is not None:
            self._emo_text.setText(getattr(mon, "last_text", "") or "（无）")
            self._emo_emotion.setText(getattr(mon, "last_emotion", "") or "（无）")
            self._emo_trigger.setText(getattr(mon, "last_trigger", "") or "（无）")
            self._emo_suppress.setText(getattr(mon, "last_suppress", "") or "（无）")
        self._sample_focus()

    def _refresh_diag(self):
        refs = self._refs
        hk = refs.get("hotkey")
        mon = refs.get("monitor")
        cfg = refs.get("config", {}) or {}

        self._d_hotkey.setText(str(cfg.get("hotkey", "-")))
        active = bool(getattr(hk, "_active", False)) if hk else False
        mode = getattr(hk, "_mode", None) if hk else None
        self._d_hk_state.setText("已注册" if active else "未注册")
        self._d_hk_mode.setText(str(mode or "-"))

        alive = False
        if mon is not None:
            th = getattr(mon, "_thread", None)
            alive = bool(th is not None and getattr(th, "is_alive", lambda: False)())
        self._d_thread.setText("存活" if alive else "未运行")

        try:
            from core import uia_text
            uia_ok = uia_text is not None
        except Exception:
            uia_ok = False
        self._d_uia.setText("可用" if uia_ok else "不可用")

        try:
            self._d_focus_cls.setText(win_utils.get_focused_control_class() or "（无焦点控件）")
        except Exception:
            self._d_focus_cls.setText("（读取失败）")

        try:
            layout = win_utils.user32.GetKeyboardLayout(0)
            self._d_layout.setText("0x%04X" % (layout & 0xFFFF))
        except Exception:
            self._d_layout.setText("未知")

        self._d_method.setText(str(cfg.get("input_method", "-")))
        self._d_theme.setText(str(cfg.get("theme", "-")))
        self._d_auto.setText(str(cfg.get("auto_popup", "-")))
        self._d_blur.setText(str(cfg.get("auto_hide_on_blur", "-")))

    def _sim_hotkey(self):
        hk = self._refs.get("hotkey")
        if hk is not None:
            hk.hotkey_pressed.emit()

    def _sim_emotion(self, emotion):
        w = self._refs.get("window")
        if w is not None:
            w.show_for_emotion(emotion)

    def _sim_trigger(self):
        w = self._refs.get("window")
        if w is not None:
            w.show_for_output("（开发者模式·模拟短语触发）")

    def _sample_focus(self):
        try:
            ft = win_utils.get_focused_text()
        except Exception:
            ft = ""
        self._focus_text.setText(ft or "（无焦点文本）")
        try:
            fc = win_utils.get_focused_control_class()
        except Exception:
            fc = ""
        self._focus_class.setText(fc or "（无焦点控件）")

    def _test_inject(self):
        inj = self._refs.get("injector")
        if inj is None:
            self._inject_result.setText("无注入器引用")
            return
        text = self._inject_text.text() or "(●'◡'●)"
        method = self._method_combo.currentText()
        t0 = QElapsedTimer(); t0.start()
        try:
            inj.inject(text, method)
            self._inject_result.setText("注入完成：%d ms（方式=%s）" % (t0.elapsed(), method))
        except Exception as e:  # pragma: no cover - 真实注入路径依赖前台窗口
            self._inject_result.setText("注入失败：%r" % (e,))

    def _refresh_data_report(self):
        try:
            with open(_KAOMOJI_PATH, encoding="utf-8") as f:
                raw = json.load(f)
        except Exception as e:
            self._data_report.setText("读取失败：%r" % (e,))
            return

        def _collect(obj):
            out = []
            if isinstance(obj, dict):
                for v in obj.values():
                    out.extend(_collect(v))
            elif isinstance(obj, list):
                for v in obj:
                    if isinstance(v, str):
                        out.append(v)
                    else:
                        out.extend(_collect(v))
            return out

        all_raw = _collect(raw)
        total = len(all_raw)
        dup = total - len(set(all_raw))
        data = self._refs.get("data")
        lib_count = len(data.get_items()) if data is not None else 0
        clean = (lib_count == total - dup)
        self._data_report.setText(
            "原始条目：%d\n全局重复：%d\n库去重后：%d\n自净误删风险：%s"
            % (total, dup, lib_count,
               "无" if clean else "需检查（库计数 != 原始-重复）")
        )

    def _detect_hotkey(self):
        hk = self._refs.get("hotkey")
        active = bool(getattr(hk, "_active", False)) if hk else False
        self._hk_state.setText(
            "已注册（%s）" % (getattr(hk, "_mode", None) or "-") if active
            else "未注册（可能被占用）"
        )

    def _reregister_hotkey(self):
        hk = self._refs.get("hotkey")
        cfg = self._refs.get("config", {}) or {}
        if hk is not None:
            try:
                hk.start(cfg.get("hotkey"))
            except Exception:
                pass
        self._detect_hotkey()
