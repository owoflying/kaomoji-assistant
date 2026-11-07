"""UI 钩子协议：让 core 层使用界面能力，而不反向 import ui.*。

**为什么需要这一层**：`core/plugins.py` 的插件 API 需要弹窗、消息框、主题令牌，
原先在函数内 `from ui.plugin_dialog import ...` 延迟 import。虽然避开了顶层循环
依赖，但 `core → ui` 的依赖方向仍然存在，破坏了「core 与 Qt 解耦、可无界面单测」
的分层原则（`core/` 其余模块确实基本不依赖 Qt）。

**做法**：core 只声明自己**需要什么**（本文件的 `UiHooks` 协议），真正的实现由
装配层（`app_runtime`）在构造时注入。core 拿不到实现时退化为安全空实现——
弹窗返回 None、消息框返回 False、主题令牌返回空 dict，插件代码不会因此崩溃。

约定：
  * 所有方法的实现方都在 `ui/` 侧，core 只按协议调用；
  * 任何方法都可能不存在、返回 None 或抛异常，调用方必须容忍
    （见 `NullUiHooks` 与 `PluginManager` 里的 `_ui_call`）。
"""
from typing import Any, Callable, Dict, Optional


class NullUiHooks:
    """无界面环境（离屏单测、纯 core 使用）下的安全空实现。

    每种能力都返回一个"无害的值"并对外报告"我没有这项能力"，
    这样插件代码无需判断宿主是否存在 UI。
    """

    #: 是否提供真实的界面能力（False 时调用方应跳过 UI 相关分支）
    available = False

    def theme_tokens(self) -> Dict[str, Any]:
        return {}

    def open_dialog(self, plugin_id, factory, title, width, height, modal):
        return None

    def show_message(self, text, title, ok_text, cancel_text):
        return False

    def notify(self, title, message, ms):
        return False

    def close_dialogs(self, plugin_id=None):
        return None


class UiHooks(NullUiHooks):
    """真实 UI 能力的注入点：由 `app_runtime` 构造并传给 `PluginManager`。

    参数全是回调（callable），不含任何 Qt 类型标注——core 侧不需要 import Qt，
    也就不会因为 PySide6 不可用而无法导入本模块。

    :param theme_tokens:   () -> dict，当前主题颜色令牌
    :param open_dialog:    (plugin_id, factory, title, width, height, modal) -> Any
    :param show_message:   (text, title, ok_text, cancel_text) -> bool
    :param notify:         (title, message, ms) -> bool
    :param close_dialogs:  (plugin_id or None) -> None
    """

    available = True

    def __init__(self,
                 theme_tokens: Optional[Callable[[], Dict[str, Any]]] = None,
                 open_dialog: Optional[Callable[..., Any]] = None,
                 show_message: Optional[Callable[..., Any]] = None,
                 notify: Optional[Callable[..., Any]] = None,
                 close_dialogs: Optional[Callable[..., Any]] = None):
        self._theme_tokens = theme_tokens
        self._open_dialog = open_dialog
        self._show_message = show_message
        self._notify = notify
        self._close_dialogs = close_dialogs

    # 每个方法都显式判断回调是否存在：装配层可以只注入一部分能力
    # （例如托盘就绪前只有弹窗、没有通知），未注入的项自动退化为空实现。
    def theme_tokens(self):
        if not callable(self._theme_tokens):
            return {}
        return self._theme_tokens() or {}

    def open_dialog(self, plugin_id, factory, title, width, height, modal):
        if not callable(self._open_dialog):
            return None
        return self._open_dialog(plugin_id, factory, title, width, height, modal)

    def show_message(self, text, title, ok_text, cancel_text):
        if not callable(self._show_message):
            return False
        return bool(self._show_message(text, title, ok_text, cancel_text))

    def notify(self, title, message, ms):
        if not callable(self._notify):
            return False
        return bool(self._notify(title, message, ms))

    def close_dialogs(self, plugin_id=None):
        if callable(self._close_dialogs):
            self._close_dialogs(plugin_id)
