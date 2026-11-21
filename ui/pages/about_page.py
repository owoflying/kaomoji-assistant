"""关于页：应用信息、开源链接、致谢、开发者模式入口。

开发者模式解锁方式：在关于页连续点击「版本号」8 次。启用后：
- 关于页会显示「开发者模式」徽标与「查看运行日志」入口；
- 系统托盘菜单的「查看日志」项也随之可见。
日志功能因此完全被开发者模式门控，便于调试与诊断。
"""
import os

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QFrame, QPushButton, QHBoxLayout,
    QScrollArea, QDialog, QPlainTextEdit, QFileDialog, QTabWidget,
    QMessageBox,
)
from PySide6.QtCore import Qt, QUrl, QTimer, QEvent, Signal
from PySide6.QtGui import QFont, QDesktopServices

from ui.win11_theme import kaomoji_font
from core.version import get_app_version
from core.github_release import ReleasesAPI, pick_windows_asset, downloads_dir


_CLICK_TARGET = 8          # 连续点击版本号多少次解锁开发者模式
_CLICK_TIMEOUT_MS = 6000    # 两次点击间隔超过该值才清零（放宽到 6s，避免正常节奏点击被误重置导致“要点好久”）


class UsageStatsDialog(QDialog):
    """使用统计详情弹窗。

    ## 为什么改成分页

    原来只有一个纯文本排行框，展示的是「累计次数 Top N」。
    但程序实际记录了六类数据（recent / favorites / usage / last_used /
    hour_usage / history），其中后两类是本轮新增的、而且**完全不外露**：
    用户只感觉到候选条顺序变了，不知道为什么。

    现在分四页，把每类数据都摊开：

        排行     —— 用得最多的
        时段     —— 我在什么时间爱用什么
        流水     —— 我最近依次用了什么
        推荐理由 —— 每条为什么排在当前位置

    「推荐理由」页最重要：它把黑盒的排序逻辑说出来，
    用户才知道"为什么我常用的那条被挤下去了"。
    """

    def __init__(self, state, theme, parent=None):
        super().__init__(parent)
        self._state = state
        self._theme = theme
        self.setWindowTitle("使用统计")
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self.setMinimumSize(520, 460)
        self.resize(600, 580)
        self._init_ui()
        self._refresh()

    def _init_ui(self):
        t = self._theme
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(12)

        title = QLabel("使用统计")
        title.setObjectName("PageTitle")
        root.addWidget(title)

        self._total = QLabel("")
        self._total.setObjectName("SectionTitle")
        root.addWidget(self._total)

        self._tabs = QTabWidget()
        root.addWidget(self._tabs, 1)

        def _page():
            w = QWidget()
            lay = QVBoxLayout(w)
            lay.setContentsMargins(4, 10, 4, 4)
            edit = QPlainTextEdit()
            edit.setReadOnly(True)
            edit.setLineWrapMode(QPlainTextEdit.NoWrap)
            edit.setFont(kaomoji_font(13))
            edit.setStyleSheet(
                "QPlainTextEdit{background:%s;border:1px solid %s;"
                "border-radius:8px;padding:10px 12px;color:%s;}"
                % (t.card, t.card_border, t.text)
            )
            lay.addWidget(edit, 1)
            return w, edit

        self._rank_view = None
        self._hour_view = None
        self._hist_view = None
        self._why_view = None
        for label, attr in (("排行", "_rank_view"), ("时段", "_hour_view"),
                            ("流水", "_hist_view"), ("推荐理由", "_why_view")):
            w, edit = _page()
            setattr(self, attr, edit)
            self._tabs.addTab(w, label)

        bar = QHBoxLayout()
        # 完整数据导出/导入：给人看的是上面四页文本，
        # 这两个按钮处理的是**可回灌的原始数据**（换机器迁移用）
        export_btn = QPushButton("导出数据…")
        export_btn.setToolTip("导出完整使用记录（可导入到另一台机器）")
        export_btn.clicked.connect(self._export_data)
        bar.addWidget(export_btn)
        import_btn = QPushButton("导入数据…")
        import_btn.setToolTip("从导出的文件恢复使用记录，与现有数据合并")
        import_btn.clicked.connect(self._import_data)
        bar.addWidget(import_btn)
        bar.addStretch(1)
        save_btn = QPushButton("保存为文件")
        save_btn.clicked.connect(self._save)
        bar.addWidget(save_btn)
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.close)
        bar.addWidget(close_btn)
        root.addLayout(bar)

        # 用完整主题 QSS 覆盖按钮等样式
        self.setStyleSheet(t.style_sheet())

    # ------------------------------------------------------------ 各页内容
    def _text_rank(self, total):
        top = self._state.top_usage(200)
        if not top:
            return "还没有使用记录，去插入几个颜文字吧～"
        lines = ["累计插入：%d 次" % total, ""]
        width = max(len(x) for x, _ in top)
        for i, (text, cnt) in enumerate(top, 1):
            lines.append("%3d. %s  %d 次" % (i, text.ljust(width), cnt))
        return "\n".join(lines)

    def _text_hours(self):
        """时段分布：每个时段我最爱用的几条。"""
        try:
            buckets = self._state.summarize_habits()
        except Exception:
            buckets = []
        if not buckets:
            return ("还没有足够的时段数据。\n\n"
                    "用满 %d 次之后才会开始统计——样本太少时"
                    "「最常用」可能只是顺手用了一次，拿它推荐纯属噪声。"
                    % _hour_min_samples())

        zh = {
            "dawn": "清晨 05-09", "morning": "上午 09-12",
            "noon": "午后 12-14", "afternoon": "下午 14-18",
            "evening": "夜晚 18-23", "night": "深夜 23-05",
        }
        # 按时段的自然顺序展示，不是按次数——
        # 用户读的是"我的一天"，时间顺序才符合直觉
        order = ["dawn", "morning", "noon", "afternoon", "evening", "night"]
        got = {name: items for name, items in buckets}
        lines = []
        for key in order:
            items = got.get(key)
            if not items:
                continue
            lines.append("%s" % zh.get(key, key))
            for text, cnt in items:
                lines.append("    %s  %d 次" % (text, cnt))
            lines.append("")
        return "\n".join(lines).rstrip() or "暂无数据"

    def _text_history(self, n=50):
        """注入流水：最近依次用了什么。"""
        try:
            recs = self._state.recent_injected(n)
        except Exception:
            recs = []
        if not recs:
            return "还没有注入记录。"
        import datetime

        lines = ["最近 %d 条（最新的在上）：" % min(n, len(recs)), ""]
        for r in recs:
            try:
                ts = datetime.datetime.fromtimestamp(float(r.get("ts") or 0))
                stamp = ts.strftime("%m-%d %H:%M")
            except Exception:
                stamp = "--------"
            src = r.get("source") or ""
            src_tag = {"picker": "候选条", "repeat": "重复",
                       "search": "搜索"}.get(src, src)
            tag = ("  [%s]" % src_tag) if src_tag else ""
            lines.append("%s  %s%s" % (stamp, r.get("text", ""), tag))
        return "\n".join(lines)

    def _text_why(self):
        """推荐理由：把用户常用条目的排序依据摊开。

        只列**真正有理由的**（用过的、收藏的、最近用过的），
        没理由的条目列出来也没有信息量。
        """
        try:
            top = [t for t, _c in self._state.top_usage(20)]
        except Exception:
            top = []
        if not top:
            return ("还没有足够的记录来判断你的偏好。\n\n"
                    "用得越多，这里会越准确地说明「为什么某条排在前面」。")

        lines = []
        for text in top:
            try:
                reasons = self._state.explain(text)
            except Exception:
                reasons = []
            if not reasons:
                continue
            lines.append(text)
            for label, desc in reasons:
                lines.append("    · %s —— %s" % (label, desc))
            lines.append("")
        if not lines:
            return "暂无推荐理由。"
        lines.append("——")
        lines.append("说明：候选条按「频率 + 新近度 + 收藏 + 时段偏好」综合排序，")
        lines.append("用得越久这个模型越贴合你的习惯。")
        return "\n".join(lines)

    def _refresh(self):
        total = self._state.total_inserts() if self._state else 0
        self._total.setText("累计插入：%d 次" % total)
        if self._rank_view is not None:
            self._rank_view.setPlainText(self._text_rank(total))
        if self._hour_view is not None:
            self._hour_view.setPlainText(self._text_hours())
        if self._hist_view is not None:
            self._hist_view.setPlainText(self._text_history())
        if self._why_view is not None:
            self._why_view.setPlainText(self._text_why())

    def _export_data(self):
        """导出完整使用记录（JSON，可回灌）。"""
        if self._state is None or not hasattr(self._state, "export_data"):
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "导出使用数据", "kaomoji-usage-data.json",
            "数据文件 (*.json);;所有文件 (*.*)",
        )
        if not path:
            return
        ok, msg = self._state.export_data(path)
        QMessageBox.information(self, "导出数据",
                                msg if ok else msg)

    def _import_data(self):
        """导入并合并使用记录。"""
        if self._state is None or not hasattr(self._state, "import_data"):
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "导入使用数据", "",
            "数据文件 (*.json);;所有文件 (*.*)",
        )
        if not path:
            return
        ok, msg = self._state.import_data(path, merge=True)
        QMessageBox.information(self, "导入数据", msg)
        if ok:
            self._refresh()

    def _save(self):
        """把四页内容一起导出成文本。"""
        total = self._state.total_inserts() if self._state else 0
        parts = [
            "使用统计",
            "=" * 40,
            "累计插入：%d 次" % total,
            "",
            "【排行】",
            self._text_rank(total),
            "",
            "【时段】",
            self._text_hours(),
            "",
            "【流水】",
            self._text_history(200),
            "",
            "【推荐理由】",
            self._text_why(),
        ]
        path, _ = QFileDialog.getSaveFileName(
            self, "保存使用统计", "kaomoji-usage-stats.txt",
            "文本文件 (*.txt);;所有文件 (*.*)",
        )
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write("\n".join(parts) + "\n")
        except Exception:  # pragma: no cover
            pass


