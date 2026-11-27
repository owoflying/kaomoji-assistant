"""极简「Win11 输入法候选条」。

设计原则：只保留输入法该有的东西，其余全部砍掉。
  * 一条横向候选条：序号 + 颜文字，选中项带浅色高亮，右侧一个小页码；
  * 没有搜索框、没有分类页签、没有收藏星标、没有标题栏按钮 —— 越轻越好；
  * 窗口尺寸随内容自适应（SetFixedSize），不再有大片空白；
  * 键盘：**不需要焦点**就能捕获——由全局低层键盘钩子（core.global_keys）接管，
    候选条本身永不抢焦点，不打断用户写字；用户继续正常打字时自动关闭；
  * 自动弹出时贴着输入光标出现（GUIThreadInfo -> UIA 包围盒 -> 焦点窗口 兜底），
    手动唤起时居中偏上；
  * 选中后先还回前台窗口，再把颜文字送进去；
  * 淡入淡出动画，去掉“僵硬”感；
  * 翻页只更新已有芯片，不再整体重建，避免“-/+ 切换时整屏闪一下”。

键盘动作（全局钩子映射）：
  1-9 选字 · ←/→(或 ↑/↓) 移动 · -/= 翻页 · 空格/回车 上屏 · Esc 关闭。
任何其它键都原样送回目标程序并自动关闭面板。

显示/隐藏状态机（本文件最容易写乱的地方，收敛成两个布尔 + 两个方法）：
  ``_hk_active``  面板“逻辑可见”：全局键鼠钩子是否应当响应；
  ``_hiding``     正在播放淡出动画（此时 Qt 仍认为窗口 visible）。
  ``dismiss()``   **唯一的收起入口**。选字、Esc、外部点击、失焦、继续打字、
                  托盘切换全部走它，统一停钩子 / 停焦点轮询 / 淡出或立即收尾。
  ``_finalize_hide()`` 真正的 ``QWidget.hide()``：停动画、透明度归零、清瞬态。
  之所以不再重写 Qt 的 ``hide()``：那是公共 API，一旦被 Qt 内部或第三方调用，
  “隐藏”就悄悄变成了“先淡出 130ms 再隐藏”，排查起来极其痛苦。
"""
import time

from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QLabel, QPushButton, QLayout,
)
from PySide6.QtCore import (
    Qt, Signal, QEvent, QTimer, QPoint, QPropertyAnimation, QEasingCurve,
    QAbstractAnimation,
)
from PySide6.QtGui import QColor, QPainter, QGuiApplication, QCursor

from core.kaomoji_data import KaomojiData
from core.user_state import UserState
from core import win_utils
from core.global_keys import GlobalKeyInterceptor
from core.global_mouse import GlobalMouseInterceptor
from ui.win_style import apply_dark_mode
from ui.window_material_mixin import WindowMaterialMixin
from ui.frameless_panel import paint_frameless_panel
from ui.win11_theme import resolved_mode_of

# 顶层窗口向外留白，用于自绘柔和阴影（阴影画在窗口矩形内，避免 UpdateLayeredWindow 越界报错）
SHADOW_PAD = 12
# 面板圆角
RADIUS = 10

# “刚自动关闭”的冷却时间：这段时间内忽略热键/托盘切换，避免“点一下就开→关→开”抖动
DISMISS_COOLDOWN = 0.4
# 淡入淡出时长（毫秒）
FADE_MS = 110      # 见 core/anim_tuning.py 的统一时长规范

def _palette_from_theme(theme):
    """把统一主题色板转成候选条需要的字段。

    ## 为什么不再用静态 PALETTES

    原来这里写死了两套色板（light/dark），换主题预设时**候选条不会跟着变**
    ——主窗口是午夜蓝、候选条还是经典灰，看起来像两个程序。

    现在从 `Theme` 派生，任何预设/强调色都自动一致。

    三个特殊字段说明：
      * `bg` 要 "r,g,b" 形式（给 `setWindowOpacity` 之外的透明度算法用）
      * `border` 是 0~255 的 alpha 整数（QSS 里拼进 rgba）
      * `shadow` 是 "r,g,b,a" 给阴影绘制用
    """
    from core.theme_presets import parse_color

    bg_rgb = parse_color(theme.card) or (255, 255, 255, 1.0)
    # 候选条比卡片略实一点，边缘才清晰
    accent = theme.accent
    return {
        "bg": "%d,%d,%d" % (bg_rgb[0], bg_rgb[1], bg_rgb[2]),
        "text": theme.text,
        "num": theme.text_tertiary,
        "accent": accent,
        # 选中/悬停都用主题派生的半透明叠加，深浅自动适配
        "active_bg": theme.accent_bg if not theme.dark
                     else theme.accent_bg,
        "hover_bg": theme.nav_hover,
        "nav": theme.text_secondary,
        "border": "70" if theme.dark else "18",
        "shadow": "0,0,0,110" if theme.dark else "0,0,0,40",
    }


