"""插件管理页：浏览已安装插件、启用/停用、重载、打开插件目录。

按项目规范归入「测试功能」：use_test_features 关闭时导航项与页面整体隐藏
（见 unified_window._sync_plugin_tab），确认稳定后再移出测试模式。

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
from ui.win11_theme import Theme


def _state_text(state):
    return {"loaded": "已启用", "disabled": "已停用", "error": "错误"}.get(state, state)


class _PluginCard(QFrame):
    """单个插件卡片：名称/版本/状态徽标 + 描述 + 作者 + 重载按钮 + 启用开关。"""

    def __init__(self, rec, manager, theme, parent=None):
        super().__init__(parent)
        self.rec = rec
        self.manager = manager
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
            rec["state"] == "loaded" or manager.is_enabled(rec["id"]))
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

        self.set_theme(theme)

    def _on_toggle(self, checked):
        # ToggleSwitch.mouseReleaseEvent 已自改状态，这里只把结果交给管理器
        self.manager.set_enabled(self.rec["id"], bool(checked))

    def set_theme(self, t):
        self._theme = t
        err_color = "#ff99a4" if t.dark else "#c42b1c"
        state_color, state_bg = {
            "loaded": (t.accent, t.accent_bg),
            "disabled": (t.text_tertiary, "transparent"),
            "error": (err_color, "transparent"),
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


class PluginPage(QWidget):
    """「插件」页：管理本地插件。"""

    def __init__(self, manager, theme=None, parent=None):
        super().__init__(parent)
        self.manager = manager
        # theme 接受 Theme 实例或主题名（与 ToggleSwitch 等控件约定一致）
        self._theme = theme if isinstance(theme, Theme) else Theme(theme or "light")
        self._cards = []
        self._init_ui()
        self.refresh()

    # ---------- UI ----------
    def _init_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(36, 28, 36, 28)
        root.setSpacing(22)

        title = QLabel("插件")
        title.setObjectName("PageTitle")
        root.addWidget(title)

        # 顶部说明卡：插件目录 + 操作
        tip_card = QFrame()
        tip_card.setObjectName("Card")
        tip_root = QVBoxLayout(tip_card)
        tip_root.setContentsMargins(16, 14, 16, 14)
        tip_root.setSpacing(10)
        tip = QLabel(
            "把插件文件夹放进插件目录即可被识别（需包含 plugin.json 与入口 main.py）。\n"
            "插件可以追加颜文字分类、注册上屏前的文本转换、订阅插入等事件。")
        tip.setObjectName("Caption")
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
        """重建插件卡片列表（启用/停用/重载/重扫后由 changed 信号触发）。"""
        # 清空旧卡片
        for card in self._cards:
            card.setParent(None)
            card.deleteLater()
        self._cards = []

        recs = sorted(self.manager.plugins.values(), key=lambda r: r["id"])
        t = self._theme or Theme("light")
        n_loaded = sum(1 for r in recs if r["state"] == "loaded")
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
            card = _PluginCard(rec, self.manager, t)
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
        for card in self._cards:
            if isinstance(card, _PluginCard):
                card.set_theme(theme)
