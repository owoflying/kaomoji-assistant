# -*- coding: utf-8 -*-
"""启动提示示例插件。

演示三件事的组合用法：
    1. startup 事件  —— 主窗口与托盘就绪、事件循环跑起来之后由主程序发出（仅一次）；
    2. 主题化弹窗    —— api.show_message / api.notify（无需自己管圆角、阴影、深浅色）；
    3. 卡片内嵌设置面板 —— api.register_settings_widget，可在插件页卡片下方改配置。

行为：启动完成后弹「应用加载成功」；在设置面板里可切换为托盘气泡，或彻底关掉。
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
    cfg = api.settings
    cfg.setdefault("mode", "dialog")
    cfg.setdefault("text", "应用加载成功，插件已就绪。")

    # ---------------------------------------------------------------- 启动回调
    def on_startup():
        mode = cfg.get("mode", "dialog")
        text = str(cfg.get("text") or "应用加载成功")
        if mode == "off":
            api.log("启动提示已关闭，跳过")
            return
        if mode == "notify":
            api.notify(api.name, text)
            api.log("已发送托盘通知：%s" % text)
            return
        # modal 弹窗：与主窗口同风格（无边框+圆角+亚克力色），按主题自动配色
        api.show_message(text, title="启动完成", ok_text="知道了")
        api.log("已弹出启动提示：%s" % text)

    api.on("startup", on_startup)

    # ------------------------------------------------------------ 卡片设置面板
    def build_settings(parent):
        w = QWidget(parent)
        lay = QVBoxLayout(w)
        lay.setContentsMargins(16, 12, 16, 12)
        lay.setSpacing(10)

        tip = QLabel("应用启动成功后如何提示（立即生效，自动保存）：")
        tip.setObjectName("Caption")
        tip.setWordWrap(True)
        lay.addWidget(tip)

        # 提示文本
        row = QHBoxLayout()
        edit = QLineEdit(str(cfg.get("text") or ""))
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
            mode_btn.setText("当前：%s（点击切换）" % _MODE_TEXT.get(
                cfg.get("mode", "dialog"), "弹窗提示"))

        def _cycle_mode():
            cur = cfg.get("mode", "dialog")
            nxt = _MODES[(_MODES.index(cur) + 1) % len(_MODES)] if cur in _MODES else "notify"
            cfg["mode"] = nxt
            api.save_settings()
            _refresh_mode_btn()
            api.log("启动提示模式改为 %s" % nxt)

        def _save_text():
            cfg["text"] = edit.text().strip() or "应用加载成功"
            api.save_settings()
            edit.setText(cfg["text"])
            api.log("启动提示文本已保存")

        mode_btn.clicked.connect(_cycle_mode)
        save_btn.clicked.connect(_save_text)
        _refresh_mode_btn()
        lay.addWidget(mode_btn)

        # 立刻预览一次当前效果（非阻塞，用气泡展示）
        preview_btn = QPushButton("预览当前效果")
        preview_btn.clicked.connect(lambda: api.notify(api.name, str(cfg.get("text") or "")))
        lay.addWidget(preview_btn)

        lay.addStretch(1)
        return w

    api.register_settings_widget(build_settings, "启动提示设置")

    api.log("启动提示插件已载入（模式=%s）" % cfg.get("mode"))
