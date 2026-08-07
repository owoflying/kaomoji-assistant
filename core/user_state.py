"""最近使用 / 收藏夹 的持久化（存储于 data/user_state.json）。"""
import json
import os

from PySide6.QtCore import QObject, Signal, QTimer

from core.runtime import state_path

STATE_PATH = state_path()


class UserState(QObject):
    """记录最近使用与收藏夹，并在变更时发出 changed 信号。

    valid_items: 可选的合法颜文字集合。传入后会启用「自净」——
      载入与写入时都会丢弃不在库中的条目。这样任何来源的脏数据
      （测试脚本误写、手改 JSON 出错、旧版本残留）都不会出现在候选条里。
    """

    changed = Signal()

    def __init__(self, path=STATE_PATH, max_recent=30, valid_items=None):
        super().__init__()
        self.path = path
        self.max_recent = max_recent
        self.valid_items = set(valid_items) if valid_items else None
        self.recent = []
        self.favorites = []
        self.usage = {}           # {text: 累计插入次数}，用于使用统计
        # 落盘去抖：500ms 内的多次 save 合并为一次整文件写盘，
        # 避免连续选词 / 切换收藏反复重写 user_state.json。changed 信号仍即时发出。
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(500)
        self._save_timer.timeout.connect(self.save)
        self.load()

    # ---------- 自净 ----------
    def _accepts(self, text):
        if self.valid_items is None:
            return True
        return text in self.valid_items

    def _clean(self, seq):
        """去掉非法项与重复项，保持原顺序。"""
        out = []
        seen = set()
        for x in seq:
            s = str(x)
            if not s or s in seen or not self._accepts(s):
                continue
            seen.add(s)
            out.append(s)
        return out

    def load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            raw_recent = data.get("recent") or []
            raw_fav = data.get("favorites") or []
            raw_usage = data.get("usage") or {}
        except Exception:
            self.recent = []
            self.favorites = []
            self.usage = {}
            return
        self.recent = self._clean(raw_recent)[: self.max_recent]
        self.favorites = self._clean(raw_fav)
        # 使用统计：仅保留可解析为 int 的计数；若设了合法集合则自净掉不在库内的旧键
        usage = {}
        for k, v in raw_usage.items():
            try:
                usage[str(k)] = int(v)
            except (TypeError, ValueError):
                pass
        if self.valid_items is not None:
            usage = {k: c for k, c in usage.items() if k in self.valid_items}
        self.usage = usage
        # 载入时若清掉了脏数据，立刻回写，避免下次启动又读到
        if len(self.recent) != len(raw_recent) or len(self.favorites) != len(raw_fav):
            self.save()

    def save(self):
        # 任何显式 save 都取消待定的延迟写，避免重复落盘
        self._save_timer.stop()
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(
                    {"recent": self.recent, "favorites": self.favorites,
                     "usage": self.usage},
                    f, ensure_ascii=False, indent=2,
                )
        except Exception:
            pass

    def _schedule_save(self):
        """合并 500ms 内的多次落盘请求为一次（见 __init__ 的 _save_timer）。"""
        if not self._save_timer.isActive():
            self._save_timer.start()

    def flush(self):
        """立即落盘并取消待定的延迟写（应用退出前调用，防 500ms 窗口内丢数据）。"""
        self._save_timer.stop()
        self.save()

    def add_recent(self, text):
        text = str(text)
        if not self._accepts(text):
            return          # 非库内条目一律不落盘，从源头挡住脏数据
        if text in self.recent:
            if self.recent[0] == text:
                return      # 已是最新一条，列表未变，跳过落盘与刷新
            self.recent.remove(text)
        self.recent.insert(0, text)
        if len(self.recent) > self.max_recent:
            self.recent = self.recent[: self.max_recent]
        self._schedule_save()
        self.changed.emit()

    def is_favorite(self, text):
        return str(text) in self.favorites

    # ---------- 使用统计 ----------
    def record_usage(self, text):
        """记录一次颜文字插入，累加计数（不区分来源，统计所有插入）。"""
        text = str(text)
        if not text:
            return
        self.usage[text] = self.usage.get(text, 0) + 1
        self._schedule_save()

    def top_usage(self, n=10):
        """返回使用次数最多的前 n 个 (text, count)，降序。"""
        return sorted(self.usage.items(), key=lambda kv: kv[1], reverse=True)[:n]

    def total_inserts(self):
        """累计插入总次数。"""
        return sum(self.usage.values())

    def toggle_favorite(self, text):
        text = str(text)
        if text in self.favorites:
            self.favorites.remove(text)
        elif self._accepts(text):
            self.favorites.insert(0, text)
        else:
            return
        self._schedule_save()
        self.changed.emit()
