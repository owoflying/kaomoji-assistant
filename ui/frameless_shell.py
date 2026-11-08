"""无边框窗口外壳：WinUI 3 风格窗口的"机械部分"基类。

**拆出来的理由**：`UnifiedSettingsWindow` 原先 1172 行里混着三件互不相干的事——
1. 无边框窗口的机械逻辑（圆角遮罩、八向缩放热区、最大化贴工作区、状态保持）；
2. 侧边栏导航与页面栈调度；
3. 业务页本身。

这里接管第 1 件。子类只管"放什么内容"，不必再关心 `WM_NCHITTEST` 怎么算
或隐藏重开时原生 `WS_MAXIMIZE` 残留怎么清。

**子类契约**（缺一不可，缺了表现为"窗口拖不动/缩不小/最大化盖住任务栏"）：
  * `_RADIUS` / `_PAD` / `_TITLEBAR_H` —— 圆角、留白、标题栏高度；
  * `self.theme`  —— 有 `.dark` 与 `.window_tint` 等令牌的主题对象；
  * `self.central`  —— 内容根 widget（圆角遮罩作用在它身上）；
  * `self._root_layout` —— 内容根布局（最大化时留白归零）；
  * `self._title_btn_rects()` —— [(x, y, w, h)] 标题按钮矩形，避免被 HTCAPTION 吞掉点击。

⚠ 所有 Win32 常量与 `argtypes` 都在本模块内声明：ctypes 默认 `restype` 是
`c_int`，64 位下 HWND/HMONITOR 超过 2^31 会被截断，多显示器时最大化会贴错屏。
"""
import ctypes
from ctypes.wintypes import DWORD, MSG, POINT, RECT

from PySide6.QtCore import QPoint, QRect, QRectF, Qt, QEvent, QTimer
from PySide6.QtGui import QPainter, QPainterPath, QRegion, QTransform
from PySide6.QtWidgets import QMainWindow

from ui.frameless_panel import paint_frameless_panel
from ui.win_style import (
    DWMSBT_TRANSIENTWINDOW, _has_dwm, apply_backdrop, apply_dark_mode,
)


# ---- 最大化贴合工作区所需的 Windows 结构 ----
class _MINMAXINFO(ctypes.Structure):
    _fields_ = [
        ("ptReserved", POINT),
        ("ptMaxSize", POINT),
        ("ptMaxPosition", POINT),
        ("ptMinTrackSize", POINT),
        ("ptMaxTrackSize", POINT),
    ]


class _MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", DWORD),
        ("rcMonitor", RECT),
        ("rcWork", RECT),
        ("dwFlags", DWORD),
    ]


# WM 消息与 NCHITTEST 命中测试返回值
WM_NCHITTEST = 0x0084
WM_GETMINMAXINFO = 0x0024
WM_NCLBUTTONDOWN = 0x00A1
HTCLIENT, HTCAPTION = 1, 2
HTLEFT, HTRIGHT, HTTOP = 10, 11, 12
HTTOPLEFT, HTTOPRIGHT = 13, 14
HTBOTTOM, HTBOTTOMLEFT, HTBOTTOMRIGHT = 15, 16, 17


def _init_user32_signatures():
    """显式声明用到的 Win32 函数签名（模块导入时执行一次）。

    ctypes 默认 restype 是 c_int：64 位下 HWND/HMONITOR 这类句柄一旦超过 2^31
    就会被截断。日常单显示器看不出问题，多显示器/高句柄值时会算错工作区，
    表现为最大化后尺寸不对或贴错屏幕。
    """
    try:
        u = ctypes.windll.user32
        u.MonitorFromWindow.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        u.MonitorFromWindow.restype = ctypes.c_void_p
        u.GetMonitorInfoW.argtypes = [ctypes.c_void_p, ctypes.POINTER(_MONITORINFO)]
        u.GetMonitorInfoW.restype = ctypes.c_bool
        u.GetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int]
        u.GetWindowLongW.restype = ctypes.c_long
        u.SetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_long]
        u.SetWindowLongW.restype = ctypes.c_long
        u.SetWindowPos.argtypes = [
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
            ctypes.c_int, ctypes.c_int, ctypes.c_uint]
        u.SetWindowPos.restype = ctypes.c_bool
    except Exception:      # 非 Windows / 受限环境：保持 ctypes 默认行为
        pass


