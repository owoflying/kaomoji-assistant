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
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkRequest, QNetworkReply


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
        self._mgr = QNetworkAccessManager(self)
        self._reply = None
        self._dl_reply = None
        self._dl_file = None
        self._expected_sha = None    # 来自资产 digest 的预期 SHA-256
        self._server_sha = None      # 实际用于比对的服务端哈希（header 或 digest）
        self._hash_worker = None

    def fetch_latest_release(self):
        """拉取最新发行版；结果经 latest_ready / error_occurred 返回。"""
        req = QNetworkRequest(QUrl(API_LATEST))
        req.setHeader(QNetworkRequest.UserAgentHeader, _user_agent())
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
        try:
            if reply.error() != QNetworkReply.NoError:
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
            reply.deleteLater()
            self._reply = None

    def download_asset(self, url, dest_path, expected_sha256=None):
        """下载资产到 dest_path；进度经 download_progress，结果经 download_finished/error。

        expected_sha256 为服务端权威 SHA-256（来自资产 digest），用于下载后校验。
        为 None 且下载响应头也无哈希时，跳过校验。
        """
        self._expected_sha = expected_sha256 or None
        try:
            self._dl_file = open(dest_path, "wb")
        except Exception as e:
            self.download_error.emit("无法创建文件：%s" % e)
            return
        req = QNetworkRequest(QUrl(url))
        req.setHeader(QNetworkRequest.UserAgentHeader, _user_agent())
        try:
            req.setTransferTimeout(REQUEST_TIMEOUT_MS)
        except Exception:
            pass
        self._dl_reply = self._mgr.get(req)
        self._dl_reply.downloadProgress.connect(self._on_dl_progress)
        self._dl_reply.finished.connect(self._on_dl_finished)
        self.download_progress.emit(0)

    def _on_dl_progress(self, received, total):
        if total > 0:
            self.download_progress.emit(int(received * 100 / total))

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
            if reply.error() != QNetworkReply.NoError:
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
            reply.deleteLater()
            self._dl_reply = None

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