def _hour_min_samples():
    """取时段统计的最小样本门槛（避免对话框里写死数字）。"""
    try:
        from core.user_state import HOUR_MIN_BUCKET_SAMPLES
        return HOUR_MIN_BUCKET_SAMPLES
    except Exception:
        return 8


class AboutPage(QWidget):
    developer_mode_enabled = Signal()  # 解锁开发者模式后实时通知主窗口添加标签

    def __init__(self, config=None, state=None, save_config=None, open_log=None, parent=None):
        super().__init__(parent)
        self.config = config or {}
        self._state = state
        self._save_config = save_config
        self._open_log = open_log
        self._click_count = 0
        self._click_timer = QTimer(self)
        self._click_timer.setSingleShot(True)
        self._click_timer.setInterval(_CLICK_TIMEOUT_MS)
        self._click_timer.timeout.connect(self._reset_clicks)
        self._init_ui()

    def _init_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(36, 28, 36, 28)
        root.setSpacing(22)

        title = QLabel("关于")
        title.setObjectName("PageTitle")
        root.addWidget(title)

        # 把卡片内容放进可滚动区域：统计行数多时会超出可用高度，
        # 没有滚动会导致布局被压缩、文字被截断。
        scroll = QScrollArea()
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)

        body = QWidget()
        # 关键：与设置页保持一致，使用 SettingsBody 匹配全局 QSS 的透明规则，
        # 避免 QScrollArea viewport 的默认浅色背景透出（深色模式下尤其明显）。
        body.setObjectName("SettingsBody")
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(22)

        card = QFrame()
        card.setObjectName("Card")
        croot = QVBoxLayout(card)
        croot.setContentsMargins(24, 22, 24, 22)
        croot.setSpacing(14)

        name = QLabel("颜文字输入辅助器")
        name.setFont(QFont("Segoe UI Variable", 20, QFont.Weight.Bold))
        croot.addWidget(name)

        # 版本号：显示当前 commit 短哈希（如 f94d0d4），点击 8 次解锁开发者模式
        #
        # ⚠ 点击热区：QLabel 默认尺寸贴文字（实测高度只有 13px），
        # 作为"点 8 次"的目标太细了，手感很差。这里给它撑到至少 24px 高
        # （Windows 触控指南的最小点击目标），并加一点左右内边距。
        ver = QLabel("版本 " + get_app_version())
        ver.setObjectName("BodyText")
        ver.setCursor(Qt.PointingHandCursor)
        ver.setMinimumHeight(24)
        ver.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)
        ver.setContentsMargins(0, 4, 0, 4)
        ver.setToolTip("点击 8 次可解锁开发者模式")
        ver.installEventFilter(self)
        self._ver_label = ver
        croot.addWidget(ver)

        # 开发者模式徽标（启用前隐藏）
        self._dev_badge = QLabel("● 开发者模式")
        self._dev_badge.setObjectName("Caption")
        # 颜色在 set_theme 中按当前主题 accent 设置，避免硬编码导致深浅色下不协调
        self._dev_badge.setVisible(False)
        croot.addWidget(self._dev_badge)

        # 点击版本号的反馈提示
        self._dev_hint = QLabel("")
        self._dev_hint.setObjectName("Caption")
        croot.addWidget(self._dev_hint)

        desc = QLabel("一款 Windows 11 风格的颜文字输入辅助工具。\n"
                      "支持全局热键唤起候选条、情绪识别自动弹出、快捷短语、自定义颜文字与搜索。")
        desc.setObjectName("BodyText")
        desc.setWordWrap(True)
        croot.addWidget(desc)

        link_row = QHBoxLayout()
        gh = QPushButton("GitHub 仓库")
        gh.clicked.connect(lambda: QDesktopServices.openUrl(QUrl("https://github.com/owoflying/kaomoji-assistant")))
        link_row.addWidget(gh)
        link_row.addStretch(1)
        croot.addLayout(link_row)

        croot.addSpacing(8)
        thanks = QLabel("技术栈：Python · PySide6 · pynput · Windows DWM")
        thanks.setObjectName("Caption")
        croot.addWidget(thanks)

        body_layout.addWidget(card)

        # 更新与下载卡片：从 GitHub 直接获取并下载项目发行包
        self._init_release_card(body_layout)

        # 使用统计卡片（摘要 + 详情弹窗）
        if self._state is not None:
            stat_card = QFrame()
            stat_card.setObjectName("Card")
            scard = QVBoxLayout(stat_card)
            scard.setContentsMargins(24, 22, 24, 22)
            scard.setSpacing(14)

            stitle = QLabel("使用统计")
            stitle.setObjectName("PageTitle")
            scard.addWidget(stitle)

            self._stat_total = QLabel("累计插入：%d 次" % self._state.total_inserts())
            self._stat_total.setObjectName("BodyText")
            scard.addWidget(self._stat_total)

            top = self._state.top_usage(3)
            if top:
                preview = QLabel("\n".join("%s  %d 次" % (text, cnt) for text, cnt in top))
                preview.setObjectName("Caption")
                scard.addWidget(preview)
            else:
                empty = QLabel("还没有使用记录，去插入几个颜文字吧～")
                empty.setObjectName("Caption")
                scard.addWidget(empty)

            btn_row = QHBoxLayout()
            btn_row.addStretch(1)
            detail_btn = QPushButton("查看详情")
            detail_btn.setObjectName("AccentButton")
            detail_btn.clicked.connect(self._show_stats_dialog)
            btn_row.addWidget(detail_btn)
            scard.addLayout(btn_row)

            body_layout.addWidget(stat_card)

        body_layout.addStretch(1)
        scroll.setWidget(body)
        root.addWidget(scroll)

        # 若已处于开发者模式，直接进入启用态
        if self.config.get("developer_mode", False):
            self._enter_developer_mode(announce=False)

    # ---------- 统计详情弹窗 ----------
    def _show_stats_dialog(self):
        from ui.win11_theme import Theme, system_theme_name
        theme_name = self.config.get("theme", "light")
        # 跟随系统主题时，取实际生效的主题
        if theme_name == "system":
            theme_name = system_theme_name()
        dlg = UsageStatsDialog(self._state, Theme(theme_name), parent=self.window())
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()
        return dlg

    # ---------- 更新与下载（GitHub 发行包） ----------
    def _init_release_card(self, body_layout):
        card = QFrame()
        card.setObjectName("Card")
        root = QVBoxLayout(card)
        root.setContentsMargins(24, 22, 24, 22)
        root.setSpacing(12)

        title = QLabel("更新与下载")
        title.setObjectName("PageTitle")
        root.addWidget(title)

        self._rel_current = QLabel("当前版本：%s" % get_app_version())
        self._rel_current.setObjectName("Caption")
        root.addWidget(self._rel_current)

        self._rel_status = QLabel("点击「检查更新」获取项目最新发行包")
        self._rel_status.setObjectName("BodyText")
        root.addWidget(self._rel_status)

        self._rel_notes = QLabel("")
        self._rel_notes.setObjectName("Caption")
        self._rel_notes.setWordWrap(True)
        self._rel_notes.setVisible(False)
        root.addWidget(self._rel_notes)

        self._rel_progress = QLabel("")
        self._rel_progress.setObjectName("Caption")
        self._rel_progress.setVisible(False)
        root.addWidget(self._rel_progress)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)
        self._rel_check = QPushButton("检查更新")
        self._rel_check.clicked.connect(self._on_check_update)
        btn_row.addWidget(self._rel_check)

        self._rel_download = QPushButton("下载最新发行包")
        self._rel_download.setObjectName("AccentButton")
        self._rel_download.setMaximumWidth(260)
        self._rel_download.setVisible(False)
        self._rel_download.clicked.connect(self._on_download_release)
        btn_row.addWidget(self._rel_download)

        self._rel_open = QPushButton("打开下载文件夹")
        self._rel_open.setVisible(False)
        self._rel_open.clicked.connect(self._on_open_downloads)
        btn_row.addWidget(self._rel_open)

        btn_row.addStretch(1)
        root.addLayout(btn_row)

        self._rel_link = QPushButton("在浏览器查看发布页")
        self._rel_link.setVisible(False)
        self._rel_link.clicked.connect(self._on_open_release_page)
        root.addWidget(self._rel_link)

        # ---------- 自动更新状态（由 AppRuntime 驱动） ----------
        # 与上面的"手动检查/下载"区分：这里是后台自动更新的进度与安装入口。
        self._auto_status = QLabel("")
        self._auto_status.setObjectName("Caption")
        self._auto_status.setWordWrap(True)
        self._auto_status.setVisible(False)
        root.addWidget(self._auto_status)

        self._auto_install = QPushButton("立即重启并更新")
        self._auto_install.setObjectName("AccentButton")
        self._auto_install.setMaximumWidth(260)
        self._auto_install.setVisible(False)
        self._auto_install.clicked.connect(self._on_install_now)
        root.addWidget(self._auto_install)

        body_layout.addWidget(card)

        # 异步网络：持有 QNetworkAccessManager 的轻量封装
        self._api = ReleasesAPI(self)
        self._api.latest_ready.connect(self._on_latest_ready)
        self._api.error_occurred.connect(self._on_rel_error)
        self._api.download_progress.connect(self._on_dl_progress)
        self._api.download_finished.connect(self._on_dl_finished)
        self._api.download_error.connect(self._on_dl_error)
        self._api.verifying.connect(self._on_dl_verifying)

        self._latest_info = None   # 解析后的发行版信息
        self._dl_url = None        # 待下载资产 URL
        self._dl_name = None       # 待下载资产文件名
        self._dl_sha256 = None     # 待下载资产服务端 SHA-256（来自 digest）
        self._dl_size = 0          # 待下载资产真实字节数（来自 assets[].size）

    def _set_busy(self, busy):
        """检查/下载期间禁用触发按钮，避免重复请求。"""
        self._rel_check.setEnabled(not busy)
        self._rel_download.setEnabled(not busy)

    def _on_install_now(self):
        """「立即重启并更新」：安装已下载的包并退出程序。"""
        cb = getattr(self, "_install_cb", None)
        if not callable(cb):
            self._auto_status.setText("自动更新不可用")
            return
        self._auto_status.setText("正在安装并重启…")
        self._auto_install.setEnabled(False)
        try:
            cb()
        except Exception as e:
            self._auto_status.setText("安装失败：%r" % (e,))
            self._auto_install.setEnabled(True)

    # ---------- 自动更新状态（由 AppRuntime 调用） ----------
    def set_update_progress(self, pct):
        """下载进度（0~100）。"""
        self._auto_status.setVisible(True)
        self._auto_status.setText("正在后台下载新版本：%d%%" % int(pct))

    def set_update_ready(self, version):
        """新版本已下载并校验完成，等待安装。"""
        self._auto_status.setVisible(True)
        self._auto_status.setText(
            "新版本 %s 已下载。关闭程序时会自动安装；也可点右侧按钮立即更新。"
            % version)
        self._auto_install.setVisible(True)
        self._auto_install.setEnabled(True)

    def set_update_unsupported(self, reason):
        """当前形态不支持自动更新时说明原因。"""
        self._auto_status.setVisible(True)
        self._auto_status.setText(reason)

    def set_install_callback(self, fn):
        """由 AppRuntime 注入"立即安装"回调。"""
        self._install_cb = fn

    def set_update_failed_note(self, note):
        """显示"上次更新失败"的说明（由 AppRuntime 在启动时注入）。

        用户最怕的是"点了更新、程序没了、也不知道为什么"。
        这条提示告诉他们：没更新成功，但原版本已经恢复，可以正常用。
        """
        self._auto_status.setVisible(True)
        self._auto_status.setText("上次更新未完成：%s" % note)
        self._auto_install.setVisible(False)

    def set_install_package_callback(self, fn):
        """注入"安装指定的更新包"回调（手动检查更新时用）。

        与 `set_install_callback` 的区别：
          * 那个是"装我已经下载好的包"（自动更新流程）；
          * 这个是"装这个路径的包"（用户在关于页手动下载的）。
        """
        self._install_package_cb = fn

    def _on_check_update(self):
        self._rel_status.setText("正在获取最新发行版信息…")
        self._rel_notes.setVisible(False)
        self._rel_download.setVisible(False)
        self._rel_open.setVisible(False)
        self._rel_link.setVisible(False)
        self._rel_progress.setVisible(False)
        self._set_busy(True)
        self._api.fetch_latest_release()

    def _on_latest_ready(self, info):
        self._set_busy(False)
        self._latest_info = info
        tag = info.get("tag") or "(未知版本)"
        self._rel_status.setText("最新发行版：%s" % tag)
        # 发布说明摘要（截断到前若干字符，避免长文本撑爆卡片）
        body = (info.get("body") or "").strip()
        if body:
            snippet = body if len(body) <= 240 else body[:240].rstrip() + "…"
            self._rel_notes.setText(snippet)
            self._rel_notes.setVisible(True)
        asset = pick_windows_asset(info.get("assets") or [])
        if asset and asset.get("url"):
            self._dl_url = asset["url"]
            self._dl_name = asset.get("name") or "KaomojiAssistant-release"
            self._dl_sha256 = asset.get("sha256") or None
            self._dl_size = asset.get("size") or 0
            display_name = self._dl_name if len(self._dl_name) <= 28 else self._dl_name[:25] + "…"
            self._rel_download.setText("下载 %s" % display_name)
            self._rel_download.setToolTip(self._dl_name)
            self._rel_download.setVisible(True)
        else:
            self._rel_status.setText("最新发行版：%s（无可用下载资产）" % tag)
        if info.get("html_url"):
            self._rel_link.setVisible(True)
            self._rel_html_url = info["html_url"]

    def _on_rel_error(self, msg):
        self._set_busy(False)
        self._rel_status.setText(msg)
        self._rel_download.setVisible(False)
        self._rel_link.setVisible(False)

    def _on_download_release(self):
        if not self._dl_url or not self._dl_name:
            return
        dest = os.path.join(downloads_dir(), self._dl_name)
        self._rel_status.setText("正在下载：%s" % self._dl_name)
        self._rel_progress.setText("下载进度：0%")
        self._rel_progress.setVisible(True)
        self._rel_open.setVisible(False)
        self._dl_dest = dest
        self._set_busy(True)
        self._api.download_asset(self._dl_url, dest, self._dl_sha256, expected_size=self._dl_size)

    def _on_dl_progress(self, pct):
        self._rel_progress.setText("下载进度：%d%%" % pct)

    def _on_dl_verifying(self):
        self._rel_progress.setText("校验中…")
        self._rel_status.setText("下载完成，正在校验文件完整性…")

    def _on_dl_finished(self, path):
        """下载完成。

        ## 两种模式

        * **能自动更新**（打包态）：下载完直接进入安装流程，
          用户点一下就从旧版升到新版，不需要自己去解压替换。
        * **不能自动更新**（源码态等）：只保存文件并提示打开文件夹。

        这解决了"用户没开自动更新就装不上"的问题——
        手动检查更新也能一键安装，不依赖后台自动更新开关。
        """
        self._set_busy(False)

        if self._dl_sha256:
            self._rel_progress.setText("✓ 校验通过")
        else:
            self._rel_progress.setText("下载完成")

        # 尝试交给自动安装器
        installed = False
        try:
            from core import updater_install
            if updater_install.can_self_update():
                layout = updater_install.detect_layout(
                    path, updater_install.exe_name())
                if layout.get("ok"):
                    cb = getattr(self, "_install_package_cb", None)
                    if callable(cb):
                        installed = bool(cb(path))
        except Exception:
            installed = False

        if installed:
            self._rel_status.setText(
                "新版本已下载并校验通过，正在准备安装……")
            self._rel_open.setVisible(False)
            return

        # 走不通就走原来的"保存文件"路径
        self._rel_status.setText("已下载并校验通过：%s" % path)
        self._rel_open.setVisible(True)

    def _on_dl_error(self, msg):
        self._set_busy(False)
        self._rel_progress.setVisible(False)
        self._rel_status.setText(msg)

    def _on_open_downloads(self):
        try:
            QDesktopServices.openUrl(QUrl.fromLocalFile(downloads_dir()))
        except Exception:
            pass

    def _on_open_release_page(self):
        url = getattr(self, "_rel_html_url", "")
        if url:
            QDesktopServices.openUrl(QUrl(url))

    # ---------- 主题切换 ----------
    def set_theme(self, theme):
        """主题切换时刷新开发者徽标等硬编码颜色，避免深浅色下不协调。

        ⚠ **不 unpolish + polish 整页**：那会让本页所有子控件重新计算样式，
        在主题切换的总开销里占不小一块。这里真正要更新的只是下面两个
        用硬编码颜色的标签，`setStyleSheet` 本身已经会让它们重新 polish。
        """
        self._dev_badge.setStyleSheet("color:%s;font-weight:bold;" % theme.accent)
        # 版本号点击高亮反馈也跟随主题 accent
        self._ver_label_flash_style = "color:%s;font-weight:bold;" % theme.accent
        self.update()

    # ---------- 事件过滤：捕获版本号点击 ----------
    def eventFilter(self, obj, event):
        """处理版本号的连点解锁。

        ⚠ 用 **MouseButtonRelease** 而不是 MouseButtonPress：
        按下就计数的话，按住后拖开、或按住久一点松手都会被算作多次点击，
        手感非常"贼"。release 才是用户心智里的"我点了一下"。

        ⚠ 不 echo 事件（不 return True 吞掉）会有副作用：label 需要收到
        后续事件才能完成 Qt 内部的鼠标状态机。这里只处理 release，
        其余事件照常放行给 super()。
        """
        if obj is self._ver_label and event.type() == QEvent.MouseButtonRelease \
                and event.button() == Qt.LeftButton:
            # 只有"在标签内释放"才算有效点击（按下后拖出去松手 = 取消）
            try:
                inside = self._ver_label.rect().contains(event.position().toPoint())
            except (AttributeError, TypeError):
                inside = True
            if inside:
                self._on_version_clicked()
            return True
        return super().eventFilter(obj, event)

    def _on_version_clicked(self):
        if self.config.get("developer_mode", False):
            self._dev_hint.setText("开发者模式已启用")
            return
        self._click_count += 1
        self._flash_version()  # 每次点击即时反馈，避免"点了没反应"的卡顿感
        remaining = _CLICK_TARGET - self._click_count
        if remaining <= 0:
            self._enter_developer_mode(announce=True)
            return
        self._dev_hint.setText("再点击 %d 次解锁开发者模式" % remaining)
        self._click_timer.start()   # 重置超时

    def _flash_version(self):
        """点击版本号时的视觉反馈。

        ⚠ **绝不能在点击时 setStyleSheet**（这是"连点失灵"的根因）：
        实测真实鼠标连点时，`setStyleSheet` 会让标签发生
        `StyleChange + FontChange×2 + PaletteChange + Paint` 一整套重算，
        Qt 的鼠标事件序列被这些内部事件打断，**后续的 press/release 收不到**
        —— 用户表现为"必须点一下停一下"。

        现在改用**完全不触碰控件样式/字体**的方案：把反馈放在提示行
        （`_dev_hint`，本来就是文字变化）上，并用一次 `update()` 让标签重绘。
        `update()` 只安排重绘、不改几何与样式，不会打断鼠标事件链。

        视觉上通过 `_flash_on` 标志在 `paintEvent` 之外用属性切换即可——
        这里最简单可靠的是**不改任何视觉**，只依靠提示文字变化作为反馈
        （"再点击 N 次"本身就足够了）。
        """
        # 只安排一次重绘，不修改样式表 / 字体 / 几何
        label = self._ver_label
        if label is not None:
            label.update()

    def _reset_clicks(self):
        self._click_count = 0
        self._dev_hint.setText("")

    def _enter_developer_mode(self, announce):
        self.config["developer_mode"] = True
        if self._save_config:
            self._save_config(self.config)
        self._click_count = 0
        self._click_timer.stop()
        self._dev_badge.setVisible(True)
        self.developer_mode_enabled.emit()
        if announce:
            self._dev_hint.setText("已启用开发者模式，可在「开发者」标签查看运行日志与实时事件流")
        else:
            self._dev_hint.setText("开发者模式已启用")

    def reset_developer_mode(self):
        """由统一窗口在「关闭开发者模式」时调用：清空本页的开发者模式状态，
        使后续再次连点版本号能重新解锁（否则本页仍记着 developer_mode=True，
        会直接短路点击计数、不再发出 developer_mode_enabled 信号，导致无法二次进入）。
        """
        self._click_count = 0
        self._click_timer.stop()
        self._dev_badge.setVisible(False)
        self._dev_hint.setText("")
        self.config["developer_mode"] = False
