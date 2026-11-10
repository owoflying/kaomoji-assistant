"""安装更新：把下载好的 onedir 压缩包替换到安装目录并重启。

## 为什么不能就地替换

onedir 形态下，程序运行时会锁定 `KaomojiAssistant.exe` 和 `_internal/` 下的
所有 DLL/pyd。Windows 的规则是：

    * 正在运行的可执行文件 **不能删除**；
    * 但 **可以重命名**（实测验证过）。

所以"自己替换自己"的标准做法是：

    1. 启动一个**独立的外部脚本**（它不加载任何被锁定的文件）；
    2. 主程序退出；
    3. 脚本等待主进程真正结束，然后：
        - 把旧 exe / `_internal` 改名成 `.old`；
        - 解压新版本到安装目录；
        - 启动新 exe；
        - 清理 `.old`。

脚本用批处理（`.bat`）而不是 Python：批处理不占用任何 DLL，
也不依赖安装目录里的 Python 运行时——若用 Python 写，脚本自己就活在被
替换的目录里，同样会被锁住。

## 失败回滚

替换过程中任何一步失败，都要能把 `.old` 改回来，否则用户会得到一个
既不能运行、又找不到旧版本的目录。策略：

    * 解压到**临时目录**，成功后才整体搬到安装目录（先改名旧目录）；
    * 搬到一半失败 → 删除半成品、把 `.old` 改回原名；
    * 新版本启动失败 → 由用户在下次启动时看到"更新可能失败"的提示
      （见 `check_pending_update_result`）。

## 与自动更新的关系

本模块只负责"已经验校通过的压缩包 → 安装并重启"这一段。
检查版本、下载、校验由 `core/updater.py` 负责。
"""
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile

#: 更新脚本文件名（放在安装目录外的临时目录）
SCRIPT_NAME = "_apply_update.bat"

#: 安装前备份的后缀
OLD_SUFFIX = ".old"

#: 更新状态文件：记录"正在更新"，用于下次启动判断是否失败
PENDING_FLAG = "update_pending.txt"


class InstallError(Exception):
    """安装失败（面向用户的说明）。"""


def is_frozen():
    """是否打包态（只有打包态才有"安装目录"可替换）。"""
    return bool(getattr(sys, "frozen", False))


def install_dir():
    """当前安装目录（onedir 下是 exe 所在目录）；源码态返回 None。"""
    if not is_frozen():
        return None
    return os.path.dirname(os.path.abspath(sys.executable))


def exe_name():
    """当前 exe 文件名。"""
    return os.path.basename(sys.executable) if is_frozen() else ""


def can_self_update():
    """当前形态是否支持自更新。

    三条硬条件：
      1. 打包态（有安装目录）；
      2. 安装目录**可写**（装在 Program Files 时普通用户无权限）；
      3. 有 exe。
    """
    d = install_dir()
    if not d or not os.path.isdir(d):
        return False
    if not os.path.isfile(sys.executable):
        return False
    return _is_writable(d)


def _is_writable(path):
    """目录是否可写（试建临时文件，比检查 ACL 可靠）。"""
    try:
        fd, tmp = tempfile.mkstemp(prefix=".wtest_", dir=path)
        os.close(fd)
        os.remove(tmp)
        return True
    except OSError:
        return False


def locate_exe_in(zip_path, expect_name=None):
    """在 onedir 压缩包里找到 exe 的相对路径（压缩包内一级目录下）。

    onedir 包的结构通常是：

        KaomojiAssistant/KaomojiAssistant.exe
        KaomojiAssistant/_internal/...

    也兼容"压缩包直接是内容"（没有外层目录）的情况。
    找不到返回 None。
    """
    expect = expect_name or exe_name()
    try:
        with zipfile.ZipFile(zip_path) as z:
            names = z.namelist()
    except Exception:
        return None
    if not names:
        return None
    # 优先找同名 exe
    if expect:
        hits = [n for n in names if os.path.basename(n) == expect]
        if hits:
            # 取层级最浅的那个
            return min(hits, key=lambda n: n.count("/"))
    # 退回：任意 .exe
    exes = [n for n in names if n.lower().endswith(".exe")]
    if exes:
        return min(exes, key=lambda n: n.count("/"))
    return None


def payload_root(zip_path, expect_name=None):
    """压缩包里"程序根目录"的相对前缀（含尾部分隔符，可能为空串）。

    例：exe 在 `KaomojiAssistant/KaomojiAssistant.exe` 时返回 `"KaomojiAssistant/"`。
    """
    rel = locate_exe_in(zip_path, expect_name)
    if rel is None:
        return None
    idx = rel.rfind("/")
    return rel[:idx + 1] if idx >= 0 else ""


