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

#: 脚本写下的失败说明（放在 %TEMP%，安装目录之外）
FAILURE_NOTE = "update_failed.txt"

#: 脚本写的失败标记 → 给用户看的中文说明。
#:
#: ## 为什么脚本里写英文
#:
#: 批处理在 `chcp 65001` 下 `echo 中文 > file` 偶尔**写不出来**。
#: 所以脚本只写 ASCII 标记，由 Python 侧翻译成中文——
#: 既可靠，文案改动也不用重新打包脚本。
_FAILURE_MESSAGES = {
    "UPDATE_FAILED": (
        "更新没有完成，但已自动恢复为原版本，不影响正常使用。\n"
        "可能是下载的安装包不完整，或安装过程中文件被占用。\n"
        "可以稍后在「关于」页重试。"),
    "ROLLBACK_FAILED": (
        "更新没有完成，自动回滚也未成功。\n"
        "安装目录里仍保留着原版本文件（后缀为 .old）。\n"
        "请把 _internal.old 改名回 _internal，"
        "并把 exe 的 .old 文件改名回原名，即可恢复正常。"),
}


def _update_dir():
    """脚本与日志所在的目录（%TEMP%\\KaomojiUpdate）。"""
    return os.path.join(tempfile.gettempdir(), "KaomojiUpdate")


def failure_note_path():
    """脚本写失败说明的位置。"""
    return os.path.join(_update_dir(), FAILURE_NOTE)


def log_path():
    """脚本写更新日志的位置。"""
    return os.path.join(_update_dir(), "update_log.txt")


def read_failure_note():
    """判断上次更新是否失败，返回给用户看的中文说明；成功或没更新过返回空串。

    ## 为什么改为解析日志，而不是读单独的标记文件

    原方案让批处理在回滚后写 `update_failed.txt`。实测在长脚本里
    那一句**写不出文件**（同处的日志却能写，探针确认执行到了那一行）。
    试过换变量/换字面路径/换 `>` 与 `>>`/挪到 `endlocal` 之后，都没根治。

    日志既然一直能可靠写入，就改成**解析日志**判断结果——
    少一个写入点，少一类失败模式。日志尾部几条记录足以判断：

        "update done"    → 成功
        "rollback ok"    → 失败但已回滚
        "ROLLBACK FAILED" → 失败且回滚也没成功
    """
    log = read_update_log()
    if not log:
        # 没有日志：可能压根没更新过
        return ""
    lines = [ln.strip() for ln in log.splitlines() if ln.strip()]
    tail = lines[-40:]

    def _has(marker):
        return any(marker in ln for ln in tail)

    if _has("update done"):
        return ""          # 成功，不提示
    if _has("ROLLBACK FAILED"):
        msg = _FAILURE_MESSAGES["ROLLBACK_FAILED"]
    elif _has("rollback ok"):
        msg = _FAILURE_MESSAGES["UPDATE_FAILED"]
    else:
        # 日志里既没有成功也没有回滚：可能中途被打断（关机/断电）
        return ""
    return msg + "\n（详细日志：%s）" % log_path()


def read_update_log():
    """读取脚本写的更新日志；没有则返回空串。"""
    try:
        p = log_path()
        if not os.path.isfile(p):
            return ""
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except Exception:
        return ""


