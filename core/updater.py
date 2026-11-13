"""自动更新调度：检查 → 下载 → 校验 → 安装并重启。

## 分工

    github_release.py   网络：拉 Releases、下载资产、SHA-256 校验（已有能力）
    updater_install.py  文件：解压、替换安装目录、回滚（本模块的下游）
    updater.py（本文件）流程：串起来，并在合适时机触发安装

## 触发策略

自动更新**不能太吵**。策略：

    * 启动后延迟 N 秒（默认 30s）才检查，避免拖慢启动、也避开用户刚打开的忙碌期；
    * 每 **24 小时**最多自动检查一次（记录在 user_state 同级的 last_update_check.txt）；
    * 检查到新版本后再**静默下载**，下载校验通过才提示"将重启更新"；
    * 用户可以关掉自动更新（`config["auto_update"]`），关掉后只在关于页手动检查。

## 为什么"下载完还要用户确认"

虽然是"全自动"，但**重启会打断用户正在做的事**。所以流程是：

    下载 → 校验 → 通知用户（托盘气泡 + 关于页状态）→ 用户点"立即重启更新"
      → 或者等用户下次退出程序时自动安装

后者是关键：**退出时安装**对用户完全无感——反正程序已经不跑了，
这时候替换文件最安全。只有用户想立刻用新版时才需要主动重启。

## 平台限制

只有打包态的 onedir 才能自更新（源码态没有安装目录）。
不满足条件时 `is_supported()` 返回 False，调用方不启动任何定时器。
"""
import os
import sys
import time

from PySide6.QtCore import QObject, Signal

from core import github_release, runtime, updater_install
from core.version import (
    get_build_version, is_newer_version, normalize_tag,
)

#: 自动检查的间隔（秒）。24 小时。
CHECK_INTERVAL_SEC = 24 * 3600

#: 启动后延迟多久开始检查（秒）。避开启动高峰。
STARTUP_DELAY_SEC = 30

#: 上次检查时间记录文件名
_LAST_CHECK_FILE = "last_update_check.txt"


def is_supported():
    """当前形态是否支持自动更新。

    条件与 `updater_install.can_self_update()` 一致，另外要求是**正式发布版**
    （有版本标签）——开发构建的 commit 哈希无法与发布版本比较。
    """
    if not updater_install.can_self_update():
        return False
    return bool(get_build_version())


def unsupported_reason():
    """不支持自动更新时给出原因。"""
    if not getattr(sys, "frozen", False):
        return "源码运行不支持自动更新（仅打包后的 exe）"
    if not updater_install.can_self_update():
        return "当前安装目录不可写，无法自动更新（可能需要管理员权限）"
    if not get_build_version():
        return "开发构建不含版本标签，无法与发布版本比较"
    return ""


def _last_check_path():
    return os.path.join(runtime.app_data_dir(), _LAST_CHECK_FILE)


def last_check_time():
    """上次自动检查的时间戳（秒）；从未检查过返回 0。"""
    try:
        with open(_last_check_path(), "r", encoding="utf-8") as f:
            return float(f.read().strip() or 0)
    except Exception:
        return 0.0


def mark_checked(now=None):
    """记录本次检查时间。"""
    try:
        with open(_last_check_path(), "w", encoding="utf-8") as f:
            f.write(str(now if now is not None else time.time()))
    except Exception:
        pass


def should_check_now(config=None, now=None):
    """距离上次检查是否已超过间隔。"""
    if config is not None and not config.get("auto_update", True):
        return False
    now = now if now is not None else time.time()
    return (now - last_check_time()) >= CHECK_INTERVAL_SEC


