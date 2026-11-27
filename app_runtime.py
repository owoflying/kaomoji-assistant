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
from PySide6.QtWidgets import QMessageBox, QSystemTrayIcon

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
    resolve_theme_name, set_app_theme, system_theme_name,
    resolved_mode_of,
)
from ui.theme_manager import build_theme
from ui.theme_controller import ThemeController

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
    "panel_alpha",   # 面板不透明度：只影响 paintEvent 的合成图
    "opacity",       # 候选条不透明度：只影响窗口透明度
    # ---- 背景材质参数 ----
    #
    # 这些只影响窗口 `paintEvent` 的合成图，**不需要**重贴 QSS、
    # 也不需要重建设置页控件。之前它们落不进这个集合，于是调一下
    # 材质走的是全量刷新（重贴 321 条 QSS 规则 + 刷所有子页，
    # 实测 ≈320ms 起），表现为"切材质卡好久"。
    "material_enabled",
    "material_type",
    "material_strength",
    "material_saturate",
    "material_brightness",
    "material_dim",
    "material_image_opacity",
})


def _glass_enabled(config):
    """材质开启 → 控件用玻璃样式（底色透、边框高光）。

    单一判断点：材质到底有没有实际内容（开关 + 背景图/材质类型），
    由 MaterialComposer 统一回答，避免各窗口各判一套。
    """
    try:
        from ui.material_composer import MaterialComposer

        return MaterialComposer(config or {}, lambda: None).enabled()
    except Exception:
        return False