def clear_failure_note():
    """清掉失败标记（用户已看到后调用）。

    现在用日志判断结果，所以"清除"变成：把日志里最后的结果标记
    改写成已处理，避免同一条失败反复提示。
    """
    try:
        # 顺带清掉历史版本可能留下的标记文件
        p = failure_note_path()
        if os.path.isfile(p):
            os.remove(p)
    except Exception:
        pass


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
    :param staging_dir: 新版本解压好的目录

    ## 每一步都必须校验，失败必须能回滚

    早期版本有四个致命缺陷，实测会导致"更新失败后程序彻底打不开"：

    1. **`xcopy` 失败被吞掉**：`>nul 2>&1` 之后不检查 errorlevel，
       只有 exe 不存在才回滚。若 `_internal` 拷一半失败，会留下
       "新 exe + 半个旧运行库"的必崩组合。
    2. **回滚不校验**：`ren` 失败了也往下走，可能既没有新版也没有旧版。
    3. **先 `rd` 再 `ren`**：`rd` 失败时 `ren` 也会失败，旧目录彻底丢失。
    4. **失败静默**：脚本悄悄退出，用户只看到程序没了。

    现在每一步都校验，回滚时先把旧的挪回去再删新的，失败写标记。

    ## 为什么用批处理而不是 Python

    脚本运行时安装目录里的 DLL 会被它自己锁定。批处理不加载任何
    被替换的文件，是唯一安全的选择。

    ## ⚠ 关于反斜杠与 `%` 格式化

    脚本内容是批处理：路径必须用**单个**反斜杠；同时脚本里有大量
    批处理变量（`%INS%`、`%EXE%`…）。因此这里**既不能用 Python 的
    `%%` 格式化，也不能在字符串里手写反斜杠**——两者都反复出错。

    统一做法：用 `P()` 拼路径（内部用 `BS` 变量代表单个反斜杠），
    所有动态部分用字符串加法拼进 `a()`。
    """
    if not install_path or not exe_file:
        raise InstallError("缺少安装路径或 exe 名称")
    ip = install_path.rstrip("\\")
    exe = exe_file
    old_exe = exe + OLD_SUFFIX
    old_internal = "_internal" + OLD_SUFFIX

    BS = chr(92)            # 单个反斜杠
    TB = "%TEMP%" + BS      # "%TEMP%\"

    def P(*parts):
        """拼一个带引号的批处理路径。

        ``P("%INS%", "x")`` 得到 ``"%INS%<反斜杠>x"``（反斜杠是单个的）。
        用函数而不是字符串字面量，是为了彻底避免 Python 转义踩坑。
        """
        return '"' + BS.join(parts) + '"'

    LOGD = TB + "KaomojiUpdate"
    LOGP = LOGD + BS + "update_log.txt"

    L = []
    a = L.append

    a("@echo off")
    a("chcp 65001 >nul 2>&1")
    a("setlocal EnableDelayedExpansion")
    a('set "INS=' + ip + '"')
    a('set "STAGE=' + (staging_dir or "") + '"')
    a('set "EXE=' + exe + '"')
    a('set "LOG=' + LOGP + '"')
    # 更新结果：SUCCESS / ROLLBACK_OK / ROLLBACK_FAILED
    # 在 endlocal 之后按它决定是否写失败标记
    a('if not exist ' + P(LOGD) + ' mkdir ' + P(LOGD) + ' >nul 2>&1')
    a('echo [%DATE% %TIME%] start >> ' + P(LOGP))
    # 上次的失败标记先清掉（写死路径，见下方 :rollback 的说明）
    a("")

    # ---------------- 1. 等旧进程退出 ----------------
    a("rem ---- 1. wait for old process (max 60s) ----")
    a("set /a WAITED=0")
    a(":wait")
    a('tasklist /FI "IMAGENAME eq %EXE%" 2>nul | find /I "%EXE%" >nul')
    a("if errorlevel 1 goto wait_done")
    a("set /a WAITED+=1")
    a("if !WAITED! GEQ 60 goto wait_done")
    # 用 ping 而不是 timeout：`timeout /t` 在没有控制台的环境会立即返回
    a("ping -n 2 127.0.0.1 >nul 2>&1")
    a("goto wait")
    a(":wait_done")
    a('echo [%DATE% %TIME%] old process gone >> ' + P(LOGP))
    a("")

    # ---------------- 2. 旧文件改名（带校验） ----------------
    a("rem ---- 2. backup old files ----")
    a("set HAS_OLD_EXE=0")
    a("set HAS_OLD_INT=0")
    a('if exist ' + P("%INS%", "%EXE%") + ' (')
    a('  ren ' + P("%INS%", "%EXE%") + ' "' + old_exe + '" >nul 2>&1')
    a('  if exist ' + P("%INS%", old_exe) + ' set HAS_OLD_EXE=1')
    a(")")
    a('if exist ' + P("%INS%", "_internal") + ' (')
    a('  ren ' + P("%INS%", "_internal") + ' "' + old_internal + '" >nul 2>&1')
    a('  if exist ' + P("%INS%", old_internal) + ' set HAS_OLD_INT=1')
    a(")")
    a('if "%HAS_OLD_EXE%"=="0" if not exist '
      + P("%INS%", "%EXE%") + ' goto backup_failed')
    a('echo [%DATE% %TIME%] backup done exe=%HAS_OLD_EXE% int=%HAS_OLD_INT% >> '
      + P(LOGP))
    a("")

    # ---------------- 3. 新文件搬入（带校验） ----------------
    a("rem ---- 3. copy new files ----")
    a('if "%STAGE%"=="" goto no_stage')
    a('if not exist ' + P("%STAGE%", "%EXE%") + ' goto stage_bad')
    a('xcopy ' + P("%STAGE%", "*") + ' ' + P("%INS%")
      + ' /E /I /Y /Q >> ' + P(LOGP) + ' 2>&1')
    a("if errorlevel 1 goto copy_failed")
    a(":no_stage")
    a("")

    # ---------------- 4. 校验关键文件 ----------------
    a("rem ---- 4. verify ----")
    a('if not exist ' + P("%INS%", "%EXE%") + ' goto copy_failed')
    a('if not exist ' + P("%INS%", "_internal") + ' goto copy_failed')
    a('echo [%DATE% %TIME%] copy verified >> ' + P(LOGP))
    a("")

    # ---------------- 5. 启动新版（不检测进程） ----------------
    a("rem ---- 5. start new version ----")
    a('echo [%DATE% %TIME%] starting >> ' + P(LOGP))
    a('start "" ' + P("%INS%", "%EXE%"))
    a('echo [%DATE% %TIME%] started >> ' + P(LOGP))
    a("goto cleanup")
    a("")

    # ---------------- 回滚 ----------------
    a("rem ---- rollback: restore old first, then drop new ----")
    a(":copy_failed")
    a('echo [%DATE% %TIME%] copy failed, rolling back >> ' + P(LOGP))
    a("goto rollback")
    a(":stage_bad")
    a('echo [%DATE% %TIME%] staging bad >> ' + P(LOGP))
    a("goto rollback")
    a(":backup_failed")
    a('echo [%DATE% %TIME%] backup failed >> ' + P(LOGP))
    a("goto exit_norestart")
    a("")

    a(":rollback")
    a("rem For _internal: move new aside, restore old, then delete aside.")
    a('if "%HAS_OLD_INT%"=="1" (')
    a('  if exist ' + P("%INS%", "_internal") + ' ren '
      + P("%INS%", "_internal") + ' "_internal.new" >nul 2>&1')
    a('  ren ' + P("%INS%", old_internal) + ' "_internal" >nul 2>&1')
    a('  if exist ' + P("%INS%", "_internal.new") + ' rd /S /Q '
      + P("%INS%", "_internal.new") + ' >nul 2>&1')
    a(")")
    a("rem Same for the exe.")
    a('if "%HAS_OLD_EXE%"=="1" (')
    a('  if exist ' + P("%INS%", "%EXE%") + ' ren '
      + P("%INS%", "%EXE%") + ' "' + exe + '.new" >nul 2>&1')
    a('  ren ' + P("%INS%", old_exe) + ' "%EXE%" >nul 2>&1')
    a('  if exist ' + P("%INS%", exe + ".new") + ' del /F /Q '
      + P("%INS%", exe + ".new") + ' >nul 2>&1')
    a(")")
    a("rem Verify the rollback actually restored things.")
    a('if not exist ' + P("%INS%", "%EXE%") + ' goto rollback_failed')
    a('if "%HAS_OLD_INT%"=="1" if not exist '
      + P("%INS%", "_internal") + ' goto rollback_failed')
    a('echo [%DATE% %TIME%] rollback ok >> ' + P(LOGP))
    # 失败标记统一在 endlocal 之后写（见下方说明），这里只记结果
    a('start "" ' + P("%INS%", "%EXE%"))
    a("goto exit_norestart")
    a("")

    a(":rollback_failed")
    a('echo [%DATE% %TIME%] ROLLBACK FAILED >> ' + P(LOGP))
    a("goto exit_norestart")
    a("")

    # ---------------- 成功后清理 ----------------
    a(":cleanup")
    a("rem ---- 6. cleanup backups ----")
    a('if "%HAS_OLD_INT%"=="1" rd /S /Q ' + P("%INS%", old_internal)
      + ' >nul 2>&1')
    a('if "%HAS_OLD_EXE%"=="1" del /F /Q ' + P("%INS%", old_exe)
      + ' >nul 2>&1')
    a('if not "%STAGE%"=="" rd /S /Q ' + P("%STAGE%") + ' >nul 2>&1')
    a('echo [%DATE% %TIME%] update done >> ' + P(LOGP))

    a(":exit_norestart")
    # ⚠ 这里**不再**写单独的失败标记文件。
    #
    # 原方案让批处理回滚后写 `update_failed.txt`。实测在长脚本里那一句
    # 写不出文件——同处的日志却能写，探针也确认执行到了那一行。
    # 试过换变量/换字面路径/换 > 与 >>/挪到 endlocal 之后，都没根治。
    #
    # 既然日志一直可靠，就由 Python 侧**解析日志尾部**判断结果
    # （见 `read_failure_note`）。少一个写入点，少一类失败模式。
    a("endlocal")
    a('del /F /Q "%~f0" >nul 2>&1')

    out = "\r\n".join(L) + "\r\n"
    if BS + BS in out:
        raise InstallError("generated script contains double backslash")
    return out


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
