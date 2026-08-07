import os
import sys
import json
import time
import traceback

# 确保项目根目录在 sys.path，方便以脚本方式直接运行
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from PySide6.QtWidgets import QApplication, QSystemTrayIcon, QMenu
from PySide6.QtGui import QIcon, QAction
from PySide6.QtCore import QTimer, QObject, Signal

from core.kaomoji_data import KaomojiData
from core.hotkey import NativeHotkeyManager
from core.emotion_monitor import EmotionMonitor
from core.injector import KaomojiInjector
from core.user_state import UserState
from core.user_kaomoji import UserKaomoji
from core.user_triggers import UserTriggers
from core import runtime
from core import autostart
from core import win_utils
try:
    from core import uia_text   # 浏览器/Electron 读取真实焦点文本；非 Windows 下为 None
except Exception:               # pragma: no cover - COM 不可用
    uia_text = None
from core.app_icon import make_icon
from ui.picker_window import PickerWindow
from ui.unified_window import UnifiedSettingsWindow
from ui.win11_theme import Theme, set_app_theme
from ui.fluent_icons import ensure_icon_font
from ui.log_viewer import show_log_viewer

# 运行时日志环形缓冲：收集 Qt 消息 + 未捕获异常，供「查看日志」对话框读取。
LOG_BUFFER = []
LOG_BUFFER_MAX = 2000


def _append_log(level, source, message):
    """把一条日志写入环形缓冲（超过上限丢弃最旧的）。"""
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    if source:
        line = "[%s] [%s] (%s) %s" % (ts, level, source, message)
    else:
        line = "[%s] [%s] %s" % (ts, level, message)
    LOG_BUFFER.append(line)
    if len(LOG_BUFFER) > LOG_BUFFER_MAX:
        del LOG_BUFFER[:len(LOG_BUFFER) - LOG_BUFFER_MAX]

# 配置项的默认值；load_config 会把磁盘值合并进来，缺失项自动补默认，
# 这样新增配置项时旧配置不会因缺字段而报错。
class DevEventBus(QObject):
    """开发者模式事件总线：把核心组件的关键事件聚合为统一信号，供开发者标签页订阅。

    event: (level, source, message)
    """
    event = Signal(str, str, str)

    def log(self, level, source, message):
        self.event.emit(level, source, message)


DEFAULT_CONFIG = {
    "hotkey": "<ctrl>+<shift>+k",
    "theme": "light",
    "panel_alpha": 0.92,
    "opacity": 0.98,
    "acrylic": True,
    "input_method": "clipboard",
    "max_recent": 30,
        "auto_popup": True,
        "page_size": 3,
        "autostart": False,
        "auto_hide_on_blur": True,
        "developer_mode": False,
    }


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    try:
        with open(runtime.config_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            cfg.update(data)
    except FileNotFoundError:
        pass
    except Exception:
        pass
    return cfg


def save_config(config):
    path = runtime.config_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)


