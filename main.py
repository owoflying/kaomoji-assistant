"""程序入口。

只做四件事：装日志/异常处理器、创建 Qt 应用、注册图标字体、把装配工作交给
`app_runtime.AppRuntime`。具体组件创建与信号接线都在那边，这里保持薄壳——
入口一旦膨胀，任何组件的初始化顺序问题都会被埋进几百行闭包里。

命令行：
    python main.py            正常启动
    python main.py --diag     写 diag.txt 自检后退出（排查图标豆腐块/字体缺失）
"""
import os
import sys

# 确保项目根目录在 sys.path，方便以脚本方式直接运行（打包态已是包根）
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from PySide6.QtWidgets import QApplication

from core.applog import append_log, install_excepthook, install_qt_log_filter


def main():
    install_qt_log_filter()
    install_excepthook()

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("颜文字输入辅助器")
    append_log("info", "app", "日志开始记录。")

    # 注册内置图标字体（Fluent System Icons，随包分发，Win10 也能正常显示）。
    # 必须在 QApplication 之后：字体数据库由 QGuiApplication 提供。
    from ui.fluent_icons import ensure_icon_font
    ensure_icon_font()

    # 自检模式：KaomojiAssistant.exe --diag 会在 exe 同目录写 diag.txt 后退出，
    # 用来排查「图标显示成豆腐块」这类打包资源问题（窗口程序看不到控制台输出）。
    if "--diag" in sys.argv:
        from ui.diagnostics import write_diag
        write_diag()
        return

    # 更新功能自检：验证**打包态**下自动更新是否真的可用。
    #
    # 为什么需要它：`updater.is_supported()` 在源码态返回 False，
    # 相关分支根本不会执行——曾经因此漏掉一个"一打包就崩"的 bug。
    # 这个模式把关键状态写成文本文件，方便在真实 exe 上确认。
    if "--selftest-update" in sys.argv:
        _selftest_update(app)
        return

    # 本次是否由「UAC 提权申请」启动的新实例。
    # 看过这个参数就标记下来，避免新进程再次弹 UAC（防无限弹窗循环）。
    from core import elevate
    if "--elevated-by-app" in sys.argv:
        elevate.mark_guarded()
        append_log("info", "app", "本次为提权启动（由程序自身申请管理员权限）")

    from app_runtime import AppRuntime
    from core.config_store import load_config

    # 运行期组件通过 app 反向持有，避免被垃圾回收提前收走
    app.runtime = AppRuntime(app, load_config())
    sys.exit(app.exec())


def _selftest_update(app):
    """把自动更新的关键状态写到 exe 同目录的 update_selftest.txt。

    覆盖源码态测不到的几条：is_supported、设置页开关是否可用、
    关于页安装回调是否注入、updater 是否被创建。
    """
    import os
    import sys as _sys
    import traceback

    lines = []

    def w(s=""):
        lines.append(str(s))

    w("frozen = %r" % getattr(_sys, "frozen", False))
    try:
        from core import updater
        w()
        w("[updater]")
        w("  is_supported      = %r" % updater.is_supported())
        w("  unsupported_reason= %r" % (updater.unsupported_reason() or ""))
        w("  should_check_now  = %r"
          % updater.should_check_now({}))

        from core.config_store import load_config
        from app_runtime import AppRuntime

        cfg = load_config()
        cfg["plugins_enabled"] = {}
        rt = AppRuntime(app, cfg)
        app.processEvents()

        sp = rt.unified.settings_page
        ab = rt.unified.about_page
        w()
        w("[settings]")
        w("  has_toggle      = %r" % hasattr(sp, "auto_update_check"))
        w("  toggle_enabled  = %r"
          % (sp.auto_update_check.isEnabled()
             if hasattr(sp, "auto_update_check") else "n/a"))
        w("  toggle_checked  = %r"
          % (sp.auto_update_check.isChecked()
             if hasattr(sp, "auto_update_check") else "n/a"))
        w("  status_text     = %r"
          % (getattr(sp, "_auto_update_status", None).text()
             if hasattr(sp, "_auto_update_status") else ""))

        w()
        w("[about-page]")
        w("  install_cb        = %r"
          % callable(getattr(ab, "_install_cb", None)))
        w("  install_package_cb= %r"
          % callable(getattr(ab, "_install_package_cb", None)))

        w()
        w("[runtime]")
        up = getattr(rt, "updater", None)
        w("  updater_created   = %r" % (up is not None))
        w("  updater_type      = %s" % (type(up).__name__ if up else "None"))
        w("  pending_package   = %r"
          % (up.has_pending_package() if up else "n/a"))
    except Exception:
        w()
        w("[ERROR]")
        w(traceback.format_exc())

    # 写到 exe 同目录（onedir 下是 dist/KaomojiAssistant/）
    try:
        base = os.path.dirname(_sys.executable) if getattr(
            _sys, "frozen", False) else os.getcwd()
    except Exception:
        base = os.getcwd()
    out = os.path.join(base, "update_selftest.txt")
    try:
        with open(out, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    except Exception:
        pass


if __name__ == "__main__":
    main()
