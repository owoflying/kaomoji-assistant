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

    from app_runtime import AppRuntime
    from core.config_store import load_config

    # 运行期组件通过 app 反向持有，避免被垃圾回收提前收走
    app.runtime = AppRuntime(app, load_config())
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