def _apply_tooltip_palette(app, theme):
    """把主题交给自绘 tooltip（并保留调色板兜底）。

    ## 为什么最后改成完全自绘

    原生 `QToolTip` 在 Windows 上有两个治不好的毛病，前前后后试了三种方案：

    1. **QSS 的 `QToolTip {{}}` 规则不生效** —— 它是原生弹出窗口，
       Qt 绘制时优先取 QPalette，QSS 被跳过。改 QPalette 能救文字，
       但救不了背景。

    2. **系统阴影变成黑块** —— 实测枚举窗口能看到：

           Qt6111QWindowToolTipDropShadowSaveBits   423x33
           SysShadow                                428x38

       `SysShadow` 是 Windows 给带 `CS_DROPSHADOW` 风格的窗口画的系统阴影。
       Qt 的 tooltip 默认带这个风格，同时又设半透明背景，两边打架，
       阴影就变成**一条纯黑块**露在提示框外面。

       试过用事件过滤器在显示后补 `Qt.NoDropShadowWindowHint`——**无效**，
       窗口类风格在创建时就定了，改 flag 不会让 Windows 收回阴影。

    所以最终方案是 `ui/fluent_tooltip.py` 的**完全自绘气泡**：
    纯 Qt 绘制、无边框、无系统阴影，颜色 100% 由主题决定。
    事件过滤器会吃掉原生 tooltip（返回 True），业务代码的
    `setToolTip(...)` 一行都不用改。

    ## 这里还要设 palette 吗

    要。有些老路径（比如插件里自己 new 的 QToolTip）可能绕过过滤器；
    设了调色板至少不会出现"黑底黑字"这种完全看不见的情况。
    """
    try:
        from PySide6.QtGui import QColor, QPalette
        from core.theme_presets import parse_color

        pal = app.palette()
        bg = parse_color(theme.card) or (255, 255, 255, 1.0)
        fg = parse_color(theme.text) or (0, 0, 0, 1.0)
        pal.setColor(QPalette.ToolTipBase,
                     QColor(int(bg[0]), int(bg[1]), int(bg[2])))
        pal.setColor(QPalette.ToolTipText,
                     QColor(int(fg[0]), int(fg[1]), int(fg[2])))
        # ⚠ 只改 ToolTipBase / ToolTipText，**不要**动 Window / WindowText：
        # 那两个是全局窗口色，改了会影响所有没写样式的原生窗口
        # （包括文件对话框、消息框），副作用远大于收益。
        app.setPalette(pal)
    except Exception:
        # 调色板设置失败不该影响主题切换本身
        pass

    # 装自绘 tooltip（幂等）。provider 每次取**当前**主题对象，
    # 所以主题切换后新弹出的提示自动是新配色，不用重新安装。
    try:
        from ui.fluent_tooltip import install_tooltips

        install_tooltips(app, lambda: getattr(app, "_kao_theme_obj", None)
                         or theme)
        app._kao_theme_obj = theme
    except Exception:
        pass


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

        # ---- 崩溃兜底（始终开启）----
        #
        # 异常不再静默：写日志 + 弹窗告知 + 可导出诊断报告。
        # 这里注入弹窗回调（无头/测试环境里 UI 层可能还没准备好，
        # 回调内部会兜住异常，不影响安装）。
        self._install_crash_guard()
        # 动画开关集中读取（core/anim_settings 缓存配置引用）
        try:
            from core import anim_settings

            anim_settings.install(self.config)
        except Exception:
            pass

        self._uia_warn_shown = False
        #: 情绪推荐"已展示待确认"：(emotion, source_text)。
        #: 面板关闭时若它还在，说明用户没采用这次推荐 → 弱负反馈。
        self._pending_emotion = None
        #: 自动更新的延迟检查定时器（可被"关闭自动更新"取消）
        self._auto_update_timer = None
        self._log_viewer = None
        # 上一次真正贴到 QApplication 上的样式串，**仅用于幂等短路**。
        # ⚠ 它不再代表"当前主题是什么"——那是 theme_ctrl 的职责。
        self._applied_style = None
        #: 主题唯一权威源。所有窗口从这里取主题，不要自己 build/cache。
        self.theme_ctrl = ThemeController(self.config)
        self.theme_ctrl.changed.connect(self._on_theme_changed)
        # 让主线程槽能取到排队中的弹窗回调（见 _dispatch_to_main）
        self.theme_ctrl._dispatch_owner = self

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

    def _on_theme_changed(self, _palette=None):
        """ThemeController 广播：把新主题应用到所有窗口。

        ## 这是**唯一**的换肤入口

        重构前有三条互不相同的路径（正式 / 预览 / 系统跟随），
        各自手动改 `app`、`unified.theme`、`window._theme_obj`——
        状态必然不同步，用户看到的"切换自定义主题有概率生效"就是这么来的。

        现在所有变更（改配置、预览、系统强调色变化）都收敛到 controller，
        它广播一次，这里统一重绘。预览和正式只是 controller 内部
        数据来源不同，对外的重绘流程**完全一致**。

        ⚠ 带重入保护：换肤动画是分帧跑的，期间不应该再触发一轮。
        """
        if getattr(self, "_in_theme_broadcast", False):
            return
        ctrl = getattr(self, "theme_ctrl", None)
        if ctrl is None:
            return
        self._in_theme_broadcast = True
        try:
            theme = ctrl.current()
            self.theme_obj = theme
            self._apply_theme_object(theme)

            # 候选条：让它重新取主题（内部会从 controller 拿）
            try:
                if self.window is not None:
                    self.window.apply_config(self.config)
            except Exception as e:
                append_log("warn", "theme", "候选条换肤失败：%r" % (e,))

            # 主窗口：走它自己的应用链路（含动画判定）
            try:
                if self.unified is not None:
                    self.unified.apply_config(self.config)
            except Exception as e:
                append_log("warn", "theme", "主窗口换肤失败：%r" % (e,))

            # 日志窗口（可能开着）—— 传完整主题，才能带上配色预设
            self._theme_log_viewer(theme=theme)

            # 托盘右键菜单：独立 QMenu 不继承全局 QSS，必须显式刷新
            self._theme_tray_menu(theme)

        finally:
            self._in_theme_broadcast = False

    def _preview_theme_palette(self, cfg=None, palette=None):
        """主题编辑器的实时预览：按编辑中的色板重绘，**不落盘**。

        :param cfg: 忽略（保留参数只为兼容旧的注入签名）；
            主题状态已由 ThemeController 统一持有
        :param palette: 编辑中的色板 `{"name","light","dark"}`；
            传 None 表示退出预览，回到已保存的主题

        ## 重构后的实现

        原来这里手动拼了一堆：改全局 QSS、改 `unified.theme`、
        置空 `window._theme_obj` 再 apply——三个窗口三套写法，
        必然不同步（用户看到"切换自定义主题有概率生效"）。

        现在只做一件事：告诉 controller"进入/退出预览"。
        它内部的 `rebuild()` 走的是和正式应用**完全相同**的路径，
        所有窗口通过订阅统一收到通知。

        ## 为什么不落盘

        `apply_settings` 会写磁盘、触发换肤动画、广播给所有窗口——
        拖动调色板时每秒几十次，会把磁盘和界面都打爆。
        预览只更新内存态并重绘，保存时才走 `apply_settings`。
        """
        ctrl = getattr(self, "theme_ctrl", None)
        if ctrl is None:
            return
        try:
            if palette is None:
                ctrl.clear_preview()
            else:
                ctrl.set_preview(palette)
        except Exception as e:
            append_log("warn", "theme", "主题预览失败：%r" % (e,))

    def _refresh_material(self):
        """背景材质参数变化后重绘主窗口。

        ## 为什么只重绘主窗口

        背景材质只作用于统一主窗口（卡片列表那种大界面）。候选条和
        各种弹窗保持纯色——它们很小，背景图在上面反而看不清内容，
        而且每多一个窗口就要多算一次模糊。

        ## 为什么要清缓存

        材质 pixmap 按输入做缓存（见 `UnifiedSettingsWindow._background_material`），
        调了强度/换了图之后缓存键会变，自然重算。这里只需触发重绘。
        """
        try:
            if self.unified is not None:
                self.unified.update()
        except Exception as e:
            append_log("warn", "theme", "背景材质刷新失败：%r" % (e,))

    def _install_crash_guard(self):
        """安装崩溃兜底：异常不静默、程序不退出、可导出报告。

        ## 三层

            1. `core.crash_guard.install()` 捕获 sys / threading 的未捕获异常
            2. 回调里弹 `ui.crash_dialog.CrashDialog`（非阻塞）
            3. 弹窗提供「导出报告」，内含堆栈 + 环境 + 最近日志 + 配置

        同一异常 30 秒内只弹一次（`crash_guard` 内部去重），
        否则循环里的异常会刷屏。
        """
        try:
            from core import crash_guard

            crash_guard.set_alert_callback(self._on_crash_alert)
            # 关键：告诉 crash_guard 怎么把弹窗转回主线程。
            #
            # Qt 控件只能在主线程创建。子线程异常时若直接在异常线程里
            # new QDialog，会触发
            #   QObject::setParent: Cannot set parent, new parent is in
            #   a different thread
            # 进而让整个进程无响应退出——崩溃兜底反而成了崩溃源。
            crash_guard.set_dispatch(self._dispatch_to_main)
            crash_guard.install()
            append_log("info", "app", "崩溃兜底已启用（异常会弹窗提示并记录）")
        except Exception as e:
            append_log("error", "app", "崩溃兜底安装失败：%r" % (e,))

        # ---- 开发者模式：error/exception 级别日志即时弹窗 ----
        #
        # 订阅中央日志流，而不是改每一个 append_log 调用点。
        # 只有 `developer_mode` 打开时才真的弹（普通用户仍只看日志）。
        try:
            from core.applog import subscribe

            self._log_unsubscribe = subscribe(self._on_log_for_dev)
        except Exception:
            pass

    def _on_log_for_dev(self, level, source, message):
        """日志订阅者：开发者模式下把 error / exception 弹出来。

        ## ⚠ 这个函数可能在**子线程**里被调用

        `append_log` 是全局的，任何线程都可能写日志（子线程崩溃时
        `threading.excepthook` 就会写一条 exception 日志）。

        如果在子线程里直接创建 QWidget 弹窗，会触发

            QObject::setParent: Cannot set parent, new parent is in a
            different thread

        实测后果：**进程无响应然后退出**——崩溃兜底自己变成了崩溃源。
        这就是用户报的"子线程异常会导致进程无响应后退出"。

        所以这里必须：
          1. 只读配置（纯 Python，线程安全）
          2. 弹窗交给 `_dev_error_alert`，它内部会调度到主线程
        """
        try:
            if level not in ("error", "exception"):
                return
            if not bool(self.config.get("developer_mode", False)):
                return
            summary = "[%s] %s" % (source or "-", str(message)[:200])
            self._dev_error_alert(summary, str(message))
        except Exception:
            pass

    def _dispatch_to_main(self, fn):
        """把可调用对象调度到 Qt 主线程执行。

        ## 为什么不能在子线程里造 QObject

        第一版我 `new` 一个一次性 QObject 再 `moveToThread(app.thread())`，
        结果反而触发了原本要避免的错误：

            QObject::setParent: Cannot set parent, new parent is in a
            different thread

        因为**对象本身是在子线程创建的**，跨线程设置父子关系就是非法的。
        这等于把问题原样搬了一遍，进程照样无响应退出。

        ## 正确做法

        用一个**主线程已经存在的** QObject 当接收者，通过
        `QMetaObject.invokeMethod(..., Qt.QueuedConnection)` 投递。
        队列连接由 Qt 内部把调用排进**接收者所属线程**的事件队列，
        跨线程调用是合法的。

        接收者用 `self.theme_ctrl`——它由 AppRuntime 在主线程构造，
        生命周期与程序一致，不需要额外管理。回调内容存在
        `self._pending_dispatch` 里，由接收者的槽取走。
        """
        try:
            from PySide6.QtCore import QCoreApplication, Qt

            app = QCoreApplication.instance()
            if app is None:
                fn()
                return
            receiver = getattr(self, "theme_ctrl", None)
            if receiver is None:
                fn()
                return
            # 队列里可能排着多个回调，用列表保序
            pending = getattr(self, "_pending_dispatch", None)
            if pending is None:
                pending = []
                self._pending_dispatch = pending
            pending.append(fn)
            from PySide6.QtCore import QMetaObject

            QMetaObject.invokeMethod(receiver, "_run_pending_dispatch",
                                     Qt.QueuedConnection)
        except Exception:
            # 兜底：直接调用。宁可冒线程风险，也不要让异常彻底消失
            try:
                fn()
            except Exception:
                pass

    def _on_crash_alert(self, summary, detail):
        """崩溃回调：弹窗告知用户（非阻塞）。"""
        try:
            from ui.crash_dialog import show_crash

            parent = self.unified if getattr(self, "unified", None) else None
            # 保存引用，避免被 GC 掉导致窗口一闪而过
            self._crash_dialogs = getattr(self, "_crash_dialogs", [])
            dlg = show_crash(summary, detail, parent=parent)
            if dlg is not None:
                self._crash_dialogs.append(dlg)
                # 只保留最近 3 个引用
                self._crash_dialogs = self._crash_dialogs[-3:]
                append_log("error", "app", "已弹出崩溃提示：%s" % summary)
        except Exception:
            pass

    def _dev_error_alert(self, summary, detail):
        """开发者模式下的即时报错弹窗。

        只有在 `developer_mode` 打开时才弹——普通用户不需要看到
        每一次内部错误的细节（那些仍然写日志）。

        ## ⚠ 必须调度到主线程

        这个方法可能被**子线程**调用（子线程崩溃 → `threading.excepthook`
        → `append_log` → 日志订阅者 → 这里）。Qt 控件只能在主线程创建，
        否则会触发 "Cannot set parent, new parent is in a different thread"，
        进而让进程无响应退出。
        """
        try:
            if not bool(self.config.get("developer_mode", False)):
                return
            from ui.crash_dialog import show_error

            parent = self.unified if getattr(self, "unified", None) else None

            def _show():
                try:
                    from core.crash_guard import _on_main_thread

                    if not _on_main_thread():
                        return
                    self._error_dialogs = getattr(self, "_error_dialogs", [])
                    dlg = show_error(summary, detail, parent=parent)
                    if dlg is not None:
                        self._error_dialogs.append(dlg)
                        self._error_dialogs = self._error_dialogs[-3:]
                except Exception:
                    pass

            self._dispatch_to_main(_show)
        except Exception:
            pass

    def _apply_initial_theme(self):
        """启动时应用主题（含 "system" 解析）。"""
        # controller 在 _build_core 里已按 self.config 初始化过，
        # 这里只需把它算出的主题贴到应用级 QSS 上
        ctrl = getattr(self, "theme_ctrl", None)
        theme = ctrl.current() if ctrl is not None else None
        if theme is None:
            from ui.theme_manager import build_theme

            theme = build_theme(self.config)
        self._apply_theme_object(theme)
        # set_app_theme 供独立弹层（拿不到 config）用
        set_app_theme(theme)

    def _apply_theme_object(self, theme, chunked=False):
        """把一个 Theme 对象贴成应用级 QSS。

        :return: `chunked=True` 时返回生成器，否则返回 None

        ## 这里也是 `theme_changed` 事件的发生点

        **所有换肤路径都必经这里**（首次应用、设置里改、跟随系统、
        换肤动画的分步执行）。之前我把事件挂在 `_on_theme_changed`
        上——那个方法实际上**从来没被调用过**（换肤走的是
        `apply_settings → _apply_theme_with_fade`），插件永远收不到。

        放在入口而不是末尾，是因为这个方法有两条返回路径
        （同步 `return None` 和生成器 `return _steps()`），
        放末尾要写两遍。
        """
        # 通知插件：主题变了（放在真正贴 QSS 之前，
        # 让插件的界面重建能和换肤同一帧完成）
        try:
            if getattr(self, "plugins", None) is not None:
                self.plugins.emit("theme_changed", theme)
        except Exception:
            pass

        ss = theme.style_sheet(glass=_glass_enabled(self.config))
        if ss == self._applied_style:
            return None
        if not chunked:
            self.app.setStyleSheet(ss)
            _apply_tooltip_palette(self.app, theme)
            if self.menu is not None:
                self.menu.setStyleSheet(theme.menu_style(
            glass=getattr(self.menu, "material_enabled", lambda: False)()))
            self._applied_style = ss
            return None

        def _steps():
            self.app.setStyleSheet(ss)
            _apply_tooltip_palette(self.app, theme)
            self._applied_style = ss
            yield                      # 让事件循环跑一轮（遮罩/图标能刷一帧）
            if self.menu is not None:
                self.menu.setStyleSheet(theme.menu_style(
            glass=getattr(self.menu, "material_enabled", lambda: False)()))
            yield

        return _steps()

    def _apply_app_theme(self, theme_name=None, chunked=False, preview=None):
        """按配置重贴应用级 QSS（兼容旧调用点的薄包装）。

        ⚠ 新代码请直接用 `_apply_theme_object(ctrl.current())`——
        主题的唯一真相是 `self.theme_ctrl`，不要绕过它自己 build。
        """
        from ui.theme_manager import build_theme

        ctrl = getattr(self, "theme_ctrl", None)
        if ctrl is not None and preview is None and theme_name is None:
            theme = ctrl.current()
        else:
            theme = build_theme(self.config, mode_override=theme_name,
                                preview_palette=preview)
        self.theme_obj = theme
        return self._apply_theme_object(theme, chunked=chunked)

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
            theme_provider=lambda: resolved_mode_of(self.config),
            # UI 能力注入点：core 只声明协议，实现留在 ui/（见 core/ui_hooks.py）。
            # 托盘尚未创建，notify 由 _build_tray 用 set_ui_hooks 后置补齐。
            ui_hooks=build_ui_hooks(
                theme_provider=lambda: resolved_mode_of(self.config)),
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
        # 推荐排序网络：从磁盘读回权重；没有就用全新网络（输出接近 0.5，
        # blend_weight 为 0，完全不影响排序，见 core/rank_net.py 冷启动说明）
        try:
            net = self.state.load_rank_net()
            if net is not None:
                self.state.attach_rank_net(net, self.data)
        except Exception as e:
            append_log("warn", "rank", "推荐网络加载失败：%r" % (e,))
        self.injector = KaomojiInjector()
        self.bus = DevEventBus()

    def _build_windows(self):
        self.window = PickerWindow(
            self.data, self.config, self.state, self.user_kao)
        # 候选条也从同一个 controller 取主题，不再自己 build/缓存
        self.window._theme_ctrl = self.theme_ctrl
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
            # 注入唯一主题源：主窗口不再自己 build，避免两份状态不同步
            theme_ctrl=self.theme_ctrl,
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
        # Ctrl+R：重复上一条（同一句里想再用一次同一个颜文字）
        w.repeat_last_requested.connect(self.repeat_last_injected)
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

        # ⚠ 设置页的信号**不能在启动时连**：页面改成了懒加载，
        # 这里一访问 `settings_page` 就会立刻把它构造出来，
        # 等于把刚省下的 1.6 秒又花回去。
        #
        # 改为"页面第一次创建时再连"。`unified.page_created` 在每次
        # 物化懒加载页面后发出（见 ui/unified_window._build_pages）。
        self.unified.page_created.connect(self._on_unified_page_created)
        # 如果设置页已经存在（例如用户配置里 developer_mode 导致提前建了），
        # 立刻补连一次。
        if self.unified.has_page("settings"):
            self._connect_settings_page()

        # 数据源变化：用户新增/编辑自定义颜文字、插件启用/停用都要刷新白名单
        self.user_kao.changed.connect(self._refresh_valid_items)
        self.plugins.changed.connect(self._on_plugins_changed)

    def _about_page_if_ready(self):
        """取关于页——**已构造才返回**，没构造返回 None。

        关于页是懒加载的。这里如果直接 `getattr(self.unified, "about_page")`，
        `__getattr__` 会立刻把它构造出来（482ms）——等于把懒加载白做了。

        所以：只在用户已经打开过关于页时才给它更新状态；
        没打开过的话，进页面时会按当前状态重新渲染（页面自己会读 config）。
        """
        try:
            if not self.unified.has_page("about"):
                return None
            return self.unified._page("about")
        except Exception:
            return None

    def _on_unified_page_created(self, key):
        """懒加载页面刚建好——给它补连启动时连不上的信号。"""
        if key == "settings":
            self._connect_settings_page()
        elif key == "about":
            self._connect_about_page()

    def _connect_settings_page(self):
        """把设置页的两个信号接到本运行时。

        ⚠ 必须幂等：页面可能被销毁重建，重复连接会导致回调执行两次。
        用标记属性记住"已经连过这个实例"。
        """
        page = self.unified._page("settings")
        if page is None or getattr(page, "_runtime_connected", False):
            return
        try:
            page.opacity_preview.connect(self._preview_picker_opacity)
            # 录制热键期间临时停用全局热键（其余时间热键始终可用）
            page.hotkey_capture_changed.connect(self._on_hotkey_capture)
            # 主题编辑器的实时预览：直接改样式，不落盘
            page._theme_preview_cb = self._preview_theme_palette
            # 背景材质参数变化：清缓存并重绘（模糊要重算）
            page._material_changed_cb = self._refresh_material
            page._runtime_connected = True
        except Exception as e:
            append_log("warn", "app", "设置页信号连接失败：%r" % (e,))

    def _connect_about_page(self):
        """关于页：注入"立即安装"与"安装指定包"两个回调。"""
        page = self.unified._page("about")
        if page is None or getattr(page, "_runtime_connected", False):
            return
        try:
            setter = getattr(page, "set_install_callback", None)
            if callable(setter):
                setter(self.install_update_now)
            setter2 = getattr(page, "set_install_package_callback", None)
            if callable(setter2):
                setter2(self.install_update_from_package)
            page._runtime_connected = True
        except Exception as e:
            append_log("warn", "app", "关于页回调注入失败：%r" % (e,))

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
        # 托盘菜单用 GlassMenu：**自绘**玻璃底（背景图裁剪 + 模糊 + 釉面高光）。
        # 托盘菜单用**自绘玻璃材质**（背景图裁剪 + 模糊 + 釉面高光）。
        # 与统一主窗口共用一套 MaterialComposer，材质开关/强度跟着主配置走。
        # 深色玻璃的额外压暗在 `GlassMenu._render_glass()` 里按主题处理。
        from ui.glass_menu import GlassMenu

        _t0 = (getattr(self, "theme_obj", None)
               or build_theme(self.config))
        self.menu = GlassMenu(
            config=self.config,
            theme_provider=lambda: self._menu_surface_theme(),
            surface="menu")
        # 若用户给托盘菜单单独配了外观，立刻应用
        self._theme_tray_menu(_t0)

        show_action = QAction("唤起面板", self.app)
        show_action.triggered.connect(self.window.toggle)
        panel_action = QAction("打开主面板", self.app)
        panel_action.triggered.connect(self.open_unified)
        settings_action = QAction("设置", self.app)
        settings_action.triggered.connect(lambda: self.open_unified("settings"))
        # 从剪贴板提取别人用的颜文字
        extract_action = QAction("从剪贴板提取颜文字", self.app)
        extract_action.setToolTip(
            "先在聊天里选中含颜文字的文字并复制，再点这里")
        extract_action.triggered.connect(self.extract_from_clipboard)
        quit_action = QAction("退出", self.app)
        quit_action.triggered.connect(self.app.quit)
        # 日志入口整合进开发者模式：未启用时不可访问。菜单每次弹出时重新判定，
        # 因为用户可能刚在设置里开关过开发者模式。
        log_action = QAction("查看日志", self.app)
        log_action.triggered.connect(self.open_log_viewer)
        log_action.setVisible(self._developer_mode())

        for act in (show_action, panel_action, extract_action,
                    settings_action, log_action):
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
        # startup：窗口已建好、元素表已登记 —— 这是读界面的正确时机。
        #
        # ⚠ 与 setup() 的区别：setup 时窗口还没建，pi.ui.ids() 是空的、
        # pi.ui.exists() 恒为 False（写操作会排队，读不会）。
        # 需要先读原值再改的插件必须等到这里。
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
                theme_name=resolved_mode_of(self.config),
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

        # 关于页的「立即重启并更新」按钮接到本运行时。
        # 页面是懒加载的，这里若不存在就等它创建时由
        # `_on_unified_page_created` 补连（见 _connect_about_page）。
        page = self._about_page_if_ready()
        if page is not None:
            setter = getattr(page, "set_install_callback", None)
            if callable(setter):
                try:
                    setter(self.install_update_now)
                except Exception:
                    pass
            # 「手动检查更新 → 下载完直接安装」也接到本运行时
            setter2 = getattr(page, "set_install_package_callback", None)
            if callable(setter2):
                try:
                    setter2(self.install_update_from_package)
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

        # 用可取消的 QTimer 而不是 singleShot：用户可能在延迟期间
        # 就去设置里关掉了自动更新，那时要能真的取消
        self._auto_update_timer = QTimer(self.app)
        self._auto_update_timer.setSingleShot(True)
        self._auto_update_timer.timeout.connect(self._check_update_quietly)
        self._auto_update_timer.start(int(updater.STARTUP_DELAY_SEC * 1000))

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
        page = self._about_page_if_ready()
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
        page = self._about_page_if_ready()
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
        """上次更新若未成功，提示一次（不重复安装，避免死循环）。

        两个信息来源：
          * `check_pending_result()` 比对**实际版本号**；
          * 脚本写的 `update_failed.txt`（回滚成功/失败都会写，
            内容是给人看的中文说明）。
        """
        from core import updater_install
        from core.version import get_build_version

        # 先处理脚本留下的失败说明（比版本号比对更直接）
        note = ""
        try:
            note = updater_install.read_failure_note()
        except Exception:
            note = ""

        try:
            state, ver = updater_install.check_pending_result(get_build_version())
        except Exception:
            state, ver = "none", ""

        if note:
            append_log("warn", "update", "更新失败：%s" % note)
            try:
                if self.tray is not None:
                    self.tray.showMessage(
                        "更新未完成", note, QSystemTrayIcon.Warning, 8000)
            except Exception:
                pass
            # 让用户在关于页也能看到
            try:
                page = self._about_page_if_ready()
                fn = getattr(page, "set_update_failed_note", None)
                if callable(fn):
                    fn(note)
            except Exception:
                pass
            updater_install.clear_failure_note()

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
            if not note:
                append_log("warn", "update",
                           "上次更新到 %s 未成功（可能被取消或文件被占用）" % ver)
                try:
                    if self.tray is not None:
                        self.tray.showMessage(
                            "更新未完成",
                            "上次更新到 %s 没有成功，当前仍是原版本。"
                            % ver,
                            QSystemTrayIcon.Warning, 8000)
                except Exception:
                    pass
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

        effective_theme = resolve_theme_name(
            self.config.get("theme_mode") or self.config.get("theme", "light"))
        # ⚠ 新主题系统有四个相关键，任何一个变了都要走换肤。
        # 原来只判断 "theme" in changed —— 改预设/强调色时不会重贴 QSS，
        # 用户会以为设置没生效。
        _THEME_KEYS = ("theme", "theme_mode", "theme_preset",
                       "accent_mode", "accent_custom")
        theme_changed = bool(set(_THEME_KEYS) & changed)

        # 把最新配置推给 controller —— 它是主题的唯一真相。
        # controller 内部 rebuild 后会广播，`_on_theme_changed` 负责重绘。
        ctrl = getattr(self, "theme_ctrl", None)
        if ctrl is not None and theme_changed:
            ctrl.set_config(self.config, notify=False)

        # 幂等：样式串没变就不重贴（详见 _apply_theme_object 注释）
        #
        # ⚠ 深色↔浅色切换时，这一整套工作（本函数后续的 app 级 QSS 重贴
        # ≈1 秒 + 主窗口重铺）必须**藏进主窗口的淡出过渡里**，否则用户
        # 会看到界面亮着卡住一两秒。做法：
        #   * 告诉主窗口「先淡出，再执行这个回调」；
        #   * 回调里做完所有换肤动作，主窗口随后自己淡入。
        # 没换主题（改不透明度等）时不做动画，直接应用。
        if theme_changed:
            self._apply_theme_with_fade(effective_theme)
            return

        if ctrl is not None:
            theme_now = ctrl.current()
            self._apply_theme_object(theme_now)
            set_app_theme(theme_now)
        else:
            self._apply_app_theme(effective_theme)
            set_app_theme(getattr(self, "theme_obj", None) or effective_theme)
        # system 跟随定时器的启停也由所有主题键共同决定
        if theme_changed:
            # 已打开的日志窗口同步换肤（否则它会停留在启动时的主题）
            self._theme_log_viewer(
            theme_name=effective_theme,
            theme=getattr(self, "theme_obj", None))
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
        # 自动更新开关变化：开→立即排一次检查；关→取消已排的检查
        if "auto_update" in changed:
            self._on_auto_update_switched()
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

    def _apply_theme_with_fade(self, effective_theme):
        """换主题：把耗时的换肤动作藏进「后退 + 遮罩」的过渡里。

        ## 为什么返回生成器

        换肤分成几步，每步几百毫秒。如果一口气同步跑完，事件循环
        在这 1~2 秒里完全不转——**加载图标会定格不动**，遮罩也静止，
        用户看到一个僵死的界面。

        所以这里写成生成器，每做完一步 `yield` 一次；
        `ThemeFader._run_steps` 会在两次 yield 之间跑一轮事件循环，
        图标的旋转定时器因此能持续触发。

        ⚠ 不能改用后台线程做这些事：Qt 控件只能在主线程碰。
        """

        def _steps():
            self.unified._in_theme_fade = True
            try:
                # ① 应用级 QSS：最大的一块（0.9~1.4 秒）。
                #    分两段贴，中间让出一轮事件循环——否则整段一次贴完，
                #    加载图标在这 1.3 秒里一格都转不动。
                #
                # 主题从 controller 取（唯一真相），不再自己 build。
                ctrl = getattr(self, "theme_ctrl", None)
                theme_now = ctrl.current() if ctrl is not None else None
                if theme_now is not None:
                    self.theme_obj = theme_now
                    chunked = self._apply_theme_object(theme_now, chunked=True)
                    set_app_theme(theme_now)
                else:
                    chunked = self._apply_app_theme(effective_theme, chunked=True)
                    set_app_theme(getattr(self, "theme_obj", None)
                                  or effective_theme)
                if chunked is not None:
                    for _ in chunked:
                        yield
                yield
                # ② 日志窗口（可能开着）
                # 传完整主题：明暗名会丢掉配色预设，日志窗口就跟不上换肤
                self._theme_log_viewer(
                    theme=getattr(self, "theme_obj", None))
                self._update_system_theme_watcher()
                yield
                # ③ 候选条窗口
                self.window.apply_config(self.config)
                yield
                # ④ 主窗口：主题、QSS、子页广播
                self.unified._apply_config_now(self.config)
                yield
                # ⑤ 插件与背景
                self.plugins.set_theme_name(effective_theme)
                self.plugins.emit("config_changed", self.config)
                self.unified._update_backdrop()
                yield
            except Exception as e:
                append_log("error", "theme", "切换主题失败：%r" % (e,))
                import traceback
                traceback.print_exc()
            finally:
                self.unified._in_theme_fade = False

        # 主窗口不可见时（无头/后台）不做动画，直接执行
        fader = getattr(self.unified, "_theme_fader", None)
        if fader is None or not self.unified.isVisible():
            for _ in _steps():
                pass
            return
        fader.run(_steps, dark=(effective_theme == "dark"))

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
        """轮询系统深浅色与系统强调色；只有相关配置开启时才生效。

        ## 为什么要同时盯两个

        `theme_mode=system` 时用户改系统深浅色，界面要跟着变；
        `accent_mode=system` 时用户在 Windows 设置里换了个强调色，
        按钮/选中态也要跟着变。

        两者独立，所以定时器在任一模式开启时都要跑。用"上次签名"
        比较，避免每 2 秒无谓地重贴 QSS（那东西一次 0.4~1 秒）。
        """
        from ui.theme_manager import system_accent_color

        need_mode = (self._theme_mode() == "system")
        need_accent = (self.config.get("accent_mode") == "system")
        if not need_mode and not need_accent:
            return

        current = system_theme_name() if need_mode else None
        accent = system_accent_color() if need_accent else None
        sig = (current, accent)
        if sig == getattr(self, "_last_system_signature", None):
            return
        self._last_system_signature = sig
        if need_mode:
            self._last_system_theme = current

        theme = build_theme(self.config, mode_override=current)
        self.theme_obj = theme
        self.app.setStyleSheet(
            theme.style_sheet(glass=_glass_enabled(self.config)))
        if self.menu is not None:
            self.menu.setStyleSheet(theme.menu_style(
            glass=getattr(self.menu, "material_enabled", lambda: False)()))
        set_app_theme(theme)
        if self.window is not None:
            self.window.apply_config(self.config)
        if self.unified is not None:
            self.unified.apply_config(self.config)
        self._theme_log_viewer(
                theme_name=current or resolved_mode_of(self.config),
                theme=getattr(self, "theme_obj", None))

    def _theme_mode(self):
        """当前配置的明暗模式（兼容旧 theme 键）。"""
        return (self.config.get("theme_mode")
                or self.config.get("theme", "light"))

    def _update_system_theme_watcher(self):
        # 任一"跟随系统"开启就要轮询：明暗变了、或系统强调色变了，
        # 都要重新构建主题。两者都关掉时定时器完全不跑（零开销）。
        need = (self._theme_mode() == "system"
                or self.config.get("accent_mode") == "system")
        if need:
            if not self._system_theme_timer.isActive():
                self._last_system_theme = system_theme_name()
                self._last_system_signature = None   # 强制首帧同步一次
                self._system_theme_timer.start()
        else:
            self._system_theme_timer.stop()

    def _theme_log_viewer(self, theme_name=None, theme=None):
        """给开着的日志窗口换肤（销毁过/从没开过则跳过）。

        ⚠ 必须传**完整 Theme 对象**。

        原来只传 `theme.name`（"light"/"dark"），日志窗口内部用
        `Theme(name)` 重建，配色预设（午夜蓝/自定义）全部丢失——
        表现就是"日志窗口没接入主题"。
        """
        w = self._log_viewer
        if w is not None and shiboken_is_valid(w):
            try:
                if theme is not None:
                    w.set_theme(theme=theme)
                else:
                    w.set_theme(theme_name)
            except Exception as e:
                append_log("warn", "theme", "日志窗口换肤失败：%r" % (e,))

    def _theme_tray_menu(self, theme=None):
        """给托盘右键菜单换肤。

        ## 为什么要单独处理

        独立 `QMenu`（由托盘图标弹出、无父 widget）**不继承 app 的全局
        QSS**，所以主题切换后它仍是系统默认的白底黑字——与整体风格脱节。
        创建时虽然涂过一次色，但那个样式是**快照**，切主题后不会自己更新，
        必须由 ThemeController 的广播重新推一次。

        ## 支持单独配置（surface="menu"）

        菜单很小，有时主窗口用深色、菜单反而保持浅色更好认。所以这里
        不再直接复用主主题，而是按 `menu` 表面解析：

            主配置 ← menu 表面的覆盖层（用户在外观页为托盘菜单单独设的）

        没单独设过就沿用主配置，行为和以前一致。
        """
        menu = getattr(self, "menu", None)
        if menu is None:
            return
        try:
            t = self._menu_surface_theme(theme)
            # 自绘玻璃：刷新 composer（读 menu 表面配置）+ 清缓存重绘。
            # QSS 只负责菜单项的文字/选中色，背景由 GlassMenu 自绘。
            if hasattr(menu, "refresh_material"):
                menu.refresh_material()
            menu.setStyleSheet(t.menu_style(
                glass=bool(getattr(menu, "material_enabled", lambda: False)())))
        except Exception as e:
            append_log("warn", "theme", "托盘菜单换肤失败：%r" % (e,))

    def _menu_surface_theme(self, fallback=None):
        """按 `menu` 表面解析出托盘菜单该用的主题。

        ## 两种来源

        * 用户在「外观 → 托盘菜单 → 独立配置」里设过 → 用那份
        * 没设过 → 用主主题（`fallback`，缺省取 theme_obj）

        ## 为什么要重新 build 而不是改 fallback

        `fallback` 是**主主题对象**，直接改它会污染其它窗口。
        单界面覆盖只影响这一个菜单，所以必须按解析结果重建一个新主题。
        """
        base = fallback if fallback is not None else getattr(
            self, "theme_obj", None)

        try:
            from core.theme_profile import has_override, resolve

            if has_override(self.config, "menu"):
                params = resolve(self.config, "menu")
                from ui.theme_manager import build_theme

                return build_theme(
                    {"theme_mode": params.get("theme_mode", "light"),
                     "theme_preset": params.get("theme_preset", "classic")})
        except Exception:
            pass

        if base is not None:
            return base
        from ui.theme_manager import build_theme

        return build_theme(self.config)

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

        # 记进「注入历史」：与 usage（累计次数）不同，这是一条按时间排的
        # 流水，用来支持「刚才那个再来一次」。
        try:
            self.state.add_history(text, source=ctx.get("source") or "")
        except Exception as e:
            append_log("warn", "inject", "记录注入历史失败：%r" % (e,))

        # 教推荐网络：这次在「这些候选」里选了「这条」。
        # 只在从候选条里选的时候学——库页浏览、Ctrl+R 重复这类
        # 不代表「在当前情境下的偏好」，喂进去反而是噪声。
        #
        # 用户可在设置里关闭学习（`rank_learning`）。关闭后**只是不再学**，
        # 已经学到的不受影响——和情绪学习那个开关的行为保持一致。
        try:
            shown = ctx.get("_shown")
            if (shown and ctx.get("source") in (None, "", "picker")
                    and bool(self.config.get("rank_learning", True))):
                self.state.learn_selection(text, shown)
        except Exception as e:
            append_log("warn", "rank", "推荐网络学习失败：%r" % (e,))

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

    def install_update_from_package(self, package_path, version=""):
        """安装**指定路径**的更新包（关于页手动下载后用）。

        ## 为什么需要它

        自动更新开关关掉后，用户仍可能在「关于」页手动检查更新。
        以前那条路径只把 zip 保存下来、提示用户自己去解压替换——
        对普通用户来说等于"没更新成功"。

        现在手动下载完也走同一套安装器：解压 → 启动替换脚本 → 重启。

        :return: 是否成功启动了安装流程
        """
        try:
            from core import updater_install

            if not updater_install.can_self_update():
                append_log("warn", "update",
                           "当前形态无法自动安装：%s"
                           % updater_install.install_dir())
                return False

            staging, _exe = updater_install.prepare(package_path, append_log)
            try:
                updater_install.write_pending(version or "")
                updater_install.launch_installer(staging, append_log)
            except Exception:
                # 启动失败：清掉暂存目录和标记，不能留下半成品
                import shutil
                shutil.rmtree(staging, ignore_errors=True)
                updater_install.clear_pending()
                raise

            append_log("info", "update",
                       "已启动安装程序，即将退出并替换为新版本")
            QTimer.singleShot(200, self.app.quit)
            return True
        except Exception as e:
            append_log("error", "update", "安装失败：%r" % (e,))
            return False

    def _on_auto_update_switched(self):
        """用户在设置里切换「自动检查更新」后的即时反应。

        * 打开：不必等到下次启动，**立刻**排一次检查（30 秒后）。
        * 关闭：把已排的检查取消，避免用户关了还偷偷检查。
        """
        from core import updater

        enabled = bool(self.config.get("auto_update", True))
        if not enabled:
            t = getattr(self, "_auto_update_timer", None)
            if t is not None:
                try:
                    t.stop()
                except Exception:
                    pass
            append_log("info", "update", "已关闭自动检查更新")
            return

        if getattr(self, "updater", None) is None:
            if not updater.is_supported():
                return
            self.updater = updater.AutoUpdater(self.config)
            self.updater.update_available.connect(self._on_update_available)
            self.updater.up_to_date.connect(
                lambda v: append_log("info", "update", "已是最新版本 %s" % v))
            self.updater.error.connect(
                lambda m: append_log("warn", "update", m))
            self.updater.progress.connect(self._on_update_progress)
            self.updater.ready_to_install.connect(self._on_update_ready)
            self.updater.installing.connect(self._on_installing)

        append_log("info", "update", "已开启自动检查更新，稍后开始检查")
        QTimer.singleShot(3000, self._check_update_quietly)

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

    def extract_from_clipboard(self):
        """从剪贴板提取颜文字，弹窗供一键收藏。

        ## 为什么用剪贴板

        试过用 UIA 遍历聊天窗口的 UI 树，结论是不该走那条路：
        微信/QQ 大量自绘控件，UIA 树经常是空的；还要处理"读哪个窗口"
        "权限够不够"等问题。

        剪贴板方案则是**用户已经明确表达了意图**——他手动选中了、
        手动复制了。我们只负责从中挑出颜文字，兼容一切软件。

        :return: 收藏的条数（0 表示没提取到或用户取消）
        """
        from ui.extract_dialog import ExtractDialog, read_clipboard_items

        # 已知条目：用于在弹窗里标注"已在库中"，默认不重复勾选。
        #
        # ⚠ 这里**只算「我的颜文字」**，不要把内置库和收藏夹算进来。
        #
        # 原来的写法把三种来源混在一起，导致一个真实的 bug：
        # 用户从聊天里提取到一条**内置库里已有的**颜文字
        # （比如 `(◕‿◕)`），勾选后却只进了"收藏夹"，
        # 「我的颜文字」里看不到——但用户的本意就是"把它收进我的列表"。
        #
        # 内置库是只读素材，「我的颜文字」才是用户自己的收藏。
        # 两者不该混为一谈。
        try:
            known = set(self.user_kao.get_all()) if self.user_kao else set()
        except Exception:
            known = set()
        #: 内置库里的条目。只用于弹窗里的"内置库已有"标注——
        #: **不阻止**用户把它收进「我的颜文字」。
        try:
            builtin = set(self.data.get_items())
        except Exception:
            builtin = set()

        items, raw = read_clipboard_items()
        if not items:
            append_log("info", "extract",
                       "剪贴板里没找到颜文字（内容长度 %d）" % len(raw or ""))
            # 明确告知，而不是弹一个空窗口让人困惑
            self.tray.showMessage(
                "没找到颜文字",
                ("剪贴板里没有可识别的颜文字。\n"
                 "请先在聊天里选中含颜文字的文字并复制。")
                if not raw else
                ("剪贴板内容里没识别出颜文字。\n"
                 "（已读取 %d 个字符）" % len(raw)),
                QSystemTrayIcon.MessageIcon.Information, 4000)
            return 0

        dlg = ExtractDialog(items, theme=build_theme(self.config),
            parent=self.unified if self.unified.isVisible() else None,
            preexisting=known,
            in_builtin=builtin)
        if dlg.exec() != dlg.DialogCode.Accepted:
            return 0

        chosen = dlg.selected()
        if not chosen:
            return 0
        added = 0
        for text in chosen:
            try:
                if self.user_kao is None:
                    continue
                # 统一加进「我的颜文字」——这就是用户勾选时的心智模型。
                #
                # 不再区分"是否已在内置库/收藏夹"：
                # 用户点了收藏，东西就该出现在他自己的列表里。
                # 已在「我的颜文字」里的会被 UserKaomoji 自己去重。
                before = set(self.user_kao.get_all())
                self.user_kao.add_item(text, "收藏", "")
                if text not in before:
                    added += 1
                # 顺便标记为收藏，让它排在候选条前面
                try:
                    if text not in self.state.favorites:
                        self.state.toggle_favorite(text)
                except Exception:
                    pass
            except Exception as e:
                append_log("warn", "extract", "收藏失败：%r" % (e,))
        append_log("info", "extract", "从剪贴板收藏了 %d 条颜文字" % added)
        if added:
            self.tray.showMessage(
                "已收藏",
                "从剪贴板收下了 %d 条颜文字，可在「我的颜文字」里查看"
                % added,
                QSystemTrayIcon.MessageIcon.Information, 3000)
        return added

    def repeat_last_injected(self):
        """把上一条注入的颜文字再插一次（候选条里 Ctrl+R）。

        设计取舍：**只取上一条，不做"上一条历史"面板**。
        实景是"刚发完一句，下一句还想用同一个"——一次按键就够。
        翻更早的历史属于低频操作，不值得占一个热键。

        跳过最近一条是因为历史里第一条就是"上一次注入"，
        而用户此刻正在候选条里想复用**那个**。
        """
        text = ""
        try:
            text = self.state.last_injected(0)
        except Exception:
            text = ""
        if not text:
            append_log("info", "inject", "没有可重复的颜文字（历史为空）")
            return False
        # 走统一注入入口，保证监听器静默期、插件转换、历史记录都一致
        self.on_selected(text, {"source": "repeat"})
        return True

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
        name = resolved_mode_of(self.config)
        # 用完整主题对象，而不是明暗名——只有它能带上配色预设
        theme = getattr(self, "theme_obj", None)
        if theme is None:
            from ui.theme_manager import build_theme

            theme = build_theme(self.config)
        w = self._log_viewer
        # 关闭时窗口自带 WA_DeleteOnClose，destroyed 会把引用置空；这里再兜一层
        # 有效性判断，防止极端时序下拿到已销毁的 C++ 对象。
        if not (w is not None and shiboken_is_valid(w)):
            w = show_log_viewer(LOG_BUFFER, parent=self.unified,
                                theme_name=name, theme=theme)
            self._log_viewer = w
            w.destroyed.connect(self._forget_log_viewer)
        else:
            w.set_theme(theme_name=name, theme=theme)
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
                theme_name=resolved_mode_of(self.config))
        except Exception as e:
            append_log("warn", "update", "更新弹窗失败：%s" % e)


def create_runtime(app, config=None):
    """按当前磁盘配置构建运行时（供入口与测试共用）。"""
    return AppRuntime(app, config if config is not None else load_config())
