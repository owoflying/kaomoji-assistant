"""应用重启辅助（开发者模式「模拟升级」用）。

restart_with_update_popup()：关闭当前进程并以新进程重启自身，给子进程注入
KAOMOJI_FORCE_UPDATE_POPUP=1 环境变量，使新进程启动后强制弹出更新提示框。
用于在不重新打包的情况下测试更新弹窗流程。

重启复用 CREATE_NO_WINDOW，避免在 GUI 形态下弹出命令行黑框（参见 core/version.py
_git_commit 的同类处理）。
"""
import os
import sys
import subprocess

from PySide6.QtWidgets import QApplication

FORCE_UPDATE_ENV = "KAOMOJI_FORCE_UPDATE_POPUP"


def restart_with_update_popup():
    """关闭当前进程并以新进程重启，启动时强制弹出更新提示框。

    本函数不返回（成功路径下旧进程会 quit）。
    """
    env = dict(os.environ)
    env[FORCE_UPDATE_ENV] = "1"

    # 区分打包态（sys.executable 即 exe）与源码态（sys.executable 是 python，argv[0] 为脚本）。
    if getattr(sys, "frozen", False):
        args = [sys.executable] + sys.argv[1:]
    else:
        args = [sys.executable] + sys.argv

    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        subprocess.Popen(args, env=env, creationflags=flags)
    except Exception:
        # 兜底：不带环境变量重试，至少把应用重新拉起来
        subprocess.Popen(args, creationflags=flags)

    app = QApplication.instance()
    if app is not None:
        app.quit()
    else:  # 极端情况：没有 QApplication 实例，直接退出进程
        sys.exit(0)
