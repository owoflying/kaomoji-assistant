"""正面示例：用官方 `api.ui` 修改内置界面。

════════════════════════════════════════════════════════════════════
这是与 `plugins/home_greeting/`（邪修反面教材）**一一对照**的正规写法。
同一个需求——往主页加一张自己的卡片、改一改内置界面——
这边不需要锚点页、不需要爬 Qt 对象树、不需要任何清理代码。
════════════════════════════════════════════════════════════════════

## 它做了什么

1. 改问候语标题（`set_text`）
2. 给问候卡片加边框（`set_style`）
3. 隐藏「使用提示」卡片（`set_visible`）
4. 改一个快捷入口的文字（`set_text`）
5. 插入一张自己的卡片（`insert`）
6. 给快捷入口绑定点击（`on`）

## 关键点

- **全部修改由宿主记录**，插件停用/卸载/崩溃时自动逐项回滚；
- 本文件**没有任何清理代码** —— 这是与邪修版最大的区别；
- 所有目标都是 `core/ui_ids.py` 里发布的稳定 id；
- 返回值一律是 bool，目标不存在就跳过，不抛异常。

## 对照：邪修版要写多少东西

| 事项 | 本插件 | 邪修版 `home_greeting` |
|------|--------|----------------------|
| 定位宿主 | `api.ui.find("home.greeting")` | register_page + window() + 能力探测 |
| 改界面 | 一行 `set_text` | `host.home_page.layout().insertWidget(...)` |
| 清理 | **不用写** | destroyed 信号/标志位/轮询，试错 7 轮 |
| 主程序重构后 | id 契约保证不碎 | 静默失效 |
"""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout

from core import ui_ids


def _build_tip_card(parent=None):
    """插件自己的卡片：复用宿主 objectName 以继承全局 QSS（自动跟随深浅色）。"""
    card = QFrame(parent)
    card.setObjectName("Card")
    root = QHBoxLayout(card)
    root.setContentsMargins(20, 18, 20, 18)
    root.setSpacing(16)

    emoji = QLabel("✨")
    emoji.setObjectName("HomeTitle")
    emoji.setFixedWidth(40)
    emoji.setAlignment(Qt.AlignCenter)
    root.addWidget(emoji)

    box = QVBoxLayout()
    box.setContentsMargins(0, 0, 0, 0)
    box.setSpacing(4)

    title = QLabel("这张卡片来自插件")
    title.setObjectName("HomeTitle")
    box.addWidget(title)

    sub = QLabel("用 api.ui.insert() 插入，插件停用后会被宿主自动移除。")
    sub.setObjectName("BodyText")
    sub.setWordWrap(True)
    box.addWidget(sub)

    root.addLayout(box, 1)
    return card


def setup(api):
    ui = api.ui

    # ---------- 1. 改内置问候语 ----------
    # ⚠ 注意：不要在 setup() 里先 text_of() 读原值再拼接——装配顺序是
    # 「先加载插件、后建窗口」，此刻界面元素还没登记，读出来是空串。
    # api.ui 的**修改类**方法会自动排队等界面就绪后重放，但**读取类**
    # （text_of / find）不会排队（它们需要立即返回值）。
    # 所以这类"基于原值做改动"的写法要放进 startup 事件里。
    ui.set_style(ui_ids.HOME_GREETING, "border: 1px solid #4f8cff;")

    # ---------- 2. 隐藏「使用提示」卡片 ----------
    ui.set_visible(ui_ids.HOME_TIP, False)

    # ---------- 3. 改一个快捷入口的文字 ----------
    ui.set_text(ui_ids.HOME_ACTION_PREFIX + "search", "找颜文字")

    # ---------- 4. 插一张自己的卡片 ----------
    ui.insert(ui_ids.HOME_GREETING, _build_tip_card(), index=0)

    # ---------- 5. 绑定点击事件 ----------
    def _on_search():
        api.notify("界面定制示例", "你点了改过名的「找颜文字」")
        api.log("快捷入口被点击")

    ui.on(ui_ids.HOME_ACTION_PREFIX + "search", "clicked", _on_search)

    # ---------- 6. 需要读原值的改动放到 startup（此时界面已就绪）----------
    _greeted = {"done": False}

    def on_startup():
        # 幂等保护：startup 正常只发一次，但若被重复触发（手动调试 emit），
        # 无尽叠加后缀会变成"你好，插件向你问好！，插件向你问好！"。
        if _greeted["done"]:
            return
        _greeted["done"] = True
        original = ui.text_of(ui_ids.HOME_GREETING_TITLE, default="")
        if original:
            ui.set_text(ui_ids.HOME_GREETING_TITLE, "%s，插件向你问好！" % original)
        api.log("界面定制示例：共修改 %d 处界面（卸载时自动回滚）" % ui.pending())

    api.on("startup", on_startup)

    # ⚠ 注意：这里**没有**任何清理代码。
    # 停用插件时宿主会调用 MutatorHub.rollback() 逐项还原上面每一次修改。
