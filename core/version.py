"""应用版本号统一来源：始终使用当前 git commit 短哈希（如 f94d0d4）。

- 开发/源码形态：直接执行 `git rev-parse --short HEAD` 取当前 commit。
- 打包形态（onedir 内无 .git、也无 git 命令）：回退到构建期由 build.py
  生成的 `core._build_version.BUILD_COMMIT` 常量（真正的 Python 模块，
  PyInstaller 能正确收集，不依赖运行时文件路径）。
- 都没有时返回 "unknown"。
"""
import os
import subprocess

_DEFAULT = "unknown"


def get_app_version(default=_DEFAULT):
    # 1) 源码/开发形态：当前 commit 短哈希
    try:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=root, capture_output=True, text=True, timeout=2,
        )
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except Exception:
        pass
    # 2) 打包形态（无 git）：回退到构建期写入的常量
    try:
        from core._build_version import BUILD_COMMIT
        if BUILD_COMMIT:
            return BUILD_COMMIT
    except Exception:
        pass
    return default
