import json
import os
import unicodedata

from core.runtime import kaomoji_path

DATA_PATH = kaomoji_path()


def _is_valid_kaomoji(text):
    """颜文字不应包含任何中文字符（CJK 统一表意文字）。

    用于载入时自查：任何混入中文（如测试残留、手改 JSON 出错、从别处
    复制时夹带的“哦哦”之类）的条目一律丢弃，绝不上屏成为候选。
    半角片假名（｡ ヽ ﾉ 等）与符号是合法颜文字成分，放行。
    """
    for ch in text:
        if unicodedata.category(ch).startswith("Lo") and _is_cjk(ch):
            return False
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
