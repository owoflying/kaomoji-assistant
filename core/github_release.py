"""GitHub 发行包获取与下载（关于页「检查更新 / 下载」功能使用）。

- 拉取公开仓库的最新发行版信息（无需鉴权，走公开 API）。
- 下载发行资产（如 Windows 压缩包 / exe）到用户 Downloads 目录。

网络调用统一用 QNetworkAccessManager（Qt 原生、非阻塞），配合超时与错误信号，
避免在主线程同步请求导致 GUI 卡顿 / 假死。
"""
import os
import json
import tempfile
import hashlib

from PySide6.QtCore import QObject, QUrl, Signal, QStandardPaths, QThread, QByteArray

# ⚠ `QtNetwork` **不在模块顶层导入**。
#
# 实测：`from PySide6.QtNetwork import ...` 冷启动要 **885ms**，
# 因为它会拉起整个网络栈（SSL、DNS、代理探测）。而检查更新一天才跑一次，
# 却让每次启动都付这个代价——这是启动慢的头号原因（占 5.2s 的 17%）。
#
# 改成懒加载：只有真的要发请求时才 import。
_QNAM = None
_QNREQ = None
_QNREP = None


def _ensure_qtnetwork():
    """首次发网络请求时才加载 QtNetwork。"""
    global _QNAM, _QNREQ, _QNREP
    if _QNAM is None:
        from PySide6.QtNetwork import (          # noqa: PLC0415
            QNetworkAccessManager, QNetworkRequest, QNetworkReply,
        )
        _QNAM = QNetworkAccessManager
        _QNREQ = QNetworkRequest
        _QNREP = QNetworkReply
    return _QNAM, _QNREQ, _QNREP


REPO_OWNER = "owoflying"
REPO_NAME = "kaomoji-assistant"
API_LATEST = "https://api.github.com/repos/%s/%s/releases/latest" % (REPO_OWNER, REPO_NAME)
REQUEST_TIMEOUT_MS = 15000
_USER_AGENT = "KaomojiAssistant/%s (about-page release checker)"


def _user_agent():
    try:
        from core.version import get_app_version
        return _USER_AGENT % get_app_version()
    except Exception:
        return _USER_AGENT % "dev"


def parse_release(payload):
    """把 GitHub releases/latest 的 JSON 解析为简化结构（纯函数，便于离线测试）。

    返回 dict 或 None（解析失败 / 非发行版返回）。字段：
      tag, name, body, html_url, published_at,
      assets: [{name, size, url, content_type}]
    """
    if not payload:
        return None
    try:
        if isinstance(payload, (bytes, bytearray)):
            payload = payload.decode("utf-8", "replace")
        data = json.loads(payload) if isinstance(payload, str) else payload
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    # 常见错误返回形如 {"message": "Not Found", "documentation_url": ...}
    if "message" in data and "tag_name" not in data:
        return None
    assets = []
    for a in data.get("assets", []) or []:
        if not isinstance(a, dict):
            continue
        # GitHub 资产自 2023-02 起提供 digest 字段，形如 "sha256:<hex>"
        digest = a.get("digest", "") or ""
        sha = ""
        if ":" in digest:
            algo, _, h = digest.partition(":")
            if algo.lower() == "sha256":
                sha = h.strip()
        assets.append({
            "name": a.get("name", ""),
            "size": a.get("size", 0),
            "url": a.get("browser_download_url", ""),
            "content_type": a.get("content_type", ""),
            "sha256": sha,
        })
    return {
        "tag": data.get("tag_name", ""),
        "name": data.get("name", ""),
        "body": data.get("body", "") or "",
        "html_url": data.get("html_url", ""),
        "published_at": data.get("published_at", ""),
        "assets": assets,
    }


def pick_windows_asset(assets):
    """从资产列表中挑 Windows 可下载项：优先 .zip，其次 .exe，否则首个。"""
    if not assets:
        return None
    for a in assets:
        if a.get("name", "").lower().endswith(".zip"):
            return a
    for a in assets:
        if a.get("name", "").lower().endswith(".exe"):
            return a
    return assets[0]


def downloads_dir():
    """用户下载目录（无则回退临时目录）。"""
    try:
        d = QStandardPaths.writableLocation(QStandardPaths.DownloadLocation)
        if d and os.path.isdir(d):
            return d
    except Exception:
        pass
    return tempfile.gettempdir()


