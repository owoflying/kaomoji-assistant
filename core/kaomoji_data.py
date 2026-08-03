import json
import os
import unicodedata

from core.runtime import kaomoji_path

DATA_PATH = kaomoji_path()


def _is_valid_kaomoji(text):
    """载入时自查，剔除中文文本脏数据，但保留合法颜文字。

    核心判据：是否存在「连续 2 个及以上汉字」的片段。
      * 正常颜文字里即便用到汉字，也只是「益 / 皿」之类孤立单字当五官，
        绝不会两个汉字连在一起；
      * 而混入的中文文本（如「哦哦」、整句中文）天然是连续汉字串。
    因此：只要出现连续 2+ 汉字就判为脏数据丢弃；其余一律保留。
    半角片假名（｡ ヽ ﾉ 等）与符号属合法颜文字成分，不计入。
    """
    run = 0
    for ch in text:
        if _is_cjk(ch):
            run += 1
            if run >= 2:
                return False
        else:
            run = 0
    return True


def _is_cjk(ch):
    cp = ord(ch)
    # CJK 统一表意文字 + 扩展 A + 兼容汉字
    return (
        (0x4E00 <= cp <= 0x9FFF)
        or (0x3400 <= cp <= 0x4DBF)
        or (0xF900 <= cp <= 0xFAFF)
    )



class KaomojiData:
    def __init__(self, path=DATA_PATH):
        self.path = path
        self.categories = []
        self._all = []
        self.load()

    def load(self):
        with open(self.path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.categories = data.get("categories", [])
        self._all = []
        dropped = 0
        for cat in self.categories:
            clean_items = []
            for item in cat.get("items", []):
                if not _is_valid_kaomoji(item):
                    dropped += 1
                    continue
                if item not in clean_items:
                    clean_items.append(item)
            cat["items"] = clean_items
            for item in clean_items:
                if item not in self._all:
                    self._all.append(item)
        if dropped:
            # 载入时清掉脏数据，避免下次启动又读到
            self.save()

    def save(self):
        """把（已剔除脏数据的）分类写回磁盘；目录不存在则自动创建。"""
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(
                    {"categories": self.categories},
                    f, ensure_ascii=False, indent=2,
                )
        except Exception:
            pass

    def get_category_names(self):
        return [c.get("name", "") for c in self.categories]

    def get_items(self, category=None):
        if category is None:
            return list(self._all)
        for c in self.categories:
            if c.get("name") == category:
                return list(c.get("items", []))
        return []

    def search(self, query):
        q = (query or "").strip().lower()
        if not q:
            return list(self._all)
        return [k for k in self._all if q in k.lower()]
