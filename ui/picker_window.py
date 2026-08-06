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
"""
import random
import time

from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QLabel, QPushButton, QLayout, QGraphicsOpacityEffect,
)
from PySide6.QtCore import (
    Qt, Signal, QEvent, QTimer, QPoint, QPropertyAnimation, QEasingCurve,
)
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QGuiApplication, QCursor

from core.kaomoji_data import KaomojiData
from core.user_state import UserState
from core import win_utils
from core.global_keys import GlobalKeyInterceptor
from core.global_mouse import GlobalMouseInterceptor
from ui.win_style import (
    apply_backdrop, apply_dark_mode,
    DWMSBT_TRANSIENTWINDOW, DWMSBT_NONE,
)

# 顶层窗口向外留白，用于自绘柔和阴影（阴影画在窗口矩形内，避免 UpdateLayeredWindow 越界报错）
SHADOW_PAD = 12
# 面板圆角
RADIUS = 10

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
    QPushButton#nav {{
        border: none; background: transparent; color: {nav};
        font-size: 15px; border-radius: 5px; padding: 0;
        min-width: 18px; max-width: 18px; min-height: 24px; max-height: 24px;
    }}
    QPushButton#nav:hover {{ background: {hover_bg}; color: {accent}; }}
    """.format(**p)


class CandidateChip(QWidget):
    """候选条里的一项：序号 + 颜文字。芯片预创建、复用，翻页时只改文本。"""

    selected = Signal(int)
    hovered = Signal(int)

    def __init__(self, slot, parent=None):
        super().__init__(parent)
        self._slot = slot
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

    def set_text(self, index, text):
        self.num.setText(str(index))
        self.kao.setText(text)

    def set_active(self, on):
        self.setProperty("active", "true" if on else "false")
        self.style().unpolish(self)
        self.style().polish(self)

    def enterEvent(self, e):
        self.hovered.emit(self._slot)
        super().enterEvent(e)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.selected.emit(self._slot)
            return
        super().mousePressEvent(e)