def compute_sha256(path, chunk_size=1024 * 1024):
    """计算文件 SHA-256（分块读取，纯函数，便于离线测试）。

    返回 hex 串；文件不可读时返回 None。
    """
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(chunk_size), b""):
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return None


def _has_error(reply):
    """这个 reply 是否出错（等价于 `reply.error() != QNetworkReply.NoError`）。

    包一层是为了不把 `QNetworkReply` 这个名字散落在各处——
    QtNetwork 是懒加载的，类名在模块顶层拿不到。
    """
    _qnam, _qreq, qrep = _ensure_qtnetwork()
    return reply.error() != qrep.NoError


def _safe_delete_reply(reply):
    """安全销毁 QNetworkReply。

    ## 为什么要绕一下

    直接在 `finished` 槽里 `reply.deleteLater()` 会触发控制台噪音：

        QIODevice::read (QSslSocket): device not open

    原因：`deleteLater()` 只是**安排**删除，但当前信号派发还没结束；
    Qt 在收尾时仍会尝试从这个 reply 的底层 socket 读剩余数据，
    而此时 TLS 层已经关闭 → 报错。功能上不影响（请求已经完成），
    但每次检查更新都会刷一行，调试时很碍事。

    做法：用 `QTimer.singleShot(0, ...)` 把删除推迟到**下一轮事件循环**，
    等这次信号派发彻底结束了再删。

    拿不到 QTimer 时（极端情况）退化为 `deleteLater()`，功能不受影响。
    """
    if reply is None:
        return
    try:
        from PySide6.QtCore import QTimer

        def _do():
            try:
                reply.deleteLater()
            except RuntimeError:
                pass        # 对象已被销毁

        QTimer.singleShot(0, _do)
    except Exception:
        try:
            reply.deleteLater()
        except RuntimeError:
            pass


class HashWorker(QThread):
    """后台线程计算文件 SHA-256，避免大文件阻塞主线程（GUI 卡顿）。"""

    done = Signal(str, object)  # (path, hex_or_None)

    def __init__(self, path):
        super().__init__()
        self._path = path

    def run(self):
        self.done.emit(self._path, compute_sha256(self._path))


