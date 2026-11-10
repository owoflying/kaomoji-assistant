"""应用版本号统一来源。

- 开发/源码形态：直接执行 `git rev-parse --short HEAD` 取当前 commit 短哈希。
- 打包形态（onedir 内无 .git）：回退到构建期由 build.py 生成的
  `core._build_version` 模块（BUILD_COMMIT / BUILD_VERSION / BUILD_NOTES）。
- 正式发布版本可带人工版本标签（如 v1.2），组合显示为 `v1.2(bdb300a)`；
  开发构建不带标签，仅显示 commit 短哈希。

关于页与更新弹窗统一从这里取版本字符串，避免版本来源分散。
"""
import os
import re
import sys
import subprocess

_DEFAULT = "unknown"
_SEEN_FILE = "last_seen_version.txt"

#: 首次运行欢迎页的标记文件（**与版本号无关**：弹过一次就永不再弹）
_WELCOME_FILE = "welcome_shown.txt"


def _git_commit():
    try:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        # CREATE_NO_WINDOW：即使本程序以 GUI 形态运行，也不弹出命令行黑框
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=root, capture_output=True, text=True, timeout=2,
            creationflags=flags,
        )
        if out.returncode == 0:
            return out.stdout.strip()
    except Exception:
        pass
    return ""


_META_CACHE = None  # 进程内缓存：版本信息在生命周期内不变，避免重复调 git 弹窗/开销


def _build_meta():
    """返回 (commit, version_label, notes)。

    源码/开发形态（sys.frozen 为假）：始终以 git HEAD 为准，忽略 build.py 写入的
    core/_build_version.py —— 该文件只用于「打包态」回退（onedir 内无 .git）、
    不应污染源码态的版本号。
    打包形态（frozen）：无 .git，使用构建期烘焙文件；缺失时再回退 git。
    结果缓存，避免重复调 git。
    """
    global _META_CACHE
    if _META_CACHE is not None:
        return _META_CACHE
    commit = ""
    label = ""
    notes = ""
    frozen = getattr(sys, "frozen", False)
    if frozen:
        # 打包态：读取构建期烘焙的版本信息（core/_build_version.py 已随包分发）
        try:
            from core import _build_version as bv
            commit = getattr(bv, "BUILD_COMMIT", "") or ""
            label = getattr(bv, "BUILD_VERSION", "") or ""
            notes = getattr(bv, "BUILD_NOTES", "") or ""
        except Exception:
            commit = _git_commit()
    else:
        # 源码/开发态：直接 git 取 commit 短哈希，不被构建脚本污染
        commit = _git_commit()
    _META_CACHE = (commit, label, notes)
    return _META_CACHE


def format_version(commit, label=""):
    """组合显示：有标签时 `v1.2(bdb300a)`，否则仅 commit 短哈希。"""
    if label:
        return "%s(%s)" % (label, commit) if commit else label
    return commit or _DEFAULT


def get_app_version(default=_DEFAULT):
    """关于页/更新弹窗使用的版本字符串。"""
    commit, label, _ = _build_meta()
    return format_version(commit, label) or default


def get_build_version():
    """人工版本标签（如 v1.2）；开发构建返回空串。"""
    _, label, _ = _build_meta()
    return label


def get_build_notes():
    """构建期烘焙的更新说明（changelog）；无则返回空串。"""
    _, _, notes = _build_meta()
    return notes


def is_release_build():
    """是否为正式发布构建（带人工版本标签）。"""
    return bool(get_build_version())


def load_seen_version():
    """读取上次已见版本（存于 %APPDATA%/KaomojiAssistant/last_seen_version.txt）。"""
    try:
        from core import runtime
        p = os.path.join(runtime.app_data_dir(), _SEEN_FILE)
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8") as f:
                return f.read().strip()
    except Exception:
        pass
    return ""


def save_seen_version(version):
    """记录本次已见版本，用于判断下次是否需要弹更新提示。"""
    try:
        from core import runtime
        p = os.path.join(runtime.app_data_dir(), _SEEN_FILE)
        with open(p, "w", encoding="utf-8") as f:
            f.write(version or "")
    except Exception:
        pass


# ---------------------------------------------------------------- 首次运行欢迎页
def has_shown_welcome():
    """首次运行欢迎页是否已经弹过。"""
    try:
        from core import runtime
        p = os.path.join(runtime.app_data_dir(), _WELCOME_FILE)
        return os.path.exists(p)
    except Exception:
        # 读不到就当已弹过：宁可漏弹一次，也不要每次启动都弹
        return True