class AutoUpdater(QObject):
    """自动更新的状态机：检查 → 下载 → 待安装。

    所有结果通过信号发出，UI 只负责展示；真正的文件替换在
    `install_now()`（立即）与 `install_on_exit()`（退出时）里做。
    """

    #: 发现新版本 (版本号, 发行信息 dict)
    update_available = Signal(str, object)
    #: 已是最新（或无法比较）
    up_to_date = Signal(str)
    #: 检查/下载出错 (消息)
    error = Signal(str)
    #: 下载进度 0~100
    progress = Signal(int)
    #: 新版本已下载并校验完成，可以安装 (版本号, 压缩包路径)
    ready_to_install = Signal(str, str)
    #: 安装脚本已启动（即将退出）
    installing = Signal(str)

    def __init__(self, config=None, parent=None):
        super().__init__(parent)
        self.config = config or {}
        self.api = github_release.ReleasesAPI(self)
        self.api.latest_ready.connect(self._on_latest)
        self.api.error_occurred.connect(self._on_error)
        self.api.download_progress.connect(self.progress.emit)
        self.api.download_finished.connect(self._on_downloaded)
        self.api.download_error.connect(self._on_error)

        #: 已发现的新版本（tag，规范化后）与发行信息
        self.new_version = ""
        self.release = None
        #: 已下载好的更新包路径
        self.package_path = ""

    # ---------- 检查 ----------
    def check(self):
        """拉取最新发行版并与当前版本比较。"""
        if not is_supported():
            self.error.emit(unsupported_reason())
            return False
        self.api.fetch_latest_release()
        return True

    def _current_version(self):
        return normalize_tag(get_build_version())

    def _on_latest(self, release):
        if not release:
            self.error.emit("未找到可用的发行版")
            return
        mark_checked()
        latest = normalize_tag(release.get("tag", ""))
        current = self._current_version()
        if not is_newer_version(latest, current):
            self.up_to_date.emit(current)
            return
        self.new_version = latest
        self.release = release
        self.update_available.emit(latest, release)

    def _on_error(self, message):
        # 错误不打断用户，只记录（由 UI 决定是否展示）
        self.error.emit(message or "未知错误")

    # ---------- 下载 ----------
    def download(self):
        """下载并校验新版本包。"""
        if not self.release:
            self.error.emit("还没有可下载的版本信息")
            return False
        asset = github_release.pick_windows_asset(self.release.get("assets", []))
        if not asset or not asset.get("url"):
            self.error.emit("该发行版没有可用的 Windows 安装包")
            return False
        dest = os.path.join(
            github_release.downloads_dir(),
            asset.get("name") or ("KaomojiAssistant-%s.zip" % self.new_version))
        self.api.download_asset(
            asset["url"], dest,
            expected_sha256=asset.get("sha256") or None,
            expected_size=int(asset.get("size") or 0))
        return True

    def _on_downloaded(self, path):
        self.package_path = path or ""
        if not self.package_path:
            self.error.emit("下载完成但未拿到文件路径")
            return
        # 校验压缩包结构，尽早发现"包不对"，而不是等退出后替换失败
        layout = updater_install.detect_layout(
            self.package_path, updater_install.exe_name())
        if not layout.get("ok"):
            self._fail("更新包结构异常：%s" % layout.get("reason", ""))
            # 坏包要删掉：留着不仅占空间，下次还可能被误用
            self._cleanup_bad_package()
            return
        self.ready_to_install.emit(self.new_version, self.package_path)

    def _cleanup_bad_package(self):
        """删除下载下来但校验失败的包，并清空路径。"""
        p = self.package_path
        self.package_path = ""
        if not p:
            return
        try:
            if os.path.isfile(p):
                os.remove(p)
        except Exception:
            pass

    # ---------- 安装 ----------
    def install_now(self, log=None):
        """立即安装并退出程序。

        调用方（AppRuntime）负责在 `installing` 信号后退出事件循环。
        """
        ok = self._prepare_and_launch(log)
        return ok

    def install_on_exit(self, log=None):
        """在程序退出时安装（不重启，下次启动即为新版）。

        这是"最无感"的路径：用户关程序时顺手完成替换。
        """
        return self._prepare_and_launch(log, relaunch_note=True)

    def _prepare_and_launch(self, log=None, relaunch_note=False):
        """解压 → 启动安装脚本。**任何一步失败都要完整回退。**

        ## 回退要回退什么

        这个方法会改动两处外部状态，失败时都必须还原：

          1. **暂存目录**：`prepare()` 解压出来的临时文件。
             不清理会一直堆在 %TEMP%，时间长了很占空间。
          2. **pending 标记**：`write_pending()` 写的"正在更新到 X"。
             留着它，下次启动会误判为"上次更新失败"，
             而且 `check_pending_result` 会反复提示。

        早期版本只清了 pending、没清暂存目录，而且 `launch_installer`
        失败时连 pending 都可能没清干净（异常路径不完整）。
        现在用 `finally` 统一兜底。
        """
        if not self.package_path or not os.path.isfile(self.package_path):
            self._fail("更新包不存在或已被删除", log)
            return False

        staging = None
        launched = False
        try:
            updater_install.write_pending(self.new_version)
            staging, _exe = updater_install.prepare(self.package_path, log)
            updater_install.launch_installer(staging, log)
            launched = True
        except Exception as e:
            self._fail("安装失败：%s" % e, log, exc=e)
            return False
        finally:
            if not launched:
                # 没成功启动安装脚本 → 完整回退
                if staging:
                    try:
                        import shutil
                        shutil.rmtree(staging, ignore_errors=True)
                    except Exception:
                        pass
                try:
                    updater_install.clear_pending()
                except Exception:
                    pass
                if log is not None:
                    try:
                        log("info", "update", "已回退：清理暂存文件与更新标记")
                    except Exception:
                        pass
            else:
                # 启动成功：标记留给下次启动判断结果
                pass

        self.installing.emit(self.new_version)
        return True

    def _fail(self, message, log=None, exc=None):
        """统一失败处理：记日志 + 发信号。"""
        if log is not None:
            try:
                log("error", "update",
                    message if exc is None else "%s（%r）" % (message, exc))
            except Exception:
                pass
        try:
            self.error.emit(message)
        except Exception:
            pass

    def has_pending_package(self):
        """是否已有下载好待安装的包。"""
        return bool(self.package_path and os.path.isfile(self.package_path))