def _write_diag():
    """把图标字体加载情况写到 diag.txt（exe 同目录 / 源码根目录）。"""
    from ui.fluent_icons import font_status, FONT_FILE, GLYPHS, char
    from PySide6.QtGui import QFont, QFontInfo, QImage, QPainter, QColor
    from PySide6.QtCore import Qt

    family, err = font_status()
    path = runtime.resource_path("ui", "fonts", FONT_FILE)
    lines = [
        "frozen        = %s" % runtime.is_frozen(),
        "font path     = %s" % path,
        "  isfile      = %s" % os.path.isfile(path),
        "  isdir       = %s" % os.path.isdir(path),
        "registered    = %r" % family,
        "error         = %r" % err,
    ]

    # 逐个图标「真的画一遍」：豆腐块/缺字形的表现是——要么一片空白，
    # 要么所有图标画出来长得一模一样。这比 supportsCharacter 可靠。
    f = QFont(family)
    f.setPixelSize(24)
    lines.append("resolved      = %r" % QFontInfo(f).family())

    def render(ch):
        img = QImage(32, 32, QImage.Format_ARGB32)
        img.fill(QColor(255, 255, 255))
        p = QPainter(img)
        p.setFont(f)
        p.setPen(QColor(0, 0, 0))
        p.drawText(img.rect(), Qt.AlignCenter, ch)
        p.end()
        data = img.constBits().tobytes()
        ink = sum(1 for y in range(32) for x in range(32)
                  if img.pixelColor(x, y).lightness() < 200)
        return data, ink

    shapes, blank = {}, []
    for n in GLYPHS:
        data, ink = render(char(n))
        if ink < 6:
            blank.append(n)
        shapes.setdefault(data, []).append(n)
    identical = [v for v in shapes.values() if len(v) > 1]

    lines.append("blank glyphs  = %s" % (blank or "none"))
    lines.append("identical sets= %s" % (identical or "none"))

    # 颜文字字体链：库里混着印度语系字符（ಠ 卡纳达 / દ 古吉拉特），
    # 检查它们能不能真的画出字形——注意不要用 QRawFont.supportsCharacter 判断，
    # 它在 Windows 上会顺着 DirectWrite 的隐式回退返回假阳性（会说 YaHei 支持 ಠ，
    # 其实 YaHei 的 cmap 里根本没有这个字形）。还是实际画一遍比像素最可靠。
    from PySide6.QtGui import QFontDatabase
    from ui.win11_theme import kaomoji_font

    kf = kaomoji_font(14)
    kf.setPixelSize(24)
    installed = set(QFontDatabase.families())
    lines.append("")
    lines.append("kaomoji font chain:")
    # 无头/offscreen 环境枚举不到系统字体，此时该项无意义，跳过判定。
    # 注意不能用 len(installed)==0 判断：ensure_icon_font() 已注册了内置图标
    # 字体，字体库永远至少有 1 个。改判「回退链里一个系统字体都没装上」。
    degenerate = not any(fam in installed for fam in kf.families())
    if degenerate:
        lines.append("  (枚举不到任何系统字体——offscreen/无头环境，跳过此项检查)")
    lines.append("  families    = %s" % ", ".join(kf.families()))

    def render_with(font, ch):
        img = QImage(48, 48, QImage.Format_ARGB32)
        img.fill(QColor(255, 255, 255))
        p = QPainter(img)
        p.setFont(font)
        p.setPen(QColor(0, 0, 0))
        p.drawText(img.rect(), Qt.AlignCenter, ch)
        p.end()
        ink = sum(1 for y in range(48) for x in range(48)
                  if img.pixelColor(x, y).lightness() < 200)
        return img.constBits().tobytes(), ink

    missing_kao = []
    if not degenerate:
        tofu, _ = render_with(kf, "\uffff")   # 一定是 .notdef 豆腐块，用作参照
        # 标签只用 ASCII：诊断结果要往 stdout 打，Windows 控制台是 GBK 代码页，
        # 直接把 ಠ/દ 写进去会 UnicodeEncodeError。
        for label, ch in (("Kannada  U+0CA0", "\u0ca0"),
                          ("Gujarati U+0AA6", "\u0aa6")):
            data, ink = render_with(kf, ch)
            if data == tofu:
                state = "TOFU 豆腐块"
            elif ink < 6:
                state = "BLANK 空白"
            else:
                state = "OK"
            lines.append("  %-16s ink=%-5d %s" % (label, ink, state))
            if state != "OK":
                missing_kao.append(label)

    # 上面这几笔渲染必然会触发 qt.text.font.db 的噪声告警，正好用来验证过滤器。
    lines.append("  噪声告警过滤    = 已拦下 %d 条 qt.text.font.db"
                 % _MUTED_LOG_COUNT["n"])

    ok = (not blank) and (not identical) and not err and not missing_kao
    lines.append("")
    lines.append("RESULT        = %s" % ("OK" if ok else "FAIL"))

    base = os.path.dirname(sys.executable) if runtime.is_frozen() else runtime.source_base_dir()
    out = os.path.join(base, "diag.txt")
    text = "\n".join(lines) + "\n"
    try:
        with open(out, "w", encoding="utf-8") as fp:
            fp.write(text)
    except Exception:
        pass
    # 控制台可能是 GBK 代码页，写不进去的字符降级处理，别让自检自己崩掉。
    try:
        sys.stdout.write(text)
    except UnicodeEncodeError:
        enc = getattr(sys.stdout, "encoding", None) or "utf-8"
        sys.stdout.write(text.encode(enc, "replace").decode(enc, "replace"))


