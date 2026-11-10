"""插件管理页：浏览已安装插件、启用/停用、重载、打开插件目录。

插件是**正式功能**，不再受「测试功能」开关门控（早期曾在测试门控下，
现已转正）。首次启用插件时会弹一次风险说明，见 `ui/risk_dialog.py`。

视觉沿用 Win11 设置风格：PageTitle + Card 卡片 + ToggleSwitch 开关。
"""
import os
import subprocess
import sys

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame,
    QScrollArea, QSizePolicy,
)
from PySide6.QtCore import Qt

from ui.toggle_switch import ToggleSwitch
from ui.fluent_icons import plugin_pixmap
from ui.pages._scaffold import (
    caption, card, card_layout, page_layout, page_title,
)
from ui.win11_theme import Theme, apply_child_theme
from core.plugins import STATE_DISABLED, STATE_ERROR, STATE_LOADED, STATE_TEXT


def _state_text(state):
    return STATE_TEXT.get(state, state)


class _PluginCard(QFrame):
    """单个插件卡片：名称/版本/状态徽标 + 描述 + 作者 + 重载按钮 + 启用开关。

    若插件通过 api.register_settings_widget() 注册了设置面板（API v2），
    卡片底部会多出一块可折叠的设置区（懒加载、实例复用）。
    """

    def __init__(self, rec, manager, theme, host=None, parent=None):
        super().__init__(parent)
        self.rec = rec
        self.manager = manager
        self._host = host                # PluginPage：提供设置面板的构建/缓存
        self._theme = theme if isinstance(theme, Theme) else Theme(theme or "light")
        theme = self._theme
        self.setObjectName("Card")
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(8)

        # ---- 第一行：拼图图标 + 名称 + 版本 + 状态徽标 +（右侧）开关 ----
        top = QHBoxLayout()
        top.setSpacing(10)
        self.icon_lbl = QLabel()
        self.icon_lbl.setFixedSize(22, 22)
        self.icon_lbl.setAlignment(Qt.AlignCenter)
        top.addWidget(self.icon_lbl)

        self.name_lbl = QLabel(rec["manifest"].get("name") or rec["id"])
        # 注意：该 label 有自身样式表，会切断祖先 QSS 的 color 级联（Qt 行为），
        # 必须把 color 一并写进自身样式，否则深色模式下白卡片配黑字不可读。
        self.name_lbl.setStyleSheet("color:%s;font-weight:600;background:transparent;" % theme.text)
        top.addWidget(self.name_lbl)

        self.ver_lbl = QLabel("v%s" % rec["manifest"].get("version", "?"))
        self.ver_lbl.setObjectName("Caption")
        top.addWidget(self.ver_lbl)

        self.state_lbl = QLabel()
        self.state_lbl.setObjectName("Caption")
        top.addWidget(self.state_lbl)
        top.addStretch(1)

        self.toggle = ToggleSwitch(theme)
        self.toggle.setChecked(
            rec["state"] == STATE_LOADED or manager.is_enabled(rec["id"]))
        self.toggle.toggled.connect(self._on_toggle)
        top.addWidget(self.toggle)
        root.addLayout(top)

        # ---- 第二行：描述 ----
        desc = rec["manifest"].get("description") or "（无描述）"
        self.desc_lbl = QLabel(desc)
        self.desc_lbl.setObjectName("Caption")
        self.desc_lbl.setWordWrap(True)
        root.addWidget(self.desc_lbl)

        # ---- 第三行：作者 + 提供能力摘要 + 重载 ----
        bottom = QHBoxLayout()
        bottom.setSpacing(10)
        author = rec["manifest"].get("author") or ""
        meta = []
        if author:
            meta.append("作者：%s" % author)
        api = rec.get("api")
        if api is not None:
            n_src = len(getattr(manager, "_sources", {}).get(rec["id"], []))
            n_tr = sum(1 for t in getattr(manager, "_transforms", [])
                       if t["plugin"] == rec["id"])
            caps = []
            if n_src:
                caps.append("%d 个分类" % n_src)
            if n_tr:
                caps.append("%d 个转换" % n_tr)
            if caps:
                meta.append("提供：" + "、".join(caps))
        self.meta_lbl = QLabel("  ·  ".join(meta))
        self.meta_lbl.setObjectName("Caption")
        bottom.addWidget(self.meta_lbl, 1)

        self.reload_btn = QPushButton("重载")
        self.reload_btn.setCursor(Qt.PointingHandCursor)
        self.reload_btn.clicked.connect(lambda: self.manager.reload(self.rec["id"]))
        bottom.addWidget(self.reload_btn)
        root.addLayout(bottom)

        # ---- 错误信息（仅 error 状态显示） ----
        self.err_lbl = QLabel(rec["error"] or "")
        self.err_lbl.setWordWrap(True)
        root.addWidget(self.err_lbl)
        self.err_lbl.setVisible(bool(rec["error"]))

        # ---- 插件自定义设置区（API v2 register_settings_widget，可选） ----
        self.settings_btn = None
        self.settings_box = None
        self._settings_built = False
        if self._host is not None and self._host.has_settings(rec["id"]):
            self._build_settings_shell()

        self.set_theme(theme)

    # ---------- 插件自定义设置区 ----------
    def _build_settings_shell(self):
        """构建「设置」折叠按钮与容器；真正的内容首次展开时才向插件索取（懒加载）。"""
        self.settings_btn = QPushButton("设置  ▾")
        self.settings_btn.setCursor(Qt.PointingHandCursor)
        self.settings_btn.setCheckable(True)
        self.settings_btn.setFixedHeight(30)
        self.settings_btn.clicked.connect(self._toggle_settings)

        self.settings_box = QWidget()
        sl = QVBoxLayout(self.settings_box)
        sl.setContentsMargins(0, 6, 0, 2)
        sl.setSpacing(8)
        self._settings_layout = sl
        # 顶部一条细分隔线，视觉上把插件信息与自定义设置区分开
        line = QFrame()
        line.setObjectName("Divider")
        line.setFixedHeight(1)
        sl.addWidget(line)
        self.settings_box.setVisible(False)

        lay = self.layout()
        lay.insertWidget(lay.count() - 1, self.settings_box)
        lay.insertWidget(lay.count() - 1, self.settings_btn)

    def _toggle_settings(self, checked):
        if self.settings_box is None:
            return
        self.settings_box.setVisible(bool(checked))
        self.settings_btn.setText("设置  ▴" if checked else "设置  ▾")
        if checked and not self._settings_built:
            self._build_settings_content()

    def _build_settings_content(self):
        """向插件 factory 索取内容；失败时展示错误占位而非让主页面崩掉。"""
        self._settings_built = True
        widget, err = (None, None)
        try:
            widget = self._host.build_settings(self.rec["id"], self._theme)
        except Exception as e:
            err = "%s: %s" % (type(e).__name__, e)
        if isinstance(widget, QWidget):
            try:
                widget.setParent(self.settings_box)
                self._settings_layout.addWidget(widget)
                widget.show()
            except RuntimeError as e:
                err = "挂载设置面板失败：%s" % e
                widget = None
        if not isinstance(widget, QWidget):
            # factory 没返回 widget、抛了异常、或返回对象已失效：给一个可读的占位
            placeholder = QLabel("（插件设置面板加载失败）" if not err else err)
            placeholder.setObjectName("Caption")
            placeholder.setWordWrap(True)
            placeholder.setStyleSheet(
                "color:%s;" % ("#ff99a4" if self._theme.dark else "#c42b1c"))
            self._settings_layout.addWidget(placeholder)
            self._host.forget_settings(self.rec["id"])

    def _on_toggle(self, checked):
        # ToggleSwitch.mouseReleaseEvent 已自改状态，这里只把结果交给管理器
        if checked:
            # 第一次启用插件时说明一下：插件权限和主程序一样高
            try:
                from ui import risk_dialog
                risk_dialog.maybe_show_plugin_risk(
                    parent=self.window(),
                    theme_name=getattr(self._theme, "name", "light"))
            except Exception:
                pass
        self.manager.set_enabled(self.rec["id"], bool(checked))

    def set_theme(self, t):
        self._theme = t
        err_color = "#ff99a4" if t.dark else "#c42b1c"
        state_color, state_bg = {
            STATE_LOADED: (t.accent, t.accent_bg),
            STATE_DISABLED: (t.text_tertiary, "transparent"),
            STATE_ERROR: (err_color, "transparent"),
        }.get(self.rec["state"], (t.text_secondary, "transparent"))
        self.state_lbl.setText(_state_text(self.rec["state"]))
        self.state_lbl.setStyleSheet(
            "color:%s;background:%s;border-radius:8px;padding:1px 8px;"
            "font-weight:600;" % (state_color, state_bg))
        self.icon_lbl.setPixmap(plugin_pixmap(22, state_color))
        self.name_lbl.setStyleSheet(
            "color:%s;font-weight:600;background:transparent;" % t.text)
        self.err_lbl.setStyleSheet("color:%s;" % err_color)
        self.toggle.update_theme(t)
        if self.settings_btn is not None:
            self.settings_btn.setStyleSheet(
                "QPushButton{color:%s;background:transparent;"
                "border:1px solid %s;border-radius:6px;padding:2px 10px;}"
                "QPushButton:hover{background:%s;}"
                "QPushButton:checked{color:%s;}"
                % (t.text_secondary, t.card_border, t.nav_hover, t.accent))


