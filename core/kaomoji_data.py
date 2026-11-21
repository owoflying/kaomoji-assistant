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
        #: 条目 → 所属分类名集合（**反向索引**）。
        #:
        #: ## 为什么要建索引
        #:
        #: 搜索时每条颜文字都要判断"它属不属于命中的那个分类"。
        #: 原来用 `_in_category()` 逐个分类去列表里线性查找，
        #: 复杂度 O(条目数 × 分类数 × 分类长度)，实测一次搜索 30ms。
        #: 用户每敲一个字符就搜一次，打字明显发涩。
        #:
        #: 建好反向索引后降到 O(条目数)，一次约 3ms。
        self._cat_index = {}
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
        # 外部分类会改变"某条属于哪些分类"，索引必须重建
        self._rebuild_category_index()

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
            self._cat_index = {}
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
        # 建反向索引（见 _cat_index 的说明）
        self._rebuild_category_index()
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

        ## 单字符查询的特殊处理

        实测：搜 `e` 会命中 283 条（几乎整个库），因为大量颜文字
        含 ASCII 字母。这等于没搜。

        规则：**单个字符只做"分类名/别名"和"开头匹配"**，不做
        全文包含。这样 `心` 能出"喜欢"类整类，`e` 只出个别以它
        开头的，结果数量落到可浏览的范围。

        两个字符以上才按完整评分（含子串包含）走。

        ## 评分（从高到低）

          100  完全等于某个颜文字（用户直接打出了它）
           90  颜文字本身包含关键词
           80  某条别名完全等于关键词（如 happy / kx）
           70  某个分类名/分类别名完全等于关键词（如 开心）
           60  别名以关键词开头
           50  分类名包含关键词
           40  某个别名包含关键词
           30  模糊匹配（错字容错，编辑距离 ≤1）
        """
        pool = self.get_items()
        q = (query or "").strip().lower()
        if not q:
            return pool

        # 单字符：只认可信的匹配，避免整库泛滥
        single = len(q) == 1

        scored = []
        for idx, text in enumerate(pool):
            score = self._score(text, q, single=single)
            if score > 0:
                # 同分时保持原顺序（索引做次级键），结果稳定
                scored.append((score, -idx, text))
        scored.sort(reverse=True)
        results = [t for _s, _i, t in scored]

        # 否定查询兜底："不开心" 不该返回 0 条，它是有明确意图的搜索。
        #
        # 做法：把否定词剥掉再搜一次，但结果**整体降权**（排到肯定结果后面）。
        # 用户真想找"不开心"的表情，看到伤心类总比看到空白好。
        if not results:
            results = self._search_negated(q)
        return results

    def _search_negated(self, q):
        """否定查询的兜底：剥掉否定词再搜，并补上对立分类。

        "不开心" → 搜"开心" → 顺便把"伤心"类也给出来。

        ⚠ **不能直接调 `self.search()`**：那个方法在结果为空时又会回调
        本方法，形成无限递归（实测 RecursionError）。这里改成直接调
        单条打分的 `_score()`，绕开兜底逻辑。
        """
        stripped = str(q or "")
        for neg in _NEGATIONS:
            stripped = stripped.replace(neg, "")
        stripped = stripped.strip()
        if not stripped:
            return []

        # 直接打分，不走 search() 的兜底分支
        base_scored = []
        single = len(stripped) == 1
        for idx, text in enumerate(self.get_items()):
            s = self._score(text, stripped, single=single)
            if s > 0:
                base_scored.append((s, -idx, text))
        if not base_scored:
            return []
        base_scored.sort(reverse=True)
        base = [t for _s, _i, t in base_scored]

        # 找出这次命中的分类，把它们对立类的条目提到前面
        hit_cats = set()
        for text in base[:40]:
            hit_cats.update(self.categories_of(text))

        extra = []
        seen = set(base)
        for cat in hit_cats:
            for opp in _OPPOSITES.get(cat, ()):
                for text in self.get_items(opp):
                    if text not in seen:
                        seen.add(text)
                        extra.append(text)
        # 对立类放前面：搜"不开心"时用户想要的就是难过那一类
        return extra + base

    def _score(self, text, q, single=False):
        """给单条颜文字打分；0 表示不匹配。

        :param single: 是否单字符查询。为 True 时跳过"子串包含"和
            模糊匹配——这两条在单字符下会把整个库都扫进来。
        """
        low = text.lower()
        if low == q:
            return 100

        im = self.meta.get(text) or {}
        aliases = [a.lower() for a in im.get("aliases", [])]

        if not single:
            if q in low:
                # 命中得越靠前、占比越大越相关
                return 90 + (1.0 - low.index(q) / max(1, len(low))) * 0.9

        if q in aliases:
            return 80
        if any(a.startswith(q) for a in aliases):
            return 60
        # ⚠ 同样只做单向包含（q in a）。
        #
        # 反向（a in q）会把 "unhappy" 当成命中 "happy"——搜开心结果
        # 里混进一堆伤心的，是最招人烦的误命中之一。
        if not single and any(q in a for a in aliases
                              if not _is_negated(a, q)):
            return 40

        # 分类级：命中的话该分类下所有条目都相关
        for name, cm in self.cat_meta.items():
            if not self._in_category(text, name):
                continue
            cat_aliases = [a.lower() for a in cm.get("aliases", [])]
            low_name = (name or "").lower()
            if low_name and q == low_name:
                return 70
            if q in cat_aliases:
                return 70
            # 用户输入里包含完整分类名（"开心快乐" → 命中"开心"）
            #
            # ⚠ 但否定形式不算：「不开心」不该命中"开心"类。
            # 颜文字的表情语义由否定词直接反转，这是最伤体验的误命中之一。
            if low_name and low_name in q and not _is_negated(q, low_name):
                return 50
            # ⚠ 这里**只做单向包含**（q in a），不做 a in q。
            #
            # 反向包含会误命中："happy" 是"伤心"类别名 "unhappy" 的子串，
            # 双向匹配会把整个伤心类的 24 条全捞进"happy"的结果里。
            # 用户搜 happy 期望看到开心的，结果一半是难过的。
            #
            # 还要再排除否定前缀：`q in a` 对 "happy" in "unhappy" 成立，
            # 但语义相反，所以用 `_is_negated` 过滤。
            if not single and any(
                    q in a and not _is_negated(a, q)
                    for a in cat_aliases):
                return 50

        # 单字符不模糊匹配：文本只有 2~3 个字符时，编辑距离 1
        # 几乎等于"任意字符"，全是噪声
        if not single and len(q) >= 3:
            for a in aliases:
                if abs(len(a) - len(q)) <= 1 and _edit_distance(a, q) <= 1:
                    return 30
        return 0

    def _rebuild_category_index(self):
        """重建「条目 → 分类名集合」的反向索引（见 `_cat_index` 的说明）。

        任何改动分类内容的地方（load / set_external_categories）都要调用。
        """
        idx = {}
        for c in self.categories:
            name = c.get("name", "")
            for text in (c.get("items") or []):
                idx.setdefault(text, set()).add(name)
        for c in self.external:
            name = c.get("name", "")
            for text in (c.get("items") or []):
                idx.setdefault(text, set()).add(name)
        self._cat_index = idx

    def _in_category(self, text, category):
        """这条颜文字是否属于该分类（走反向索引，O(1)）。"""
        return category in (self._cat_index.get(text) or ())

    def categories_of(self, text):
        """这条颜文字属于哪些分类（搜索结果的分类标注用）。"""
        return sorted(self._cat_index.get(str(text)) or ())

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


#: 否定词：出现在关键词前面时，语义反转。
#:
#: 用途见 `_is_negated`。只收常见单字/双字否定，太长反而误伤
#: （比如"不仅"后面接的往往是肯定内容）。
_NEGATIONS = ("不", "没", "无", "别", "非", "未", "勿")

#: 英文否定前缀。`unhappy` 里的 `happy` 是"不开心"，不该算命中"开心"。
#:
#: ⚠ 只收明确的否定/相反前缀，不要收 `re`、`pre` 这类中性前缀——
#: 那会把 reply 当成"ply"的否定，误伤正常词。
_EN_NEG_PREFIXES = ("un", "dis", "non", "anti", "in", "im", "ir", "il")

#: 分类的对立关系。用于否定查询（"不开心" → 顺便给出伤心类）。
#:
#: 只收语义确实相反的成对分类。不必对称完备——给不出的就返回空，
#: 不要硬凑（比如"动物"没有对立面）。
_OPPOSITES = {
    "开心": ("伤心",),
    "伤心": ("开心",),
    "喜欢": ("无语", "生气"),
    "生气": ("喜欢", "开心"),
    "得意": ("害羞", "无语"),
    "害羞": ("得意",),
    "加油": ("疲惫",),
    "疲惫": ("加油",),
    "晚安": ("打招呼",),
    "打招呼": ("告别",),
    "告别": ("打招呼",),
    "感谢": ("道歉",),
    "道歉": ("感谢",),
    "庆祝": ("伤心",),
}


def _is_negated(query, word):
    """`word` 在 `query` 里是否被否定了（如「不开心」里的「开心」）。

    两套判据：

    * **中文**：看 `word` 出现位置前面 1~2 个字符是不是否定词。
      中文的否定词都紧贴在被否定的词前面；离得远的否定
      （"不开心，但是今天还好"）不算，否则整句意图都被带偏。
    * **英文**：看 `word` 前面是不是否定前缀（`unhappy` → `happy` 被否定）。
    """
    q = str(query or "")
    w = str(word or "")
    if not q or not w:
        return False
    start = 0
    while True:
        idx = q.find(w, start)
        if idx < 0:
            return False
        # 中文：前面最多两个字符
        prefix = q[max(0, idx - 2):idx]
        if any(neg in prefix for neg in _NEGATIONS):
            return True
        # 英文：紧邻的前缀
        if idx > 0:
            head = q[max(0, idx - 3):idx].lower()
            for p in _EN_NEG_PREFIXES:
                if head.endswith(p):
                    return True
        start = idx + len(w)


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
