"""应用版本号统一来源。

- 开发/源码形态：直接执行 `git rev-parse --short HEAD` 取当前 commit 短哈希。
- 打包形态（onedir 内无 .git）：回退到构建期由 build.py 生成的
  `core._build_version` 模块（BUILD_COMMIT / BUILD_VERSION / BUILD_NOTES）。
- 正式发布版本可带人工版本标签（如 v1.2），组合显示为 `v1.2(bdb300a)`；
  开发构建不带标签，仅显示 commit 短哈希。

关于页与更新弹窗统一从这里取版本字符串，避免版本来源分散。
"""
import os
import subprocess

_DEFAULT = "unknown"
_SEEN_FILE = "last_seen_version.txt"


def _git_commit():
    try:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=root, capture_output=True, text=True, timeout=2,
        )
        if out.returncode == 0:
            return out.stdout.strip()
    except Exception:
        pass
    return ""


def _build_meta():
    """返回 (commit, version_label, notes)。打包态从 core._build_version 读取。"""
    commit = _git_commit()
    label = ""
    notes = ""
    try:
        from core import _build_version as bv
        commit = getattr(bv, "BUILD_COMMIT", commit) or commit
        label = getattr(bv, "BUILD_VERSION", "") or ""
        notes = getattr(bv, "BUILD_NOTES", "") or ""
    except Exception:
        pass
    return commit, label, notes


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


def should_show_update_popup():
    """仅正式发布构建（有版本标签）且本次版本与上次已见版本不同时弹出。"""
    if not is_release_build():
        return False
    cur = get_app_version()
    return bool(cur) and cur != load_seen_version()
