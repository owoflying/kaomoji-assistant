"""用户自定义颜文字：增删改、分组、标签、搜索。

与只读的 data/kaomoji.json 完全分离，存于可写区（%APPDATA%/KaomojiAssistant
或源码态的项目 data/）。任何来源的数据都按用户意图原样保存，不做「连续汉字」
之类的脏数据过滤——那是只读库的自我保护，用户自己加的东西理应信任。
落盘用 QTimer 合并（与主程序一致的去抖思路），changed 信号供 UI / 自动弹出刷新。
"""
import json
import os
from datetime import datetime

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

    # ---------- 迁移：导入 / 导出 / 备份 / 排序 ----------
    def export_dict(self):
        """导出当前全部数据为可序列化字典（不含内部状态）。"""
        return {
            "groups": list(self.groups),
            "items": [dict(it) for it in self.items],
        }

    def import_data(self, groups, items, merge=True):
        """从外部数据导入（合并模式）。

        返回 (added, updated, skipped) 计数，供 UI 反馈。
        - 分组取并集，保持「默认」置首。
        - 条目按 text 匹配：已存在则更新 group/tags（保留原位置），不存在则追加。
          merge=False 时行为一致（仍只更新 group/tags），不删除现有条目。
        """
        added = updated = skipped = 0
        for g in (groups or []):
            g = str(g).strip()
            if g and g not in self.groups:
                self.groups.append(g)
        if "默认" in self.groups:
            self.groups.remove("默认")
            self.groups.insert(0, "默认")
        for it in (items or []):
            if not isinstance(it, dict):
                skipped += 1
                continue
            text = str(it.get("text", "")).strip()
            if not text:
                skipped += 1
                continue
            group = str(it.get("group", "默认")) or "默认"
            tags = _norm_tags(it.get("tags"))
            if group not in self.groups:
                self.groups.append(group)
            existing = next((x for x in self.items if x["text"] == text), None)
            if existing is not None:
                existing["group"] = group
                existing["tags"] = tags
                updated += 1
            else:
                self.items.append({"text": text, "group": group, "tags": tags})
                added += 1
        if added or updated:
            self._schedule_save()
            self.changed.emit()
        return added, updated, skipped

    def backup(self, dest_path=None):
        """把当前数据备份到 dest_path；省略则写到数据目录下的时间戳文件。"""
        if dest_path is None:
            ts = datetime.now().strftime("%Y%m%d-%H%M%S")
            dest_path = os.path.join(
                os.path.dirname(self.path),
                "user_kaomoji.backup-%s.json" % ts,
            )
        try:
            os.makedirs(os.path.dirname(dest_path), exist_ok=True)
            with open(dest_path, "w", encoding="utf-8") as f:
                json.dump(self.export_dict(), f, ensure_ascii=False, indent=2)
        except Exception:
            return None
        return dest_path

    def move_item(self, dragged_text, target_text):
        """在 self.items 中将 dragged 移到 target 之前，保持其它顺序。

        用于列表拖拽重排：拖起项放到目标项上方即视为"插到它前面"。
        """
        if not dragged_text or dragged_text == target_text:
            return
        items = self.items
        dragged = None
        for i, it in enumerate(items):
            if it["text"] == dragged_text:
                dragged = items.pop(i)
                break
        if dragged is None:
            return
        if target_text is None:
            items.append(dragged)
        else:
            idx = next((i for i, it in enumerate(items) if it["text"] == target_text), len(items))
            items.insert(idx, dragged)
        self._schedule_save()
        self.changed.emit()