# 需要静音的 Qt 噪声日志：(日志类别, 消息前缀)
_MUTED_QT_LOGS = (
    ("qt.text.font.db", "OpenType support missing for"),
)
_MUTED_LOG_COUNT = {"n": 0}   # 供 --diag 自检用


def _install_qt_log_filter():
    """过滤掉 Qt 字体库的「文种探测」噪声告警。

    现象（每次渲染颜文字列表都会刷屏）：
      qt.text.font.db: OpenType support missing for "Segoe UI Emoji", script 18
      qt.text.font.db: OpenType support missing for "Segoe UI Symbol", script 14
      ...
    成因：颜文字库里混着印度语系字符——ಠ(U+0CA0 卡纳达, script 18) 与
    દ(U+0AA6 古吉拉特, script 14)，见 (╬ಠ益ಠ) / ♡(˃͈ દ ˂͈ ༶ )。
    kaomoji_font() 是一条「逐字回退」的多字体链，Qt 排版时会挨个探测链里的字体
    有没有该文种的 OpenType(GSUB) 表，Emoji/Symbol/YaHei 本来就不含这些文种，
    于是 3 字体 × 2 文种 = 6 条告警。字符最终由系统回退字体（Nirmala UI）正常
    渲染出来，**显示是好的**，纯粹是日志噪声。

    为什么不用 QLoggingCategory.setFilterRules("qt.text.font.db.warning=false")：
    实测无效。该类别的 isWarningEnabled() 确实会变成 False，但这条告警在 Qt 内部
    并没有走 category 的开关检查，消息照样发出来（已用 qInstallMessageHandler
    抓到 6 条验证）。所以只能在消息处理器这一层拦。

    也不能靠把 Nirmala UI 显式塞进字体链来消除：实测加与不加，告警都是 6 条，
    且 ಠ/દ 渲染出来的像素完全一致（系统隐式回退早就找到它了）。

    过滤只针对上面白名单里的固定前缀，其它 Qt 日志一律照常输出，不影响排查问题。
    """
    from PySide6.QtCore import qInstallMessageHandler, QtMsgType

    level_name = {
        QtMsgType.QtDebugMsg: "debug",
        QtMsgType.QtInfoMsg: "info",
        QtMsgType.QtWarningMsg: "warning",
        QtMsgType.QtCriticalMsg: "critical",
        QtMsgType.QtFatalMsg: "fatal",
    }

    def handler(mode, ctx, msg):
        category = getattr(ctx, "category", "") or ""
        for cat, prefix in _MUTED_QT_LOGS:
            if category == cat and msg.startswith(prefix):
                _MUTED_LOG_COUNT["n"] += 1
                return
        # 收集到运行时日志缓冲（供「查看日志」对话框读取）
        level = level_name.get(mode, "qt")
        src = category if category and category != "default" else ""
        _append_log(level, src, msg)
        # 其余原样放行到 stderr，格式尽量贴近 Qt 默认：带类别的打 "类别: 内容"，
        # 无类别（Qt 记作 default）的直接打内容；critical/fatal 额外标出级别。
        parts = []
        if mode in (QtMsgType.QtCriticalMsg, QtMsgType.QtFatalMsg):
            parts.append("[%s]" % level)
        if category and category != "default":
            parts.append("%s:" % category)
        parts.append(msg)
        try:
            sys.stderr.write(" ".join(parts) + "\n")
            sys.stderr.flush()
        except Exception:
            pass

    qInstallMessageHandler(handler)


def _install_excepthook():
    """把未捕获的 Python 异常也收进日志缓冲（含 Qt 信号槽里抛出的异常）。"""
    def hook(exc_type, exc_value, exc_tb):
        text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb)).rstrip()
        _append_log("exception", "", text)
    sys.excepthook = hook