#: 兜底色板：万一拿不到 Theme（极端情况）仍能画出来。
PALETTES = {
    "light": {
        "bg": "255,255,255",
        "text": "#1f1f1f", "num": "#8a8a8e", "accent": "#0067c0",
        "active_bg": "rgba(0,103,192,0.10)", "hover_bg": "rgba(0,0,0,0.05)",
        "nav": "#6b6b70", "border": "18", "shadow": "0,0,0,40",
    },
    "dark": {
        "bg": "43,43,43",
        "text": "#f0f0f0", "num": "#a0a0a6", "accent": "#60cdff",
        "active_bg": "rgba(96,205,255,0.16)", "hover_bg": "rgba(255,255,255,0.07)",
        "nav": "#b8b8be", "border": "70", "shadow": "0,0,0,110",
    },
}


def _style(p):
    return """
    QWidget {{
        font-family: "Segoe UI Variable", "Segoe UI", "Segoe UI Symbol",
                     "Microsoft YaHei UI", "Yu Gothic UI", "Arial Unicode MS", sans-serif;
        color: {text};
    }}
    QWidget#chip {{ background: transparent; border-radius: 6px; }}
    QWidget#chip:hover {{ background: {hover_bg}; }}
    QWidget#chip[active="true"] {{ background: {active_bg}; }}
    QLabel#num {{ color: {num}; font-size: 12px; }}
    QWidget#chip[active="true"] QLabel#num {{ color: {accent}; font-weight: 600; }}
    QLabel#kao {{ color: {text}; font-size: 15px; }}
    QLabel#page {{ color: {num}; font-size: 11px; padding-left: 2px; }}
QLabel#reason {{ color: {num}; font-size: 10px; padding-left: 4px; }}
    QPushButton#nav {{
        border: none; background: transparent; color: {nav};
        font-size: 15px; border-radius: 5px; padding: 0;
        min-width: 18px; max-width: 18px; min-height: 24px; max-height: 24px;
    }}
    QPushButton#nav:hover {{ background: {hover_bg}; color: {accent}; }}
    """.format(**p)


# 键盘兜底映射：本窗口意外拿到焦点时（正常路径走全局钩子）也要能操作。
# 值与 core.global_keys 的动作名对齐，直接复用 _on_hk_action，省掉第二套逻辑。
_KEY_ACTIONS = {
    Qt.Key_Left: "left", Qt.Key_Up: "left",
    Qt.Key_Right: "right", Qt.Key_Down: "right",
    Qt.Key_Minus: "prev", Qt.Key_PageUp: "prev",
    Qt.Key_Plus: "next", Qt.Key_Equal: "next", Qt.Key_PageDown: "next",
    Qt.Key_Return: "confirm", Qt.Key_Enter: "confirm", Qt.Key_Space: "confirm",
    Qt.Key_Escape: "cancel",
}


class CandidateChip(QWidget):
    """候选条里的一项：序号 + 颜文字。芯片预创建、复用，翻页时只改文本。"""

    selected = Signal(int)
    hovered = Signal(int)

    def __init__(self, slot, parent=None):
        super().__init__(parent)
        self._slot = slot
        self._active = False
        #: 高亮色（由 PickerWindow 在应用主题时注入）
        self._active_color = "#0067c0"
        self.setObjectName("chip")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setCursor(Qt.PointingHandCursor)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 3, 8, 3)
        lay.setSpacing(6)
        self.num = QLabel("")
        self.num.setObjectName("num")
        lay.addWidget(self.num)
        self.kao = QLabel("")
        self.kao.setObjectName("kao")
        lay.addWidget(self.kao)
        # 推荐理由角标（"常用"/"收藏"/"刚用过"…）。
        #
        # ## 为什么做成**默认隐藏的小字**而不是图标
        #
        # 候选条是"扫一眼就选"的界面，任何额外元素都会拖慢扫描。
        # 所以：
        #   * 只在有理由时显示，没理由的条目完全不留白；
        #   * 用极小字号 + 次要色，不与颜文字本身争视觉；
        #   * 默认在设置里关闭——用户没主动开启时，候选条保持原样。
        self.reason = QLabel("")
        self.reason.setObjectName("reason")
        self.reason.hide()
        lay.addWidget(self.reason)

    def set_text(self, index, text):
        self.num.setText(str(index))
        self.kao.setText(text)

    def set_reason(self, label):
        """设置推荐理由角标；空字符串表示不显示。"""
        label = (label or "").strip()
        if label:
            self.reason.setText(label)
            self.reason.show()
        else:
            self.reason.setText("")
            self.reason.hide()

    def set_active(self, on):
        """设置选中/悬停高亮。

        ⚠ **不用 unpolish/polish**：那会让 Qt 重新计算这个控件的整棵样式表，
        鼠标划过每个候选都会触发一次——表现为"鼠标滑动不跟手、一顿一顿"。
        改为记录状态 + `update()`，由 `paintEvent` 自绘背景，亚毫秒级。
        """
        on = bool(on)
        if self._active == on:
            return
        self._active = on
        self.update()

    def paintEvent(self, e):
        # 自绘圆角高亮背景（选中/悬停）
        if self._active:
            p = QPainter(self)
            p.setRenderHint(QPainter.Antialiasing)
            c = QColor(self._active_color)
            c.setAlpha(38)
            p.setPen(Qt.NoPen)
            p.setBrush(c)
            r = self.rect().adjusted(0, 0, -1, -1)
            p.drawRoundedRect(r, 6, 6)
            p.end()
        super().paintEvent(e)

    def enterEvent(self, e):
        self.hovered.emit(self._slot)
        super().enterEvent(e)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.selected.emit(self._slot)
            return
        super().mousePressEvent(e)


