"""应用装配层：把配置、核心组件、窗口、托盘、热键接线成一个可运行的应用。

为什么不再写在 `main()` 里：原先入口函数一个人干了 400 多行，内部十几个闭包
互相引用（`on_selected` 调 `_apply_trigger_with_delete`、`apply_settings` 调
`_warn_uia_once`、主题轮询回调又要摸 `menu`/`window`/`unified`），结果是
**无法单独测试任何一个环节**，也没法说清某个函数到底依赖哪些组件。
现在这些状态与行为成为实例属性/方法，`main()` 只负责"建窗口程序 → 交给运行时 →
进事件循环"。

装配顺序是有讲究的（改动前请先读）：
  1. UIA 提权必须最早——它可能直接重启进程，后面的组件都不必建；
  2. 插件必须在 UserState 之前加载——插件提供的颜文字要进「最近/收藏」白名单；
  3. 托盘最后建——插件 UI 能力（托盘气泡）需要它已就绪，故用后置注入
     （`plugins.set_ui_hooks`）而不是构造参数；
  4. 「欢迎更新」弹窗与插件 startup 事件都用 `QTimer.singleShot(0)` 投递，
     靠投递顺序保证前者先弹，避免两个模态框抢焦点。
"""
from PySide6.QtCore import QObject, QTimer, Qt, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMenu, QMessageBox, QSystemTrayIcon

from core import autostart, runtime, uia_elevation, win_utils
from core.app_icon import make_icon
from core.applog import LOG_BUFFER, append_log
from core.config_store import DEFAULT_CONFIG, load_config, save_config
from core.emotion_monitor import EmotionMonitor
from core.hotkey import NativeHotkeyManager
from core.injector import KaomojiInjector
from core.kaomoji_data import KaomojiData
from core.plugins import PluginManager
from core.ui_registry import UiRegistry
from core.user_kaomoji import UserKaomoji
from core.user_state import UserState
from core.user_triggers import UserTriggers
from ui.log_viewer import show_log_viewer
from ui.picker_window import PickerWindow
from ui.plugin_ui_hooks import build_ui_hooks
from ui.unified_window import UnifiedSettingsWindow
from ui.win11_theme import (
    Theme, resolve_theme_name, set_app_theme, system_theme_name,
)

try:                                    # 判断窗口 C++ 对象是否仍然有效
    from shiboken6 import isValid as shiboken_is_valid
except Exception:                       # pragma: no cover - 极端环境兜底
    def shiboken_is_valid(obj):
        try:
            return bool(obj)
        except RuntimeError:
            return False

try:
    from core import uia_text   # 浏览器/Electron 读取真实焦点文本；非 Windows 下为 None
except Exception:               # pragma: no cover - COM 不可用
    uia_text = None

HOTKEY_DEFAULT = DEFAULT_CONFIG["hotkey"]
# 「跟随系统」主题的轮询间隔：系统主题变化没有可靠的回调，只能低频轮询
SYSTEM_THEME_POLL_MS = 3000
# 因外部点击/继续打字关闭面板后，热键在此时长内视为同一次关闭，避免抖动重开
DISMISS_GUARD_SEC = 0.4

#: 只影响「绘制」而不影响「布局/控件结构」的配置项。
#:
#: 这类改动走 `refresh_visuals()`（只重绘）而不是 `apply_config()`
#: （重贴 QSS + 刷全部子页，实测 ≈320ms）。用户拖动不透明度滑块时
#: 每一档都走全量刷新会明显卡顿。
_LIGHT_CHANGE_KEYS = frozenset({
    "panel_alpha",   # 面板不透明度：只影响 paintEvent 的基底色
    "opacity",       # 候选条不透明度：只影响窗口透明度
})


def _valid_item_pool(data, user_kao, triggers):
    """「合法条目」集合：内置库 + 插件数据源 + 我的颜文字 + 快捷短语输出。

    这是 UserState 的「自净」白名单：不在集合里的最近使用/收藏/统计条目会被丢弃。
    快捷短语的输出必须算进来 —— 它们同样是用户真的上屏过的内容，漏掉就会出现
    「统计里明明有，重启后凭空少了几条」。
    """
    pool = list(data.get_items()) + list(user_kao.get_all())
    if triggers is not None:
        pool += triggers.outputs()
    return pool


class DevEventBus(QObject):
    """开发者模式事件总线：把核心组件的关键事件聚合为统一信号，供开发者标签页订阅。

    event: (level, source, message)
    """
    event = Signal(str, str, str)

    def log(self, level, source, message):
        self.event.emit(level, source, message)


