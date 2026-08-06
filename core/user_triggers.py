"""用户快捷短语：触发词 -> 输出。

打字时若焦点文本尾部出现某个触发词，就在候选条给出该「特定输出」
（复用现有自动弹出 -> 候选条上屏的链路，保持一致的交互）。

匹配规则（v1，力求简单可靠）：
  * 多个触发词都命中时，取「最长」的那个（更具体的优先）；
  * 不强制词边界——中文本身没有空白分词，substring 最直观；
    代价是可能在某些词内误命中，但用户自定义的词通常短且特异，实践中影响很小。
落盘同样走 QTimer 去抖，changed 信号供 UI 刷新。
"""
import json
import os

from PySide6.QtCore import QObject, Signal, QTimer

from core.runtime import user_triggers_path


class UserTriggers(QObject):
    changed = Signal()

    def __init__(self, path=None):
        super().__init__()
        self.path = path or user_triggers_path()
        self.items = []          # [{"trigger": str, "output": str}]
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(400)
        self._save_timer.timeout.connect(self.save)
        self.load()

    def load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.items = [
                it for it in (data.get("items") or [])
                if isinstance(it, dict) and it.get("trigger") and it.get("output")
            ]
            # 兼容历史数据：旧条目没有 delete_trigger 字段，补默认 False（不删除）。
            # 旧行为即“应用后保留触发词”，保持原有逻辑不变，仅新条目可显式开启。
            for it in self.items:
                if not isinstance(it.get("delete_trigger"), bool):
                    it["delete_trigger"] = False
        except Exception:
            self.items = []

    def save(self):
        self._save_timer.stop()
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump({"items": self.items}, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _schedule_save(self):
        if not self._save_timer.isActive():
            self._save_timer.start()

    def flush(self):
        self._save_timer.stop()
        self.save()

    def get_all(self):
        return [dict(it) for it in self.items]

    def match(self, text):
        """返回尾部命中触发词对应的条目 dict（含 trigger/output/delete_trigger）；无命中返回 None。

        取最长匹配，避免「k」先命中而「kk」后到的短词误触发。delete_trigger 表示
        该短语「应用后是否删除触发词」，供上屏逻辑决定是否清理用户刚输入的触发词。
        """
        if not text or not self.items:
            return None
        best = None
        for it in self.items:
            trig = it.get("trigger", "")
            if trig and trig in text:
                if best is None or len(trig) > len(best["trigger"]):
                    best = it
        if best is None:
            return None
        return {
            "trigger": best["trigger"],
            "output": best["output"],
            "delete_trigger": bool(best.get("delete_trigger", False)),
        }

    def add(self, trigger, output, delete_trigger=False):
        trigger = str(trigger).strip()
        output = str(output).strip()
        delete_trigger = bool(delete_trigger)
        if not trigger or not output:
            return
        for it in self.items:
            if it["trigger"] == trigger:
                it["output"] = output
                it["delete_trigger"] = delete_trigger
                break
        else:
            self.items.append({
                "trigger": trigger, "output": output,
                "delete_trigger": delete_trigger,
            })
        self._schedule_save()
        self.changed.emit()

    def remove(self, trigger):
        before = len(self.items)
        self.items = [it for it in self.items if it["trigger"] != trigger]
        if len(self.items) != before:
            self._schedule_save()
            self.changed.emit()
