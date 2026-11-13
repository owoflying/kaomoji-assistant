"""程序入口。

只做四件事：装日志/异常处理器、创建 Qt 应用、注册图标字体、把装配工作交给
`app_runtime.AppRuntime`。具体组件创建与信号接线都在那边，这里保持薄壳——
入口一旦膨胀，任何组件的初始化顺序问题都会被埋进几百行闭包里。

命令行：
    python main.py                    正常启动
    python main.py --diag             写 diag.txt 自检后退出（排查图标豆腐块/字体缺失）
    python main.py --selftest-update  写 update_selftest.txt 自检后退出
                                      （验证**打包态**下自动更新是否可用，
                                       源码态测不出这条路径）
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
        try:
            _selftest_update(app)
        except Exception:
            import traceback
            traceback.print_exc()
        # ⚠ 必须显式退出：自检里构造过 AppRuntime（会建窗口/托盘图标/
        # 定时器），光 return 掉 main 进程不会结束，实测卡死。
        os._exit(0)

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

    def _out_path():
        """自检结果的落盘路径。

        ⚠ 打包成窗口程序后没有控制台，`print` 等于扔掉；
        必须落盘。而 `--diag` 的经验是写 exe 同目录可行。
        """
        try:
            if getattr(_sys, "frozen", False):
                return os.path.join(os.path.dirname(_sys.executable),
                                    "update_selftest.txt")
        except Exception:
            pass
        return os.path.join(os.getcwd(), "update_selftest.txt")

    lines = []

    def w(s=""):
        lines.append(str(s))
        # 每写一行就落盘：万一后面构造 AppRuntime 时崩了，
        # 也能看到崩在哪一步（这是排查打包态问题的关键）。
        try:
            with open(_out_path(), "w", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
        except Exception:
            pass

    w("frozen = %r" % getattr(_sys, "frozen", False))
    w("step = start")
    try:
        from core import updater
        w()
        w("[updater]")
        w("  is_supported      = %r" % updater.is_supported())
        w("  unsupported_reason= %r" % (updater.unsupported_reason() or ""))
        w("  should_check_now  = %r" % updater.should_check_now({}))

        w("step = importing AppRuntime")
        from core.config_store import load_config
        from app_runtime import AppRuntime

        w("step = constructing AppRuntime")
        cfg = load_config()
        cfg["plugins_enabled"] = {}
        rt = AppRuntime(app, cfg)
        w("step = AppRuntime ok")
        # ⚠ **不要调 app.processEvents()**：打包态下它会处理托盘图标、
        # 网络请求等事件，实测会卡住不返回（源码态没事）。
        # 自检只需要读状态，不需要跑事件循环。
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
        w("step = done")
    except Exception:
        w()
        w("[ERROR]")
        w(traceback.format_exc())


if __name__ == "__main__":
    main()
