"""首次使用引导：**分步向导**，一步一个功能。

## 为什么改成分步

原来是一屏塞三块（特色列表 + 配置 + 按钮）。用户反馈：

    "多添加点东西啊，然后不要那么密集，优雅点，分步来，
     一个功能一步，可以点击下一步"

确实——一屏把所有信息推给新用户，密集且没有重点，
最后"读完再说"的心态占上风，什么都记不住。

## 现在的结构

    步骤 0  欢迎        （一句话说明这是什么）
    步骤 1  唤起热键    （怎么用：按一下就叫出来）
    步骤 2  智能推荐    （情绪联想）
    步骤 3  插件扩展    （能装东西）
    步骤 4  自动更新    （后台维护）
    步骤 5  个性化      （主题 + 热键，唯一可以改设置的一步）

每页一个大图标、标题、一段说明，底部「上一步 / 下一步」。
最后一页按钮变成「开始使用」。

## 动画

* 窗口整体淡入
* 每次翻页：新页从右侧轻滑入场 + 淡入（方向与"下一步"一致）
* 内容元素在页内错峰浮现，不密集
"""
import os

from PySide6.QtCore import (
    QAbstractAnimation, QEasingCurve, QPropertyAnimation, QPoint, Qt, QTimer,
)
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QComboBox, QDialog, QFrame, QGraphicsOpacityEffect, QHBoxLayout, QLabel,
    QPushButton, QVBoxLayout, QWidget,
)

#: 热键预设
HOTKEY_PRESETS = (
    ("Ctrl+Shift+K（默认）", "<ctrl>+<shift>+k"),
    ("Ctrl+Alt+K", "<ctrl>+<alt>+k"),
    ("Ctrl+Shift+空格", "<ctrl>+<shift>+space"),
    ("Alt+`", "<alt>+`"),
)

#: 外观预设（应用内固定用词：浅色 / 深色）
THEME_PRESETS = (
    ("跟随系统", "system"),
    ("浅色", "light"),
    ("深色", "dark"),
)

#: 向导步骤：(大图标, 标题, 正文, 附加行的标题（None = 没有）)
STEPS = (
    ("👋", "欢迎使用颜文字输入辅助器",
     "一个常驻托盘的小工具，让你在任何程序里随手打出颜文字，不用记符号。\n\n"
     "接下来用 30 秒带你看看它能做什么。", None),
    ("⌨", "按一下，就叫出来",
     "在任何程序里按下**唤起热键**，候选条就会贴在光标旁边出现。\n\n"
     "用数字键 1~9 选，空格或回车直接上屏，Esc 关掉。", None),
    ("💬", "听得懂你在说什么",
     "输入框里打出「开心」「好累」这类词，它会认出情绪，"
     "自动推荐对应的表情。\n\n"
     "你每次的选择都会被记住——用久了，常点的那几个会排在前面。", None),
    ("🧩", "能装插件",
     "在「插件」页可以添加新的表情库、改写上屏文字，甚至改造界面外观。\n\n"
     "不喜欢默认的？换掉就行。", None),
    ("🔄", "自己保持更新",
     "新版本会在后台悄悄下好，关掉程序时顺手装上。\n\n"
     "不想被打扰可以在「设置」里关掉自动更新。", None),
    ("🎨", "最后，挑两件事",
     "选好唤起热键和外观偏好就能开始了。\n\n"
     "这些之后随时都能在「设置 → 外观」里改。", "setup"),
)


#: 特色条目（图标, 名称, 说明）。
#:
#: ⚠ 这是**旧接口**：分步向导改造后内容已挪进 STEPS。
#: 保留它是为了不破坏外部引用（测试、插件），值从 STEPS 派生，
#: 两边永远一致。
def _highlight_from_step(step):
    """从 STEPS 的一个条目提取（图标, 名称, 首段说明）。"""
    icon, title, text, _extra = step
    first = text.split(chr(10) * 2)[0]
    return (icon, title, first.replace('**', ''))


#: 特色条目（图标, 名称, 说明）—— 旧接口，供外部引用。
#:
#: 分步向导改造后内容在 STEPS 里，这里派生出来保证两边一致。
HIGHLIGHTS = tuple(_highlight_from_step(s) for s in STEPS[1:5])


