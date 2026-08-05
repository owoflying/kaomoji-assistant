import os
import sys
import json

# 确保项目根目录在 sys.path，方便以脚本方式直接运行
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from PySide6.QtWidgets import QApplication, QSystemTrayIcon, QMenu
from PySide6.QtGui import QIcon, QPixmap, QPainter, QColor, QFont, QAction
from PySide6.QtCore import Qt, QTimer

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
from ui.settings_dialog import SettingsDialog
from ui.custom_dialog import CustomKaomojiDialog
from ui.trigger_dialog import TriggerDialog
from ui.search_dialog import SearchDialog

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

    config = load_config()
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

    # 设置面板
    settings = SettingsDialog(config)

    def open_settings():
        # 打开设置时暂停全局热键与自动弹出监听：否则录制/操作时按到原组合键会误呼出面板
        hotkey.stop()
        monitor.pause()
        # 候选条若还开着，它的全局按键捕获会吞掉设置面板里的输入，先收起来
        if window.isVisible():
            window.hide()
        settings.config = dict(config)
        settings.refresh_from_config()
        settings.show()
        settings.raise_()
        settings.activateWindow()

    window.settings_requested.connect(open_settings)

    # 自定义颜文字 / 快捷短语 / 搜索 的「暂停监听 + 打开 + 关闭后恢复」封装
    def _resume_main():
        hotkey.start(config.get("hotkey", "<ctrl>+<shift>+k"))
        if config.get("auto_popup", True):
            monitor.resume()

    def open_user_kao():
        hotkey.stop()
        monitor.pause()
        if window.isVisible():
            window.hide()
        dlg = CustomKaomojiDialog(user_kao)
        dlg.finished.connect(lambda *_: (user_kao.flush(), _resume_main()))
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    def open_triggers():
        hotkey.stop()
        monitor.pause()
        if window.isVisible():
            window.hide()
        dlg = TriggerDialog(triggers)
        dlg.finished.connect(lambda *_: (triggers.flush(), _resume_main()))
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    def open_search():
        hotkey.stop()
        monitor.pause()
        if window.isVisible():
            window.hide()
        dlg = SearchDialog(data, user_kao, config.get("theme", "light"))
        dlg.selected.connect(on_selected)
        dlg.finished.connect(lambda *_: _resume_main())
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    def close_settings():
        # 设置关闭后恢复全局热键（使用最新配置；取消则仍是原热键）
        hotkey.start(config.get("hotkey", "<ctrl>+<shift>+k"))
        if config.get("auto_popup", True):
            monitor.resume()

    settings.finished.connect(close_settings)

    def apply_settings(new_cfg):
        # 更新内存配置
        config.clear()
        config.update(new_cfg)
        save_config(config)
        # 开机自启动：仅在打包 exe 形态下实际写注册表
        if autostart.is_supported():
            autostart.set_enabled(bool(config.get("autostart", False)))
        # 实时应用：换肤等；热键的实际重新注册放到设置关闭时统一处理，
        # 避免“先注册、又在录制/操作期间被旧热键误触发”的竞态
        window.apply_config(config)
        state.max_recent = int(config.get("max_recent", 30))
        # 自动弹出开关：按需开启/关闭全局监听
        if config.get("auto_popup", True):
            monitor.start()
            if window.isVisible():
                monitor.pause()  # 面板已可见时不重复监听，避免误触发
        else:
            monitor.stop()

    settings.config_applied.connect(apply_settings)

    # 系统托盘（唤起 / 设置 / 退出）
    icon = make_icon()
    app.setWindowIcon(icon)
    tray = QSystemTrayIcon(icon, app)
    menu = QMenu()
    show_action = QAction("唤起面板", app)
    show_action.triggered.connect(window.toggle)
    my_kao_action = QAction("我的颜文字", app)
    my_kao_action.triggered.connect(open_user_kao)
    trigger_action = QAction("快捷短语", app)
    trigger_action.triggered.connect(open_triggers)
    search_action = QAction("搜索颜文字", app)
    search_action.triggered.connect(open_search)
    settings_action = QAction("设置", app)
    settings_action.triggered.connect(open_settings)
    quit_action = QAction("退出", app)
    quit_action.triggered.connect(app.quit)
    menu.addAction(show_action)
    menu.addAction(my_kao_action)
    menu.addAction(trigger_action)
    menu.addAction(search_action)
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
