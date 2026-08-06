"""开发者模式标签页：集事件流、诊断、情绪可视化、模拟触发、焦点原始流、
输入方式对比、即时配置实验、数据校验、热键冲突检测于一体。

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
    QPlainTextEdit, QComboBox, QCheckBox, QLineEdit, QGridLayout, QScrollArea,
)
from PySide6.QtCore import Qt, Signal, QTimer, QElapsedTimer
from PySide6.QtGui import QFont, QFontDatabase

from ui.win11_theme import Theme
from core import win_utils


_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_KAOMOJI_PATH = os.path.join(_ROOT, "data", "kaomoji.json")


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
        self._apply_style()

    def _init_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(6)
        bar = QHBoxLayout()
        self._auto = QCheckBox("自动刷新")
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
    def __init__(self, bus, parent=None):
        super().__init__(parent)
        self._bus = bus
        self._paused = False
        self._init_ui()
        if bus is not None:
            bus.event.connect(self.on_event)

    def set_theme(self, theme):
        self._theme = theme

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
        self._text.setStyleSheet(
            "QPlainTextEdit{background:#ffffff;border:1px solid #d0d0d0;"
            "border-radius:8px;padding:8px 10px;color:#222;}"
        )
        root.addWidget(self._text, 1)

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
    config_applied = Signal(dict)

    def __init__(self, dev_refs, config=None, save_config=None,
                 theme_name="light", parent=None):
        super().__init__(parent)
        self._refs = dev_refs or {}
        self.config = config or {}
        self._save_config = save_config
        self._theme = Theme(theme_name)
        self._init_ui()
        self._tick_timer = QTimer(self)
        self._tick_timer.setInterval(600)
        self._tick_timer.timeout.connect(self._tick)
        self._tick_timer.start()

    # ---------- UI ----------
    def _init_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        inner = QWidget()
        body = QVBoxLayout(inner)
        body.setContentsMargins(28, 22, 28, 28)
        body.setSpacing(16)
        scroll.setWidget(inner)
        root.addWidget(scroll)

        title = QLabel("开发者模式")
        title.setObjectName("PageTitle")
        body.addWidget(title)
        sub = QLabel("实时事件流、诊断自检、情绪可视化与调试工具，便于排查自动弹出的触发与误触发。")
        sub.setObjectName("BodyText")
        sub.setWordWrap(True)
        body.addWidget(sub)

        body.addWidget(self._build_event_card())
        body.addWidget(self._build_diag_card())
        body.addWidget(self._build_emotion_card())
        body.addWidget(self._build_sim_card())
        body.addWidget(self._build_focus_card())
        body.addWidget(self._build_inject_card())
        body.addWidget(self._build_experiment_card())
        body.addWidget(self._build_data_card())
        body.addWidget(self._build_hotkey_card())
        body.addStretch(1)

    def _card(self, title):
        card = QFrame()
        card.setObjectName("Card")
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
        self._stream = EventStream(self._refs.get("bus"))
        self._stream.setMinimumHeight(150)
        v.addWidget(self._stream)

        self._log_panel = LogPanel(self._refs.get("log_buffer") or [],
                                   self._theme.name)
        self._log_panel.setMinimumHeight(150)
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
        self._method_combo = QComboBox()
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

    # ---- 7. 即时配置实验 ----
    def _build_experiment_card(self):
        card, v = self._card("即时配置实验（热生效）")
        tip = QLabel("改动后点击应用，立即热生效（不等重启），便于对比不同设置的表现。")
        tip.setObjectName("BodyText")
        tip.setWordWrap(True)
        v.addWidget(tip)
        h = QHBoxLayout()
        self._exp_method = QComboBox(); self._exp_method.addItems(["clipboard", "direct", "type"])
        self._exp_theme = QComboBox(); self._exp_theme.addItems(["light", "dark"])
        h.addWidget(QLabel("输入方式")); h.addWidget(self._exp_method)
        h.addWidget(QLabel("主题")); h.addWidget(self._exp_theme)
        v.addLayout(h)
        h2 = QHBoxLayout()
        self._exp_auto_popup = QCheckBox("自动弹出")
        self._exp_blur = QCheckBox("失焦隐藏")
        h2.addWidget(self._exp_auto_popup)
        h2.addWidget(self._exp_blur)
        h2.addStretch(1)
        v.addLayout(h2)
        b = QPushButton("应用并热生效")
        b.setObjectName("AccentButton")
        b.clicked.connect(self._apply_experiment)
        v.addWidget(b)
        return card

    # ---- 8. 数据校验报告 ----
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

    # ---------- 行为 ----------
    def showEvent(self, e):
        super().showEvent(e)
        self._refresh_diag()
        self._refresh_data_report()
        self._detect_hotkey()

    def set_theme(self, theme):
        self._theme = theme
        if hasattr(self, "_log_panel"):
            self._log_panel.set_theme(theme)
        if hasattr(self, "_stream"):
            self._stream.set_theme(theme)

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

    def _apply_experiment(self):
        cfg = dict(self._refs.get("config", {}) or {})
        cfg["input_method"] = self._exp_method.currentText()
        cfg["theme"] = self._exp_theme.currentText()
        cfg["auto_popup"] = self._exp_auto_popup.isChecked()
        cfg["auto_hide_on_blur"] = self._exp_blur.isChecked()
        self.config_applied.emit(cfg)

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