class PluginPage(QWidget):
    """「插件」页：管理本地插件。"""

    def __init__(self, manager, theme=None, parent=None):
        super().__init__(parent)
        self.manager = manager
        # theme 接受 Theme 实例或主题名（与 ToggleSwitch 等控件约定一致）
        self._theme = theme if isinstance(theme, Theme) else Theme(theme or "light")
        self._cards = []
        # 插件自定义设置面板实例缓存：plugin_id -> QWidget。
        # 缓存的意义在于「刷新插件页不丢输入」——用户填了一半的设置不会因为
        # 重载别的插件而被重建。卸载/关页时统一释放。
        self._settings_cache = {}
        self._init_ui()
        self.refresh()
        # 插件注册/注销 UI 能力时同步刷新卡片（是否出现「设置」入口）
        if manager is not None and hasattr(manager, "ui_changed"):
            try:
                manager.ui_changed.connect(self.refresh)
            except Exception:
                pass

    # ---------- 插件设置面板缓存（供 _PluginCard 通过 host 调用） ----------
    def _drop_stale_settings(self):
        """丢掉已不属于任何已加载插件的缓存面板（插件被停用/卸载后，它的
        widget 不应继续留在宿主里占内存、持有回调引用）。"""
        try:
            live = set(self.manager.settings_widgets())
        except Exception:
            return
        for pid in [p for p in self._settings_cache if p not in live]:
            self.forget_settings(pid)

    def has_settings(self, plugin_id):
        try:
            return plugin_id in self.manager.settings_widgets()
        except Exception:
            return False

    def build_settings(self, plugin_id, theme):
        """取得插件设置面板 widget；已缓存则复用，否则调用插件 factory 构建。"""
        cached = self._settings_cache.get(plugin_id)
        if cached is not None:
            try:
                # 脱离旧容器前先 hide：否则 setParent(None) 瞬间它会成为
                # 独立顶层窗口闪一下（同 refresh 里的卡片问题）
                cached.hide()
                cached.setParent(None)      # 先脱离旧容器，交给新卡片重新挂载
                return cached
            except RuntimeError:
                self._settings_cache.pop(plugin_id, None)
        factory = self.manager.settings_widgets()[plugin_id]["factory"]
        widget = factory(None)
        if not isinstance(widget, QWidget):
            return None
        apply_child_theme(widget, theme)
        self._settings_cache[plugin_id] = widget
        return widget

    def forget_settings(self, plugin_id):
        """设置面板构建失败：清掉缓存，避免下次还拿同一个坏对象。"""
        w = self._settings_cache.pop(plugin_id, None)
        if w is not None:
            try:
                w.hide()            # 先隐藏，避免 setParent(None) 时闪现为顶层窗口
                w.setParent(None)
                w.deleteLater()
            except RuntimeError:
                pass

    def release_settings(self):
        """刷新卡片前把缓存 widget 摘出卡片树，避免被卡片 deleteLater 连带析构。"""
        for pid in list(self._settings_cache):
            w = self._settings_cache.get(pid)
            try:
                w.hide()
                w.setParent(None)
            except RuntimeError:
                self._settings_cache.pop(pid, None)

    def discard_settings(self):
        """整页销毁前彻底释放插件 widget（关闭/隐藏插件页时调用）。"""
        for pid in list(self._settings_cache):
            w = self._settings_cache.pop(pid, None)
            if w is not None:
                try:
                    w.hide()        # 先隐藏，避免 setParent(None) 时闪现为顶层窗口
                    w.setParent(None)
                    w.deleteLater()
                except RuntimeError:
                    pass

    # ---------- UI ----------
    def _init_ui(self):
        root = page_layout(self)
        root.addWidget(page_title("插件"))

        # 顶部说明卡：插件目录 + 操作
        tip_card = card()
        tip_root = card_layout(tip_card, spacing=10)
        tip = caption(
            "把插件文件夹放进插件目录即可被识别（需包含 plugin.json 与入口 main.py）。\n"
            "插件可以追加颜文字分类、注册上屏前的文本转换、订阅插入等事件；\n"
            "声明 api_version 2 后还能扩展界面：卡片内嵌设置面板、侧边栏独立页、主题化弹窗与托盘通知。")
        tip.setWordWrap(True)
        tip_root.addWidget(tip)

        btn_row = QHBoxLayout()
        self.open_btn = QPushButton("打开插件目录")
        self.open_btn.setCursor(Qt.PointingHandCursor)
        self.open_btn.clicked.connect(self._open_dir)
        self.scan_btn = QPushButton("重新扫描")
        self.scan_btn.setCursor(Qt.PointingHandCursor)
        self.scan_btn.clicked.connect(self._rescan)
        self.count_lbl = QLabel("")
        self.count_lbl.setObjectName("Caption")
        btn_row.addWidget(self.open_btn)
        btn_row.addWidget(self.scan_btn)
        btn_row.addStretch(1)
        btn_row.addWidget(self.count_lbl)
        tip_root.addLayout(btn_row)
        root.addWidget(tip_card)

        # 插件列表（滚动）
        self.scroll = QScrollArea()
        self.scroll.setFrameShape(QScrollArea.NoFrame)
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)

        body = QWidget()
        # 与设置页/关于页一致：SettingsBody 匹配全局 QSS 透明规则，
        # 避免 QScrollArea viewport 默认浅色背景在深色模式下透出。
        body.setObjectName("SettingsBody")
        self._body_layout = QVBoxLayout(body)
        self._body_layout.setContentsMargins(0, 0, 0, 0)
        self._body_layout.setSpacing(12)
        self._body_layout.addStretch(1)
        self._body = body
        self.scroll.setWidget(body)
        root.addWidget(self.scroll, 1)

    # ---------- 数据 ----------
    def refresh(self):
        """重建插件卡片列表（启用/停用/重载/重扫/UI 注册变化后触发）。"""
        self._drop_stale_settings()
        # 先把留存的插件设置面板摘出卡片树，否则下面的 deleteLater 会连带析构它
        self.release_settings()
        # 清空旧卡片
        #
        # ⚠ 必须先 hide() 再 setParent(None)：`setParent(None)` 会把控件变成
        # **独立顶层窗口**（Qt 语义：无父的 widget 就是窗口），在 deleteLater()
        # 真正执行前它会以顶层窗口身份显示出来 —— 表现为启用/停用一个插件时
        # 屏幕上闪过好几个小窗口（卡片有几个就闪几个）。
        # 正确姿势：removeWidget 摘出布局 → hide 隐藏 → setParent(None) → deleteLater。
        for old in self._cards:
            try:
                self._body_layout.removeWidget(old)
            except RuntimeError:
                pass
            try:
                old.hide()              # 关键：脱离父级前先隐藏，避免成为可见顶层窗口
                old.setParent(None)
                old.deleteLater()
            except RuntimeError:
                pass
        self._cards = []

        recs = sorted(self.manager.plugins.values(), key=lambda r: r["id"])
        t = self._theme or Theme("light")
        n_loaded = sum(1 for r in recs if r["state"] == STATE_LOADED)
        self.count_lbl.setText("共 %d 个 · 已启用 %d" % (len(recs), n_loaded) if recs else "")

        if not recs:
            empty = QLabel(
                "还没有插件。\n\n"
                "在插件目录里新建文件夹，放入 plugin.json 与 main.py 即可创建一个插件，\n"
                "例如最小示例：\n\n"
                '  plugin.json → {"name": "示例", "version": "1.0.0", "entry": "main.py"}\n\n'
                "  main.py     → def setup(api):\n"
                '                     api.add_source("示例", ["(￣▽￣)-c", "ヽ(°〇°)ﾉ"])')
            empty.setObjectName("Caption")
            empty.setWordWrap(True)
            empty.setAlignment(Qt.AlignCenter)
            self._body_layout.insertWidget(0, empty)
            self._cards.append(empty)   # 复用清理通道
        for rec in recs:
            card = _PluginCard(rec, self.manager, t, host=self)
            self._body_layout.insertWidget(self._body_layout.count() - 1, card)
            self._cards.append(card)

    # ---------- 操作 ----------
    def _open_dir(self):
        path = self.manager.plugins_dir
        try:
            os.makedirs(path, exist_ok=True)
        except OSError:
            pass
        try:
            if sys.platform == "win32":
                os.startfile(path)      # noqa: S606 - 用户主动打开资源管理器
            else:
                subprocess.Popen(["xdg-open", path])
        except Exception:
            pass

    def _rescan(self):
        self.manager.discover()
        self.manager.load_all()
        # discover/load_all 内部已 emit changed；这里兜底再刷一次 UI
        self.refresh()

    # ---------- 主题 ----------
    def set_theme(self, theme):
        self._theme = theme
        for page_card in self._cards:
            if isinstance(page_card, _PluginCard):
                page_card.set_theme(theme)
        # 插件自建的设置面板：先重垫完整主题 QSS（幂等，不累积），再回调插件可选的
        # set_theme(theme)。顺序很重要——插件哪怕实现了空 set_theme，QSS 也已刷新，
        # 不会出现「深色下停留在浅色样式」的白底漏底。
        for w in list(self._settings_cache.values()):
            apply_child_theme(w, theme)
            setter = getattr(w, "set_theme", None)
            if callable(setter):
                try:
                    setter(theme)
                except Exception:
                    pass

    # ---------- 生命周期 ----------
    def shutdown(self):
        """页面不再使用时释放插件持有的 Qt 对象（防止关闭后仍有 widget 存活）。"""
        self.discard_settings()