class WelcomeDialog(QDialog):
    """首次使用引导：分步向导。"""

    #: 兼容旧调用 WelcomeDialog.maybe_show(...)
    maybe_show = staticmethod(lambda *a, **kw: maybe_show(*a, **kw))

    def __init__(self, theme_name="light", version="", config=None, parent=None,
                 preview=False):
        """欢迎向导。

        :param preview: **预览模式**（开发者页「弹窗预览」用）。
            预览时界面与真实向导完全一致，但：

              * 「从备份导入」**不写盘**，只做一次演练（选包、检查、
                展示摘要、给出"如果真导入会还原哪些文件"的结果）
              * 不写任何标记文件

            这样改界面时能完整点到每个按钮，又不会把用户真实数据覆盖掉
            ——预览一次就把人家数据弄没了是不可接受的。
        """
        super().__init__(parent)
        self._preview = bool(preview)
        self.setWindowTitle("欢迎使用")
        self.setWindowFlags(
            self.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)
        self._config = dict(config or {})
        #: 启动时的配置快照。`accept()` 只跟它比差异——
        #: 导入备份后 `self._config` 会被更新，拿它当基准会让
        #: "刚导入的值"被判成没变化，导致当前会话不生效。
        self._start_config = dict(self._config)
        # 从主题包取主题（默认沿用主配置）
        from ui.window_material_mixin import surface_theme

        t, _params = surface_theme("dialog")
        self.setStyleSheet(t.style_sheet())
        self.setMinimumWidth(560)
        self.setMinimumHeight(420)

        #: 用户在引导页里选的配置（点「开始使用」后由调用方合并进主配置）
        self.chosen = {}

        self._step = 0
        self._pages = []
        self._anims = []
        self._reveal_done = False
        self._busy = False

        self._build()

    # ------------------------------------------------------------ 构建
    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ---- 内容区（每步一页）----
        self._body = QWidget()
        body_lay = QVBoxLayout(self._body)
        body_lay.setContentsMargins(40, 32, 40, 8)
        body_lay.setSpacing(0)
        for i, (icon, title, text, extra) in enumerate(STEPS):
            page = self._make_page(icon, title, text, extra)
            page.hide()
            body_lay.addWidget(page)
            self._pages.append(page)
        root.addWidget(self._body, 1)

        # ---- 底部：进度点 + 按钮 ----
        foot = QWidget()
        froot = QHBoxLayout(foot)
        froot.setContentsMargins(40, 8, 40, 24)
        froot.setSpacing(10)

        self._dots = QLabel()
        self._dots.setObjectName("Caption")
        froot.addWidget(self._dots)
        froot.addStretch(1)

        self._back = QPushButton("上一步")
        self._back.clicked.connect(self._go_back)
        froot.addWidget(self._back)

        self._next = QPushButton("下一步")
        self._next.setObjectName("AccentButton")
        self._next.setMinimumHeight(34)
        self._next.setMinimumWidth(110)
        self._next.clicked.connect(self._go_next)
        froot.addWidget(self._next)

        root.addWidget(foot)
        self._sync_buttons()
        self._pages[0].show()

    def _make_page(self, icon, title, text, extra):
        """造一步的内容页。"""
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(14)

        # 大图标
        ic = QLabel(icon)
        ic.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        f = QFont("Segoe UI Emoji", 40)
        ic.setFont(f)
        lay.addWidget(ic)

        # 标题
        tp = QLabel(title)
        tp.setObjectName("PageTitle")
        tp.setWordWrap(True)
        lay.addWidget(tp)

        # 正文
        tx = QLabel(text.replace("**", ""))
        tx.setObjectName("BodyText")
        tx.setWordWrap(True)
        tx.setTextFormat(Qt.PlainText)
        lay.addWidget(tx)

        # 附加：配置控件（只有最后一页有）
        if extra == "setup":
            lay.addSpacing(6)
            lay.addWidget(self._setup_block())
        lay.addStretch(1)

        page._reveal_items = [ic, tp, tx]
        return page

    def _setup_block(self):
        """最后一页：热键 + 外观 + 从备份导入。"""
        from ui.pages._scaffold import card, card_layout

        box = card()
        lay = card_layout(box, margins=(16, 14, 16, 14), spacing=10)

        # 热键
        r1 = QHBoxLayout()
        r1.setSpacing(10)
        l1 = QLabel("唤起热键")
        l1.setObjectName("BodyText")
        l1.setFixedWidth(72)
        r1.addWidget(l1)
        self._hotkey_combo = QComboBox()
        for label, value in HOTKEY_PRESETS:
            self._hotkey_combo.addItem(label, value)
        self._hotkey_combo.setMinimumWidth(240)
        r1.addWidget(self._hotkey_combo)
        r1.addStretch(1)
        lay.addLayout(r1)

        # 外观
        r2 = QHBoxLayout()
        r2.setSpacing(10)
        l2 = QLabel("外观")
        l2.setObjectName("BodyText")
        l2.setFixedWidth(72)
        r2.addWidget(l2)
        self._theme_combo = QComboBox()
        for label, value in THEME_PRESETS:
            self._theme_combo.addItem(label, value)
        self._theme_combo.setMinimumWidth(240)
        r2.addWidget(self._theme_combo)
        r2.addStretch(1)
        lay.addLayout(r2)

        # 从备份导入（老用户换机器时最需要的一步）
        #
        # 为什么放在欢迎页：
        #   刚装好的第一次启动，正是"我之前用过，能不能把数据带过来"
        #   这个念头最强的时候。让用户先走完向导、进了设置页再去找
        #   「数据迁移」，等于要他多绕一圈。
        #
        # 为什么单独占一块（不和上面两行挤在一起）：
        #   它和「热键 / 外观」是**不同性质**的操作——前两个是"选一个值"，
        #   这个是"覆盖全部设置"。混在同一张卡片同一列里，视觉上像是
        #   第三个下拉框，而且按钮 + 一长串提示挤在一行会显得拥挤。
        #
        # 导入后不重启也能让热键/外观立即对——这两项直接改界面上的值
        # 并经 accept() 提交给宿主；其余数据（使用记录、训练数据）
        # 会在下次启动时自然读入。
        lay.addSpacing(4)
        sep = QFrame()
        sep.setObjectName("Divider")
        sep.setFrameShape(QFrame.HLine)
        sep.setFixedHeight(1)
        lay.addWidget(sep)
        lay.addSpacing(2)

        r3 = QHBoxLayout()
        r3.setSpacing(10)
        l3 = QLabel("已有备份")
        l3.setObjectName("BodyText")
        l3.setFixedWidth(72)
        r3.addWidget(l3)
        self._import_btn = QPushButton("从备份导入…")
        self._import_btn.setMinimumHeight(30)
        self._import_btn.setMinimumWidth(120)
        self._import_btn.setToolTip(
            "如果你之前用过这个程序、做过「导出全部数据」，"
            "可以在这里直接把设置、外观、训练数据和使用记录带过来。")
        self._import_btn.clicked.connect(self._on_import_backup)
        r3.addWidget(self._import_btn)
        r3.addStretch(1)
        lay.addLayout(r3)

        # 提示单独一行：文字较长，和按钮挤一行会显得很满
        self._import_hint = QLabel("从旧机器迁移？选这个可以跳过手动配置。")
        self._import_hint.setObjectName("Caption")
        self._import_hint.setWordWrap(True)
        lay.addWidget(self._import_hint)

        # 回填当前值
        cur_hotkey = str(self._config.get("hotkey", ""))
        for i in range(self._hotkey_combo.count()):
            if self._hotkey_combo.itemData(i) == cur_hotkey:
                self._hotkey_combo.setCurrentIndex(i)
                break
        cur_theme = str(self._config.get("theme", "system"))
        for i in range(self._theme_combo.count()):
            if self._theme_combo.itemData(i) == cur_theme:
                self._theme_combo.setCurrentIndex(i)
                break
        return box

    def _on_import_backup(self):
        """从备份包导入全部数据，并把热键/外观的选择同步到界面上。

        ## 预览模式下**不写盘**

        开发者页点「首次使用引导」是来看界面长什么样的，不是来换数据的。
        如果在预览里真的导入，用户点一下就把自己的设置覆盖了——
        而且是在"只想看看界面"的意图下发生的，非常危险。

        所以预览时只做演练：选包 → 检查 → 显示摘要 → 报告
        "真实导入会还原哪些文件"，全程不碰磁盘。
        """
        from PySide6.QtWidgets import QFileDialog, QMessageBox

        from core import backup, runtime

        path, _f = QFileDialog.getOpenFileName(
            self, "选择备份包", "", "Kaomoji 备份包 (*.zip)")
        if not path:
            return

        ok, msg, manifest = backup.inspect(path)
        if not ok:
            QMessageBox.warning(self, "无法导入", msg)
            return

        # 展示内容摘要后二次确认——这是会覆盖刚生成配置的操作
        lines = []
        try:
            created = manifest.get("created_at", "?")
            lines.append("备份创建于：%s" % created)
            lines.append("")
            for label, n, size in backup.summarize(manifest):
                lines.append("  · %s：%d 项（%s）"
                             % (label, n, backup.human_size(size)))
        except Exception:
            pass
        lines.append("")
        if self._preview:
            lines.append("⚠ 当前是**预览模式**，不会真的写入任何数据。")
        else:
            lines.append("导入会覆盖当前的默认设置。确定继续？")

        box = QMessageBox(self)
        box.setWindowTitle("从备份导入")
        box.setIcon(QMessageBox.Question)
        box.setText("要用这个备份替换当前设置吗？" if not self._preview
                    else "（预览）这里会导入这个备份")
        box.setInformativeText("\n".join(lines))
        yes = box.addButton("导入" if not self._preview else "演示导入",
                            QMessageBox.AcceptRole)
        box.addButton("取消", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is not yes:
            return

        # ---- 预览：只演练，不写盘 ----
        if self._preview:
            self._preview_import_result(path, manifest)
            return

        ok, msg, detail = backup.import_all(path, runtime.app_data_dir())
        if not ok:
            QMessageBox.warning(self, "导入失败", msg)
            return

        # 把导入后的设置同步到界面
        self._sync_after_import()
        self._import_hint.setText(
            "已导入 %d 个文件。热键和外观已按备份更新。"
            % len((detail or {}).get("written", [])))
        QMessageBox.information(
            self, "导入完成",
            "%s。\n\n热键和外观已更新；使用记录、训练数据等会在"
            "下次启动后生效。" % msg)

    def _preview_import_result(self, path, manifest):
        """预览模式下报告"真实导入会做什么"，不做任何写入。

        只读 manifest，不碰磁盘——连"能还原几个文件"都是按清单统计的，
        这样预览在任何情况下都不会改变用户数据。
        """
        from PySide6.QtWidgets import QMessageBox

        will_write = self._preview_import_files(manifest)
        lines = ["真实导入会还原以下文件（本次为预览，未写入）：", ""]
        for p in will_write[:12]:
            lines.append("  · %s" % p)
        if len(will_write) > 12:
            lines.append("  … 还有 %d 个" % (len(will_write) - 12))

        QMessageBox.information(
            self, "预览：导入效果",
            "共 %d 个文件。\n\n%s" % (len(will_write), "\n".join(lines)))

    def _preview_import_files(self, manifest):
        """预览分支的**纯逻辑部分**：算出"会还原哪些文件"并更新提示。

        单独拆出来是为了可测——`_preview_import_result()` 里有
        模态弹窗，测试里点了会阻塞，没法自动化验证"不写盘"。
        """
        files = (manifest or {}).get("files", []) or []
        will_write = [str(f.get("path", "?")) for f in files
                      if str(f.get("path", "")).replace("\\", "/")
                      != "manifest.json"]
        self._import_hint.setText(
            "（预览）这个备份包含 %d 个文件，真实导入会还原它们。"
            "当前没有任何写入。" % len(will_write))
        return will_write

    def _sync_after_import(self):
        """导入后重新读配置，回填热键/外观下拉框，并记入 `chosen`。

        导入写的是磁盘上的 config.json，而本对话框的 `self._config`
        还是启动时的旧值。这里重新读一遍，让用户立刻看到变化，
        并把差异放进 `chosen`，由 `accept()` 交给宿主应用。
        """
        try:
            from core.config_store import load_config

            fresh = load_config()
        except Exception:
            return

        hk = str(fresh.get("hotkey", ""))
        for i in range(self._hotkey_combo.count()):
            if self._hotkey_combo.itemData(i) == hk:
                self._hotkey_combo.setCurrentIndex(i)
                break

        th = str(fresh.get("theme_mode") or fresh.get("theme") or "system")
        for i in range(self._theme_combo.count()):
            if self._theme_combo.itemData(i) == th:
                self._theme_combo.setCurrentIndex(i)
                break

        # 让 accept() 把这些变化交给宿主（它会写回并应用）
        self._config.update(fresh)

    # ------------------------------------------------------------ 导航
    def _sync_buttons(self):
        total = len(self._pages)
        self._dots.setText("%d / %d" % (self._step + 1, total))
        self._back.setEnabled(self._step > 0)
        self._back.setVisible(self._step > 0)
        self._next.setText("开始使用" if self._step == total - 1
                           else "下一步")

    def _go_next(self):
        if self._busy:
            return
        if self._step >= len(self._pages) - 1:
            self.accept()
            return
        self._show_step(self._step + 1, forward=True)

    def _go_back(self):
        if self._busy or self._step <= 0:
            return
        self._show_step(self._step - 1, forward=False)

    def _show_step(self, index, forward=True):
        """切到某一步：旧页淡出，新页从侧向滑入 + 内容错峰浮现。"""
        if index == self._step or not (0 <= index < len(self._pages)):
            return
        old = self._pages[self._step]
        new = self._pages[index]
        self._step = index
        self._sync_buttons()

        self._busy = True
        old.hide()
        new.show()
        new.raise_()

        # ---- 新页滑入 ----
        base = QPoint(0, 0)
        offset = 26 if forward else -26
        new.move(QPoint(offset, 0))
        anim = QPropertyAnimation(new, b"pos", self)
        anim.setDuration(260)
        anim.setStartValue(QPoint(offset, 0))
        anim.setEndValue(base)
        anim.setEasingCurve(QEasingCurve.OutCubic)

        def _done():
            try:
                new.move(base)
            except Exception:
                pass
            self._busy = False

        anim.finished.connect(_done)
        self._anims.append(anim)
        anim.start(QAbstractAnimation.DeleteWhenStopped)

        # ---- 页内内容错峰浮现 ----
        self._reveal_items(new._reveal_items, start_delay=90)

    def _reveal_items(self, items, start_delay=0):
        """让元素依次淡入。"""
        for i, w in enumerate(items):
            if w is None:
                continue
            try:
                eff = QGraphicsOpacityEffect(w)
                eff.setOpacity(0.0)
                w.setGraphicsEffect(eff)
            except Exception:
                continue
            a = QPropertyAnimation(eff, b"opacity")
            a.setDuration(280)
            a.setStartValue(0.0)
            a.setEndValue(1.0)
            a.setEasingCurve(QEasingCurve.OutCubic)
            self._anims.append(a)
            QTimer.singleShot(int(start_delay + i * 70), a.start)

    # ------------------------------------------------------------ 入场
    def showEvent(self, e):
        """显示时：窗口淡入 + 首屏内容错峰浮现。"""
        super().showEvent(e)
        if self._reveal_done:
            return
        self._reveal_done = True
        try:
            from ui.reveal_anim import install_button_hover, reveal_window

            reveal_window(self, fade_ms=200)
            install_button_hover(self)
            self._reveal_items(self._pages[0]._reveal_items, start_delay=160)
        except Exception:
            for w in self._pages[0]._reveal_items:
                try:
                    w.setGraphicsEffect(None)
                except Exception:
                    pass

    # ------------------------------------------------------------ 收尾
    def accept(self):
        """点「开始使用」：把用户改过的配置交给调用方。

        ## 为什么和 `self._start_config` 比，而不是 `self._config`

        `_sync_after_import()` 在导入后会把磁盘配置合并进
        `self._config`。如果这里仍然和 `self._config` 比，那些
        **刚刚从备份导入**的值就会被判定为"没变化"，
        于是不会交给宿主 → 当前会话里热键和外观不生效，
        用户得重启一次才看得到。

        所以另存一份启动时的快照，只跟它比。
        """
        base = getattr(self, "_start_config", None) or self._config or {}
        try:
            self.chosen = {}
            if getattr(self, "_hotkey_combo", None) is not None:
                hk = self._hotkey_combo.currentData()
                if hk and hk != base.get("hotkey"):
                    self.chosen["hotkey"] = hk
            if getattr(self, "_theme_combo", None) is not None:
                th = self._theme_combo.currentData()
                if th and th != base.get("theme"):
                    self.chosen["theme"] = th
                    self.chosen["theme_mode"] = th
        except Exception:
            self.chosen = {}
        super().accept()


def maybe_show(theme_name="light", config=None, force=False):
    """按标记决定是否弹欢迎页。

    :return: 用户选择的配置 dict（未改动或跳过时为空 dict）；
             没弹（已弹过）返回 None。
    """
    from core import version as ver

    if not ver.should_show_welcome(force=force):
        return None

    dlg = WelcomeDialog(theme_name=theme_name,
                        version=ver.get_app_version(),
                        config=config)
    accepted = dlg.exec() == QDialog.Accepted
    os.environ.pop("KAOMOJI_FORCE_WELCOME", None)
    if accepted:
        ver.mark_welcome_shown()
        return dict(dlg.chosen)
    # 跳过：不标记，下次启动还会看到引导
    return {}