def main():
    _install_qt_log_filter()
    _install_excepthook()
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("颜文字输入辅助器")
    _append_log("info", "app", "日志开始记录。")
    # 注册内置图标字体（Fluent System Icons，随包分发，Win10 也能正常显示）
    ensure_icon_font()

    # 自检模式：KaomojiAssistant.exe --diag 会在 exe 同目录写 diag.txt 后退出，
    # 用来排查“图标显示成豆腐块”这类打包资源问题（窗口程序看不到控制台输出）。
    if "--diag" in sys.argv:
        _write_diag()
        return

    config = load_config()
    # 应用级样式：让所有独立对话框（新增/编辑颜文字等）与统一面板视觉一致
    app.setStyleSheet(Theme(config.get("theme", "light")).style_sheet())
    set_app_theme(config.get("theme", "light"))
    # 让「配置里的 autostart」与注册表实际状态保持一致
    if autostart.is_supported():
        if bool(config.get("autostart", False)) != autostart.is_enabled():
            autostart.set_enabled(bool(config.get("autostart", False)))
    data = KaomojiData()
    # 用户自定义颜文字 / 快捷短语（可写，独立于只读库）
    user_kao = UserKaomoji()
    triggers = UserTriggers()
    # 传入颜文字库作为白名单：最近/收藏只保留库中真实存在的条目，
    # 任何脏数据（测试残留、手改 JSON 出错）都会在载入时被自动清掉
    state = UserState(
        max_recent=int(config.get("max_recent", 30)),
        valid_items=data.get_items() + user_kao.get_all(),
    )
    # 用户新增/编辑自定义颜文字后，同步刷新最近记录的白名单
    def _refresh_valid():
        state.valid_items = set(data.get_items() + user_kao.get_all())
    user_kao.changed.connect(_refresh_valid)

    injector = KaomojiInjector()

    window = PickerWindow(data, config, state, user_kao)

    hotkey = NativeHotkeyManager()
    hotkey.hotkey_pressed.connect(window.toggle)

    # 自动弹出监听（打字识别情绪 -> 就近弹出候选条）
    monitor = EmotionMonitor(triggers=triggers)
    monitor.emotion_detected.connect(window.show_for_emotion)
    monitor.trigger_detected.connect(window.show_for_output)
    window.isVisibleChanged.connect(
        lambda v: monitor.pause() if v else monitor.resume()
    )
    # 面板因某情绪弹出后立即锁定该情绪，避免同一句话被反复推荐
    window.emotion_shown.connect(monitor.notify_shown)

    # 开发者模式事件总线：聚合核心事件，供开发者标签页实时展示
    bus = DevEventBus()
    hotkey.hotkey_pressed.connect(lambda: bus.log("事件", "热键", "热键触发"))
    monitor.emotion_detected.connect(lambda e: bus.log("情绪", "监听", "命中情绪：%s" % e))
    monitor.trigger_detected.connect(
        lambda out, tw, dt: bus.log("触发", "监听",
                                    "命中短语：%s（触发词=%s，应用后删除=%s）" % (out, tw, dt)))
    monitor.diagnostic.connect(lambda d: bus.log("诊断", "监听", d))
    window.emotion_shown.connect(lambda e: bus.log("弹出", "面板", "候选条弹出（情绪=%s）" % e))

    def on_selected(text, ctx=None):
        method = config.get("input_method", "clipboard")
        # 先告知监听器「这段文本是我们自己送进去的」：
        # 进入静默期 + 后续采样时把它剔除，否则颜文字里的 "?"「哇」等字符
        # 会被当成用户新输入的情绪词，导致上屏后又弹一次。
        monitor.notify_injected(text)
        bus.log("注入", "主程序", "注入颜文字：%s（方式=%s）" % (text, method))
        # 快捷短语选项：应用后删除用户刚输入的触发词本身。
        # 仅当该短语显式开启 delete_trigger 且能拿到触发词时才处理；
        # 其它来源（情绪 / 手动 / 库页 / 搜索）一律不删，保持原有逻辑。
        trigger_word = (ctx or {}).get("trigger_word") or ""
        delete_trigger = bool((ctx or {}).get("delete_trigger"))
        if delete_trigger and trigger_word:
            _apply_trigger_with_delete(text, method, trigger_word)
        else:
            # 延迟一点，确保面板隐藏、焦点已回到原窗口后再注入文本
            QTimer.singleShot(0, lambda: injector.inject(text, method))
        # 使用统计（任何来源的插入都在此汇聚）：放到注入调度之后并兜底捕获异常，
        # 确保统计逻辑的任何意外都不会阻断颜文字注入（避免「剪贴板模式失效」）。
        try:
            state.record_usage(text)
        except Exception:
            pass


    def _cursor_tail_equals(word):
        """最佳努力确认光标左侧「恰好是触发词」，避免误删其它内容。

        原生编辑框走 WM_GETTEXT（亚毫秒）；浏览器 / Electron 走 UI Automation（带超时）。
        任一读取失败或结尾不匹配都返回 False —— 此时保守地不删除、只注入，
        宁可保留触发词，也绝不误删用户已写下的其它文字。
        """
        if not word:
            return False
        n = len(word)
        try:
            t = win_utils.get_focused_text(n)
            if t and t[-n:] == word:
                return True
        except Exception:
            pass
        if uia_text is not None:
            try:
                t = uia_text.get_focused_text(n, timeout=0.15)
                if t and t[-n:] == word:
                    return True
            except Exception:
                pass
        return False


    def _apply_trigger_with_delete(text, method, trigger_word):
        """先安全删掉触发词，再注入颜文字；任何不确定都只注入不删除。"""
        n = len(trigger_word)

        def _do():
            if _cursor_tail_equals(trigger_word):
                # 必须在「注入颜文字之前」删除：删完后光标回到触发词起点，
                # 再注入颜文字，最终文本里只留下颜文字本身。若反过来先注入再退格，
                # 退格会删掉颜文字的尾部字符，造成乱码。
                injector.delete_chars(n)
                QTimer.singleShot(
                    max(80, n * 12),
                    lambda: injector.inject(text, method),
                )
            else:
                # 结尾已变化 / 无法确认：保守只注入，不删除（保持原有逻辑，绝不误删）
                injector.inject(text, method)

        # 等面板完全隐藏、焦点回到原窗口后再操作
        QTimer.singleShot(0, _do)

    window.selected.connect(on_selected)

    # 统一主面板：整合 主页/库/我的颜文字/快捷短语/搜索/设置/关于
    unified = UnifiedSettingsWindow(
        data, config, state, user_kao, triggers,
        save_config=save_config,
        open_log=lambda: show_log_viewer(
            LOG_BUFFER, parent=unified,
            theme_name=config.get("theme", "light")),
        dev_refs={
            "hotkey": hotkey, "monitor": monitor, "injector": injector,
            "data": data, "config": config, "window": window, "bus": bus,
            "log_buffer": LOG_BUFFER,
        },
    )

    def _resume_main():
        """统一窗口关闭后恢复全局热键与自动弹出监听。"""
        hotkey.start(config.get("hotkey", "<ctrl>+<shift>+k"))
        if config.get("auto_popup", True):
            monitor.resume()

    def _pause_main():
        """打开统一窗口前暂停热键与监听，避免操作冲突。"""
        hotkey.stop()
        if window.isVisible():
            window.hide()
        # 注意：window.hide() 会经 isVisibleChanged(False) -> monitor.resume() 把监听重新打开，
        # 因此 monitor.pause() 必须放在 hide 之后，否则暂停会被刚才的 resume 抵消
        # （仅在候选条当时正显示时触发），导致统一窗口打开期间自动弹出监听仍在跑。
        monitor.pause()

    def open_unified(page_key=None):
        _pause_main()
        if page_key:
            unified._set_page(page_key)
        unified.show()
        unified.raise_()
        unified.activateWindow()

    # 候选条上的设置入口也统一到主面板
    window.settings_requested.connect(lambda: open_unified("settings"))

    def on_unified_output(text):
        """统一窗口内的搜索/库页选中颜文字：隐藏窗口、注入文本。"""
        unified.hide()
        # 先通知监听器注入，再恢复监听，避免把注入内容误判为用户输入
        on_selected(text)
        _resume_main()

    unified.output_selected.connect(on_unified_output)

    def apply_settings(new_cfg):
        # 更新内存配置
        config.clear()
        config.update(new_cfg)
        save_config(config)
        # 开机自启动：仅在打包 exe 形态下实际写注册表
        if autostart.is_supported():
            autostart.set_enabled(bool(config.get("autostart", False)))
        # 实时应用：换肤等；热键的实际重新注册放到统一窗口关闭时统一处理
        # 应用级样式表也要随主题刷新，否则独立对话框（新增/编辑等）不会跟随新主题
        app.setStyleSheet(Theme(config.get("theme", "light")).style_sheet())
        menu.setStyleSheet(Theme(config.get("theme", "light")).menu_style())
        set_app_theme(config.get("theme", "light"))
        window.apply_config(config)
        unified.apply_config(config)
        state.max_recent = int(config.get("max_recent", 30))
        # 自动弹出开关：按需开启/关闭全局监听；若统一窗口仍打开则保持暂停，
        # 等窗口关闭时由 _resume_main 统一恢复。
        if config.get("auto_popup", True):
            monitor.start()
            if window.isVisible() or unified.isVisible():
                monitor.pause()
        else:
            monitor.stop()

    unified.config_applied.connect(apply_settings)
    unified.finished.connect(_resume_main)

    # 候选条不透明度实时预览：拖动滑块时即时改变候选条透明度，与滑动位置严格对应
    # （落盘与正式应用仍由 sliderReleased -> _on_apply -> apply_settings 统一完成）。
    def _preview_picker_opacity(value):
        try:
            if getattr(window, "_panel_bg", None) is not None:
                window._panel_bg.setAlphaF(max(0.3, min(1.0, value)))
                window.repaint()
        except Exception:
            pass
    unified.settings_page.opacity_preview.connect(_preview_picker_opacity)

    # 系统托盘：唤起候选条 / 打开主面板 / 退出
    icon = make_icon()
    app.setWindowIcon(icon)
    tray = QSystemTrayIcon(icon, app)
    menu = QMenu()
    menu.setStyleSheet(Theme(config.get("theme", "light")).menu_style())
    show_action = QAction("唤起面板", app)
    show_action.triggered.connect(window.toggle)
    panel_action = QAction("打开主面板", app)
    panel_action.triggered.connect(open_unified)
    settings_action = QAction("设置", app)
    settings_action.triggered.connect(lambda: open_unified("settings"))
    quit_action = QAction("退出", app)
    quit_action.triggered.connect(app.quit)
    menu.addAction(show_action)
    menu.addAction(panel_action)
    menu.addAction(settings_action)
    log_action = QAction("查看日志", app)
    log_action.triggered.connect(
        lambda: show_log_viewer(LOG_BUFFER, parent=unified,
                                theme_name=config.get("theme", "light"))
    )
    # 日志功能整合进开发者模式：未启用时不可访问
    log_action.setVisible(bool(config.get("developer_mode", False)))
    menu.addAction(log_action)
    menu.aboutToShow.connect(
        lambda: log_action.setVisible(bool(config.get("developer_mode", False)))
    )
    menu.addSeparator()
    menu.addAction(quit_action)
    tray.setContextMenu(menu)

    tray.setToolTip("颜文字输入辅助器")
    tray.show()

    hotkey.start(config.get("hotkey", "<ctrl>+<shift>+k"))
    if config.get("auto_popup", True):
        monitor.start()
    app.aboutToQuit.connect(hotkey.stop)
    app.aboutToQuit.connect(monitor.stop)
    app.aboutToQuit.connect(window.shutdown)
    app.aboutToQuit.connect(state.flush)
    app.aboutToQuit.connect(user_kao.flush)
    app.aboutToQuit.connect(triggers.flush)
    app.aboutToQuit.connect(lambda: _append_log("info", "app", "应用程序退出。"))

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
