"""示例插件：演示 kaomoji-assistant 插件 API 的完整用法（含 UI 扩展）。

非 UI 能力（api_version 1 起）：
1. 数据源：追加「示例」分类，出现在库页分类下拉 / 搜索 / 候选条；
2. 输出转换：可选地给每次上屏的文本加装饰；
3. 事件：订阅 after_insert 写日志。

UI 能力（需清单声明 api_version >= 2）：
4. register_settings_widget：在插件页自己的卡片下方嵌入一块设置区；
5. register_page：往侧边栏注册一个插件自己的页面；
6. show_dialog / show_message / ask / notify：主题化弹窗与托盘通知。

约定提醒：
- factory(parent) 必须返回 QWidget（或集成 QWidget 的自定义类）；
- 宿主会把完整主题 QSS 垫在你的 widget 上，因此没必要自己写 color/背景；
  若 widget 实现了 set_theme(theme)，主题切换时宿主会回调它。
"""
# 插件内部可以 import PySide6：与主程序同进程、主线程，这里是官方支持的用法
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QCheckBox,
    QLineEdit, QListWidget, QPlainTextEdit,
)


def setup(api):
    # ------------------------------------------------------------------
    # 1. 数据源
    # ------------------------------------------------------------------
    ITEMS = [
        "(￣▽￣)-c[].",
        "ヽ(°〇°)ﾉ",
        "☆*:.｡.o(≧▽≦)o.｡.:*☆",
        "(๑•̀ㅂ•́)و✧",
        "∑(ﾟДﾟ)",
        "(  ´•̥̥̥ω•̥̥̥`  )",
        "  ( ᐛ )σ",
        "(⁎˃ᆺ˂)",
    ]
    api.add_source("示例", ITEMS)

    # ------------------------------------------------------------------
    # 2. 输出转换（开关由下面的设置面板控制，写进 api.settings）
    # ------------------------------------------------------------------
    def decorate(text, ctx):
        suffix = api.settings.get("suffix") or ""
        if not api.settings.get("enabled", True):
            return text
        return text + suffix if suffix else text

    api.register_transform(decorate, name="尾部装饰", priority=200)

    # ------------------------------------------------------------------
    # 3. 事件
    # ------------------------------------------------------------------
    def on_insert(text, ctx):
        api.log("示例插件观察到一次插入：%s" % text)

    api.on("after_insert", on_insert)

    # ------------------------------------------------------------------
    # 4. 卡片内嵌设置面板（factor y: factory(parent) -> QWidget）
    # ------------------------------------------------------------------
    def build_settings(parent):
        # 用自定义类而不是裸 QWidget：这样可以实现 set_theme(theme)，
        # 让宿主在主题切换时回调（可选能力，不实现也能用）。
        class _SettingsPanel(QWidget):
            def __init__(self, parent=None):
                super().__init__(parent)
                root = QVBoxLayout(self)
                root.setContentsMargins(0, 0, 0, 0)
                root.setSpacing(10)

                row_top = QHBoxLayout()
                self.chk = QCheckBox("启用「尾部装饰」转换")
                self.chk.setChecked(bool(api.settings.get("enabled", True)))
                self.chk.stateChanged.connect(self._on_enabled)
                row_top.addWidget(self.chk)
                row_top.addStretch(1)
                root.addLayout(row_top)

                row_suffix = QHBoxLayout()
                lbl = QLabel("装饰后缀：")
                lbl.setObjectName("Caption")
                self.edit = QLineEdit(api.settings.get("suffix", " ☆"))
                self.edit.setMaximumWidth(140)
                self.edit.textChanged.connect(self._on_suffix)
                row_suffix.addWidget(lbl)
                row_suffix.addWidget(self.edit)
                row_suffix.addStretch(1)
                root.addLayout(row_suffix)

                btn_row = QHBoxLayout()
                self.dialog_btn = QPushButton("打开自定义弹窗…")
                self.dialog_btn.setObjectName("AccentButton")
                self.dialog_btn.clicked.connect(self._open_dialog)
                self.notify_btn = QPushButton("发一条托盘通知")
                self.notify_btn.clicked.connect(self._notify_once)
                btn_row.addWidget(self.dialog_btn)
                btn_row.addWidget(self.notify_btn)
                btn_row.addStretch(1)
                root.addLayout(btn_row)

                self.tip = QLabel("")
                self.tip.setObjectName("Caption")
                root.addWidget(self.tip)

            # ---- 写回插件私有配置（宿主不会代劳，需要插件自己调 save_settings） ----
            def _on_enabled(self, state):
                api.settings["enabled"] = bool(state)
                api.save_settings()

            def _on_suffix(self, text):
                api.settings["suffix"] = text
                api.save_settings()

            # ---- UI 能力：消息框 / 自定义弹窗 / 托盘通知 ----
            def _open_dialog(self):
                if api.ask("要打开示例弹窗吗？（这一步演示 ask 询问框）"):
                    body = build_dialog_body()
                    api.show_dialog(lambda _parent: body, title="示例弹窗",
                                    width=520, height=380)
                    self.tip.setText("已打开示例弹窗。")
                else:
                    self.tip.setText("取消了。")

            def _notify_once(self):
                api.notify("示例插件", "这是一条来自插件的托盘通知")
                self.tip.setText("已发送托盘通知。")

            # ---- 可选：主题切换回调 ----
            def set_theme(self, theme):
                # 宿主保证调用前完整主题 QSS 已垫好（原生控件深浅色自动跟随），
                # 这里只在需要「额外定制」时使用，例如根据 theme.dark 换自绘图元颜色。
                pass

        return _SettingsPanel(parent)

    api.register_settings_widget(build_settings, title="插件设置")

    # ------------------------------------------------------------------
    # 5. 侧边栏独立页
    # ------------------------------------------------------------------
    def build_page(parent):
        class _DemoPage(QWidget):
            def __init__(self, parent=None):
                super().__init__(parent)
                root = QVBoxLayout(self)
                root.setContentsMargins(36, 28, 36, 28)
                root.setSpacing(16)

                title = QLabel("示例插件 · 演示页")
                title.setObjectName("PageTitle")
                root.addWidget(title)

                sub = QLabel(
                    "这是插件通过 api.register_page() 注册的独立页面，"
                    "由统一窗口托管（切页带动画、跟随主题、插件卸载即移除）。")
                sub.setObjectName("Caption")
                sub.setWordWrap(True)
                root.addWidget(sub)

                list_box = QListWidget()
                list_box.addItems(ITEMS)
                list_box.itemDoubleClicked.connect(
                    lambda it: api.show_message("你双击了：%s" % it.text(),
                                                title="插件消息框"))
                root.addWidget(list_box, 1)

                bar = QHBoxLayout()
                self.log_box = QPlainTextEdit()
                self.log_box.setReadOnly(True)
                self.log_box.setPlaceholderText("操作日志会显示在这里…")
                self.log_box.setFixedHeight(90)
                root.addWidget(self.log_box)

                btn_count = QPushButton("统计 items")
                btn_count.clicked.connect(
                    lambda: self._say("共 %d 条示例颜文字" % list_box.count()))
                btn_tokens = QPushButton("打印主题令牌")
                btn_tokens.clicked.connect(
                    lambda: self._say("当前主题 dark=%s accent=%s"
                                      % (api.theme_tokens().get("dark"),
                                         api.theme_tokens().get("accent"))))
                bar.addWidget(btn_count)
                bar.addWidget(btn_tokens)
                bar.addStretch(1)
                root.addLayout(bar)

            def _say(self, msg):
                self.log_box.appendPlainText(msg)
                api.log(msg)

        return _DemoPage(parent)

    api.register_page("demo", "示例演示", build_page)

    # ------------------------------------------------------------------
    # 6. 弹窗内容工厂（供上面的「打开自定义弹窗」按钮复用）
    # ------------------------------------------------------------------
    def build_dialog_body():
        body = QWidget()
        lay = QVBoxLayout(body)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        info = QLabel(
            "这个弹窗与主窗口同风格：无边框 + 圆角 + 阴影 + 跟随深浅色。\n"
            "插件只需要提供一个内容 QWidget，外壳由主程序负责。")
        info.setWordWrap(True)
        lay.addWidget(info)
        lay.addStretch(1)
        return body
