"""最近使用 / 收藏夹 的持久化（存储于 data/user_state.json）。"""
import json
import os

from PySide6.QtCore import QObject, Signal

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
        except Exception:
            self.recent = []
            self.favorites = []
            return
        self.recent = self._clean(raw_recent)[: self.max_recent]
        self.favorites = self._clean(raw_fav)
        # 载入时若清掉了脏数据，立刻回写，避免下次启动又读到
        if len(self.recent) != len(raw_recent) or len(self.favorites) != len(raw_fav):
            self.save()

    def save(self):
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(
                    {"recent": self.recent, "favorites": self.favorites},
                    f, ensure_ascii=False, indent=2,
                )
        except Exception:
            pass

    def add_recent(self, text):
        text = str(text)
        if not self._accepts(text):
            return          # 非库内条目一律不落盘，从源头挡住脏数据
        if text in self.recent:
            self.recent.remove(text)
        self.recent.insert(0, text)
        if len(self.recent) > self.max_recent:
            self.recent = self.recent[: self.max_recent]
        self.save()
        self.changed.emit()

    def is_favorite(self, text):
        return str(text) in self.favorites

    def toggle_favorite(self, text):
        text = str(text)
        if text in self.favorites:
            self.favorites.remove(text)
        elif self._accepts(text):
            self.favorites.insert(0, text)
        else:
            return
        self.save()
        self.changed.emit()
