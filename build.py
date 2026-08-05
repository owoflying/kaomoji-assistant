"""打包脚本：把项目构建为 KaomojiAssistant 可执行程序（onedir）。

用法：
    .venv/Scripts/python.exe build.py

产物：
    dist/KaomojiAssistant/KaomojiAssistant.exe  （连同依赖 DLL 与 data 资源）
"""
import os
import sys

import PyInstaller.__main__
from core.app_icon import save_ico

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data", "kaomoji.json")
FONT = os.path.join(HERE, "ui", "fonts", "FluentSystemIcons-Regular.ttf")
# 应用图标：与系统托盘同款（白底圆角 + 颜文字），构建时自动生成 .ico
ICO = os.path.join(HERE, "app.ico")

ARGS = [
    "main.py",
    "--name", "KaomojiAssistant",
    "--onedir",
    "--windowed",          # 无控制台窗口，作为托盘程序运行
    "--icon", ICO,         # exe 文件图标，与托盘同款
    "--noconfirm",
    "--clean",
    # 只读资源 data/kaomoji.json：运行时用 resource_path 还原到 data/ 子目录
    "--add-data", "%s%sdata" % (DATA, os.pathsep),
    # 内置图标字体（Fluent System Icons，MIT 许可）：随包分发，Win10 也能显示
    # 目标路径需与 core.runtime.resource_path("ui","fonts",...) 完全对应。
    # 注意：--add-data 的目标必须是“目录”，文件名由源自动带入；
    # 若写成 ".../ui/fonts/FluentSystemIcons-Regular.ttf" 会建出同名子目录导致嵌套一层！
    "--add-data", "%s%sui/fonts" % (FONT, os.pathsep),
    # 确保 Windows 平台后端被打包（否则运行到全局键盘监听时报 ImportError）
    "--hidden-import", "pynput.keyboard._win32",
    "--hidden-import", "pynput.mouse._win32",
    "--hidden-import", "pynput._util.win32",
    # 强制收集项目自有包的全部子模块，避免 __init__.py 缺失/命名空间包导致被 PyInstaller 漏打包
    "--collect-submodules", "ui",
    "--collect-submodules", "core",
    # 把项目根加入分析路径，确保 ui/core 等顶层包被定位
    "--paths", HERE,
]

if __name__ == "__main__":
    # 构建前先生成与托盘同款的 ico 图标
    save_ico(ICO)
    sys.exit(PyInstaller.__main__.run(ARGS))