class AppRuntime:
    """持有全部运行期组件，并负责它们之间的信号接线与配置应用。"""

    def __init__(self, app, config):
        self.app = app
        self.config = config          # 权威配置字典：**原地更新**，引用始终不变
        self._uia_warn_shown = False
        #: 情绪推荐"已展示待确认"：(emotion, source_text)。
        #: 面板关闭时若它还在，说明用户没采用这次推荐 → 弱负反馈。
        self._pending_emotion = None
        self._log_viewer = None
        # 上一次真正贴到 QApplication 上的样式串，用于幂等短路（见 _apply_app_theme）
        self._applied_style = None

        self.data = None
        self.plugins = None
        self.user_kao = None
        self.triggers = None
        self.state = None
        self.injector = None
        self.hotkey = None
        self.monitor = None
        self.bus = None
        self.window = None            # 候选条
        self.unified = None           # 统一主面板
        self.tray = None
        self.menu = None

        # 系统主题轮询（仅「跟随系统」模式启用）
        self._last_system_theme = system_theme_name()
        self._system_theme_timer = QTimer(app)
        self._system_theme_timer.setInterval(SYSTEM_THEME_POLL_MS)
        self._system_theme_timer.timeout.connect(self._check_system_theme)

        self._build()

    # ------------------------------------------------------------------
    # 装配
    # ------------------------------------------------------------------
    def _build(self):
        self._ensure_uiaccess_at_startup()
        self._apply_initial_theme()
        self._sync_autostart()
        self._build_core()
        self._build_windows()
        self._wire_components()
        self._build_tray()
        self._update_system_theme_watcher()
        self._schedule_startup_hooks()
        self._start_services()
        self._install_quit_hooks()

    def _ensure_uiaccess_at_startup(self):
        """配置开启「使用 UIA 提权」时，在启动期为自身进程尝试 UIAccess。

        成功时会以 UIAccess 令牌重启自身（本进程随即退出），失败/未开启时
        零副作用——函数内部先读配置。
        """
        uia_elevation.ensure_uiaccess(self.config, append_log)

    def _apply_initial_theme(self):
        """应用级样式表：让独立对话框与统一面板视觉一致（含 "system" 解析）。"""
        self._apply_app_theme(resolve_theme_name(self.config.get("theme", "light")))
        set_app_theme(self.config.get("theme", "light"))

    def _apply_app_theme(self, theme_name):
        """全应用 QSS 重贴（幂等）。

        `QApplication.setStyleSheet` 会触发所有控件重新 polish，代价随控件数量线性
        增长（本项目实测 ≈500ms）。设置页任何一处改动都会走 apply_settings，若不做
        幂等短路，拖一下滑块、录一次热键都要白卡近一秒——这正是「点完成特别卡顿」
        的主要来源。样式串不变时直接返回。
        """
        theme = Theme(theme_name)
        ss = theme.style_sheet()
        if ss == self._applied_style:
            return
        self.app.setStyleSheet(ss)
        if self.menu is not None:
            self.menu.setStyleSheet(theme.menu_style())
        self._applied_style = ss

    def _sync_autostart(self):
        """让「配置里的 autostart」与注册表实际状态保持一致（仅 exe 形态）。"""
        if not autostart.is_supported():
            return
        want = bool(self.config.get("autostart", False))
        if want != autostart.is_enabled():
            autostart.set_enabled(want)

    def _build_core(self):
        self.data = KaomojiData()
        # 界面元素登记表：插件通过 api.ui.find("home.greeting.title") 定位控件。
        # 窗口还没建，先把 registry 造出来并注入 manager；等 _build_windows 之后
        # 再让窗口把自己登记的 id 写进来（见 _register_ui_elements）。
        self.ui_registry = UiRegistry()
        self.plugins = PluginManager(
            plugins_dir=runtime.plugins_dir(),
            config=self.config,
            save_config=save_config,
            log=append_log,
            settings_dir=runtime.plugin_settings_dir(),
            # 主题提供者：插件弹窗据此取当前深浅色（避免 core 反向依赖 ui）
            theme_provider=lambda: resolve_theme_name(self.config.get("theme", "light")),
            # UI 能力注入点：core 只声明协议，实现留在 ui/（见 core/ui_hooks.py）。
            # 托盘尚未创建，notify 由 _build_tray 用 set_ui_hooks 后置补齐。
            ui_hooks=build_ui_hooks(
                theme_provider=lambda: resolve_theme_name(
                    self.config.get("theme", "light"))),
            ui_registry=self.ui_registry,
        )
        self.plugins.discover()
        # 插件已是**正式功能**：不再受 use_test_features 门控，按 plugins_enabled 加载。
        # （历史：插件曾在测试门控下，关闭测试模式时一个都不加载。）
        n_loaded = self.plugins.load_all()
        if n_loaded:
            append_log("info", "plugins", "已加载 %d 个插件" % n_loaded)
        self.plugins.refresh_sources(self.data)

        # 用户自定义颜文字 / 快捷短语（可写，独立于只读库）
        self.user_kao = UserKaomoji()
        self.triggers = UserTriggers()
        # 传入颜文字库作为白名单：最近/收藏只保留库中真实存在的条目，
        # 任何脏数据（测试残留、手改 JSON 出错）都会在载入时被自动清掉
        self.state = UserState(
            max_recent=int(self.config.get("max_recent", 30)),
            valid_items=_valid_item_pool(self.data, self.user_kao, self.triggers),
        )
        self.injector = KaomojiInjector()
        self.bus = DevEventBus()

    def _build_windows(self):
        self.window = PickerWindow(
            self.data, self.config, self.state, self.user_kao)
        self.hotkey = NativeHotkeyManager()
        self.monitor = EmotionMonitor(triggers=self.triggers)
        self.unified = UnifiedSettingsWindow(
            self.data, self.config, self.state, self.user_kao, self.triggers,
            save_config=save_config,
            open_log=self.open_log_viewer,
            dev_refs={
                "hotkey": self.hotkey, "monitor": self.monitor,
                "injector": self.injector, "data": self.data,
                "config": self.config, "window": self.window, "bus": self.bus,
                "log_buffer": LOG_BUFFER, "plugins": self.plugins,
                "ui_registry": self.ui_registry,
            },
            plugins=self.plugins,
            ui_registry=self.ui_registry,
        )

    def _wire_components(self):
        w = self.window
        self.hotkey.hotkey_pressed.connect(w.toggle)
        # emotion_detected 现在带两个参数：(emotion, source_text)。
        # source_text 是触发那次弹窗的用户原句，在线学习要拿它当训练样本。
        # 外面包一层：`emotion_suggest` 关闭时不弹（热键唤起等不受影响）。
        self.monitor.emotion_detected.connect(self._on_emotion_detected)
        self.monitor.trigger_detected.connect(w.show_for_output)
        # 候选条可见时暂停监听、隐藏时恢复（避免把候选条自身当输入）
        w.isVisibleChanged.connect(self._on_picker_visibility)        # 面板因某情绪弹出后立即锁定该情绪，避免同一句话被反复推荐
        w.emotion_shown.connect(self.monitor.notify_shown)
        w.selected.connect(self.on_selected)
        w.settings_requested.connect(lambda: self.open_unified("settings"))

        # 开发者事件流
        self.hotkey.hotkey_pressed.connect(
            lambda: self.bus.log("事件", "热键", "热键触发"))
        self.monitor.emotion_detected.connect(
            lambda e, src="": self.bus.log(
                "情绪", "监听", "命中情绪：%s（原句：%s）" % (e, src)))
        self.monitor.trigger_detected.connect(
            lambda out, tw, dt: self.bus.log(
                "触发", "监听",
                "命中短语：%s（触发词=%s，应用后删除=%s）" % (out, tw, dt)))
        self.monitor.diagnostic.connect(
            lambda d: self.bus.log("诊断", "监听", d))
        w.emotion_shown.connect(
            lambda e: self.bus.log("弹出", "面板", "候选条弹出（情绪=%s）" % e))

        # 配置与页面联动
        self.unified.config_applied.connect(self.apply_settings)
        self.unified.finished.connect(self.resume_global_hooks)
        self.unified.output_selected.connect(self._on_unified_output)
        self.unified.settings_page.opacity_preview.connect(
            self._preview_picker_opacity)
        # 录制热键期间临时停用全局热键（其余时间热键始终可用）
        self.unified.settings_page.hotkey_capture_changed.connect(
            self._on_hotkey_capture)

        # 数据源变化：用户新增/编辑自定义颜文字、插件启用/停用都要刷新白名单
        self.user_kao.changed.connect(self._refresh_valid_items)
        self.plugins.changed.connect(self._on_plugins_changed)

    def _on_picker_visibility(self, visible):
        """候选条显隐联动监听器：显示时暂停，隐藏时恢复。

        恢复有个前提——统一主面板没开着。设置界面里用户在搜索框打字同样是「输入」，
        监听器若在这时恢复，会把设置页的输入当成触发词乱弹候选条。
        """
        if visible:
            self.monitor.pause()
        else:
            if not self.unified.isVisible():
                self.monitor.resume()
            # 面板关掉了但「待确认」还挂着 = 用户没采用这次情绪推荐。
            # 给一次很弱的负反馈（见 _learn_emotion_ignored）。
            self._learn_emotion_ignored()

    def _build_tray(self):
        icon = make_icon()
        self.app.setWindowIcon(icon)
        self.tray = QSystemTrayIcon(icon, self.app)
        self.menu = QMenu()
        self.menu.setStyleSheet(
            Theme(resolve_theme_name(self.config.get("theme", "light"))).menu_style())

        show_action = QAction("唤起面板", self.app)
        show_action.triggered.connect(self.window.toggle)
        panel_action = QAction("打开主面板", self.app)
        panel_action.triggered.connect(self.open_unified)
        settings_action = QAction("设置", self.app)
        settings_action.triggered.connect(lambda: self.open_unified("settings"))
        quit_action = QAction("退出", self.app)
        quit_action.triggered.connect(self.app.quit)
        # 日志入口整合进开发者模式：未启用时不可访问。菜单每次弹出时重新判定，
        # 因为用户可能刚在设置里开关过开发者模式。
        log_action = QAction("查看日志", self.app)
        log_action.triggered.connect(self.open_log_viewer)
        log_action.setVisible(self._developer_mode())

        for act in (show_action, panel_action, settings_action, log_action):
            self.menu.addAction(act)
        self.menu.addSeparator()
        self.menu.addAction(quit_action)
        self.menu.aboutToShow.connect(
            lambda: log_action.setVisible(self._developer_mode()))
        self.tray.setContextMenu(self.menu)

        self.tray.setToolTip("颜文字输入辅助器")
        self.tray.show()
        # 插件 UI 能力所需的宿主回调（托盘通知）；托盘此刻才就绪
        self.plugins.set_ui_hooks(
            notify=lambda title, message, ms: self.tray.showMessage(
                title, message, QSystemTrayIcon.MessageIcon.NoIcon, int(ms)))

    def _schedule_startup_hooks(self):
        """投递「欢迎更新」弹窗与插件 startup 事件（顺序敏感）。

        两者都走 `QTimer.singleShot(0)`，按投递顺序执行：先弹更新框，再发 startup
        事件，避免插件在回调里弹窗抢在界面初始化之前、或与更新框抢模态。
        startup 整个生命周期只发一次——中途启用的插件收不到，需重启生效。

        **首次使用的欢迎页排在最前**：它是"第一次接触软件"的仪式，
        要在任何其它弹窗之前出现（否则用户先看到更新提示会莫名其妙）。
        """
        QTimer.singleShot(0, self._maybe_show_welcome)
        QTimer.singleShot(0, self._maybe_show_update)
        QTimer.singleShot(0, lambda: self.plugins.emit("startup"))
        # 自动更新：延迟检查（避开启动高峰），见 core/updater.py
        self._schedule_auto_update()

    def _maybe_show_welcome(self):
        """首次使用引导：介绍用法 + 让用户定下热键和主题。

        用户选的配置会合并进主配置并立即生效（不必再进设置页改一遍）。
        风险提示**不在这里**——它挂在各自功能的入口上，见 ui/risk_dialog.py。
        """
        try:
            from ui.welcome_dialog import WelcomeDialog
            chosen = WelcomeDialog.maybe_show(
                theme_name=resolve_theme_name(self.config.get("theme", "light")),
                config=self.config,
                parent=self.unified)
        except Exception as e:
            append_log("warn", "app", "欢迎页展示失败：%r" % (e,))
            return
        if not chosen:
            return
        # 用户在引导页里改了热键/主题：直接应用，省掉再进设置页的步骤
        merged = dict(self.config)
        merged.update(chosen)
        append_log("info", "app", "首次使用：已应用引导设置 %s" % dict(chosen))
        self.apply_settings(merged)

    def _start_services(self):
        self.hotkey.start(self.config.get("hotkey", HOTKEY_DEFAULT))
        if self.config.get("auto_popup", True):
            self.monitor.start()

    # ------------------------------------------------------------------
    # 自动更新
    # ------------------------------------------------------------------
    def _schedule_auto_update(self):
        """启动后延迟检查更新（每天最多一次）。

        延迟而非立即：启动瞬间要建窗口、加载插件、注册热键，此时发起网络请求
        与用户操作争资源；而且刚开机时用户往往还没准备好被打扰。
        """
        from core import updater

        # 上次更新是否成功（有新版本但版本号没变 = 失败，提示一次）
        self._report_pending_update_result()

        # 关于页的「立即重启并更新」按钮接到本运行时
        page = getattr(self.unified, "about_page", None)
        if page is not None:
            setter = getattr(page, "set_install_callback", None)
            if callable(setter):
                try:
                    setter(self.install_update_now)
                except Exception:
                    pass

        if not updater.is_supported():
            msg = updater.unsupported_reason()
            append_log("info", "update", "自动更新不可用：%s" % msg)
            if page is not None:
                fn = getattr(page, "set_update_unsupported", None)
                if callable(fn):
                    try:
                        fn(msg)
                    except Exception:
                        pass
            return
        if not updater.should_check_now(self.config):
            return

        # ⚠ 不能把 self（AppRuntime）当 parent 传进去：AppRuntime 是普通
        # Python 类，不是 QObject，QObject.__init__ 会直接抛 TypeError。
        # 这个错误只在打包后的 exe 里暴露（源码态那条路径恰好没走到），
        # 一度导致程序启动即崩。见 tests/test_runtime_wiring.py。
        self.updater = updater.AutoUpdater(self.config)
        self.updater.update_available.connect(self._on_update_available)
        self.updater.up_to_date.connect(
            lambda v: append_log("info", "update", "已是最新版本 %s" % v))
        self.updater.error.connect(
            lambda m: append_log("warn", "update", m))
        self.updater.progress.connect(self._on_update_progress)
        self.updater.ready_to_install.connect(self._on_update_ready)
        self.updater.installing.connect(self._on_installing)

        QTimer.singleShot(
            int(updater.STARTUP_DELAY_SEC * 1000), self._check_update_quietly)

    def _check_update_quietly(self):
        if getattr(self, "updater", None) is None:
            return
        append_log("info", "update", "开始检查更新…")
        self.updater.check()

    def _on_update_available(self, version, release):
        """发现新版本：静默下载（用户不必等）。"""
        append_log("info", "update", "发现新版本 %s，开始下载" % version)
        self._update_version = version
        self.updater.download()

    def _on_update_progress(self, pct):
        # 进度只在关于页展示，不打扰用户
        page = getattr(self.unified, "about_page", None)
        fn = getattr(page, "set_update_progress", None)
        if callable(fn):
            try:
                fn(int(pct))
            except Exception:
                pass

    def _on_update_ready(self, version, path):
        """下载并校验完成：通知用户（可立即重启，也可退出时自动装）。"""
        append_log("info", "update",
                   "新版本 %s 已下载并校验通过，退出程序时会自动安装" % version)
        page = getattr(self.unified, "about_page", None)
        fn = getattr(page, "set_update_ready", None)
        if callable(fn):
            try:
                fn(version)
            except Exception:
                pass
        try:
            if self.tray is not None:
                self.tray.showMessage(
                    "更新已就绪",
                    "新版本 %s 已下载。关闭程序时自动安装，或在「关于」页立即重启更新。"
                    % version,
                    QSystemTrayIcon.Information, 6000)
        except Exception:
            pass

    def _on_installing(self, version):
        append_log("info", "update", "正在安装 %s 并重启…" % version)

    def install_update_now(self):
        """关于页「立即重启更新」：安装后退出当前进程。"""
        if getattr(self, "updater", None) is None:
            return False
        ok = self.updater.install_now(append_log)
        if ok:
            self.app.quit()
        return ok

    def _install_update_on_exit(self):
        """退出时安装已下载的更新（对用户最无感）。"""
        try:
            if getattr(self, "updater", None) is not None \
                    and self.updater.has_pending_package():
                self.updater.install_on_exit(append_log)
        except Exception as e:
            append_log("warn", "update", "退出时安装更新失败：%r" % (e,))

    def _report_pending_update_result(self):
        """上次更新若未成功，提示一次（不重复安装，避免死循环）。"""
        from core import updater_install
        from core.version import get_build_version

        try:
            state, ver = updater_install.check_pending_result(get_build_version())
        except Exception:
            return
        if state == "ok":
            append_log("info", "update", "已成功更新到 %s" % ver)
            try:
                if self.tray is not None:
                    self.tray.showMessage(
                        "更新完成", "已更新到 %s" % ver,
                        QSystemTrayIcon.Information, 4000)
            except Exception:
                pass
        elif state == "failed":
            append_log("warn", "update",
                       "上次更新到 %s 未成功（可能被取消或文件被占用）" % ver)
            updater_install.clear_pending()

    def _install_quit_hooks(self):
        q = self.app.aboutToQuit
        q.connect(self.hotkey.stop)
        q.connect(self.monitor.stop)
        q.connect(self.window.shutdown)
        # 退出时若有已下载待安装的更新，顺手装上（下次启动即为新版）。
        # 放在 flush 之前：安装脚本要等进程真正退出才开始替换文件，
        # 这里只是"启动脚本"，不影响随后的收尾。
        q.connect(self._install_update_on_exit)
        q.connect(self.state.flush)
        q.connect(self.user_kao.flush)
        q.connect(self.triggers.flush)
        # 情绪网络的在线学习结果：退出前落盘（否则这次会话学到的权重全丢）
        q.connect(self._flush_emotion_model)
        # 插件 shutdown 事件：主程序退出前通知（emit 内部已逐个隔离异常）
        q.connect(lambda: self.plugins.emit("shutdown"))
        # 先关掉插件可能残留的弹窗，再走各自的 flush：避免退出时弹窗回调打到已销毁对象
        q.connect(self.plugins.close_plugin_dialogs)
        q.connect(lambda: append_log("info", "app", "应用程序退出。"))

    # ------------------------------------------------------------------
    # 配置应用
    # ------------------------------------------------------------------
    def apply_settings(self, new_cfg):
        """配置变更的唯一落盘 + 广播入口。

        按「真正变化的键」分流：QSS 重贴、注册表写入、热键重注册都只在对应键
        变化时才做。全量重刷实测 ≈900ms，其中 app 级 QSS ≈500ms、统一窗口整窗
        重铺 ≈340ms —— 全部无条件执行的话，改任何一项设置都会卡掉近一秒。
        """
        old = dict(self.config)
        # 原地更新：config 这个 dict 的身份必须保持不变——PluginManager 持有引用，
        # 替换对象会让它继续读写一份已被丢弃的旧字典。
        self.config.clear()
        self.config.update(new_cfg)
        changed = {k for k, v in self.config.items() if old.get(k) != v}
        changed |= {k for k in old if k not in self.config}

        try:
            save_config(self.config)
        except Exception as e:
            # 磁盘只读 / 被占用 / 目录不可写时，不能因为落盘失败就中断整个「应用设置」：
            # 内存配置已更新，先让各项功能按新值生效，写盘失败单独记一条日志。
            append_log("error", "config", "配置落盘失败：%r" % (e,))

        if "autostart" in changed and autostart.is_supported():
            # 开启 UIA 提权时，自启动要改用「计划任务」才能真正带管理员权限；
            # 否则每次开机启动后还得再弹一次 UAC（本功能就是要避免这个）。
            want = bool(self.config.get("autostart", False))
            use_task = bool(self.config.get("use_uia_elevation", False))
            ok, msg = autostart.set_autostart(want, use_task=want and use_task)
            append_log("info" if ok else "warn", "autostart", msg)

        effective_theme = resolve_theme_name(self.config.get("theme", "light"))
        # 幂等：样式串没变就不重贴（详见 _apply_app_theme 注释）
        self._apply_app_theme(effective_theme)
        set_app_theme(self.config.get("theme", "light"))
        if "theme" in changed:
            # 已打开的日志窗口同步换肤（否则它会停留在启动时的主题）
            self._theme_log_viewer(effective_theme)
            self._update_system_theme_watcher()

        # ---------- 按"变化的内容"决定刷新广度 ----------
        # 全量刷新实测 ≈320ms（unified.apply_config 160 + settings 刷新 152），
        # 拖动不透明度滑块松手就走一遍会明显卡顿。但改 alpha 根本不需要
        # 重贴 QSS、也不需要重建设置页控件——只需要重绘。
        visual_only = changed <= _LIGHT_CHANGE_KEYS

        self.window.apply_config(self.config)
        if visual_only:
            # 透明/不透明度这类只影响绘制的改动：全窗口 update() 即可。
            # unified 内部会重新计算基底色，paintEvent 下一帧就反映新值。
            self.unified.refresh_visuals()
        else:
            self.unified.apply_config(self.config)
        self.plugins.set_theme_name(effective_theme)
        self.state.max_recent = int(self.config.get("max_recent", 30))
        self.plugins.emit("config_changed", self.config)
        # 情绪推荐：把「开关 / 灵敏度 / 是否学习」同步给分类器，实时生效
        self._apply_emotion_config()

        if "hotkey" in changed:
            # 录制完新热键必须立刻重注册，否则新组合键要等下次启动才生效。
            self._reload_hotkey()

        # 只有「用户本次主动把 UIA 提权开关打开」才做 UIA 检查。
        # 原先每次 apply_settings 都调一次，既白跑一遍 native 调用，
        # 又可能弹出模态提示框卡住界面（改个不透明度被 UIA 弹窗拦下）。
        uia_turned_on = ("use_uia_elevation" in changed
                         and bool(self.config.get("use_uia_elevation", False)))
        if uia_turned_on:
            self._handle_uia_status(allow_elevate=True)
        elif "use_uia_elevation" in changed:
            # 关掉时也要检查（要从 UIAccess 令牌重启回普通权限）
            self._handle_uia_status(allow_elevate=False)
        self._sync_auto_popup()

    def _reload_hotkey(self):
        """按当前配置重新注册全局热键。"""
        if getattr(self.unified.settings_page, "_capturing", False):
            # 正在录制：此时注册会把刚录的组合键当场吃掉，等录制结束后的恢复流程处理
            return
        self.hotkey.start(self.config.get("hotkey", HOTKEY_DEFAULT))

    def _handle_uia_status(self, allow_elevate=False):
        """开启了 UIA 提权但本次没生效时，给出一次性提示。

        :param allow_elevate: 是否允许弹 UAC 申请管理员权限。
            **只在用户主动开启该功能时传 True**——启动期必须传 False，
            否则每次开机都会弹 UAC，这与"开机自启自动提权"的目标相反。
        """
        status = uia_elevation.ensure_uiaccess(
            self.config, append_log, allow_elevate=allow_elevate)
        if not self.config.get("use_uia_elevation", False):
            return
        if status.get("granted"):
            return
        # 已经弹过 UAC 并以管理员重启：旧进程必须退出，否则会有两个实例
        # （新实例会继续走 UIAccess 令牌流程）。
        if status.get("relaunched") and status.get("elevate_requested"):
            self._quit_for_elevation()
            return
        if status.get("relaunched"):
            return
        self._warn_uia_once(status)

    def _quit_for_elevation(self):
        """UAC 提权已成功启动新实例，优雅退出当前进程。"""
        try:
            append_log("info", "uia_elev",
                       "已以管理员身份启动新实例，当前进程退出")
        except Exception:
            pass
        try:
            # 先把插件与各类 store 收尾，避免半写文件
            self.plugins.emit("shutdown")
            for obj in (self.state, self.user_kao, self.triggers):
                flush = getattr(obj, "flush", None)
                if callable(flush):
                    flush()
        except Exception:
            pass
        self.app.quit()

    def _warn_uia_once(self, uia_status):
        """「已开启 UIA 提权但本次未生效」只提示一次。

        必须用 self._uia_warn_shown（而不是函数内局部变量）：原先是模块级标记 +
        函数内赋值却漏写 global，`if not uia_warn_shown` 会抛 UnboundLocalError，
        异常从 apply_settings 中途抛出，把它后面的「自动弹出监听启停」整段跳过，
        表现为开过 UIA 提权之后改自动弹出开关不生效。

        ⚠ **用非模态弹窗**：`QMessageBox.warning()` 会开一个嵌套事件循环，
        在 `apply_settings` 调用链里会卡住整个界面（用户感觉"点一下卡住"）。
        改用 `open()` + `setAttribute(WA_DeleteOnClose)`，立即返回。
        """
        if self._uia_warn_shown:
            return
        self._uia_warn_shown = True
        try:
            box = QMessageBox(
                QMessageBox.Warning, "UIA 提权",
                uia_status.get("message") or "UIA 提权未生效，请查看日志。",
                QMessageBox.Ok, self.unified)
            box.setAttribute(Qt.WA_DeleteOnClose, True)
            box.open()
        except Exception:
            pass

    def _sync_auto_popup(self):
        """按「自动弹出」开关启停监听；统一窗口仍打开时保持暂停。"""
        if self.config.get("auto_popup", True):
            self.monitor.start()
            if self.window.isVisible() or self.unified.isVisible():
                self.monitor.pause()
        else:
            self.monitor.stop()

    def _preview_picker_opacity(self, value):
        """候选条不透明度实时预览（拖动滑块时即时生效，落盘仍走 apply_settings）。"""
        try:
            if getattr(self.window, "_panel_bg", None) is not None:
                self.window._panel_bg.setAlphaF(max(0.3, min(1.0, value)))
                self.window.repaint()
        except Exception:
            pass

    def _refresh_valid_items(self):
        self.state.valid_items = set(_valid_item_pool(
            self.data, self.user_kao, self.triggers))

    def _on_plugins_changed(self):
        self.plugins.refresh_sources(self.data)
        self._refresh_valid_items()

    def _developer_mode(self):
        return bool(self.config.get("developer_mode", False))

    # ------------------------------------------------------------------
    # 主题跟随
    # ------------------------------------------------------------------
    def _check_system_theme(self):
        """轮询系统深浅色；仅在配置为「跟随系统」时生效。"""
        if self.config.get("theme", "light") != "system":
            return
        current = system_theme_name()
        if current == self._last_system_theme:
            return
        self._last_system_theme = current
        effective = resolve_theme_name("system")
        self.app.setStyleSheet(Theme(effective).style_sheet())
        if self.menu is not None:
            self.menu.setStyleSheet(Theme(effective).menu_style())
        set_app_theme("system")
        if self.window is not None:
            self.window.apply_config(self.config)
        if self.unified is not None:
            self.unified.apply_config(self.config)
        self._theme_log_viewer(effective)

    def _update_system_theme_watcher(self):
        if self.config.get("theme", "light") == "system":
            if not self._system_theme_timer.isActive():
                self._last_system_theme = system_theme_name()
                self._system_theme_timer.start()
        else:
            self._system_theme_timer.stop()

    def _theme_log_viewer(self, theme_name):
        """给开着的日志窗口换肤（销毁过/从没开过则跳过）。"""
        w = self._log_viewer
        if w is not None and shiboken_is_valid(w):
            w.set_theme(theme_name)

    # ------------------------------------------------------------------
    # 注入链路
    # ------------------------------------------------------------------
    def on_selected(self, text, ctx=None):
        """候选/库页/搜索页选中颜文字后的统一上屏入口。"""
        method = self.config.get("input_method", "clipboard")
        ctx = dict(ctx or {})
        # 插件输出转换：上屏前的文本改写（priority 小者先执行；
        # 单个插件异常已在 run_transforms 内部隔离，不会阻断注入）
        text = self.plugins.run_transforms(text, ctx)
        # 先告知监听器「这段文本是我们自己送进去的」：进入静默期 + 后续采样时剔除，
        # 否则颜文字里的 "?"「哇」等字符会被当成用户新输入，导致上屏后又弹一次
        self.monitor.notify_injected(text)
        self.bus.log("注入", "主程序", "注入颜文字：%s（方式=%s）" % (text, method))
        self.plugins.emit("before_insert", text, ctx)

        trigger_word = ctx.get("trigger_word") or ""
        if ctx.get("delete_trigger") and trigger_word:
            self._apply_trigger_with_delete(text, method, trigger_word)
        else:
            # 延迟一拍，确保面板隐藏、焦点已回到原窗口后再注入文本
            QTimer.singleShot(
                0, lambda: self.injector.inject(text, method))
        self.plugins.emit("after_insert", text, ctx)
        # 使用统计（任何来源的插入都在此汇聚）：放到注入调度之后并兜底捕获异常，
        # 确保统计逻辑的任何意外都不会阻断注入（曾导致「剪贴板模式失效」）
        try:
            self.state.record_usage(text)
        except Exception:
            pass
        # 在线学习：用户选了某情绪下的颜文字 = 给了一次"情绪判断正确"的反馈。
        # 用这一次样本对网络做一次 SGD 更新（约 0.3ms），逐渐贴合这个用户的表达习惯。
        self._learn_emotion(text, ctx)

    def _on_emotion_detected(self, emotion, source_text=""):
        """情绪命中：受 `emotion_suggest` 开关控制，并记录"已展示待确认"。

        记录是为了支持"用户看到了但没选"的弱反馈（见 `_learn_emotion_ignored`）。
        """
        if not self._emotion_suggest_enabled():
            return
        self._pending_emotion = (emotion, source_text)
        self.window.show_for_emotion(emotion, source_text)

    def _learn_emotion_ignored(self):
        """面板被关掉时，如果情绪推荐没被采用，给一次很弱的负反馈。

        只在"弹了、没选、又关掉了"这三件事都成立时触发，
        且学习率极低（见 `EmotionClassifier.learn_ignored`）——
        用户可能只是没找到合适的，不该被当成"判错了"重罚。
        """
        pending = getattr(self, "_pending_emotion", None)
        if not pending:
            return
        self._pending_emotion = None
        try:
            from core import emotion_classifier
            clf = emotion_classifier.shared()
            if clf.learning_enabled:
                clf.learn_ignored(pending[1], pending[0])
                clf.maybe_autosave(every=20)
        except Exception:
            pass

    def _apply_emotion_config(self):
        """把情绪推荐相关配置同步到分类器与监听器。

        三个键各管一件事：
          * `emotion_suggest`     —— 关掉后完全不按情绪推荐（热键唤起作用照旧）
          * `emotion_sensitivity` —— 0~100，换算成置信度阈值
          * `emotion_learning`    —— 关掉后不再根据用户选择调整模型
        """
        try:
            from core import emotion_classifier
            clf = emotion_classifier.shared()
            clf.configure(
                sensitivity=self.config.get("emotion_sensitivity", 50),
                learning_enabled=bool(self.config.get("emotion_learning", True)),
                ai_enabled=bool(self.config.get("emotion_use_ai", True)),
            )
        except Exception as e:
            append_log("warn", "emotion", "同步情绪配置失败：%r" % (e,))

    def _emotion_suggest_enabled(self):
        return bool(self.config.get("emotion_suggest", True))

    def _learn_emotion(self, text, ctx):
        """把「用户选了这张颜文字」当作情绪反馈，增量更新网络。

        **什么算有效反馈**：候选条是因为某个情绪弹出的（`ctx["emotion"]`），
        用户从中选了一张——说明那一刻他确实想表达这个情绪。这是最强的信号。

        其它来源（手动唤起、搜索页、库页）不带情绪上下文，不能当作情绪标签，
        否则会把噪声教给网络。

        学完每 20 次落盘一次；应用退出时再 flush 一次。
        """
        try:
            emotion = (ctx or {}).get("emotion")
            if not emotion:
                return
            sample = (ctx or {}).get("source_text") or ""
            if not sample:
                return
            from core import emotion_classifier
            clf = emotion_classifier.shared()
            if clf.learn_from_choice(sample, emotion):
                clf.maybe_autosave(every=20)
                # 用户采用了这次推荐：清掉"待确认"，避免被当成忽略
                self._pending_emotion = None
        except Exception as e:
            append_log("warn", "emotion", "在线学习失败：%r" % (e,))

    def _flush_emotion_model(self):
        """退出前把增量学到的权重落盘。"""
        try:
            from core import emotion_classifier
            clf = emotion_classifier.shared()
            if clf.save_user_model():
                append_log("info", "emotion", "已保存情绪模型的个性化学习结果")
        except Exception:
            pass

    def _cursor_tail_equals(self, word):
        """最佳努力确认光标左侧「恰好是触发词」，避免误删其它内容。

        原生编辑框走 WM_GETTEXT（亚毫秒）；浏览器 / Electron 走 UI Automation（带超时）。
        任一读取失败或结尾不匹配都返回 False —— 此时保守地不删除、只注入，
        宁可保留触发词，也绝不误删用户已写下的其它文字。
        """
        if not word:
            return False
        n = len(word)
        try:
            text = win_utils.get_focused_text(n)
            if text and text[-n:] == word:
                return True
        except Exception:
            pass
        if uia_text is not None:
            try:
                text = uia_text.get_focused_text(n, timeout=0.15)
                if text and text[-n:] == word:
                    return True
            except Exception:
                pass
        return False

    def _apply_trigger_with_delete(self, text, method, trigger_word):
        """先安全删掉触发词，再注入颜文字；任何不确定都只注入不删除。"""
        n = len(trigger_word)

        def _do():
            if self._cursor_tail_equals(trigger_word):
                # 必须在「注入颜文字之前」删除：删完后光标回到触发词起点，
                # 再注入颜文字，最终只留下颜文字本身。反过来先注入再退格，
                # 退格会吃掉颜文字尾部字符，造成乱码。
                self.injector.delete_chars(n)
                QTimer.singleShot(
                    max(80, n * 12),
                    lambda: self.injector.inject(text, method))
            else:
                # 结尾已变化 / 无法确认：保守只注入，不删除
                self.injector.inject(text, method)

        QTimer.singleShot(0, _do)     # 等面板完全隐藏、焦点回到原窗口

    # ------------------------------------------------------------------
    # 窗口开关
    # ------------------------------------------------------------------
    def open_unified(self, page_key=None):
        """打开统一主面板（可指定页面）。

        **不动全局热键**。旧实现这里调 pause_global_hooks() 无条件 hotkey.stop()，
        而恢复只挂在 unified.finished（窗口 close 才发）上——用户只是打开设置页、
        再关掉（hide）时恢复永远不触发，热键就此永久失效，必须重启程序才能用。
        正确语义是：热键全程可用；只有「录制热键」期间临时停用（见
        _on_hotkey_capture），因为录制时按下的正是热键本身。
        """
        if self.window.isVisible():
            # 用 hide_now 而不是 dismiss：这里要求隐藏“同步完成”，
            # 因为紧跟其后的 monitor.pause() 依赖 hide 触发的 resume 已经跑完。
            self.window.hide_now()
        # 注意：hide_now() 会经 isVisibleChanged(False) → monitor.resume() 把监听
        # 重新打开，因此 monitor.pause() 必须放在 hide 之后，否则暂停会被刚才的
        # resume 抵消（仅在候选条当时正显示时触发），导致面板打开期间监听仍在跑。
        self.monitor.pause()
        if page_key:
            self.unified._set_page(page_key)
        self.unified.show()
        self.unified.raise_()
        self.unified.activateWindow()

    def _on_hotkey_capture(self, active):
        """设置页录制热键期间临时停用/恢复全局热键。

        录制时用户按下的就是即将绑定的组合键，热键若仍注册着，每按一次都会把
        候选条弹出来盖住设置页，同时也更容易和 pynput 抢同一组按键。
        """
        if active:
            self.hotkey.stop()
        else:
            self.hotkey.start(self.config.get("hotkey", HOTKEY_DEFAULT))

    def resume_global_hooks(self):
        """统一窗口关闭后恢复自动弹出监听。

        热键在打开窗口期间从未停用（见 open_unified），这里只管监听；
        若此时正好还在录制热键，则保持停用状态等录制流程收尾。
        """
        if self.config.get("auto_popup", True) and not getattr(
                self.unified.settings_page, "_capturing", False):
            self.monitor.resume()

    def _on_unified_output(self, text):
        """统一窗口内的搜索/库页选中颜文字：隐藏窗口、注入文本。"""
        self.unified.hide()
        # 先通知监听器注入，再恢复监听，避免把注入内容误判为用户输入
        self.on_selected(text)
        self.resume_global_hooks()

    def open_log_viewer(self):
        """打开（或复用）日志窗口。"""
        name = resolve_theme_name(self.config.get("theme", "light"))
        w = self._log_viewer
        # 关闭时窗口自带 WA_DeleteOnClose，destroyed 会把引用置空；这里再兜一层
        # 有效性判断，防止极端时序下拿到已销毁的 C++ 对象。
        if not (w is not None and shiboken_is_valid(w)):
            w = show_log_viewer(LOG_BUFFER, parent=self.unified, theme_name=name)
            self._log_viewer = w
            w.destroyed.connect(self._forget_log_viewer)
        else:
            w.set_theme(name)
        w.show()
        w.raise_()
        w.activateWindow()
        return w

    def _forget_log_viewer(self):
        self._log_viewer = None

    def _maybe_show_update(self):
        try:
            from ui.update_dialog import UpdateDialog
            UpdateDialog.maybe_show(
                parent=self.unified,
                theme_name=resolve_theme_name(self.config.get("theme", "light")))
        except Exception as e:
            append_log("warn", "update", "更新弹窗失败：%s" % e)


def create_runtime(app, config=None):
    """按当前磁盘配置构建运行时（供入口与测试共用）。"""
    return AppRuntime(app, config if config is not None else load_config())