_init_user32_signatures()


class FramelessShell(QMainWindow):
    """带圆角遮罩、原生拖拽/缩放、最大化贴合工作区的无边框窗口基类。"""

    #: 圆角半径 / 窗口边缘留白（也是阴影与缩放热区宽度）/ 标题栏高度
    _RADIUS = 12
    _PAD = 12
    _TITLEBAR_H = 40

    def _init_window(self):
        """无边框 + 透明背景（亚克力由 DWM 提供）。子类在 __init__ 里调用。"""
        self.setWindowFlags(self.windowFlags() | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setWindowTitle("颜文字输入辅助器")
        # 亚克力模糊为窗口核心视觉效果（DWM 不可用时 apply_backdrop 为 no-op）
        self._acrylic_on = _has_dwm
        # 窗口状态持久化：关闭/隐藏时记住是否最大化，下次 show 时恢复
        self._saved_maximized = False
        self._normal_geometry = None
        # 最小化前是否处于最大化（任务栏点击 = 最小化 -> 还原，需保住最大化）
        self._max_before_min = False

    # ---------- 子类必须提供 ----------
    def _title_btn_rects(self):
        """三个标题栏按钮在窗口坐标系中的矩形（用于 WM_NCHITTEST 排除点击）。"""
        return []

    # ---------- 圆角遮罩与留白 ----------
    def _restore_from_maximized_drag(self, lparam, enter_drag_loop=True):
        """最大化状态下按住标题栏拖动：还原窗口并让鼠标落在标题栏上，然后进入拖动。

        :param lparam: WM_NCLBUTTONDOWN 的 lParam，低 16 位为光标 x、高 16 位为 y
                       （屏幕坐标，已含符号扩展）。
        :param enter_drag_loop: 是否在最后发 `SC_MOVE` 进入系统拖动循环。
        设为 False 用于测试——那个调用会让 `SendMessageW` 阻塞到用户松开鼠标，
        在自动化测试里会直接挂住。

        三个要点：

        1. **必须先清原生 WS_MAXIMIZE 再 showNormal**（复用 `_clear_native_maximized`），
           否则窗口还原后被钳回工作区，拖动会"甩不动"；
        2. **还原尺寸用上次的普通几何**（`_normal_geometry`），没有就用
           "工作区 × 0.75" 兜底，保证还原后是一个正常大小的窗口；
        3. **窗口要摆到鼠标正下方**，且鼠标相对窗口的位置按"最大化时的相对比例"
           换算——这样用户按下时手指在哪、还原后窗口就在哪，不会突兀跳走。
        """
        try:
            x = ctypes.c_short(lparam & 0xFFFF).value
            y = ctypes.c_short((lparam >> 16) & 0xFFFF).value
        except Exception:
            return

        # 最大化时窗口的几何（用来算鼠标的相对位置比例）
        max_geo = self.geometry()
        max_w = max(1, max_geo.width())
        # 鼠标在窗口中的相对横向比例（0=最左，1=最右）
        rel_x = (x - max_geo.x()) / max_w if max_w else 0.5
        rel_x = min(1.0, max(0.0, rel_x))

        # 还原尺寸：优先用上次的普通几何
        target = self._normal_geometry
        if target is None or not target.isValid():
            try:
                avail = self.screen().availableGeometry()
            except Exception:
                avail = None
            if avail is not None:
                # 兜底尺寸：取工作区的 75%，但**不得低于窗口最小尺寸**，
                # 否则会被 minimumSize 撑开，出现"还原后比预期还小/还怪"
                # 的结果（实测 728×0.75≈546 被 minHeight 640 撑成 640）。
                min_w = max(self.minimumWidth(), 320)
                min_h = max(self.minimumHeight(), 240)
                w = max(int(avail.width() * 0.75), min_w)
                h = max(int(avail.height() * 0.75), min_h)
                w = min(w, avail.width())
                h = min(h, avail.height())
                target = QRect(avail.x() + (avail.width() - w) // 2,
                               avail.y() + (avail.height() - h) // 2, w, h)
            else:
                target = QRect(x - 100, y, 1024, 720)

        # 还原（先清原生最大化样式，否则 setGeometry 会被钳住）
        self._clear_native_maximized()
        if self.isMaximized():
            self.showNormal()

        # ⚠ 顺序很关键：**先**把窗口外观状态从"最大化态"切回"普通态"
        # （留白 _PAD 恢复），**再** setGeometry。
        # 反过来的话，setGeometry 发生时根布局边距仍是最大化时的 0，
        # 窗口内容按错误边距重算，实测会把 720 高的窗口压成 640。
        self._apply_window_chrome_state()
        self._update_mask()
        self.setGeometry(target)
        self.update()

        # 把窗口摆到鼠标正下方：鼠标落在标题栏纵向中心，
        # 横向位置按上面算出的相对比例映射到新宽度
        grab_x = int(target.width() * rel_x)
        grab_y = self._PAD + self._TITLEBAR_H // 2
        new_x = x - grab_x
        new_y = y - grab_y

        # 夹到工作区内：最大化时鼠标常贴在屏幕顶端（y≈0），
        # 直接减标题栏高度会让窗口顶部跑到屏幕外（实测 new_y = -12，
        # 标题栏被任务栏/屏幕边缘吃掉一部分，用户抓不到）。
        # 规则：**标题栏必须完整可见**，即窗口顶不高于工作区顶部。
        try:
            screen = self.screen()
            avail = screen.availableGeometry() if screen is not None else None
        except Exception:
            avail = None
        if avail is not None:
            new_y = max(avail.y(), min(new_y,
                                       avail.y() + avail.height() - self._TITLEBAR_H))
            new_x = max(avail.x() - self._PAD,
                        min(new_x, avail.x() + avail.width() - target.width() // 2))
        self.move(new_x, new_y)

        # 交给系统进入拖动循环（等价于原生"按住标题栏拖"）
        if not enter_drag_loop:
            return
        try:
            user32 = ctypes.windll.user32
            user32.ReleaseCapture()
            WM_SYSCOMMAND = 0x0112
            SC_MOVE = 0xF010
            user32.SendMessageW(int(self.winId()), WM_SYSCOMMAND, SC_MOVE, 0)
        except Exception:
            pass

    def _update_mask(self):
        """把内容区裁剪到圆角矩形内，使子控件四角随窗口一起圆角化。

        最大化时取消遮罩（全矩形，方角），让面板贴合屏幕工作区。
        """
        if getattr(self, "central", None) is None:
            return
        w, h = self.width(), self.height()
        if w <= 0 or h <= 0:
            return
        if self.isMaximized():
            self.central.clearMask()
            return
        P = self._PAD
        r = self._RADIUS
        rect = QRect(P, P, w - 2 * P, h - 2 * P)
        pp = QPainterPath()
        pp.addRoundedRect(QRectF(rect), r, r)
        poly = pp.toFillPolygon(QTransform())
        self.central.setMask(QRegion(poly.toPolygon()))

    def _apply_window_chrome_state(self):
        """根据是否最大化调整留白：最大化时留白归零、面板贴合屏幕工作区。"""
        pad = 0 if self.isMaximized() else self._PAD
        if getattr(self, "_root_layout", None) is not None:
            self._root_layout.setContentsMargins(pad, pad, pad, pad)

    def _update_backdrop(self):
        """DWM 亚克力 + 深色标题栏，跟随当前主题。"""
        hwnd = int(self.winId())
        apply_backdrop(hwnd, DWMSBT_TRANSIENTWINDOW)
        apply_dark_mode(hwnd, self.theme.dark)

    # ---------- 最大化 / 还原 ----------
    def _toggle_maximize(self):
        if self.isMaximized():
            self.showNormal()
            # 不要在这里硬 setGeometry：会和 Windows 自己的还原逻辑打架。
            # 只在还原确实没生效时兜底（见 _ensure_normal_geometry）。
            QTimer.singleShot(0, self._ensure_normal_geometry)
        else:
            # 最大化前保存普通几何，供恢复时使用
            g = self.geometry()
            if g.isValid():
                self._normal_geometry = g
            self.showMaximized()

    def _clear_native_maximized(self):
        """清掉原生 WS_MAXIMIZE 样式残留。

        无边框窗口在隐藏态下调 setWindowState(NoState) 不会清原生样式，
        重新显示时窗口仍带最大化样式：setGeometry 被钳到工作区（报
        'Unable to set geometry' 警告），且 WM_NCHITTEST 在 maximized 下禁用
        缩放热区，表现成「窗口卡住、无法缩小」。
        """
        try:
            hwnd = int(self.winId())
            if not hwnd:
                return
            user32 = ctypes.windll.user32
            GWL_STYLE = -16
            WS_MAXIMIZE = 0x01000000
            style = user32.GetWindowLongW(hwnd, GWL_STYLE)
            if style & WS_MAXIMIZE:
                user32.SetWindowLongW(hwnd, GWL_STYLE, style & ~WS_MAXIMIZE)
                # NOMOVE | NOSIZE | NOZORDER | FRAMECHANGED：仅刷新样式，使去最大化立即生效
                user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0004 | 0x0020)
        except Exception:
            pass

    def _ensure_normal_geometry(self):
        """还原后若窗口仍占满工作区，说明 showNormal 没真正落地，用保存值兜底。

        关键：先清掉原生 WS_MAXIMIZE 残留（见 _clear_native_maximized），否则窗口会
        卡在最大化态——既报 'Unable to set geometry' 警告，又因 WM_NCHITTEST 在
        maximized 下禁用缩放热区而「无法缩小」。
        """
        if not self.isVisible() or self.isMinimized():
            return
        self._clear_native_maximized()
        if self.isMaximized():
            self.showNormal()
        geom = getattr(self, "_normal_geometry", None)
        if geom is None or not geom.isValid():
            return
        try:
            avail = self.screen().availableGeometry()
        except Exception:
            return
        cur = self.geometry()
        if cur.width() >= avail.width() - 2 and cur.height() >= avail.height() - 2:
            self.setGeometry(geom)

    def _remember_and_reset_state(self):
        """窗口隐藏后：记住最大化意图，并把 Qt 侧窗口状态清零。

        为什么必须清零（「重新打开后第一次点还原被吞掉」的根因）：
          Qt 隐藏窗口时**不会**清掉 WindowMaximized 标志，而
          QWidget.setWindowState() 在 old == new 时会直接 return。
          于是「最大化 -> 关闭 -> 重新打开」时 showMaximized() 里的
          setWindowState(Maximized) 因状态没变而早退，原生侧从未收到这次
          状态设置，Qt 记的状态和 Windows 的 WINDOWPLACEMENT 失配。
          结果第一次点「还原」时 showNormal() 只改了 Qt 状态，原生窗口纹丝
          不动 —— 看起来就是这一下被吞了，得再点一次才生效。

        隐藏状态下调用 setWindowState 是安全的：Qt 的 setWindowState_sys 里有
        `visible` 守卫，不可见时只更新内部状态、不会 ShowWindow。
        """
        if self.isMinimized():
            return                        # 最小化不算隐藏，别把状态记错
        self._saved_maximized = self.isMaximized()
        if not self._saved_maximized:
            g = self.geometry()
            if g.isValid():
                self._normal_geometry = g
        if self.windowState() != Qt.WindowNoState:
            try:
                self.setWindowState(Qt.WindowNoState)
            except Exception:
                pass

    def _track_minimize_restore(self, old_state):
        """让最大化状态扛得住「任务栏图标连点」。

        点一下任务栏图标 = 最小化，再点一下 = 还原。无边框窗口接管了
        WM_GETMINMAXINFO，这条还原路径上 Windows 偶尔会把窗口还原成普通尺寸
        而不是最大化，用户看到的就是「莫名其妙退出了最大化」。这里记住
        「最小化之前是不是最大化」，还原后若掉出最大化就补一次 showMaximized。
        """
        if self.isMinimized():
            # Qt 在最小化时会保留 WindowMaximized 位，所以新旧状态都查一遍更稳
            self._max_before_min = bool(old_state & Qt.WindowMaximized) or bool(
                self.windowState() & Qt.WindowMaximized)
            return
        if old_state & Qt.WindowMinimized:
            if self._max_before_min and not self.isMaximized():
                # 延后一轮：等 Windows 把还原动画/几何处理完再拉回最大化
                QTimer.singleShot(0, self._restore_maximized)
            self._max_before_min = False

    def _restore_maximized(self):
        if not self.isVisible() or self.isMinimized() or self.isMaximized():
            return
        self.showMaximized()

    # ---------- Qt 事件 ----------
    def show(self):
        """重写 show：若上次关闭时处于最大化，则直接以最大化状态显示。

        依赖 hideEvent 里已把 Qt 侧窗口状态清成 WindowNoState，
        这样这里的 showMaximized() 一定是一次「真实的状态跃迁」，
        Qt 与原生 WINDOWPLACEMENT 保持同步（详见 _remember_and_reset_state）。
        """
        if self.isVisible():
            super().show()
            return
        if getattr(self, "_saved_maximized", False):
            self.showMaximized()
            return
        geom = getattr(self, "_normal_geometry", None)
        if geom is not None and geom.isValid():
            # 隐藏态走 hideEvent 只清了 Qt 侧状态，原生 WS_MAXIMIZE 仍残留，
            # 不先清掉就 setGeometry 会被钳到工作区。
            self._clear_native_maximized()
            self.setGeometry(geom)
        super().show()

    def hideEvent(self, e):
        super().hideEvent(e)
        self._remember_and_reset_state()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        # 只要是在「普通态」下改变尺寸，就记下它作为还原基准。
        # 这样无论用户是点最大化按钮、双击标题栏还是走系统菜单进入最大化，
        # 都能还原到正确的普通尺寸——原先只在 _toggle_maximize 里记录，
        # 走其它路径进入最大化时 _normal_geometry 仍是 None，只能靠兜底尺寸，
        # 会出现"还原后窗口尺寸不对"的问题。
        if not self.isMaximized() and not self.isMinimized():
            g = self.geometry()
            if g.isValid() and g.width() > 0 and g.height() > 0:
                self._normal_geometry = QRect(g)
        self._apply_window_chrome_state()
        self._update_mask()

    def changeEvent(self, e):
        super().changeEvent(e)
        if e.type() == QEvent.WindowStateChange:
            self._track_minimize_restore(e.oldState())
            self._apply_window_chrome_state()
            self._update_mask()
            self.update()

    def nativeEvent(self, eventType, message):
        """处理原生消息：
        * WM_GETMINMAXINFO：让最大化贴合显示器工作区（不含任务栏），
          避免无边框窗口最大化时盖住任务栏 / 边缘留白；
        * WM_NCHITTEST：标题栏原生拖拽、窗口八向缩放（最大化时禁用缩放热区）；
        * WM_NCLBUTTONDOWN：最大化时按住标题栏拖动 → 还原为普通窗口并跟随鼠标。
        """
        # ⚠ eventType 可能是 str、bytes 或 QByteArray：
        # PySide6 在真实消息派发时传 QByteArray，测试里常传 bytes/str。
        # 直接 `!= "windows_generic_MSG"` 在 bytes 情形下恒为真，
        # 会让整个 nativeEvent 逻辑被静默跳过（测试表现为 ValueError）。
        etype = eventType
        if isinstance(etype, (bytes, bytearray)):
            etype = etype.decode("ascii", "ignore")
        elif etype is not None and not isinstance(etype, str):
            try:
                etype = bytes(etype).decode("ascii", "ignore")
            except Exception:
                etype = str(etype)
        if etype != "windows_generic_MSG":
            return super().nativeEvent(eventType, message)
        msg = MSG.from_address(int(message))

        # 1) 最大化贴合工作区
        if msg.message == WM_GETMINMAXINFO:
            try:
                hwnd = int(self.winId())
                monitor = ctypes.windll.user32.MonitorFromWindow(hwnd, 2)  # MONITOR_DEFAULTTONEAREST
                mi = _MONITORINFO()
                mi.cbSize = ctypes.sizeof(_MONITORINFO)
                if ctypes.windll.user32.GetMonitorInfoW(monitor, ctypes.byref(mi)):
                    wa, mon = mi.rcWork, mi.rcMonitor
                    mmi = _MINMAXINFO.from_address(int(msg.lParam))
                    # ptMaxPosition 是**相对于所在显示器**的坐标，不是屏幕绝对坐标。
                    # 主屏原点为 (0,0) 时两者恰好相同，所以单屏看不出问题；
                    # 副屏上用绝对坐标会把窗口最大化到错误位置。
                    mmi.ptMaxPosition.x = wa.left - mon.left
                    mmi.ptMaxPosition.y = wa.top - mon.top
                    mmi.ptMaxSize.x = wa.right - wa.left
                    mmi.ptMaxSize.y = wa.bottom - wa.top
            except Exception:
                pass
            return True, 0

        # 2) 最大化时按住标题栏拖动 → 还原成普通窗口
        #
        # 原生窗口由 DefWindowProc 处理这条消息时会自动"还原并拖动"，
        # 但无边框窗口在 WM_NCHITTEST 里自己返回 HTCAPTION，等于接管了标题栏，
        # 系统就不再替我们做这件事 —— 表现为"最大化后拖下来没反应"。
        #
        # 这里手动实现：先还原（恢复上次的普通尺寸），再把窗口摆到鼠标正下方，
        # 最后交给系统进入拖动循环（SendMessage SC_MOVE），手感与原生一致。
        if msg.message == WM_NCLBUTTONDOWN and msg.wParam == HTCAPTION:
            if self.isMaximized():
                self._restore_from_maximized_drag(msg.lParam)
                return True, 0

        if msg.message != WM_NCHITTEST:
            return super().nativeEvent(eventType, message)

        x = ctypes.c_short(msg.lParam & 0xFFFF).value
        y = ctypes.c_short((msg.lParam >> 16) & 0xFFFF).value
        g = self.frameGeometry()
        lx = x - g.x()
        ly = y - g.y()
        w, h = self.width(), self.height()
        maximized = self.isMaximized()
        P = 0 if maximized else self._PAD
        TH = self._TITLEBAR_H

        # 2) 缩放热区（仅非最大化时有留白时才生效）
        if not maximized and P > 0:
            R = P  # 缩放热区覆盖整圈留白
            if lx <= R and ly <= R:
                return True, HTTOPLEFT
            if lx >= w - R and ly <= R:
                return True, HTTOPRIGHT
            if lx <= R and ly >= h - R:
                return True, HTBOTTOMLEFT
            if lx >= w - R and ly >= h - R:
                return True, HTBOTTOMRIGHT
            if lx <= R:
                return True, HTLEFT
            if lx >= w - R:
                return True, HTRIGHT
            if ly >= h - R:
                return True, HTBOTTOM
            if ly <= R:
                return True, HTTOP
        # 3) 标题栏区域：拖动移动（标题按钮除外，保证可点击）
        if P <= ly <= P + TH:
            for rect in self._title_btn_rects():
                if rect.contains(QPoint(lx, ly)):
                    return True, HTCLIENT
            return True, HTCAPTION
        return True, HTCLIENT

    def paintEvent(self, e):
        """自绘：亚克力基底（DWM 提供模糊）+ 竖向渐变 + 1px 圆角边框 + 柔和外阴影。

        最大化时取消阴影、圆角与边框，让面板贴合屏幕工作区。
        具体笔法见 ui/frameless_panel（与日志窗口/插件弹窗共用同一实现）。
        子类通过 `_adjusted_panel_base()` 提供按不透明度缩放后的基底色。
        """
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        maximized = self.isMaximized()
        P = 0 if maximized else self._PAD
        r = 0 if maximized else self._RADIUS
        t = self.theme
        if maximized:
            panel = QRect(0, 0, self.width(), self.height())
        else:
            panel = QRect(P, P, self.width() - 2 * P, self.height() - 2 * P)

        paint_frameless_panel(
            p, panel, radius=r,
            tint=self._adjusted_panel_base(),
            grad_top=t.window_grad_top,
            grad_bottom=t.window_grad_bottom,
            border=None if maximized else t.window_border,
            shadow=None if maximized else self._shadow_color,
            shadow_pad=P,
        )
        super().paintEvent(e)

    def _adjusted_panel_base(self):
        """面板基底色（子类按 panel_alpha 缩放；基类给个安全默认）。"""
        return self.theme.bg