def mark_welcome_shown():
    """标记欢迎页已弹过（此后永不再弹）。"""
    try:
        from core import runtime
        p = os.path.join(runtime.app_data_dir(), _WELCOME_FILE)
        with open(p, "w", encoding="utf-8") as f:
            f.write("1")
        return True
    except Exception:
        return False


def should_show_welcome(force=False):
    """是否应弹「首次使用欢迎界面」。

    **判定与版本号无关**：用独立标记文件，弹过一次就永不再弹。
    这与 `should_show_update_popup`（每个版本首次启动都弹）刻意区分开——
    欢迎 + 首次配置引导只在用户第一次接触这个软件时出现一次。

    :param force: 开发者模式「模拟首启」用，绕过标记检查。

    环境变量：
      * ``KAOMOJI_FORCE_WELCOME=1``  —— 强制弹（调试用）；
      * ``KAOMOJI_SKIP_WELCOME=1``   —— 强制不弹（自动化测试/截图用）。
    """
    if os.environ.get("KAOMOJI_SKIP_WELCOME") == "1":
        return False
    if force or os.environ.get("KAOMOJI_FORCE_WELCOME") == "1":
        return True
    return not has_shown_welcome()


def should_show_update_popup():
    """是否应在启动时弹出更新提示框。

    - 若环境变量 KAOMOJI_FORCE_UPDATE_POPUP=1（开发者模式「模拟升级」注入），直接返回
      True，绕过「必须正式发布构建」与「版本已见」两道门槛，使开发构建也能弹窗；
    - 否则仅正式发布构建（有版本标签）且本次版本与上次已见版本不同时弹出。
    """
    if os.environ.get("KAOMOJI_FORCE_UPDATE_POPUP") == "1":
        return bool(get_app_version())
    if not is_release_build():
        return False
    cur = get_app_version()
    return bool(cur) and cur != load_seen_version()


# ======================================================================
# 版本比较（自动更新用）
# ======================================================================
_VERSION_RE = re.compile(r"^v?(\d+)(?:\.(\d+))?(?:\.(\d+))?(?:\.(\d+))?$")


def parse_version(text):
    """把 ``v1.2.3`` / ``1.2`` / ``v1.2.3.4`` 解析成可比较的元组。

    只支持纯数字分段（本项目版本标签就是这么写的）。无法解析时返回 None，
    调用方据此决定"无法比较就当作没有新版本"，避免误触发更新。

    返回 ``(nums_tuple, is_prerelease)``：
      * nums_tuple 长度统一补到 4 位，便于直接比较；
      * ``is_prerelease`` 识别 ``-beta`` / ``-rc`` 之类的后缀（有后缀视为预发布）。
    """
    if not isinstance(text, str):
        return None
    s = text.strip()
    if not s:
        return None
    prerelease = False
    # 拆掉 -beta.1 / -rc2 这类后缀
    for sep in ("-", "+"):
        if sep in s:
            s, _, _tail = s.partition(sep)
            prerelease = True
            break
    m = _VERSION_RE.match(s)
    if not m:
        return None
    nums = tuple(int(g) if g is not None else 0 for g in m.groups())
    return nums, prerelease


def compare_versions(a, b):
    """比较两个版本字符串。返回 -1 / 0 / 1；任一无法解析时返回 None。

    规则（与常见语义化版本一致的简化版）：
      1. 逐段比数字，大者更新；
      2. 数字完全相同时，**正式版 > 预发布版**（1.2.0 > 1.2.0-beta）。
    """
    pa, pb = parse_version(a), parse_version(b)
    if pa is None or pb is None:
        return None
    na, pre_a = pa
    nb, pre_b = pb
    if na != nb:
        return 1 if na > nb else -1
    if pre_a != pre_b:
        # 正式版更新
        return -1 if pre_a else 1
    return 0


def is_newer_version(candidate, current):
    """``candidate`` 是否比 ``current`` 更新。

    两个关键取舍：
      * **无法解析时返回 False**——宁可漏掉一次更新，也不能把开发构建的
        commit 哈希当成版本号去比对，误触发下载；
      * ``current`` 为空（开发构建无版本标签）时也返回 False，
        理由同上：自动更新只对正式发布版有意义。
    """
    if not candidate or not current:
        return False
    r = compare_versions(candidate, current)
    return r == 1


def normalize_tag(tag):
    """把发布标签（如 ``v1.2.0``）规范成纯版本串（``1.2.0``）。"""
    s = (tag or "").strip()
    if s[:1] in ("v", "V"):
        s = s[1:]
    return s