def detect_layout(zip_path, expect_name=None):
    """判断压缩包结构与当前安装是否兼容。

    返回 dict：``{"root": "KaomojiAssistant/", "exe": "...", "ok": bool, "reason": str}``
    """
    rel = locate_exe_in(zip_path, expect_name)
    if rel is None:
        return {"root": None, "exe": None, "ok": False,
                "reason": "压缩包里没有找到可执行文件"}
    root = payload_root(zip_path, expect_name)
    return {"root": root, "exe": rel, "ok": True, "reason": ""}


def extract_to(zip_path, dest_dir, root_prefix=""):
    """把压缩包内容解压到 dest_dir（去掉 root_prefix 这一层）。

    返回解压出的文件数。失败抛 InstallError。
    """
    try:
        os.makedirs(dest_dir, exist_ok=True)
        count = 0
        with zipfile.ZipFile(zip_path) as z:
            for info in z.infolist():
                name = info.filename
                if not name.startswith(root_prefix):
                    continue
                rel = name[len(root_prefix):]
                if not rel:
                    continue
                # 防 zip slip：拒绝跳出目标目录的路径
                target = os.path.normpath(os.path.join(dest_dir, rel))
                if not os.path.abspath(target).startswith(
                        os.path.abspath(dest_dir) + os.sep):
                    continue
                if info.is_dir():
                    os.makedirs(target, exist_ok=True)
                    continue
                os.makedirs(os.path.dirname(target) or dest_dir, exist_ok=True)
                with z.open(info) as src, open(target, "wb") as dst:
                    shutil.copyfileobj(src, dst)
                count += 1
        if count == 0:
            raise InstallError("压缩包里没有匹配的内容（前缀 %r）" % root_prefix)
        return count
    except InstallError:
        raise
    except Exception as e:
        raise InstallError("解压失败：%r" % (e,))


def build_script(install_path, exe_file, staging_dir=None):
    """生成替换用的批处理脚本内容。

    :param install_path: 安装目录（脚本运行时内容会被替换）
    :param exe_file:     exe 文件名（相对 install_path）
    :param staging_dir: 新版本解压好的目录；为 None 表示由脚本自己解压备份

    脚本逻辑（尽量防御性，任何一步失败都尝试回滚）：

        @echo off
        rem 1. 等旧进程退出
        :wait
        tasklist | find /i "xxx.exe" >nul && (timeout /t 1 >nul & goto wait)
        rem 2. 旧文件改名
        ren "install\\xxx.exe" "xxx.exe.old"
        ren "install\\_internal" "_internal.old"
        rem 3. 新文件搬入
        xcopy /E /I /Y /Q "staging\\*" "install\\"
        rem 4. 启动新版本
        start "" "install\\xxx.exe"
        rem 5. 清理
        rd /S /Q "install\\_internal.old"
        del "install\\xxx.exe.old"
        del "%~f0"
    """
    if not install_path or not exe_file:
        raise InstallError("缺少安装路径或 exe 名称")
    ip = install_path.rstrip("\\")
    exe = exe_file
    old_exe = exe + OLD_SUFFIX
    old_internal = "_internal" + OLD_SUFFIX

    # 注意：脚本里有大量批处理变量（%INS%、%EXE%…），与 Python 的 % 格式化
    # 冲突，所以这里一律用 f-string 拼接，不出现 % 运算符。
    lines = [
        "@echo off",
        "chcp 65001 >nul 2>&1",
        "setlocal",
        f'set "INS={ip}"',
        f'set "STAGE={staging_dir or ""}"',
        f'set "EXE={exe}"',
        "",
        "rem ---- 1. 等待旧进程退出 ----",
        ":wait",
        'tasklist /FI "IMAGENAME eq %EXE%" 2>nul | find /I "%EXE%" >nul',
        "if not errorlevel 1 (",
        "    timeout /t 1 /nobreak >nul",
        "    goto wait",
        ")",
        "",
        "rem ---- 2. 旧文件改名（运行中的 exe 不能删，但可以改名） ----",
        f'if exist "%INS%\\%EXE%" ren "%INS%\\%EXE%" "{old_exe}" >nul 2>&1',
        f'if exist "%INS%\\_internal" ren "%INS%\\_internal" "{old_internal}" >nul 2>&1',
        "",
        "rem ---- 3. 新文件搬入 ----",
        'if not "%STAGE%"=="" (',
        '    xcopy "%STAGE%\\*" "%INS%\\" /E /I /Y /Q >nul 2>&1',
        ")",
        "",
        "rem ---- 4. 校验并启动 ----",
        'if not exist "%INS%\\%EXE%" goto rollback',
        'start "" "%INS%\\%EXE%"',
        "",
        "rem ---- 5. 清理旧文件 ----",
        f'rd /S /Q "%INS%\\{old_internal}" >nul 2>&1',
        f'del /F /Q "%INS%\\{old_exe}" >nul 2>&1',
        "goto done",
        "",
        "rem ---- 回滚：把改名的旧文件换回来 ----",
        ":rollback",
        f'if exist "%INS%\\{old_internal}" rd /S /Q "%INS%\\_internal" >nul 2>&1',
        f'if exist "%INS%\\{old_internal}" ren "%INS%\\{old_internal}" "_internal" >nul 2>&1',
        f'if exist "%INS%\\{old_exe}" ren "%INS%\\{old_exe}" "%EXE%" >nul 2>&1',
        'start "" "%INS%\\%EXE%"',
        "",
        ":done",
        "endlocal",
        'del /F /Q "%~f0" >nul 2>&1',
    ]
    return "\r\n".join(lines) + "\r\n"


