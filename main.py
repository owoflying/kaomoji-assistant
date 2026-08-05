import os
import sys
import json

# 确保项目根目录在 sys.path，方便以脚本方式直接运行
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from PySide6.QtWidgets import QApplication, QSystemTrayIcon, QMenu
from PySide6.QtGui import QIcon, QAction
from PySide6.QtCore import QTimer

from core.kaomoji_data import KaomojiData
from core.hotkey import NativeHotkeyManager
from core.emotion_monitor import EmotionMonitor
from core.injector import KaomojiInjector
from core.user_state import UserState
from core.user_kaomoji import UserKaomoji
from core.user_triggers import UserTriggers
from core import runtime
from core import autostart
from core.app_icon import make_icon
from ui.picker_window import PickerWindow
from ui.unified_window import UnifiedSettingsWindow
from ui.win11_theme import Theme
from ui.fluent_icons import ensure_icon_font

# 配置项的默认值；load_config 会把磁盘值合并进来，缺失项自动补默认，
# 这样新增配置项时旧配置不会因缺字段而报错。
DEFAULT_CONFIG = {
    "hotkey": "<ctrl>+<shift>+k",
    "theme": "light",
    "opacity": 0.98,
    "acrylic": True,
    "input_method": "clipboard",
    "max_recent": 30,
    "auto_popup": True,
    "page_size": 3,
    "autostart": False,
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


def main():
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("颜文字输入辅助器")
    # 注册内置图标字体（Fluent System Icons，随包分发，Win10 也能正常显示）
    ensure_icon_font()

    config = load_config()
    # 应用级样式：让所有独立对话框（新增/编辑颜文字等）与统一面板视觉一致
    app.setStyleSheet(Theme(config.get("theme", "light")).style_sheet())
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

    def on_selected(text):
        # 先告知监听器「这段文本是我们自己送进去的」：
        # 进入静默期 + 后续采样时把它剔除，否则颜文字里的 "?"「哇」等字符
        # 会被当成用户新输入的情绪词，导致上屏后又弹一次。
        monitor.notify_injected(text)
        # 延迟一点，确保面板隐藏、焦点已回到原窗口后再注入文本
        QTimer.singleShot(
            0, lambda: injector.inject(text, config.get("input_method", "clipboard"))
        )

    window.selected.connect(on_selected)

    # 统一主面板：整合 主页/库/我的颜文字/快捷短语/搜索/设置/关于
    unified = UnifiedSettingsWindow(data, config, state, user_kao, triggers)

    def _resume_main():
        """统一窗口关闭后恢复全局热键与自动弹出监听。"""
        hotkey.start(config.get("hotkey", "<ctrl>+<shift>+k"))
        if config.get("auto_popup", True):
            monitor.resume()

    def _pause_main():
        """打开统一窗口前暂停热键与监听，避免操作冲突。"""
        hotkey.stop()
        monitor.pause()
        if window.isVisible():
            window.hide()

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

    # 系统托盘：唤起候选条 / 打开主面板 / 退出
    icon = make_icon()
    app.setWindowIcon(icon)
    tray = QSystemTrayIcon(icon, app)
    menu = QMenu()
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

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