class PickerWindow(WindowMaterialMixin, QWidget):
    selected = Signal(str, object)   # (text, ctx) —— ctx 为触发词上下文 dict 或 None
    emotion_shown = Signal(str)        # 因某情绪自动弹出时发出，供监听器锁定该情绪
    settings_requested = Signal()      # 保留信号：托盘菜单走这条路
    # 自定义可见性信号：本版本 Qt6 的 QWidget 未提供 isVisibleChanged/visibilityChanged，
    # 故在 showEvent/hideEvent 中手动 emit，供 AppRuntime 暂停/恢复自动弹出监听使用。
    isVisibleChanged = Signal(bool)
    #: Ctrl+R：把上一条注入的颜文字再插一次
    repeat_last_requested = Signal()

    def __init__(self, data: KaomojiData, config: dict, state: UserState, user_kao=None):
        super().__init__()
        self.data = data
        self.config = config
        self.state = state
        self.user_kao = user_kao
        self.items = []
        self.page = 0
        self.active = 0                # 当前高亮的候选在本页内的下标
        self.page_size = max(1, int(config.get("page_size", 3)))
        self._emotion = None
        self._source_text = ""         # 触发本次弹窗的用户原句（在线学习用）
        self._trigger_word = ""        # 当前候选若来自触发词，记下触发词待上屏时清理
        self._delete_trigger = False   # 该触发词是否开启「应用后删除」
        self._chips = []
        self._saved_foreground = None
        self._pending = None
        self._pending_ctx = None
        self._drag = None
        self._press_global = QPoint()  # 按下时的全局坐标，用于区分“点击”与“拖动”
        self._drag_moved = False       # 本次按压是否发生了足以算作拖动的位移
        self._hk_active = False        # 全局钩子是否生效（面板可见时为 True）
        self._hiding = False           # 正在播放淡出动画
        self._dismissed_at = 0.0       # 上次因“继续打字 / 点外部”自动关闭的时间戳
        self._had_editable = False     # 本次显示期间是否曾检测到可编辑焦点
        self._caret = False            # True=贴着光标定位；False=居中偏上定位
        self._interceptor = None
        self._mouse_watch = None
        self._init_window()
        self._init_ui()
        self._apply_config_visuals()
        # 全局键盘拦截：始终运行，仅在 _hk_active 为真时真正捕获
        self._interceptor = GlobalKeyInterceptor(lambda: self._hk_active)
        self._interceptor.action.connect(self._on_hk_action)
        self._interceptor.dismiss.connect(self._on_hk_dismiss)
        self._interceptor.start()
        # 全局鼠标钩子：面板可见时，点在“界面外”即关闭（见 core/global_mouse.py）；
        # 内部点击交给各自控件处理，不会误伤。
        self._mouse_watch = GlobalMouseInterceptor(lambda: self._hk_active)
        self._mouse_watch.outside_click.connect(self._on_outside_click)
        self._mouse_watch.start()

    # ---------- 窗口与材质 ----------
    def _init_window(self):
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
            | Qt.Tool | Qt.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        # 显示时绝不抢焦点：不激活、不获取焦点，避免打断用户写字
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setFocusPolicy(Qt.NoFocus)
        self.winId()
        # 淡入淡出：动画「顶层窗口的 windowOpacity」，而不是 QGraphicsOpacityEffect。
        #
        # 为什么必须换成 windowOpacity（这才是「闪一下深色候选栏」的根治点）：
        #   候选条的 HWND 全程常驻、只 ShowWindow/HideWindow，不销毁重建。
        #   它是 WA_TranslucentBackground + FramelessWindowHint 的分层窗口
        #   （WS_EX_LAYERED，Qt 用 UpdateLayeredWindow 贴图）。ShowWindow 时
        #   Windows 会先把「上一次合成好的那张位图」原样贴出来，Qt 的重绘要
        #   晚一帧才到——如果隐藏期间主题从深色切成了浅色，这一帧贴出来的
        #   就是旧的深色候选条，于是看到「浅色模式下闪一下深色」。
        #   QGraphicsOpacityEffect 只作用于 Qt 自绘内容，管不到这张陈旧位图；
        #   而 windowOpacity 在 Qt/Windows 里走的是 UpdateLayeredWindow 的全局
        #   alpha，能在 show() 之前就把整个原生窗口压成完全透明，陈旧帧自然
        #   不可见。淡入由 showEvent 在首帧重绘之后再启动。
        self.setWindowOpacity(0.0)
        self._anim = QPropertyAnimation(self, b"windowOpacity", self)
        self._anim.setDuration(FADE_MS)
        # 曲线走统一规范：OutQuint（末段收敛快，没有 OutCubic 的拖尾感）
        self._anim.setEasingCurve(QEasingCurve.OutQuint)
        self._anim.finished.connect(self._on_anim_finished)
        #: 弹出时的上浮动画（见 _start_fade_in）
        self._slide_anim = None
        # 失焦检测：面板可见时定期检查焦点是否还在输入框
        self._focus_timer = QTimer(self)
        self._focus_timer.setInterval(350)
        self._focus_timer.timeout.connect(self._check_focus)

    def _apply_config_visuals(self):
        self._apply_window_material()
        self._apply_style()
        self._update_tooltip()

    def apply_config(self, config):
        """外部配置（主题/不透明度/亚克力/每页数量等）变更后刷新候选条。

        由 AppRuntime.apply_settings 调用，保证设置页改动实时生效。
        """
        self.config = config
        # 不再需要"失效缓存"：主题每次都从 controller 现取，
        # 没有跨次缓存可言（见 _apply_style）。
        new_size = max(1, int(config.get("page_size", 3)))
        if new_size != len(self._chips):
            self.page_size = new_size
            self._rebuild_chips()
            # 保留当前页码：只改“每页几个”不该把用户翻到的那一页踢回第一页
            self.set_candidates(self.items, keep_page=True)
        self._apply_config_visuals()
        if self.isVisible():
            self.repaint()               # 可见时立刻用新主题重绘，别等下一帧
        else:
            # 隐藏时改主题：分层窗口里还留着旧主题那张位图，且无法重绘。
            # 压成全透明，确保下次 ShowWindow 贴出的旧帧完全不可见。
            self._anim.stop()
            self.setWindowOpacity(0.0)

    def _apply_window_material(self):
        """设置沉浸式深色标记。

        ## 系统亚克力已移除

        这里原本调 `apply_backdrop()` 开系统亚克力。原因：

        * Win10 上 `DWMWA_SYSTEMBACKDROP_TYPE(38)` 直接失败（属性是 Win11
          才有的），整个调用是 no-op；
        * Win11 上它会接管窗口背景绘制，与候选条的自绘面板冲突。

        候选条背景现在完全由 `paintEvent` 自绘（`_panel_bg` + 圆角），
        不依赖任何系统材质。
        """
        hwnd = int(self.winId())
        apply_dark_mode(hwnd, resolved_mode_of(self.config) == "dark")
        # 接入**候选条自己的**材质配置。
        #
        # 候选条弹在光标附近，视觉需求和主窗口完全不同（它要看得清字），
        # 所以读 `picker_*` 前缀的一套独立参数，背景图也存独立命名空间。
        # 用户没勾「独立背景图」时，composer 会自动回退到主窗口那张。
        try:
            from ui.material_composer import MaterialComposer

            self._material_composer = MaterialComposer(
                self.config,
                lambda: getattr(self, "_theme_obj", None),
                surface="picker")
        except Exception:
            pass

    def _apply_style(self):
        """按当前主题重贴候选条样式。

        原来读静态 `PALETTES`，换配色预设时候选条不跟着变——主窗口
        变成午夜蓝、候选条还是经典灰，像两个程序拼在一起。
        现在优先用**主题 controller** 的当前主题派生（和主窗口同一个源，
        不会出现"主窗口变了候选条没变"）；没有 controller 时退回
        `build_theme()`；再不行才用静态表——保证候选条永远画得出来。
        """
        mode = resolved_mode_of(self.config)
        theme_obj = None
        ctrl = getattr(self, "_theme_ctrl", None)
        if ctrl is not None:
            try:
                theme_obj = ctrl.current()
            except Exception:
                theme_obj = None
        if theme_obj is None:
            try:
                from ui.theme_manager import build_theme
                theme_obj = build_theme(self.config, mode_override=mode)
            except Exception:
                theme_obj = None
        self._theme_obj = theme_obj      # 仅作本次绘制的临时引用，不再跨次缓存
        if theme_obj is not None:
            pal = _palette_from_theme(theme_obj)
        else:
            pal = PALETTES.get(mode, PALETTES["light"])
        self._applied_theme = mode    # 供 _prepare_show 判断主题是否已同步
        self.setStyleSheet(_style(pal))
        r, g, b = [int(x) for x in pal["bg"].split(",")]
        opacity = float(self.config.get("opacity", 0.98))
        self._panel_bg = QColor(r, g, b)
        self._panel_bg.setAlphaF(max(0.3, min(1.0, opacity)))
        self._shadow_color = QColor(*[int(x) for x in pal["shadow"].split(",")])
        alpha = int(pal["border"])
        self._border_color = QColor(0, 0, 0, alpha) if alpha <= 40 else QColor(255, 255, 255, alpha)
        accent = pal.get("accent") or "#0067c0"
        chip_color = QColor(accent)
        if not chip_color.isValid():
            chip_color = QColor("#0067c0")
        for chip in getattr(self, "_chips", []):
            chip._active_color = chip_color
    def _init_ui(self):
        self.root = QHBoxLayout(self)
        self.root.setContentsMargins(
            SHADOW_PAD + 6, SHADOW_PAD + 5, SHADOW_PAD + 6, SHADOW_PAD + 5
        )
        self.root.setSpacing(2)
        self.root.setSizeConstraint(QLayout.SetFixedSize)
        self._rebuild_chips()
        self.btn_prev = self._make_nav_button("‹", -1)
        self.btn_next = self._make_nav_button("›", 1)
        self.lbl_page = QLabel("", self)
        self.lbl_page.setObjectName("page")
        for w in (self.btn_prev, self.btn_next, self.lbl_page):
            self.root.addWidget(w)
            w.hide()
    def _make_nav_button(self, text, direction):
        btn = QPushButton(text, self)
        btn.setObjectName("nav")
        btn.setFocusPolicy(Qt.NoFocus)
        btn.clicked.connect(lambda: self._change_page(direction))
        return btn
    def _rebuild_chips(self):
        for c in self._chips:
            self.root.removeWidget(c)
            c.hide()          # 先隐藏，否则 setParent(None) 会让它成为顶层窗口闪一下
            c.setParent(None)
            c.deleteLater()
        self._chips = []
        for i in range(self.page_size):
            chip = CandidateChip(i, self)
            chip.selected.connect(self._pick_slot)
            chip.hovered.connect(self._set_active)
            self.root.insertWidget(i, chip)
            self._chips.append(chip)
    def _page_count(self):
        if not self.items:
            return 1
        return max(1, (len(self.items) + self.page_size - 1) // self.page_size)
    def _visible_items(self):
        start = self.page * self.page_size
        return self.items[start:start + self.page_size]
    def _render(self):
        pages = self._page_count()
        if self.page >= pages:
            self.page = pages - 1
        visible = self._visible_items()
        show_reason = bool(self.config.get("show_reason_tags", False))
        for i, chip in enumerate(self._chips):
            if i < len(visible):
                chip.set_text(i + 1, visible[i])
                if show_reason:
                    try:
                        chip.set_reason(
                            self.state.primary_reason(visible[i]))
                    except Exception:
                        chip.set_reason("")
                else:
                    chip.set_reason("")
                chip.show()
            else:
                chip.hide()
        multi = pages > 1
        self.btn_prev.setVisible(multi)
        self.btn_next.setVisible(multi)
        self.lbl_page.setVisible(multi)
        if multi:
            self.lbl_page.setText("%d/%d" % (self.page + 1, pages))
        if self.active >= len(visible):
            self.active = max(0, len(visible) - 1)
        self._sync_active()
        if self._interceptor is not None:
            self._interceptor.set_state(
                len(visible), multi, self._emotion is None)
            try:
                has_hist = bool(self.state and self.state.history)
            except Exception:
                has_hist = False
            self._interceptor.set_has_repeatable(has_hist)
        self.adjustSize()
        self.update()
    def _sync_active(self):
        for i, c in enumerate(self._chips):
            c.set_active(i == self.active)
    def _set_active(self, i):
        if 0 <= i < len(self._chips) and i != self.active:
            self.active = i
            self._sync_active()
    def _change_page(self, d):
        n = self._page_count()
        if n <= 1:
            return
        self.page = (self.page + d) % n
        self.active = 0
        self._render()
        self._keep_on_screen()
    def _move_active(self, d):
        visible = self._visible_items()
        if not visible:
            return
        nxt = self.active + d
        if nxt < 0:
            if self._page_count() > 1:
                self._change_page(-1)
                self.active = len(self._visible_items()) - 1
                self._sync_active()
            return
        if nxt >= len(visible):
            if self._page_count() > 1:
                self._change_page(1)
            return
        self.active = nxt
        self._sync_active()
    def _manual_items(self):
        """手动唤起：按「频率 + 新近度 + 收藏 + 时段习惯」评分排序。
        ⚠ 不再单纯按「最近使用」排。实测用户真实数据：
        用过 4 次的 (｡･ω･｡) 曾排在第 5 位，只用过 2 次的却排第 1——
        每次唤起都要往下翻。改用 `UserState.rank()` 后，最常用稳定在首位，
        顺序也固定（不再随使用顺序漂移），用户能形成肌肉记忆。
        当前时段若对应某个场景（比如深夜 → "night"），会把该场景下的
        颜文字提到池子**前面**作为候选。这样深夜唤起时更容易看到
        "困了/晚安"这类，早上更容易看到问候类。
        只做「提前」不做「过滤」：池子仍是全集，只是排序靠前，
        用户想找别的照样翻得到。
        """
        seen = set()
        pool = []
        scene_items = []
        try:
            scene = self.state.current_scene()
            if scene:
                scene_items = list(self.data.by_scene(scene))
        except Exception:
            scene_items = []
        for src in (scene_items,
                    list(self.state.recent),
                    (self.user_kao.get_all() if self.user_kao is not None else []),
                    list(self.data.get_items())):
            for k in src:
                k = str(k)
                if k not in seen:
                    seen.add(k)
                    pool.append(k)
        return self.state.rank(pool)
    def set_candidates(self, items, keep_page=False):
        self.items = list(items)
        if not keep_page:
            self.page = 0
            self.active = 0
        self._render()
    def _pick_slot(self, i):
        visible = self._visible_items()
        if 0 <= i < len(visible):
            self._confirm(visible[i])
    def _confirm(self, text):
        self.state.add_recent(text)
        self.dismiss()
        if self._saved_foreground:
            win_utils.set_foreground(self._saved_foreground)
        self._pending = text
        self._pending_ctx = {
            "trigger_word": self._trigger_word,
            "delete_trigger": self._delete_trigger,
            "emotion": self._emotion or "",
            "source_text": self._source_text or "",
        }
        self._trigger_word = ""
        self._delete_trigger = False
        QTimer.singleShot(40, self._emit_selected)
    def _emit_selected(self):
        if self._pending is None:
            return
        text, ctx = self._pending, self._pending_ctx
        self._pending = None
        self._pending_ctx = None
        if isinstance(ctx, dict):
            ctx.setdefault("_shown", list(self.items))
        self.selected.emit(text, ctx)
    def _on_hk_action(self, action):
        if action == "cancel":
            self.dismiss()
        elif action == "repeat":
            self.dismiss()
            self.repeat_last_requested.emit()
        elif action == "confirm":
            visible = self._visible_items()
            if visible and 0 <= self.active < len(visible):
                self._confirm(visible[self.active])
        elif action == "prev":
            self._change_page(-1)
        elif action == "next":
            self._change_page(1)
        elif action == "left":
            self._move_active(-1)
        elif action == "right":
            self._move_active(1)
        elif action.startswith("num"):
            try:
                idx = int(action[3:]) - 1
            except ValueError:
                return
            visible = self._visible_items()
            if 0 <= idx < len(visible):
                self._confirm(visible[idx])
    def _on_hk_dismiss(self):
        """用户按了“其它键”继续打字 -> 自动关闭候选条。"""
        if self.isVisible():
            self.dismiss(mark_dismiss=True)
    def keyPressEvent(self, e):
        t = e.text()
        if t and t in "123456789":
            self._on_hk_action("num" + t)
            return
        action = _KEY_ACTIONS.get(e.key())
        if action is None:
            super().keyPressEvent(e)
            return
        self._on_hk_action(action)
    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._drag = e.globalPos() - self.frameGeometry().topLeft()
            self._press_global = e.globalPos()
            self._drag_moved = False
            return
        super().mousePressEvent(e)
    def mouseMoveEvent(self, e):
        if self._drag is not None and (e.buttons() & Qt.LeftButton):
            if (e.globalPos() - self._press_global).manhattanLength() > 4:
                self._drag_moved = True
            self.move(e.globalPos() - self._drag)
            return
        super().mouseMoveEvent(e)
    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self._drag is not None and not self._drag_moved:
            self.dismiss()
        self._drag = None
        super().mouseReleaseEvent(e)
    def _on_outside_click(self):
        if not self.isVisible() or self._hiding:
            return
        if self.geometry().contains(QCursor.pos()):
            return
        self.dismiss(mark_dismiss=True)
    def dismiss(self, mark_dismiss=False):
        """**唯一的收起入口**：停钩子 → 淡出（或立即收尾）。
        mark_dismiss=True 时记下时间戳，让紧随其后的热键/托盘切换被冷却忽略，
        避免“刚刚因为继续打字被关掉，热键又被当成一次新的唤起”。
        """
        if mark_dismiss:
            self._dismissed_at = time.time()
        self._hk_active = False
        self._focus_timer.stop()
        if not self.isVisible() or self._hiding:
            if not self.isVisible():
                self._finalize_hide()
            return
        self._hiding = True
        self._anim.stop()
        self._anim.setStartValue(self.windowOpacity())
        self._anim.setEndValue(0.0)
        self._anim.start()

        # 淡出同时轻微下沉——与弹出时的上浮对称，动作才有"收回去"的感觉。
        # 只下沉 3px（弹出是 4px）：消失比出现更应该"快走"，下移少一点显得利落。
        try:
            from core.anim_settings import enabled as _anim_on

            if _anim_on("picker") and self._slide_anim is not None:
                self._slide_anim.stop()
                base = self.pos()
                self._slide_anim = QPropertyAnimation(self, b"pos", self)
                self._slide_anim.setDuration(FADE_MS)
                self._slide_anim.setStartValue(base)
                self._slide_anim.setEndValue(QPoint(base.x(), base.y() + 3))
                self._slide_anim.setEasingCurve(QEasingCurve.OutQuint)
                self._slide_anim.start(QAbstractAnimation.DeleteWhenStopped)
        except Exception:
            pass
    def _finalize_hide(self):
        """真正隐藏：停动画、透明度归零、清瞬态，再交给 QWidget.hide()。"""
        self._anim.stop()
        self.setWindowOpacity(0.0)
        self._hiding = False
        self._hk_active = False
        self._focus_timer.stop()
        self._emotion = None
        self._caret = False
        super().hide()
    def hide_now(self):
        """立即隐藏（**播淡出动画**）。供需要“隐藏必须同步完成”的场景使用：
        例如打开统一窗口时，必须先让 ``isVisibleChanged(False)`` 立刻发出，
        否则监听器的 resume 会晚于随后的 pause 到达，把暂停抵消掉。
        """
        self._finalize_hide()
    def _finish_hide_now(self):
        """若正在播淡出动画，立刻收尾真隐藏。
        否则 Qt 认为窗口仍 visible，随后的 show() 不会再触发 showEvent，
        导致“淡出途中按热键 -> 面板没打开”。
        """
        if self._hiding:
            self._finalize_hide()
    def shutdown(self):
        """退出前卸载全局键盘/鼠标钩子，避免钩子线程残留。"""
        self._focus_timer.stop()
        self._hk_active = False
        for hook in (self._interceptor, self._mouse_watch):
            if hook is None:
                continue
            try:
                hook.stop()
            except Exception:
                pass
            self._interceptor = self._mouse_watch = None
    def _on_anim_finished(self):
        if self._hiding:
            self._finalize_hide()
    def event(self, e):
        if (e.type() == QEvent.WindowDeactivate and self.isVisible()
                and not self._hiding
                and self.config.get("auto_hide_on_blur", True)):
            self.dismiss()
        return super().event(e)
    def _check_focus(self):
        if not self.isVisible() or self._hiding:
            return
        if not self.config.get("auto_hide_on_blur", True):
            return
        try:
            from core import uia_text
            if uia_text is None or not uia_text.available():
                return
            editable = uia_text.is_focused_editable()
        except Exception:
            return
        if editable:
            self._had_editable = True
            return
        if self._had_editable:
            self.dismiss()
    def _prepare_show(self):
        """show() 之前的统一准备：压成全透明 + 确认主题已同步。
        必须在 show() **之前** 把 windowOpacity 归零：常驻 HWND 的分层窗口在
        ShowWindow 时会先贴出上一次合成的位图（可能是旧主题的深色候选条），
        Qt 的重绘要晚一帧。归零后这一帧完全不可见，淡入由 showEvent 负责。
        """
        self._anim.stop()
        self.setWindowOpacity(0.0)
        if resolved_mode_of(self.config) != getattr(self, "_applied_theme", None):
            self._apply_config_visuals()
    def _present(self, items, *, emotion, caret, trigger_word="",
                 delete_trigger=False, source_text=""):
        """所有显示路径的公共后半段：收尾上次淡出 → 写状态 → 定位 → 淡入。
        :param source_text: 触发这次弹窗的原始文本（现在是用户输入的那句话）。
            在线学习需要它作为「样本输入」——用户从"开心"候选里选了一张，
            就把那句话当作"开心"的训练样本。

        ## ⚠ 输入法正在用时**不弹**

        用户反馈：有时输入法候选窗和我们的候选条同时出现，两边都响应
        数字键 1~9，按 1 会同时选中两边各自的第一项——"抢输入法的
        数字快捷键"。

        所以在真正 show 之前先问 `core.ime_guard`：只要有输入法的
        候选窗可见、或前台正处在中文输入状态，就**直接放弃这次弹出**。
        宁可不弹（用户还能用热键/托盘手动唤起），也不能抢键害人丢字。
        """
        try:
            from core.ime_guard import other_ime_active

            busy, why = other_ime_active()
            if busy:
                from core.applog import append_log

                append_log("info", "picker",
                           "检测到输入法正在输入（%s），本次不弹出候选条"
                           % why)
                return
        except Exception:
            # 检测本身失败时宁可照常弹出，不能因为一个辅助判断让功能失效
            pass
        self._finish_hide_now()
        self._emotion = emotion
        self._caret = caret
        self._trigger_word = trigger_word
        self._delete_trigger = delete_trigger
        self._source_text = source_text or ""
        self.set_candidates(items)
        self._saved_foreground = win_utils.get_foreground_hwnd()
        self._prepare_show()
        self.show()
    def toggle(self):
        if self.isVisible() and not self._hiding:
            self.dismiss()
            return
        if time.time() - self._dismissed_at < DISMISS_COOLDOWN:
            return
        self._present(self._manual_items(), emotion=None, caret=False)
    def show_for_emotion(self, emotion, source_text=""):
        """按情绪推荐候选。
        ⚠ **不再 `random.shuffle`**。随机顺序意味着同一个情绪每次弹出的候选
        排列都不同，用户永远无法靠位置记住"我常用的那个在第几个"。
        改为按使用习惯排序（`UserState.rank()`）：最常用的稳定在首位。
        :param source_text: 触发该情绪的用户原句。在线学习需要它——
            用户从这个情绪的候选里选了一张，就把这句话当训练样本。
        以前是把它们追加到内置分类后面再一起 `rank()`：一个情绪分类有
        20~30 条内置项，而 `rank()` 是稳定排序、没用过的自定义项分数为 0，
        结果自定义项沉在第 30 位以后（默认每页 3 条 = 第 11 页），
        用户永远翻不到，看起来就像"标签完全没参与推荐"。
        现在两段各自按使用习惯排序、标签段整体靠前：既保留「常用的稳定在
        首位」，也让用户主动标了情绪的颜文字真的出现在第一页。
        """
        builtin = list(self.data.get_items(emotion))
        tagged = (list(self.user_kao.items_for_emotion(emotion))
                  if self.user_kao is not None else [])
        items = self.state.rank(tagged) + self.state.rank(builtin)
        seen = set()
        items = [x for x in items if not (x in seen or seen.add(x))]
        if not items:
            return
        self._present(items, emotion=emotion, caret=True, source_text=source_text)
        self.emotion_shown.emit(emotion)
    def show_for_output(self, text, trigger_word="", delete_after=False):
        """触发词片段：在候选条给出「特定输出」单一候选（贴光标定位）。
        trigger_word / delete_after 来自快捷短语的「应用后删除触发词」开关：
        若 delete_after 为真，上屏该候选时会先把用户刚输入的触发词一并清掉。
        """
        if not text:
            return
        self._present([text], emotion=None, caret=True,
                      trigger_word=trigger_word, delete_trigger=delete_after)
    def showEvent(self, e):
        if self._caret:
            self._position_caret()
        else:
            self._position_center()
        self.raise_()                     # 仅抬升 Z 序，不激活、不抢焦点
        self._hk_active = True            # 开启全局键盘捕获
        self._interceptor.reset_session()  # 清掉上一次显示遗留的互动状态
        self._hiding = False
        self._had_editable = self._caret
        self._focus_timer.start()
        self._anim.stop()
        self.setWindowOpacity(0.0)
        QTimer.singleShot(0, self._start_fade_in)
        super().showEvent(e)
        self.isVisibleChanged.emit(True)
    def _start_fade_in(self):
        """首帧重绘之后再启动淡入 + 上浮（见 showEvent 注释）。

        ## 为什么加位移

        原来只有 `windowOpacity` 淡入——透明度从 0 到 1，位置完全不动，
        观感是"啪地出现然后渐显"，很生硬。

        现在叠加一个 4px 的上浮：从一开始就"从光标下方轻轻升起"。
        位移只有几像素，但正是这点让弹出有"从哪里来"的方向感，
        是"丝滑"和"卡顿"的观感分界。

        `windowOpacity` 那套不能省——它解决的是分层窗口贴陈旧位图的问题
        （见 `_init_window` 的长注释），和这里的位移动画互不冲突。
        """
        if self._hiding or not self.isVisible():
            return
        # 开关：关掉动画时直接设为完全不透明（否则窗口会永远透明）
        try:
            from core.anim_settings import enabled as _anim_on

            anim_on = _anim_on("picker")
        except Exception:
            anim_on = True
        if not anim_on:
            self._anim.stop()
            self.setWindowOpacity(1.0)
            return
        self._anim.stop()
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.start()

        # ---- 上浮（同样受 picker 动画开关控制）----
        try:
            if not anim_on:
                raise RuntimeError("anim off")
            if self._slide_anim is not None:
                self._slide_anim.stop()
            base = self.pos()
            self._slide_anim = QPropertyAnimation(self, b"pos", self)
            self._slide_anim.setDuration(FADE_MS)
            self._slide_anim.setStartValue(QPoint(base.x(), base.y() + 4))
            self._slide_anim.setEndValue(base)
            self._slide_anim.setEasingCurve(QEasingCurve.OutQuint)
            self.move(base.x(), base.y() + 4)
            self._slide_anim.start(QAbstractAnimation.DeleteWhenStopped)
        except Exception:
            pass
    def hideEvent(self, e):
        super().hideEvent(e)
        self.isVisibleChanged.emit(False)
    def _clamp(self, x, y):
        screen = QGuiApplication.screenAt(QPoint(x, y)) or QGuiApplication.primaryScreen()
        if screen is None:
            return x, y
        sg = screen.availableGeometry()
        x = max(sg.x(), min(x, sg.x() + sg.width() - self.width()))
        y = max(sg.y(), min(y, sg.y() + sg.height() - self.height()))
        return x, y
    def _keep_on_screen(self):
        p = self.pos()
        self.move(*self._clamp(p.x(), p.y()))
    def _position_center(self):
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return
        sg = screen.availableGeometry()
        x = sg.x() + (sg.width() - self.width()) // 2
        y = sg.y() + int(sg.height() * 0.32)
        self.move(*self._clamp(x, y))
    def _position_caret(self):
        pos = win_utils.get_caret_screen_pos()
        if pos is None:
            self._position_center()
            return
        x = pos.x() - SHADOW_PAD
        y = pos.y() + 22
        self.move(*self._clamp(x, y))
    def _update_tooltip(self):
        try:
            label = win_utils.hotkey_label(self.config.get("hotkey", "<ctrl>+<shift>+k"))
        except Exception:
            label = "热键"
        self.setToolTip(
            "颜文字输入辅助器 · %s 唤起\n"
            "1-9 选字 · ←/→ 移动 · -/= 翻页 · 空格/回车 上屏 · Esc 关闭" % label
        )
    def paintEvent(self, e):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        pad = SHADOW_PAD
        # 材质开启时由 mixin 画（含背景图/模糊），不再画纯色面板
        if self._paint_material(painter, pad, RADIUS,
                                shadow_color=self._shadow_color):
            painter.end()
            super().paintEvent(e)
            return
        panel = self.rect().adjusted(pad, pad, -pad, -pad)
        paint_frameless_panel(
            painter, panel, radius=RADIUS,
            tint=self._panel_bg, border=self._border_color,
            shadow=self._shadow_color,
            shadow_pad=pad, shadow_spread=0.30, shadow_growth=0.4,
        )
        super().paintEvent(e)
