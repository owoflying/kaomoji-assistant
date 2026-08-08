"""打包脚本：把项目构建为 KaomojiAssistant 可执行程序（onedir）。

用法：
    .venv/Scripts/python.exe build.py                       # 交互模式：无参数时逐项提示输入版本/说明
    .venv/Scripts/python.exe build.py --version v1.2        # 直接编译正式版本（关于页显示 v1.2(commit)）
    .venv/Scripts/python.exe build.py --version v1.2 --notes "本次更新：xxx"
    .venv/Scripts/python.exe build.py --version v1.2 --notes-file CHANGELOG.txt

提示：传入任意命令行参数即按参数直接编译（跳过交互）；不带参数则进入交互模式。

产物：
    dist/KaomojiAssistant/KaomojiAssistant.exe  （连同依赖 DLL 与 data 资源）

版本信息（BUILD_COMMIT / BUILD_VERSION / BUILD_NOTES）由 build.py 在 PyInstaller
运行前写入 core/_build_version.py（已加入 .gitignore，自动生成），供打包态（无 git）
关于页与首次启动「欢迎更新」弹窗使用。
"""
import os
import sys
import subprocess
import argparse

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


def write_build_version(version="", notes=""):
    """构建前把版本信息写入 core/_build_version.py，供打包态（无 git）回退显示。

    version: 人工版本标签（如 v1.2）；空串表示开发构建（关于页仅显示 commit 短哈希）。
    notes:   更新说明（changelog），将显示在首次启动的「欢迎更新」弹窗。
    该文件由本脚本自动生成，已加入 .gitignore，请勿手改。
    """
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=HERE, capture_output=True, text=True, timeout=5,
        )
        commit = out.stdout.strip() if out.returncode == 0 else ""
    except Exception:
        commit = ""
    if not commit:
        commit = "unknown"
    path = os.path.join(HERE, "core", "_build_version.py")
    with open(path, "w", encoding="utf-8") as f:
        f.write('# 由 build.py 自动生成，请勿手改；记录构建所用版本信息。\n')
        f.write('BUILD_COMMIT = "%s"\n' % commit)
        f.write('BUILD_VERSION = "%s"\n' % (version or ""))
        # 用 repr 安全转义多行/引号，确保生成的是合法 Python 字符串字面量
        f.write('BUILD_NOTES = %s\n' % repr(notes or ""))
    print("[build] 写入构建版本 commit=%s version=%s -> %s"
          % (commit, version or "(dev)", path))


def _build_with(version, notes):
    """执行实际构建：生成 ico、烘焙版本信息、调用 PyInstaller。"""
    # 构建前先生成与托盘同款的 ico 图标
    save_ico(ICO)
    # 烘焙版本信息，供关于页与更新弹窗使用
    write_build_version(version=version, notes=notes)
    sys.exit(PyInstaller.__main__.run(ARGS))


def _parse_cli_args():
    """解析命令行参数（仅在传入任意参数时调用，保留原直接编译路径）。"""
    parser = argparse.ArgumentParser(description="构建 KaomojiAssistant（onedir）")
    parser.add_argument("--version", default="",
                        help="正式版本标签，如 v1.2；省略则为开发构建（仅显示 commit 短哈希）")
    parser.add_argument("--notes", default="",
                        help="更新说明（changelog），显示在首次启动弹窗")
    parser.add_argument("--notes-file", default="",
                        help="从文件读取更新说明（优先级高于 --notes）")
    return parser.parse_args()


def _interactive_mode():
    """无任何命令行参数时的人性化交互构建模式。"""
    print("=" * 52)
    print("   KaomojiAssistant 构建脚本（交互模式）")
    print("=" * 52)
    print("说明：直接回车可跳过对应项（开发构建，关于页仅显示 commit）。\n")
    version = input("请输入版本标签（如 v1.2，留空=开发构建）：").strip()

    print()
    print("请输入更新说明（用于首次启动「欢迎更新」弹窗）：")
    print("  - 直接输入多行文本，单独一行空回车结束；")
    print("  - 或以 @ 开头输入文件路径（如 @CHANGELOG.txt）从文件读取；")
    print("  - 连续两次回车（无内容）表示无更新说明。")
    first = input("> ")
    notes = ""
    if first.startswith("@"):
        p = first[1:].strip()
        try:
            with open(p, "r", encoding="utf-8") as f:
                notes = f.read()
            print("[build] 已从 %s 读取更新说明（%d 字符）" % (p, len(notes)))
        except Exception as e:
            print("[build] 读取 %s 失败：%s，将使用空说明" % (p, e))
    elif first.strip() != "":
        lines = [first]
        while True:
            line = input("> ")
            if line == "":
                break
            lines.append(line)
        notes = "\n".join(lines)

    print()
    print("构建配置：版本=%s | 更新说明=%d 字符"
          % (version or "(开发构建)", len(notes)))
    confirm = input("确认开始构建？(Y/n)：").strip().lower()
    if confirm not in ("", "y", "yes"):
        print("[build] 已取消构建。")
        sys.exit(0)
    _build_with(version, notes)


if __name__ == "__main__":
    if len(sys.argv) > 1:
        # 传入任意命令行参数：按 argparse 直接编译（沿用原路径，跳过交互）
        args = _parse_cli_args()
        notes = args.notes
        if args.notes_file:
            try:
                with open(args.notes_file, "r", encoding="utf-8") as nf:
                    notes = nf.read()
            except Exception as e:
                print("[build] 读取 notes-file 失败：%s" % e)
        _build_with(args.version, notes)
    else:
        # 无任何参数：进入人性化交互模式
        _interactive_mode()
