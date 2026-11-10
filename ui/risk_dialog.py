"""功能首次启用时的风险提示（插件 / UIA 提权各一条）。

## 为什么放在"首次启用"而不是欢迎页

欢迎页是新手引导，讲怎么用、帮做配置、介绍亮点——用户在那种心境下
不会认真读免责条款，堆在一起只会被当噪音划过去。

风险提示的价值在于**用户正要做这件事的那一刻**：

    * 打开某个插件时 → 才知道插件能干什么、有什么风险
    * 开启 UIA 提权时 → 才知道要管理员权限、要重启

所以两条提示分别挂在自己的入口上，看一次就记住（各自有独立标记）。

## 文案原则

说人话、讲清楚"会发生什么"，不用"不会留下任何……"这类套话。
"""
import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox

from ui.win11_theme import Theme

#: 插件首次启用的提示
PLUGIN_TITLE = "关于插件"
PLUGIN_TEXT = (
    "插件是本机上的 Python 代码，运行权限和主程序一样高，"
    "能读写文件、联网，也能修改界面（包括其他插件的界面）。\n\n"
    "所以：只装你信得过的插件。从网上随手下的插件，和随手运行一个 exe "
    "风险差不多。\n\n"
    "插件对界面的改动，主程序都会记下来，停用或卸载时会还原回去。"
)

#: UIA 提权首次开启的提示
UIA_TITLE = "关于 UIA 提权"
UIA_TEXT = (
    "开启后会发生这三件事：\n\n"
    "1. 弹出 Windows 的权限确认框；\n"
    "2. 你同意后，程序会以管理员身份重新启动；\n"
    "3. 候选条获得系统级窗口权限，能盖在屏幕键盘等窗口上面。\n\n"
    "管理员状态下程序能做的事比平时多，请确认你知道自己在给哪个程序提权。\n"
    "关掉这个开关、或退出程序，都会恢复成普通权限。"
)

#: 标记文件名
_PLUGIN_FLAG = "risk_plugin_shown.txt"
_UIA_FLAG = "risk_uia_shown.txt"


def _flag_path(name):
    from core import runtime
    return os.path.join(runtime.app_data_dir(), name)


def _seen(name):
    try:
        return os.path.exists(_flag_path(name))
    except Exception:
        return True


def _mark(name):
    try:
        p = _flag_path(name)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write("1")
    except Exception:
        pass


def _ask(parent, title, text, theme_name="light"):
    """弹一次确认框，返回用户是否点了「我知道了」。"""
    box = QMessageBox(parent)
    box.setWindowTitle(title)
    box.setText(text)
    box.setIcon(QMessageBox.Information)
    box.setStandardButtons(QMessageBox.Ok)
    box.button(QMessageBox.Ok).setText("我知道了")
    try:
        box.setStyleSheet(Theme(theme_name).style_sheet())
    except Exception:
        pass
    box.setAttribute(Qt.WA_DeleteOnClose, False)
    box.exec()
    return True


def maybe_show_plugin_risk(parent=None, theme_name="light", force=False):
    """第一次启用插件时提示一次。返回是否真的弹了。"""
    if not force and _seen(_PLUGIN_FLAG):
        return False
    _ask(parent, PLUGIN_TITLE, PLUGIN_TEXT, theme_name)
    _mark(_PLUGIN_FLAG)
    return True


def maybe_show_uia_risk(parent=None, theme_name="light", force=False):
    """第一次开启 UIA 提权时提示一次。返回是否真的弹了。"""
    if not force and _seen(_UIA_FLAG):
        return False
    _ask(parent, UIA_TITLE, UIA_TEXT, theme_name)
    _mark(_UIA_FLAG)
    return True