class PickerWindow(QWidget):
    selected = Signal(str, object)   # (text, ctx) —— ctx 为触发词上下文 dict 或 None
    emotion_shown = Signal(str)        # 因某情绪自动弹出时发出，供监听器锁定该情绪
    settings_requested = Signal()      # 保留信号：托盘菜单走这条路
    # 自定义可见性信号：本版本 Qt6 的 QWidget 未提供 isVisibleChanged/visibilityChanged，
    # 故在 showEvent/hideEvent 中手动 emit，供 main.py 暂停/恢复自动弹出监听使用。
    isVisibleChanged = Signal(bool)

    def __init__(self, data: KaomojiData, config: dict, state: UserState, user_kao=None):
        super().__init__()
        self.data = data
        self.config = config
        self.state = state
        self.user_kao = user_kao
        self.items = []
        self.page = 0
        self.active = 0                # 当前高亮的候选在本页内的下标
        self.page_size = int(config.get("page_size", 3))
        self._emotion = None
        self._trigger_word = ""        # 当前候选若来自触发词，记下触发词待上屏时清理
        self._delete_trigger = False   # 该触发词是否开启「应用后删除」
        self._chips = []
        self._saved_foreground = None
        self._closing_enabled = False
        self._pending = None
        self._drag = None
        self._press_global = QPoint()  # 按下时的全局坐标，用于区分“点击”与“拖动”
        self._drag_moved = False       # 本次按压是否发生了足以算作拖动的位移
        self._hk_active = False        # 全局钩子是否生效（面板可见时为 True）
        self._hiding = False           # 正在播放淡出动画
        self._dismiss_at = 0.0         # 上次因“继续打字”自动关闭的时间戳
        self._had_editable = False     # 本次显示期间是否曾检测到可编辑焦点
        self._caret = False            # True=贴着光标定位；False=居中偏上定位
        self._pending = None
        self._pending_ctx = None
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
        # 淡入淡出：用透明通道做动画
        self._opacity_fx = QGraphicsOpacityEffect(self)
        self._opacity_fx.setOpacity(1.0)
        self.setGraphicsEffect(self._opacity_fx)
        self._anim = QPropertyAnimation(self._opacity_fx, b"opacity", self)
        self._anim.setDuration(130)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.finished.connect(self._on_anim_finished)
        # 失焦检测：面板可见时定期检查焦点是否还在输入框
        self._focus_timer = QTimer(self)
        self._focus_timer.setInterval(350)
        self._focus_timer.timeout.connect(self._check_focus)

    def _apply_config_visuals(self):
        self._apply_window_material()
        self._apply_style()
        self._update_tooltip()

    def _apply_window_material(self):
        hwnd = int(self.winId())
        if self.config.get("acrylic", True):
            apply_backdrop(hwnd, DWMSBT_TRANSIENTWINDOW)
        else:
            apply_backdrop(hwnd, DWMSBT_NONE)
        apply_dark_mode(hwnd, self.config.get("theme", "light") == "dark")

    def _apply_style(self):
        pal = PALETTES.get(self.config.get("theme", "light"), PALETTES["light"])
        self._palette = pal
        self.setStyleSheet(_style(pal))
        r, g, b = [int(x) for x in pal["bg"].split(",")]
        # 不透明度与亚克力解耦：亚克力只负责底层玻璃质感，透明度永远听配置的
        opacity = float(self.config.get("opacity", 0.98))
        self._panel_bg = QColor(r, g, b)
        self._panel_bg.setAlphaF(max(0.3, min(1.0, opacity)))
        self._shadow_color = QColor(*[int(x) for x in pal["shadow"].split(",")])
        self._border_alpha = int(pal["border"])

    # ---------- UI ----------
    def _init_ui(self):
        self.root = QHBoxLayout(self)
        self.root.setContentsMargins(
            SHADOW_PAD + 6, SHADOW_PAD + 5, SHADOW_PAD + 6, SHADOW_PAD + 5
        )
        self.root.setSpacing(2)
        # 窗口尺寸完全跟随内容，杜绝大片留白
        self.root.setSizeConstraint(QLayout.SetFixedSize)
        # 预建候选芯片（复用，翻页不再销毁重建）
        self._rebuild_chips()

        # 翻页控件常驻（只在多页时加进布局并显示），parent 固定为 self
        self.btn_prev = QPushButton("‹", self)
        self.btn_prev.setObjectName("nav")
        self.btn_prev.setFocusPolicy(Qt.NoFocus)
        self.btn_prev.clicked.connect(lambda: self._change_page(-1))
        self.btn_next = QPushButton("›", self)
        self.btn_next.setObjectName("nav")
        self.btn_next.setFocusPolicy(Qt.NoFocus)
        self.btn_next.clicked.connect(lambda: self._change_page(1))
        self.lbl_page = QLabel("", self)
        self.lbl_page.setObjectName("page")
        for w in (self.btn_prev, self.btn_next, self.lbl_page):
            self.root.addWidget(w)
            w.hide()

    def _rebuild_chips(self):
        for c in self._chips:
            self.root.removeWidget(c)
            c.setParent(None)
            c.deleteLater()
        self._chips = []
        for i in range(self.page_size):
            chip = CandidateChip(i, self)
            chip.selected.connect(self._pick_slot)
            chip.hovered.connect(self._set_active)
            self.root.insertWidget(i, chip)
            self._chips.append(chip)

    # ---------- 候选渲染 ----------
    def _page_count(self):
        if not self.items:
            return 1
        return max(1, (len(self.items) + self.page_size - 1) // self.page_size)

    def _visible_items(self):
        start = self.page * self.page_size
        return self.items[start:start + self.page_size]

    def _render(self):
        visible = self._visible_items()
        for i, chip in enumerate(self._chips):
            if i < len(visible):
                chip.set_text(i + 1, visible[i])
                chip.show()
            else:
                chip.hide()
        multi = self._page_count() > 1
        self.btn_prev.setVisible(multi)
        self.btn_next.setVisible(multi)
        self.lbl_page.setVisible(multi)
        if multi:
            self.lbl_page.setText("%d/%d" % (self.page + 1, self._page_count()))
        if self.active >= len(visible):
            self.active = max(0, len(visible) - 1)
        self._sync_active()
        # 把“当前有几个候选 / 是否多页 / 是不是手动唤起”同步给全局钩子，
        # 让它只吞真正有用的键（例如只有 3 个候选时按 5 就不该被吞掉）
        itc = getattr(self, "_interceptor", None)
        if itc is not None:
            itc.set_state(len(visible), multi, self._emotion is None)
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

    # ---------- 数据 ----------
    def _manual_items(self):
        """手动唤起：最近用过的排前面，其次「我的颜文字」，其余按原顺序跟上（去重）。"""
        seen = set()
        out = []
        src = list(self.state.recent)
        if self.user_kao is not None:
            src = src + self.user_kao.get_all()
        src = src + list(self.data.get_items())
        for k in src:
            if k not in seen:
                seen.add(k)
                out.append(k)
        return out

    def set_candidates(self, items):
        self.items = list(items)
        self.page = 0
        self.active = 0
        self._render()

    # ---------- 选择 ----------
    def _pick_slot(self, i):
        visible = self._visible_items()
        if 0 <= i < len(visible):
            self._confirm(visible[i])

    def _confirm(self, text):
        self.state.add_recent(text)
        self._closing_enabled = False
        self.hide()
        if self._saved_foreground:
            win_utils.set_foreground(self._saved_foreground)
        self._pending = text
        # 若本次候选来自触发词，把「触发词 + 是否删除」上下文随选中事件带回，
        # 供主程序决定上屏颜文字前是否先把用户刚输入的触发词清掉。
        self._pending_ctx = {
            "trigger_word": self._trigger_word,
            "delete_trigger": self._delete_trigger,
        }
        # 清空，避免影响下一次（情绪 / 手动）选择
        self._trigger_word = ""
        self._delete_trigger = False
        QTimer.singleShot(40, self._emit_selected)

    def _emit_selected(self):
        if self._pending is not None:
            text = self._pending
            ctx = self._pending_ctx
            self._pending = None
            self._pending_ctx = None
            self.selected.emit(text, ctx)

    # ---------- 全局键盘钩子动作 ----------
    def _on_hk_action(self, action):
        if action == "cancel":
            self._closing_enabled = False
            self.hide()
            return
        if action == "confirm":
            visible = self._visible_items()
            if visible and 0 <= self.active < len(visible):
                self._confirm(visible[self.active])
            return
        if action == "prev":
            self._change_page(-1)
            return
        if action == "next":
            self._change_page(1)
            return
        if action == "left":
            self._move_active(-1)
            return
        if action == "right":
            self._move_active(1)
            return
        if action.startswith("num"):
            try:
                idx = int(action[3:]) - 1
            except ValueError:
                return
            visible = self._visible_items()
            if 0 <= idx < len(visible):
                self._confirm(visible[idx])
            # 超出当前页范围则忽略（不关闭），避免误触
            return

    def _on_hk_dismiss(self):
        """用户按了“其它键”继续打字 -> 自动关闭候选条。"""
        if self.isVisible():
            self._dismiss_at = time.time()
            self._closing_enabled = False
            self.hide()

    # ---------- 键盘（仅在窗口意外获得焦点时兜底；主路径是全局钩子） ----------
    def keyPressEvent(self, e):
        t = e.text()
        if t and t in "123456789":
            idx = int(t) - 1
            visible = self._visible_items()
            if 0 <= idx < len(visible):
                self._confirm(visible[idx])
            return
        k = e.key()
        if k in (Qt.Key_Left, Qt.Key_Up):
            self._move_active(-1)
            return
        if k in (Qt.Key_Right, Qt.Key_Down):
            self._move_active(1)
            return
        if k in (Qt.Key_Minus, Qt.Key_PageUp):
            self._change_page(-1)
            return
        if k in (Qt.Key_Plus, Qt.Key_Equal, Qt.Key_PageDown):
            self._change_page(1)
            return
        if k in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
            visible = self._visible_items()
            if visible and 0 <= self.active < len(visible):
                self._confirm(visible[self.active])
            return
        if k == Qt.Key_Escape:
            self._closing_enabled = False
            self.hide()
            return
        super().keyPressEvent(e)

    # ---------- 拖动 / 点击（空白处按住拖动=挪窗，点一下=关闭） ----------
    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._drag = e.globalPos() - self.frameGeometry().topLeft()
            self._press_global = e.globalPos()
            self._drag_moved = False
            return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._drag is not None and (e.buttons() & Qt.LeftButton):
            # 位移超过阈值才记为“拖动”，否则视为一次点击（松开时关闭面板）
            if (e.globalPos() - self._press_global).manhattanLength() > 4:
                self._drag_moved = True
            self.move(e.globalPos() - self._drag)
            return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and not self._drag_moved:
            # 空白处“点一下”（几乎没移动）= 关闭面板。
            # 这能解决：手动（托盘）唤起、且当前没有输入框聚焦时，
            # 失焦自动关闭逻辑因从未见过可编辑焦点而不武装，
            # 导致点击面板空白既不会关闭、又没别的退出途径（只能 Esc 或再点托盘）。
            # 拖动（按住移动）仍用于挪窗，二者靠位移阈值区分。
            self._closing_enabled = False
            self.hide()
        self._drag = None
        super().mouseReleaseEvent(e)

    # ---------- 全局鼠标钩子回调：点在面板“外部”即关闭 ----------
    def _on_outside_click(self):
        if not self.isVisible() or self._hiding:
            return
        # 点在面板矩形内（候选、翻页、空白点一下关闭等）交给各自控件，不处理；
        # 真正的“外部”才收起。判定放在主线程里做，安全。
        if self.geometry().contains(QCursor.pos()):
            return
        self._closing_enabled = False
        # 记一个时间戳，避免“点托盘又立刻被本钩子关掉又由托盘 toggle 重开”的抖动：
        # 外部点击关闭后 0.4s 内的热键/托盘切换会被忽略（与键盘 dismiss 同机制）。
        self._dismiss_at = time.time()
        self.hide()

    # ---------- 显示：手动 / 自动弹出 ----------
    def _finish_hide_now(self):
        """若正在播淡出动画，立刻收尾真隐藏。

        否则 Qt 认为窗口仍 visible，随后的 show() 不会再触发 showEvent，
        导致“淡出途中按热键 -> 面板没打开”。
        """
        if not self._hiding:
            return
        self._anim.stop()
        self._hiding = False
        self._emotion = None
        self._caret = False
        self._closing_enabled = False
        super().hide()

    def toggle(self):
        if self.isVisible() and not self._hiding:
            self._closing_enabled = False
            self.hide()
            return
        # 刚因“继续打字”被自动关闭后极短时间内又收到热键，视为同一次关闭，避免重开
        if time.time() - self._dismiss_at < 0.4:
            return
        self._finish_hide_now()
        self._emotion = None
        self._caret = False
        self._trigger_word = ""
        self._delete_trigger = False
        self.set_candidates(self._manual_items())
        self._saved_foreground = win_utils.get_foreground_hwnd()
        self.show()

    def show_for_emotion(self, emotion):
        items = self.data.get_items(emotion)
        if self.user_kao is not None:
            items = items + self.user_kao.items_for_emotion(emotion)
        seen = set()
        items = [x for x in items if not (x in seen or seen.add(x))]
        if not items:
            return
        items = list(items)
        random.shuffle(items)
        self._finish_hide_now()
        self._emotion = emotion
        self._caret = True
        self._trigger_word = ""
        self._delete_trigger = False
        self.set_candidates(items)
        self._saved_foreground = win_utils.get_foreground_hwnd()
        self.show()
        self.emotion_shown.emit(emotion)

    def show_for_output(self, text, trigger_word="", delete_after=False):
        """触发词片段：在候选条给出「特定输出」单一候选（贴光标定位）。

        trigger_word / delete_after 来自快捷短语的「应用后删除触发词」开关：
        若 delete_after 为真，上屏该候选时会先把用户刚输入的触发词一并清掉。
        """
        if not text:
            return
        self._finish_hide_now()
        self._emotion = None
        self._caret = True
        self._trigger_word = trigger_word
        self._delete_trigger = delete_after
        self.set_candidates([text])
        self._saved_foreground = win_utils.get_foreground_hwnd()
        self.show()

    def showEvent(self, e):
        if self._caret:
            self._position_caret()
        else:
            self._position_center()
        self.raise_()                     # 仅抬升 Z 序，不激活、不抢焦点
        self._hk_active = True            # 开启全局键盘捕获
        self._interceptor.reset_session()  # 清掉上一次显示遗留的互动状态
        self._closing_enabled = True
        self._hiding = False
        # 自动弹出必然发生在输入框里，失焦守卫立即武装；
        # 手动唤起时焦点可能压根不在输入框（桌面、只读页面…），
        # 必须等真正见过一次可编辑焦点后再武装，否则面板会自己秒关。
        self._had_editable = self._emotion is not None
        self._focus_timer.start()
        # 淡入
        self._anim.stop()
        self._opacity_fx.setOpacity(0.0)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.start()
        super().showEvent(e)
        self.isVisibleChanged.emit(True)

    def hideEvent(self, e):
        super().hideEvent(e)
        self.isVisibleChanged.emit(False)

    def hide(self):
        if not self.isVisible():
            self._hk_active = False
            self._focus_timer.stop()
            self._emotion = None
            self._closing_enabled = False
            super().hide()
            return
        if self._hiding:
            return
        self._hiding = True
        self._hk_active = False           # 关掉全局捕获，避免上屏注入被误吞
        self._focus_timer.stop()
        # 淡出后再真正隐藏
        self._anim.stop()
        self._anim.setStartValue(self._opacity_fx.opacity())
        self._anim.setEndValue(0.0)
        self._anim.start()

    def shutdown(self):
        """退出前卸载全局键盘/鼠标钩子，避免钩子线程残留。"""
        try:
            self._focus_timer.stop()
        except Exception:
            pass
        self._hk_active = False
        try:
            self._interceptor.stop()
        except Exception:
            pass
        try:
            self._mouse_watch.stop()
        except Exception:
            pass

    def _on_anim_finished(self):
        if not self._hiding:
            return
        self._hiding = False
        self._emotion = None
        self._closing_enabled = False
        super().hide()

    def event(self, e):
        if e.type() == QEvent.WindowDeactivate:
            if (self.isVisible() and self._closing_enabled
                    and self.config.get("auto_hide_on_blur", True)):
                self._closing_enabled = False
                self.hide()
        return super().event(e)

    # ---------- 失焦自动关闭 ----------
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
        # 只有“曾经在输入框里”才允许因失焦自动关闭
        if self._had_editable:
            self._closing_enabled = False
            self.hide()

    # ---------- 定位 ----------
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
        # 贴在光标下方一点点，视觉上和系统输入法候选条一致
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

    # ---------- 配置热更新 ----------
    def apply_config(self, config):
        self.config = config
        self.page_size = int(config.get("page_size", 3))
        self._apply_config_visuals()
        # 切主题后强制重算样式并立即重绘，确保浅/深色切换实时生效
        self.style().unpolish(self)
        self.style().polish(self)
        if len(self._chips) != self.page_size:
            self._rebuild_chips()
        self.page = 0
        self.active = 0
        self._render()
        self.repaint()

    # ---------- 圆角半透明背景 + 自绘柔和阴影 ----------
    def paintEvent(self, e):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        pad = SHADOW_PAD
        panel = self.rect().adjusted(pad, pad, -pad, -pad)
        # 柔和阴影：多层由外向内、alpha 递减的圆角矩形，全部落在窗口矩形内
        sc = getattr(self, "_shadow_color", QColor(0, 0, 0, 40))
        base = sc.alpha()
        for d in range(pad, 0, -1):
            a = max(1, int(base * (1 - (d - 1) / pad) * 0.30))
            col = QColor(sc.red(), sc.green(), sc.blue(), a)
            rect = panel.adjusted(-d, -d, d, d)
            path = QPainterPath()
            r = RADIUS + d * 0.4
            path.addRoundedRect(rect.x(), rect.y(), rect.width(), rect.height(), r, r)
            painter.fillPath(path, col)
        # 面板本体
        bg = getattr(self, "_panel_bg", QColor(255, 255, 255, 250))
        ppath = QPainterPath()
        ppath.addRoundedRect(panel.x(), panel.y(), panel.width(), panel.height(),
                             RADIUS, RADIUS)
        painter.fillPath(ppath, bg)
        alpha = getattr(self, "_border_alpha", 18)
        pen = QPen(QColor(0, 0, 0, alpha) if alpha <= 40 else QColor(255, 255, 255, alpha))
        pen.setWidth(1)
        painter.strokePath(ppath, pen)
        super().paintEvent(e)
