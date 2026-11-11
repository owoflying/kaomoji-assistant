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
# 应用名（PyInstaller 的 --name，也是 onedir 产物目录名与发布包内顶层目录名）
_APP_NAME = "KaomojiAssistant"
DATA = os.path.join(HERE, "data", "kaomoji.json")
#: 情绪识别小网络的权重。训练脚本 tools/train_emotion_model.py 产出。
#: ⚠ 必须随包分发，否则打包后情绪识别只能退回关键词匹配。
DATA_MODEL = os.path.join(HERE, "data", "emotion_model.json")
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
    # 情绪识别网络权重（同目录，落到 data/ 下）
    "--add-data", "%s%sdata" % (DATA_MODEL, os.pathsep),
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


def _cleanup_build_version():
    """打包完成后移除源码树里的临时构建文件，避免污染源码态版本号。

    core/_build_version.py 仅用于 PyInstaller 打包期注入（随包分发到 onedir），
    构建结束后必须删掉；否则源码态程序会因读取它而显示错误的烘焙版本号。
    该文件已加入 .gitignore，删除不影响仓库。
    """
    try:
        path = os.path.join(HERE, "core", "_build_version.py")
        if os.path.exists(path):
            os.remove(path)
            print("[build] 已清理临时构建版本文件: %s" % path)
    except Exception as e:
        print("[build] 清理构建版本文件失败（可忽略）: %s" % e)


def _make_release_zip(version):
    """把 onedir 产物打成自动更新用的 zip。

    **为什么必须打 zip**：自动更新要把整份程序替换掉，而 GitHub Release 的
    资产是单个文件。onedir 是"一个文件夹"，所以必须先压缩。

    **压缩结构必须是**（auto-updater 的 `detect_layout` 依赖它）：

        KaomojiAssistant/
            KaomojiAssistant.exe
            _internal/...

    即**带一层顶层目录**。这样解压后天然是一个完整的程序目录；若把内容直接
    放在根上，用户手工解压时会散落一地。

    产物名带上版本，便于资产区分：``KaomojiAssistant-v1.2-win64.zip``。
    开发构建（无版本标签）不打 zip —— 自动更新只对正式发布版有意义。
    """
    import zipfile

    if not version:
        print("[build] 开发构建（无版本标签），跳过发布包生成。")
        return None

    dist_dir = os.path.join(HERE, "dist", _APP_NAME)
    if not os.path.isdir(dist_dir):
        print("[build] 未找到产物目录 %s，跳过发布包生成。" % dist_dir)
        return None

    tag = version if version.lower().startswith("v") else "v" + version
    out_name = "%s-%s-win64.zip" % (_APP_NAME, tag)
    out_path = os.path.join(HERE, "dist", out_name)

    print("[build] 正在生成发布包 %s ..." % out_path)
    count = 0
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED,
                         compresslevel=6) as z:
        for root, _dirs, files in os.walk(dist_dir):
            for fn in files:
                full = os.path.join(root, fn)
                rel = os.path.relpath(full, dist_dir)
                # 包内路径统一用正斜杠，且带一层顶层目录
                arc = "%s/%s" % (_APP_NAME, rel.replace("\\", "/"))
                z.write(full, arc)
                count += 1

    size_mb = os.path.getsize(out_path) / (1024.0 * 1024.0)
    print("[build] 发布包已生成：%s（%d 个文件，%.1f MB）"
          % (out_path, count, size_mb))
    print("[build] 上传到 GitHub Release 后，自动更新即可识别。")
    return out_path


def _build_with(version, notes):
    """执行实际构建：生成 ico、烘焙版本信息、调用 PyInstaller、打发布包。"""
    # 构建前先生成与托盘同款的 ico 图标
    save_ico(ICO)
    # 烘焙版本信息，供关于页与更新弹窗使用
    write_build_version(version=version, notes=notes)
    try:
        code = PyInstaller.__main__.run(ARGS)
    finally:
        # 无论成功失败，打包结束后清除源码树里的临时构建文件，
        # 否则源码态程序会误读它而导致版本号被覆盖。
        _cleanup_build_version()
    if code == 0:
        # 打包成功才生成可自动更新的发布包
        try:
            _make_release_zip(version)
        except Exception as e:
            print("[build] 生成发布包失败：%s" % e)
    sys.exit(code)


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
