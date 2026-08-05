"""用户自定义颜文字：增删改、分组、标签、搜索。

与只读的 data/kaomoji.json 完全分离，存于可写区（%APPDATA%/KaomojiAssistant
或源码态的项目 data/）。任何来源的数据都按用户意图原样保存，不做「连续汉字」
之类的脏数据过滤——那是只读库的自我保护，用户自己加的东西理应信任。
落盘用 QTimer 合并（与主程序一致的去抖思路），changed 信号供 UI / 自动弹出刷新。
"""
import json
import os

from PySide6.QtCore import QObject, Signal, QTimer

from core.runtime import user_kaomoji_path


def _norm_tags(raw):
    """把逗号/顿号/空格分隔的标签字符串整理成去重、去空的有序列表。"""
    if not raw:
        return []
    if isinstance(raw, (list, tuple)):
        parts = raw
    else:
        parts = str(raw).replace("，", ",").replace("、", ",").replace(";", ",").split(",")
    out = []
    seen = set()
    for p in parts:
        p = p.strip()
        if p and p not in seen:
            seen.add(p)
            out.append(p)
    return out


class UserKaomoji(QObject):
    changed = Signal()

    def __init__(self, path=None):
        super().__init__()
        self.path = path or user_kaomoji_path()
        self.groups = ["默认"]
        self.items = []          # [{"text", "group", "tags":[...]}]
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(400)
        self._save_timer.timeout.connect(self.save)
        self.load()

    # ---------- 持久化 ----------
    def load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.groups = data.get("groups") or ["默认"]
            self.items = [it for it in (data.get("items") or []) if isinstance(it, dict) and it.get("text")]
            # 保证所有 item.group 都在 groups 里，缺则补回
            for it in self.items:
                if it.get("group") not in self.groups:
                    self.groups.append(it["group"])
            if "默认" in self.groups:
                self.groups.remove("默认")
                self.groups.insert(0, "默认")
        except Exception:
            self.groups = ["默认"]
            self.items = []

    def save(self):
        self._save_timer.stop()
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(
                    {"groups": self.groups, "items": self.items},
                    f, ensure_ascii=False, indent=2,
                )
        except Exception:
            pass

    def _schedule_save(self):
        if not self._save_timer.isActive():
            self._save_timer.start()

    def flush(self):
        self._save_timer.stop()
        self.save()

    # ---------- 查询 ----------
    def get_all(self):
        return [it["text"] for it in self.items]

    def get_groups(self):
        return list(self.groups)

    def items_for_group(self, group):
        return [it["text"] for it in self.items if it.get("group") == group]

    def items_for_emotion(self, emotion):
        """标签里包含某情绪的自定义颜文字（用于自动弹出按情绪推荐）。"""
        return [it["text"] for it in self.items if emotion in it.get("tags", [])]

    def tags_of(self, text):
        for it in self.items:
            if it["text"] == text:
                return list(it.get("tags", []))
        return []

    def search(self, query):
        q = (query or "").strip().lower()
        if not q:
            return self.get_all()
        out = []
        seen = set()
        for it in self.items:
            blob = it["text"].lower() + " " + " ".join(it.get("tags", [])).lower()
            if q in blob and it["text"] not in seen:
                seen.add(it["text"])
                out.append(it["text"])
        return out

    # ---------- 变更 ----------
    def add_item(self, text, group="默认", tags=None):
        text = str(text).strip()
        if not text:
            return
        group = group or "默认"
        if group not in self.groups:
            self.groups.append(group)
        tags = _norm_tags(tags)
        for it in self.items:
            if it["text"] == text:
                it["group"] = group
                it["tags"] = tags
                break
        else:
            self.items.append({"text": text, "group": group, "tags": tags})
        self._schedule_save()
        self.changed.emit()

    def update_item(self, old_text, new_text, group=None, tags=None):
        new_text = str(new_text).strip()
        if not new_text:
            return
        for it in self.items:
            if it["text"] == old_text:
                if group is not None:
                    it["group"] = group or "默认"
                    if it["group"] not in self.groups:
                        self.groups.append(it["group"])
                it["text"] = new_text
                if tags is not None:
                    it["tags"] = _norm_tags(tags)
                break
        self._schedule_save()
        self.changed.emit()

    def remove_item(self, text):
        before = len(self.items)
        self.items = [it for it in self.items if it["text"] != text]
        if len(self.items) != before:
            # 清理已无成员的空分组（保留「默认」）
            used = {it.get("group") for it in self.items}
            self.groups = [g for g in self.groups if g == "默认" or g in used]
            self._schedule_save()
            self.changed.emit()

    def add_group(self, name):
        name = str(name).strip()
        if name and name not in self.groups:
            self.groups.append(name)
            self._schedule_save()
            self.changed.emit()
            return True
        return False

    def remove_group(self, name):
        if name == "默认":
            return False
        if name not in self.groups:
            return False
        self.groups.remove(name)
        # 该分组下的条目回收到「默认」
        for it in self.items:
            if it.get("group") == name:
                it["group"] = "默认"
        self._schedule_save()
        self.changed.emit()
        return True