class ReleasesAPI(QObject):
    """获取最新发行版并下载资产。所有结果通过信号异步返回。"""

    latest_ready = Signal(object)        # dict（parse_release 的结果）或 None
    error_occurred = Signal(str)         # 错误信息
    download_progress = Signal(int)      # 0~100
    verifying = Signal()                 # 开始校验文件完整性
    download_finished = Signal(str)      # 保存路径（已校验通过或无需校验）
    download_error = Signal(str)         # 错误信息

    def __init__(self, parent=None):
        super().__init__(parent)
        # 构造时才加载 QtNetwork（见模块顶部的说明）
        qnam, _req, _rep = _ensure_qtnetwork()
        self._mgr = qnam(self)
        self._reply = None
        self._dl_reply = None
        self._dl_file = None
        self._expected_sha = None    # 来自资产 digest 的预期 SHA-256
        self._expected_size = 0      # 资产真实字节数（来自 releases API），作为进度分母
        self._server_sha = None      # 实际用于比对的服务端哈希（header 或 digest）
        self._hash_worker = None

    def fetch_latest_release(self):
        """拉取最新发行版；结果经 latest_ready / error_occurred 返回。"""
        _qnam, qreq, _qrep = _ensure_qtnetwork()
        req = qreq(QUrl(API_LATEST))
        req.setHeader(qreq.UserAgentHeader, _user_agent())
        try:
            req.setTransferTimeout(REQUEST_TIMEOUT_MS)
        except Exception:
            pass
        self._reply = self._mgr.get(req)
        self._reply.finished.connect(self._on_latest_finished)

    def _on_latest_finished(self):
        reply = self._reply
        if reply is None:
            return
        # 先把引用摘掉，避免后续误用
        self._reply = None
        try:
            if _has_error(reply):
                self.error_occurred.emit("无法获取发行版信息：%s" % reply.errorString())
                return
            data = reply.readAll().data()
            result = parse_release(data)
            if not result or not result.get("tag"):
                self.error_occurred.emit("未找到可用的发行版")
                return
            self.latest_ready.emit(result)
        except Exception as e:
            self.error_occurred.emit("解析发行版信息失败：%s" % e)
        finally:
            _safe_delete_reply(reply)

    def download_asset(self, url, dest_path, expected_sha256=None, expected_size=0):
        """下载资产到 dest_path；进度经 download_progress，结果经 download_finished/error。

        expected_sha256 为服务端权威 SHA-256（来自资产 digest），用于下载后校验。
        expected_size 为资产真实字节数（来自 releases API 的 assets[].size），作为
        进度百分比的权威分母——GitHub 资产经 CDN 302 重定向，downloadProgress 信号
        给到的 total 经常不可靠（偏小或仅反映重定向响应），直接用会导致进度条提前到
        ~50% 然后卡住；用已知真实大小可让进度准确线性增长。
        """
        self._expected_sha = expected_sha256 or None
        self._expected_size = expected_size or 0
        try:
            self._dl_file = open(dest_path, "wb")
        except Exception as e:
            self.download_error.emit("无法创建文件：%s" % e)
            return
        _qnam, qreq, qrep = _ensure_qtnetwork()
        req = qreq(QUrl(url))
        req.setHeader(qreq.UserAgentHeader, _user_agent())
        # 注意：下载大文件时不设 transfer timeout（仅元数据请求设），
        # 否则慢速网络下超过 15s 无新字节会被强制中断，大发行包必失败。
        self._dl_reply = self._mgr.get(req)
        self._dl_reply.downloadProgress.connect(self._on_dl_progress)
        self._dl_reply.finished.connect(self._on_dl_finished)
        self.download_progress.emit(0)

    def _on_dl_progress(self, received, total):
        # 优先用已知的资产真实大小作分母；拿不到才回退信号给的 total
        denom = self._expected_size if self._expected_size and self._expected_size > 0 else total
        if denom > 0:
            self.download_progress.emit(int(received * 100 / denom))

    @staticmethod
    def _safe_remove(path):
        try:
            if path and os.path.exists(path):
                os.remove(path)
        except Exception:
            pass

    def _on_dl_finished(self):
        reply = self._dl_reply
        if reply is None:
            return
        try:
            if _has_error(reply):
                self.download_error.emit("下载失败：%s" % reply.errorString())
                if self._dl_file:
                    self._dl_file.close()
                    self._safe_remove(self._dl_file.name)
                return
            # 确保末尾分片写入
            last = reply.readAll()
            if last:
                self._dl_file.write(last)
            self._dl_file.close()
            saved_path = self._dl_file.name
            # 取服务端权威哈希：优先下载响应头（可能被重定向剥离），否则用资产 digest
            header_sha = ""
            try:
                raw = reply.rawHeader(QByteArray(b"x-checksum-sha256"))
                header_sha = bytes(raw).decode("ascii", "ignore").strip()
            except Exception:
                header_sha = ""
            self._server_sha = header_sha or (self._expected_sha or "")
            if not self._server_sha:
                # 服务端未提供哈希，跳过校验直接完成
                self.download_finished.emit(saved_path)
                return
            # 后台线程计算本地哈希，避免大文件阻塞 GUI
            self.verifying.emit()
            self._hash_worker = HashWorker(saved_path)
            self._hash_worker.done.connect(self._on_hash_done)
            self._hash_worker.finished.connect(self._hash_worker.deleteLater)
            self._hash_worker.start()
        except Exception as e:
            self.download_error.emit("下载完成处理失败：%s" % e)
        finally:
            if self._dl_file is not None and not self._dl_file.closed:
                try:
                    self._dl_file.close()
                except Exception:
                    pass
            self._dl_reply = None
            # 同 _on_latest_finished：延迟删除，避免 SSL socket 收尾报错
            _safe_delete_reply(reply)

    def _on_hash_done(self, path, local_sha):
        self._hash_worker = None
        if not local_sha:
            self.download_error.emit("下载完成但无法读取文件进行校验")
            self._safe_remove(path)
            return
        if local_sha.lower() == (self._server_sha or "").lower():
            self.download_finished.emit(path)
        else:
            self.download_error.emit("校验失败：文件哈希不匹配，可能下载不完整或被篡改")
            self._safe_remove(path)
