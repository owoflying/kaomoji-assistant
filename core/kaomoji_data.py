import json
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_PATH = os.path.join(BASE_DIR, "data", "kaomoji.json")


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
        for cat in self.categories:
            for item in cat.get("items", []):
                if item not in self._all:
                    self._all.append(item)

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
