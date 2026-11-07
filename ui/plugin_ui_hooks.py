"""插件 UI 能力的真实实现：把 `core.ui_hooks.UiHooks` 协议落到 Qt 上。

**为什么放在 ui/ 而不是 core/**：core 只声明「我需要弹窗 / 消息框 / 通知 / 主题
令牌」这套协议（`core/ui_hooks.py`），具体怎么画是呈现层的事。这样依赖方向是
`app_runtime → ui → core`，core 不再反向 import ui.*。

本模块的每个函数都是「协议方法」的直接对应物，签名与
`core.ui_hooks.UiHooks` 一一对齐；装配层用 `build_ui_hooks(...)` 组装后注入。

⚠ 这里的 theme 取自 `theme_provider()`，而不是缓存的配置副本——用户在设置页
切换浅色/深色后，插件下一次性打开的弹窗要立刻呈现新配色。
"""
from core.ui_hooks import UiHooks
from ui.plugin_dialog import show_message, show_plugin_dialog
from ui.win11_theme import Theme

# 暴露给插件的主题令牌白名单。
# 刻意只放"插件画控件真正用得到的颜色"：窗口基底/渐变/阴影等外壳令牌由宿主
# 负责，开放出去只会诱使插件自绘外壳，反而与统一观感打架。
_THEME_TOKEN_KEYS = (
    "dark", "name",
    "text", "text_secondary", "text_tertiary",
    "accent", "accent_hover",
    "card", "card_hover", "card_border", "divider",
    "input_bg", "input_border",
    "nav_hover", "nav_selected",
)

# 插件弹窗的登记表：manager 只登记 spec，真正创建的 QWidget 由这里持有，
# 卸载插件时集中 close + deleteLater（避免 core 层持 Qt 对象导致悬垂）。
_open_dialogs = {}       # plugin_id -> [QWidget]


def theme_tokens(theme_provider=None):
    """当前主题的颜色令牌字典（供插件自绘控件着色）。"""
    name = "light"
    try:
        if callable(theme_provider):
            name = theme_provider() or "light"
    except Exception:
        name = "light"
    t = Theme(name)
    return {key: getattr(t, key) for key in _THEME_TOKEN_KEYS
            if hasattr(t, key)}


def open_dialog(plugin_id, factory, title, width, height, modal,
                theme_provider=None, parent=None):
    """按插件工厂创建内容并弹出主题化无边框弹窗。

    factory 为 None 时弹空壳（供插件自行挂内容）；模态弹窗在
    `show_plugin_dialog` 内部同步 exec 完，返回值即结果码。
    """
    name = theme_provider() if callable(theme_provider) else "light"
    theme = Theme(name or "light")
    body = factory(None) if factory is not None else None
    dlg = show_plugin_dialog(body, title, theme, parent, width, height, modal)
    if modal:
        return dlg
    _open_dialogs.setdefault(plugin_id, []).append(dlg)
    dlg.finished.connect(
        lambda _r, d=dlg, p=plugin_id: _forget_dialog(p, d))
    return dlg


def _forget_dialog(plugin_id, dlg):
    lst = _open_dialogs.get(plugin_id)
    if not lst:
        return
    try:
        lst.remove(dlg)
    except ValueError:
        pass
    if not lst:
        _open_dialogs.pop(plugin_id, None)


def show_message_box(text, title, ok_text, cancel_text, theme_provider=None,
                     parent=None):
    """主题化消息框；返回 bool（有取消按钮时按用户选择）。"""
    name = theme_provider() if callable(theme_provider) else "light"
    return bool(show_message(text, title, Theme(name or "light"), parent,
                             ok_text, cancel_text))


def close_dialogs(plugin_id=None):
    """关闭插件弹窗（None 表示全部）；不抛异常。"""
    targets = [plugin_id] if plugin_id else list(_open_dialogs)
    for pid in targets:
        for dlg in list(_open_dialogs.pop(pid, [])):
            try:
                dlg.close()
                dlg.deleteLater()
            except RuntimeError:
                pass        # C++ 对象已销毁


def build_ui_hooks(theme_provider=None, notify=None, parent=None):
    """组装注入给 `PluginManager` 的 UI 能力集合。

    :param theme_provider: () -> "light" / "dark"（装配层传入已解析过的主题名）
    :param notify:         (title, message, ms) -> bool，托盘气泡；托盘未就绪时可空
    :param parent:         弹窗的父窗口（一般传统一主面板）
    """
    return UiHooks(
        theme_tokens=lambda: theme_tokens(theme_provider),
        open_dialog=lambda pid, factory, title, w, h, modal: open_dialog(
            pid, factory, title, w, h, modal, theme_provider, parent),
        show_message=lambda text, title, ok, cancel: show_message_box(
            text, title, ok, cancel, theme_provider, parent),
        notify=notify,
        close_dialogs=close_dialogs,
    )
