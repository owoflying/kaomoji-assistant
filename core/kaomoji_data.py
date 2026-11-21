import json
import os

from core.applog import append_log
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
        # 插件等外部来源提供的分类：只读合并进查询视图，绝不写入 kaomoji.json。
        # 结构与 self.categories 相同：[{"name": str, "items": [str, ...]}, ...]
        self.external = []
        #: 条目 → 元数据（别名/场景）。结构见 `_normalize_categories`。
        #: key 是颜文字文本，值是 {"aliases": [...], "scenes": [...]}
        self.meta = {}
        #: 分类名 → 分类级元数据（别名/场景）
        self.cat_meta = {}
        self.load()

    # ---------- 外部来源（插件） ----------
    def set_external_categories(self, categories):
        """整体替换外部分类（插件启用/停用/重载后由 PluginManager 调用）。

        逐条做与内置库相同的脏数据自查，插件提供的中文句子等脏数据同样被剔除。
        本方法只改内存视图：save() 永远只写 self.categories（内置库），
        因此插件数据不会污染随包分发的 kaomoji.json。
        """
        ext = []
        seen_names = {c.get("name") for c in self.categories}
        for c in categories or []:
            name = str((c or {}).get("name") or "").strip()
            if not name or name in seen_names:
                continue
            items = []
            for item in (c or {}).get("items", []) or []:
                # 插件也可能用带元数据的写法，统取文本
                text = item.get("text") if isinstance(item, dict) else item
                text = str(text)
                if not _is_valid_kaomoji(text) or text in items:
                    continue
                items.append(text)
            if items:
                ext.append({"name": name, "items": items})
                seen_names.add(name)
        self.external = ext

    @staticmethod
    def _normalize_categories(raw_categories):
        """把原始分类数据归一成（字符串列表 + 元数据索引）。

        ## 为什么需要

        库文件同时支持两种条目写法，要向后兼容：

            "items": ["(＾▽＾)", ...]                      ;; 纯字符串（旧版）
            "items": [{"text": "(＾▽＾)",
                       "aliases": ["happy"],
                       "scenes": ["greeting"]}, ...]      ;; 带元数据（新版）

        插件数据也只可能是字符串。所以这里统一：
          * 返回分类列表，items 一律是字符串（旧行为不变）；
          * 另返回两份元数据索引，供搜索/推荐使用。
        """
        out = []
        item_meta = {}
        cat_meta = {}
        for cat in raw_categories or []:
            name = (cat or {}).get("name", "")
            aliases = [str(a) for a in ((cat or {}).get("aliases") or [])]
            scenes = [str(s) for s in ((cat or {}).get("scenes") or [])]
            if aliases or scenes:
                cat_meta[name] = {"aliases": aliases, "scenes": scenes}

            texts = []
            for item in (cat or {}).get("items", []) or []:
                if isinstance(item, dict):
                    text = str(item.get("text") or "")
                    if not text:
                        continue
                    a = [str(x) for x in (item.get("aliases") or [])]
                    s = [str(x) for x in (item.get("scenes") or [])]
                    if a or s:
                        item_meta.setdefault(
                            text, {"aliases": [], "scenes": []})
                        item_meta[text]["aliases"].extend(a)
                        item_meta[text]["scenes"].extend(s)
                else:
                    text = str(item)
                texts.append(text)
            out.append({
                "name": name,
                "aliases": aliases,
                "scenes": scenes,
                "items": texts,
            })
        return out, item_meta, cat_meta

    def load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            # 只读库缺失 / JSON 损坏时，绝不能让整个程序起不来（本方法在启动装配期
            # 被调用，抛异常等于关掉 AppRuntime）。留空库 + 记日志即可。
            # 注意此处**不回写磁盘**：覆盖会把用户还可能手工修复的原文件清空。
            self.categories = []
            self._all = []
            append_log("error", "data", "颜文字库读取失败（%s）：%r" % (self.path, e))
            return
        norm, item_meta, cat_meta = self._normalize_categories(
            data.get("categories") or [])
        self.categories = norm
        self.meta = item_meta
        self.cat_meta = cat_meta
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
        """把（已剔除脏数据的）分类写回磁盘；目录不存在则自动创建。

        ⚠ 元数据（别名/场景）必须一起写回。只写 items 会把精心维护的
        别名和场景标签全部丢掉，下次启动搜索就退化成纯子串匹配。
        """
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            out = []
            for c in self.categories:
                name = c.get("name", "")
                entry = {"name": name}
                cm = self.cat_meta.get(name) or {}
                if cm.get("aliases"):
                    entry["aliases"] = cm["aliases"]
                if cm.get("scenes"):
                    entry["scenes"] = cm["scenes"]
                items = []
                for text in c.get("items", []):
                    im = self.meta.get(text) or {}
                    if im.get("aliases") or im.get("scenes"):
                        d = {"text": text}
                        if im.get("aliases"):
                            d["aliases"] = im["aliases"]
                        if im.get("scenes"):
                            d["scenes"] = im["scenes"]
                        items.append(d)
                    else:
                        items.append(text)
                entry["items"] = items
                out.append(entry)
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump({"categories": out}, f, ensure_ascii=False,
                          indent=2)
        except Exception as e:
            append_log("warn", "data", "颜文字库写盘失败（%s）：%r" % (self.path, e))

    def get_category_names(self):
        names = [c.get("name", "") for c in self.categories]
        # 外部（插件）分类追加在内置分类之后；同名已在上游去重
        names.extend(c["name"] for c in self.external)
        return names

    def get_items(self, category=None):
        if category is None:
            out = list(self._all)
            seen = set(out)
            for c in self.external:
                for item in c["items"]:
                    if item not in seen:
                        seen.add(item)
                        out.append(item)
            return out
        for c in self.categories:
            if c.get("name") == category:
                return list(c.get("items", []))
        for c in self.external:
            if c["name"] == category:
                return list(c["items"])
        return []

    def search(self, query):
        """按关键词搜索颜文字。

        评分从高到低：
          100  完全等于某个颜文字（用户直接打出了它）
           90  颜文字本身包含关键词
           80  某条别名完全等于关键词（如 happy / kx）
           70  某个分类名完全等于关键词（如 开心）
           60  别名以关键词开头
           50  分类名包含关键词
           40  某个别名包含关键词
           30  模糊匹配（错字容错，编辑距离 ≤1）
        """
        pool = self.get_items()
        q = (query or "").strip().lower()
        if not q:
            return pool

        scored = []
        for idx, text in enumerate(pool):
            score = self._score(text, q)
            if score > 0:
                # 同分时保持原顺序（索引做次级键），结果稳定
                scored.append((score, -idx, text))
        scored.sort(reverse=True)
        return [t for _s, _i, t in scored]

    def _score(self, text, q):
        """给单条颜文字打分；0 表示不匹配。"""
        low = text.lower()
        if low == q:
            return 100
        if q in low:
            # 命中得越靠前、占比越大越相关
            return 90 + (1.0 - low.index(q) / max(1, len(low))) * 0.9

        im = self.meta.get(text) or {}
        aliases = [a.lower() for a in im.get("aliases", [])]
        if q in aliases:
            return 80
        if any(a.startswith(q) for a in aliases):
            return 60
        if any(q in a for a in aliases):
            return 40

        # 分类级别名：命中的话该分类下所有条目都相关
        for name, cm in self.cat_meta.items():
            cat_aliases = [a.lower() for a in cm.get("aliases", [])]
            if not self._in_category(text, name):
                continue
            if name and q == name.lower():
                return 70
            if q in cat_aliases:
                return 70
            # 中文关键词按字命中分类名（"睡觉"→"晚安/疲惫"需靠别名，
            # 但"开心"这种直接命中分类名的要算）
            if name and (q in name.lower() or name.lower() in q):
                return 50
            if any(q in a or a in q for a in cat_aliases):
                return 50

        # 错字容错：与某个别名编辑距离 ≤1（关键词至少 3 字符才做，
        # 否则 "hi" 会匹配到一堆两字别名）
        if len(q) >= 3:
            for a in aliases:
                if abs(len(a) - len(q)) <= 1 and _edit_distance(a, q) <= 1:
                    return 30
        return 0

    def _in_category(self, text, category):
        for c in self.categories:
            if c.get("name") == category:
                return text in (c.get("items") or [])
        for c in self.external:
            if c.get("name") == category:
                return text in (c.get("items") or [])
        return False

    def by_scene(self, scene, limit=0):
        """取某个场景下的所有颜文字（用于"按场景推荐"）。

        场景同时来自「分类级 scenes」和「条目的 scenes」，取并集。
        """
        want = (scene or "").strip().lower()
        if not want:
            return []
        out = []
        seen = set()
        for name, cm in self.cat_meta.items():
            if want in [s.lower() for s in cm.get("scenes", [])]:
                for text in self.get_items(name):
                    if text not in seen:
                        seen.add(text)
                        out.append(text)
        for text in self.get_items():
            im = self.meta.get(text) or {}
            if want in [s.lower() for s in im.get("scenes", [])]:
                if text not in seen:
                    seen.add(text)
                    out.append(text)
        return out[:limit] if limit else out

    def all_scenes(self):
        """库里出现过的所有场景名（供界面列出可选项）。"""
        names = set()
        for cm in self.cat_meta.values():
            names.update(s.lower() for s in cm.get("scenes", []))
        for im in self.meta.values():
            names.update(s.lower() for s in im.get("scenes", []))
        return sorted(names)

    def categories_of(self, text):
        """这条颜文字属于哪些分类（搜索结果的分类标注用）。"""
        out = []
        for c in self.categories:
            if text in (c.get("items") or []):
                out.append(c.get("name", ""))
        for c in self.external:
            if text in (c.get("items") or []):
                out.append(c.get("name", ""))
        return out


def _edit_distance(a, b, cap=2):
    """Levenshtein 距离，超过 cap 就提前返回（省时间）。

    只用于"用户打错一个字"的容错，不需要精确值。
    """
    if a == b:
        return 0
    if abs(len(a) - len(b)) > cap:
        return cap + 1
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        best = i
        for j, cb in enumerate(b, 1):
            cur.append(min(
                prev[j] + 1,          # 删除
                cur[j - 1] + 1,       # 插入
                prev[j - 1] + (ca != cb),   # 替换
            ))
            best = min(best, cur[-1])
        if best > cap:
            return cap + 1
        prev = cur
    return prev[-1]