def prepare(install_zip, log=None):
    """校验压缩包并解压到临时暂存目录，返回 (暂存目录, exe 文件名)。

    这一步**在主程序退出前完成**，好处是：
      * 解压失败时程序还在，可以正常提示用户，而不是退出后什么也没发生；
      * 退出后只剩"改名 + 搬移"，脚本运行时间短、失败窗口小。
    """
    if not is_frozen():
        raise InstallError("源码运行不支持自动更新（仅打包后的 exe）")
    d = install_dir()
    if not d:
        raise InstallError("无法定位安装目录")

    expect = exe_name()
    layout = detect_layout(install_zip, expect)
    if not layout["ok"]:
        raise InstallError(layout["reason"])

    staging = tempfile.mkdtemp(prefix="kaomoji_update_")
    try:
        n = extract_to(install_zip, staging, layout["root"] or "")
    except InstallError:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    if log is not None:
        try:
            log("info", "update", "已解压 %d 个文件到暂存目录" % n)
        except Exception:
            pass
    return staging, expect


def launch_installer(staging_dir, log=None):
    """写脚本并以**独立进程**启动它；调用方随后应立即退出主程序。

    返回脚本路径。启动失败抛 InstallError。
    """
    d = install_dir()
    if not d or not staging_dir:
        raise InstallError("缺少安装目录或暂存目录")

    script = build_script(d, exe_name(), staging_dir)
    # 脚本必须放在**安装目录之外**：否则它自己也会在替换中被删掉
    script_dir = tempfile.mkdtemp(prefix="kaomoji_upd_")
    script_path = os.path.join(script_dir, SCRIPT_NAME)
    with open(script_path, "w", encoding="utf-8", newline="\r\n") as f:
        f.write(script)

    try:
        subprocess.Popen(
            ["cmd.exe", "/c", script_path],
            cwd=script_dir,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
            | getattr(subprocess, "DETACHED_PROCESS", 0),
            close_fds=True,
        )
    except Exception as e:
        raise InstallError("无法启动更新脚本：%r" % (e,))

    if log is not None:
        try:
            log("info", "update", "更新脚本已启动，准备退出主程序")
        except Exception:
            pass
    return script_path


def write_pending(version):
    """记录"正在更新到某版本"，供下次启动判断是否成功。"""
    from core import runtime
    try:
        p = os.path.join(runtime.app_data_dir(), PENDING_FLAG)
        with open(p, "w", encoding="utf-8") as f:
            f.write(version or "")
    except Exception:
        pass


def read_pending():
    """读取未完成的更新记录；没有则返回空串。"""
    from core import runtime
    try:
        p = os.path.join(runtime.app_data_dir(), PENDING_FLAG)
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8") as f:
                return f.read().strip()
    except Exception:
        pass
    return ""


def clear_pending():
    from core import runtime
    try:
        p = os.path.join(runtime.app_data_dir(), PENDING_FLAG)
        if os.path.exists(p):
            os.remove(p)
    except Exception:
        pass


def check_pending_result(current_version):
    """启动时检查上次更新是否成功。

    返回 ``("ok"|"failed"|"none", 待更新版本)``：
      * 有 pending 记录且当前版本已变成目标版本 → 成功，清掉记录；
      * 有 pending 记录但版本没变 → 更新失败（脚本没跑成/回滚了），
        保留记录让用户看到提示，但不重复尝试。
    """
    from core.version import compare_versions, normalize_tag

    pending = read_pending()
    if not pending:
        return "none", ""
    r = compare_versions(normalize_tag(current_version), normalize_tag(pending))
    if r is not None and r >= 0:
        clear_pending()
        return "ok", pending
    return "failed", pending
