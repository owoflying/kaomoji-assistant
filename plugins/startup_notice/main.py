# -*- coding: utf-8 -*-
"""启动提示示例插件。

演示三件事的组合用法：
    1. startup 事件  —— 主窗口与托盘就绪、事件循环跑起来之后由主程序发出（每次启动仅一次）；
    2. 主题化弹窗    —— api.show_message / api.notify（无需自己管圆角、阴影、深浅色）；
    3. 卡片内嵌设置面板 —— api.register_settings_widget，可在插件页卡片下方改配置。

行为：启动完成后按「提示模式」提醒一次（弹窗 / 托盘气泡 / 不提示）。
注意：startup 每次启动只发一次，面板里改的模式**下次启动生效**；
「预览」按钮则严格按当前模式演示一次，方便立刻确认改对了没有。
"""
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
)

# 三种提示模式，设置面板里的按钮按顺序循环切换
_MODES = ("dialog", "notify", "off")
_MODE_TEXT = {
    "dialog": "弹窗提示",
    "notify": "托盘气泡",
    "off": "不提示",
}


def setup(api):
    api.settings.setdefault("mode", "dialog")
    api.settings.setdefault("text", "应用加载成功，插件已就绪。")

    # ------------------------------------------------------ 统一提示入口
    def _announce():
        """按当前模式提示一次，返回实际采用的方式名（供日志/回显）。

        启动时与「预览」共用这一条路径，保证所见即所得。
        """
        mode = api.settings.get("mode", "dialog")
        text = str(api.settings.get("text") or "应用加载成功")
        if mode == "notify":
            api.notify(api.name, text)
            return "托盘气泡"
        if mode == "off":
            return "不提示"
        api.show_message(text, title="启动完成", ok_text="知道了")
        return "弹窗"

    def on_startup():
        # 把实际生效的模式写进日志：可在托盘「查看日志」里核对，
        # 避免「改了设置但不确定有没有生效」时无从判断。
        mode = api.settings.get("mode", "dialog")
        api.log("启动提示：模式=%s（来源=%s）" % (_MODE_TEXT.get(mode, mode), mode))
        how = _announce()
        if how == "不提示":
            api.log("启动提示：不提示模式，已跳过")
        else:
            api.log("启动提示已按「%s」提醒" % how)

    api.on("startup", on_startup)

    # ------------------------------------------------------------ 卡片设置面板
    def build_settings(parent):
        w = QWidget(parent)
        lay = QVBoxLayout(w)
        lay.setContentsMargins(16, 12, 16, 12)
        lay.setSpacing(10)

        tip = QLabel("启动成功后的提醒方式（改完下次启动生效）：")
        tip.setObjectName("Caption")
        tip.setWordWrap(True)
        lay.addWidget(tip)

        # 提示文本
        row = QHBoxLayout()
        edit = QLineEdit(str(api.settings.get("text") or ""))
        edit.setPlaceholderText("启动时显示的内容")
        save_btn = QPushButton("保存")
        save_btn.setObjectName("AccentButton")
        row.addWidget(edit, 1)
        row.addWidget(save_btn, 0)
        lay.addLayout(row)

        # 模式切换（循环 dialog -> notify -> off）
        mode_btn = QPushButton("")
        mode_btn.setObjectName("AccentButton")

        def _refresh_mode_btn():
            mode_btn.setText("当前：%s（点击切换）"
                             % _MODE_TEXT.get(api.settings.get("mode", "dialog"), "弹窗提示"))

        def _cycle_mode():
            cur = api.settings.get("mode", "dialog")
            nxt = _MODES[(_MODES.index(cur) + 1) % len(_MODES)] if cur in _MODES else "notify"
            api.settings["mode"] = nxt
            api.save_settings()
            _refresh_mode_btn()
            api.log("启动提示模式改为 %s（下次启动生效）" % nxt)

        def _save_text():
            api.settings["text"] = edit.text().strip() or "应用加载成功"
            api.save_settings()
            edit.setText(api.settings["text"])
            api.log("启动提示文本已保存")

        def _preview():
            """预览：与启动时走同一条路径（不提示模式除外，只回显状态）。"""
            how = _announce()
            if how == "不提示":
                api.notify(api.name, "当前是「不提示」模式：启动时不会打扰你")
            api.log("预览启动提示：%s" % how)

        mode_btn.clicked.connect(_cycle_mode)
        save_btn.clicked.connect(_save_text)
        _refresh_mode_btn()
        lay.addWidget(mode_btn)

        preview_btn = QPushButton("预览当前效果")
        preview_btn.clicked.connect(_preview)
        lay.addWidget(preview_btn)

        hint = QLabel("提示：startup 事件每次启动只发一次，改动需重启应用后生效。")
        hint.setObjectName("Caption")
        hint.setWordWrap(True)
        lay.addWidget(hint)

        lay.addStretch(1)
        return w

    api.register_settings_widget(build_settings, "启动提示设置")

    api.log("启动提示插件已载入（模式=%s）" % api.settings.get("mode"))
