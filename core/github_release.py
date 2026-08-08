"""GitHub 发行包获取与下载（关于页「检查更新 / 下载」功能使用）。

- 拉取公开仓库的最新发行版信息（无需鉴权，走公开 API）。
- 下载发行资产（如 Windows 压缩包 / exe）到用户 Downloads 目录。

网络调用统一用 QNetworkAccessManager（Qt 原生、非阻塞），配合超时与错误信号，
避免在主线程同步请求导致 GUI 卡顿 / 假死。
"""
import os
import json
import tempfile

from PySide6.QtCore import QObject, QUrl, Signal, QStandardPaths
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
        assets.append({
            "name": a.get("name", ""),
            "size": a.get("size", 0),
            "url": a.get("browser_download_url", ""),
            "content_type": a.get("content_type", ""),
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


class ReleasesAPI(QObject):
    """获取最新发行版并下载资产。所有结果通过信号异步返回。"""

    latest_ready = Signal(object)        # dict（parse_release 的结果）或 None
    error_occurred = Signal(str)         # 错误信息
    download_progress = Signal(int)      # 0~100
    download_finished = Signal(str)      # 保存路径
    download_error = Signal(str)         # 错误信息

    def __init__(self, parent=None):
        super().__init__(parent)
        self._mgr = QNetworkAccessManager(self)
        self._reply = None
        self._dl_reply = None
        self._dl_file = None

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

    def download_asset(self, url, dest_path):
        """下载资产到 dest_path；进度经 download_progress，结果经 download_finished/error。"""
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

    def _on_dl_finished(self):
        reply = self._dl_reply
        if reply is None:
            return
        try:
            if reply.error() != QNetworkReply.NoError:
                self.download_error.emit("下载失败：%s" % reply.errorString())
                if self._dl_file:
                    self._dl_file.close()
                    try:
                        os.remove(self._dl_file.name)
                    except Exception:
                        pass
                return
            # 确保末尾分片写入
            last = reply.readAll()
            if last:
                self._dl_file.write(last)
            self._dl_file.close()
            self.download_finished.emit(self._dl_file.name)
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
