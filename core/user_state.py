"""最近使用 / 收藏夹 / 使用统计的持久化（存储于 data/user_state.json）。

## 排序策略（2026-11 改）

历史上候选条按「最近使用」排序，但实测用户真实数据会发现：

    (｡･ω･｡)   用过 4 次  →  排第 5 位
    (☆*:...)   用过 2 次  →  排第 1 位

用户每次唤起都要往下翻才能选到**最常用**的那个。所以改成
「频率 + 新近度」的加权评分（见 `score()`），收藏还有额外加成。

这不是机器学习，就是一条能解释、能调参的简单评分公式——
但实测比"只按时间排"更贴近人的使用习惯。
"""
import json
import os
import time

from PySide6.QtCore import QObject, Signal, QTimer

from core.runtime import state_path

STATE_PATH = state_path()

#: 评分权重（调参就改这里）
#:
#: 直觉：
#:   * 频率是主信号——用得多的说明我喜欢；
#:   * 新近度是辅助——刚用过的那几个说明当前语境合适；
#:   * 收藏是"我明确说过它重要"，直接加固定分，压过高频项；
#:   * 对数压缩频率，避免某个用过 200 次的项把其它全挤下去。
WEIGHT_FREQ = 1.0
WEIGHT_RECENT = 0.55
WEIGHT_FAVORITE = 2.5

#: 新近度半衰位置：最近列表里第 N 个之后贡献接近 0
RECENT_DECAY = 6.0

#: 时段习惯加成权重。
#:
#: 直觉：早上爱用"早安"、深夜爱用"困了"——同一批颜文字在不同时段
#: 的使用倾向不同。统计「该时段内用过几次」并归一化，作为额外加分。
#: 权重比收藏低（收藏是明确的用户意图），比新近度高（时段是长期规律）。
WEIGHT_HOUR = 0.9

#: 时段环境加成权重：推荐时如果条目属于当前时段场景，额外加分。
WEIGHT_SCENE = 0.8

#: 时段分桶：把 24 小时压成 6 个有语义的桶。
#:
#: 不分 24 个桶是因为数据太稀疏——一天用不了几次，按小时统计
#: 每个桶平均不到 1 次，噪声比信号大。6 个桶足够区分
#: "清晨/上午/午后/傍晚/夜晚/深夜"这几种典型语境。
HOUR_BUCKETS = (
    ("dawn", 5, 8),       # 05:00–08:59 清晨
    ("morning", 9, 11),   # 09:00–11:59 上午
    ("noon", 12, 13),     # 12:00–13:59 午后
    ("afternoon", 14, 17),  # 14:00–17:59 下午
    ("evening", 18, 22),  # 18:00–22:59 夜晚
    ("night", 23, 4),     # 23:00–04:59 深夜
)


def hour_bucket(ts=None):
    """把时间戳（默认现在）映射成时段桶名。"""
    import datetime

    h = (datetime.datetime.fromtimestamp(ts) if ts is not None
         else datetime.datetime.now()).hour
    for name, lo, hi in HOUR_BUCKETS:
        if lo <= hi:
            if lo <= h <= hi:
                return name
        elif h >= lo or h <= hi:      # 跨零点的桶
            return name
    return "morning"


#: 时段桶 → 对应的时间场景名（与 kaomoji.json 里的 scenes 对齐）
BUCKET_SCENE = {
    "dawn": "greeting",
    "morning": "greeting",
    "noon": "",
    "afternoon": "",
    "evening": "farewell",
    "night": "night",
}


def _log1p(x):
    """log(1+x)，对 x<=0 安全返回 0。

    用对数压缩频率：用过 1 次与 2 次的差距（0.69 vs 1.10）比
    20 次与 21 次的差距（3.04 vs 3.09）大得多——这符合直觉，
    也避免某个用过几百次的项把其它全部挤到后面。
    """
    try:
        x = float(x)
    except (TypeError, ValueError):
        return 0.0
    if x <= 0:
        return 0.0
    import math
    return math.log1p(x)


class UserState(QObject):
    """记录最近使用与收藏夹，并在变更时发出 changed 信号。

    valid_items: 可选的合法颜文字集合。传入后会启用「自净」——
      载入与写入时都会丢弃不在库中的条目。这样任何来源的脏数据
      （测试脚本误写、手改 JSON 出错、旧版本残留）都不会出现在候选条里。
    """

    changed = Signal()

    def __init__(self, path=STATE_PATH, max_recent=30, valid_items=None,
                 max_history=100):
        super().__init__()
        self.path = path
        self.max_recent = max_recent
        self.max_history = int(max_history)
        self.valid_items = set(valid_items) if valid_items else None
        self.recent = []
        self.favorites = []
        self.usage = {}           # {text: 累计插入次数}，用于使用统计
        self.last_used = {}       # {text: 最后使用时间戳}，用于新近度衰减
        #: 注入流水：[{"text", "ts", "source"}, ...]，最新的在前。
        #: 与 recent 不同：同一条可以重复出现（见 add_history 的说明）。
        self.history = []
        #: {时段桶: {text: 次数}}——用于「时段习惯」推荐。
        #: 与 usage 分开存：usage 是总量，这里是分布（某条在深夜用得多
        #: 不代表它总量就多）。
        self.hour_usage = {}
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
            raw_last = data.get("last_used") or {}
            raw_hour = data.get("hour_usage") or {}
            raw_hist = data.get("history") or []
        except Exception:
            self.recent = []
            self.favorites = []
            self.usage = {}
            self.last_used = {}
            self.hour_usage = {}
            self.history = []
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
        # 最后使用时间（旧版本没有这个字段，缺失时按空处理即可）
        last = {}
        for k, v in raw_last.items():
            try:
                last[str(k)] = float(v)
            except (TypeError, ValueError):
                pass
        if self.valid_items is not None:
            last = {k: t for k, t in last.items() if k in self.valid_items}
        self.last_used = last
        # 时段统计：{bucket: {text: int}}，脏数据逐层过滤
        hour = {}
        if isinstance(raw_hour, dict):
            for bucket, m in raw_hour.items():
                if not isinstance(m, dict):
                    continue
                clean = {}
                for k, v in m.items():
                    try:
                        key = str(k)
                        cnt = int(v)
                    except (TypeError, ValueError):
                        continue
                    if cnt <= 0:
                        continue
                    if self.valid_items is not None and key not in self.valid_items:
                        continue
                    clean[key] = cnt
                if clean:
                    hour[str(bucket)] = clean
        self.hour_usage = hour
        # 注入历史：逐条校验，丢掉结构不对或不在库里的
        hist = []
        for rec in raw_hist:
            if not isinstance(rec, dict):
                continue
            t = str(rec.get("text") or "")
            if not t or not self._accepts(t):
                continue
            try:
                ts = float(rec.get("ts") or 0)
            except (TypeError, ValueError):
                ts = 0.0
            hist.append({"text": t, "ts": ts,
                         "source": str(rec.get("source") or "")})
            if len(hist) >= self.max_history:
                break
        self.history = hist
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
                     "usage": self.usage, "last_used": self.last_used,
                     "hour_usage": self.hour_usage,
                     "history": self.history},
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
        """记录一次颜文字插入，累加计数（不区分来源，统计所有插入）。

        同时记到当前时段桶里，供「时段习惯」推荐使用。
        """
        text = str(text)
        if not text:
            return
        self.usage[text] = self.usage.get(text, 0) + 1
        self.last_used[text] = time.time()
        bucket = hour_bucket()
        m = self.hour_usage.setdefault(bucket, {})
        m[text] = m.get(text, 0) + 1
        self._schedule_save()

    def hour_affinity(self, text, bucket=None):
        """某条颜文字在当前时段的偏好强度（0~1）。

        算法：该时段内它的次数 / 该时段内最高次数。
        归一化而不是直接用次数——否则用得多的人所有项分数都高，
        时段偏好就淹没在总量里了。
        """
        b = bucket if bucket is not None else hour_bucket()
        m = self.hour_usage.get(b) or {}
        if not m:
            return 0.0
        cnt = m.get(str(text), 0)
        if cnt <= 0:
            return 0.0
        top = max(m.values())
        return cnt / top if top > 0 else 0.0

    def top_usage(self, n=10):
        """返回使用次数最多的前 n 个 (text, count)，降序。"""
        return sorted(self.usage.items(), key=lambda kv: kv[1], reverse=True)[:n]

    def total_inserts(self):
        """累计插入总次数。"""
        return sum(self.usage.values())

    # ---------- 推荐排序（A：混合评分） ----------
    def score(self, text, now=None, hour_bucket_name=None):
        """给一个条目打分：频率 + 新近度 + 收藏 + 时段习惯。

        公式（各项都归一到 ~0..1 量级，便于调权重）::

            freq  = log1p(使用次数)                       # 对数压缩，防长尾压制
            rec   = 1 / (1 + 最近列表中的位次 / RECENT_DECAY)   # 位次越靠前越高
            hour  = 该时段次数 / 该时段最高次数                 # 时段偏好强度
            score = W_FREQ*freq + W_RECENT*rec + W_HOUR*hour
                    (+ W_FAVORITE 若收藏)

        **为什么不用神经网络**：关键词/排序这类问题，可解释的加权公式
        在数据量少（几十条）时表现更稳，也没有推理开销和模型文件。
        真要学个性化偏好，用这条公式的权重就够了。

        **为什么时段项能起作用**：早上常用"早安"、深夜常用"困了"，
        时间长了这些规律会体现在 hour_usage 里。权重比收藏低
        （收藏是明确意图），比新近度高（时段是长期规律，不是一时手滑）。

        :param now: 预留给时间衰减（当前用"最近列表位次"近似）
        :param hour_bucket_name: 指定时段桶（测试用；默认按当前时间）
        """
        text = str(text)
        count = self.usage.get(text, 0)
        freq = _log1p(count)

        # 新近度：在 recent 列表里的位次（越靠前越高）。不在列表里则为 0。
        rec = 0.0
        try:
            idx = self.recent.index(text)
        except ValueError:
            idx = -1
        if idx >= 0:
            rec = 1.0 / (1.0 + idx / RECENT_DECAY)

        aff = self.hour_affinity(text, hour_bucket_name)

        s = (WEIGHT_FREQ * freq + WEIGHT_RECENT * rec
             + WEIGHT_HOUR * aff)
        if text in self.favorites:
            s += WEIGHT_FAVORITE
        return s

    def rank(self, items, now=None):
        """按 `score()` 降序排列；同分时保持输入顺序（稳定排序）。

        这一步取代了原先的"最近优先"和情绪推荐里的 `random.shuffle`：
        每次弹出顺序稳定，用户才能形成肌肉记忆——第 1 个永远是我最常用的那个。
        """
        seq = [str(x) for x in items]
        # sorted 是稳定排序：同分项保持原相对顺序
        return sorted(seq, key=lambda t: -self.score(t, now))

    def current_scene(self):
        """当前时段对应的推荐场景名（空串表示这个时段没有特定场景）。"""
        return BUCKET_SCENE.get(hour_bucket(), "")

    # ---------- 注入历史 ----------
    def add_history(self, text, source=""):
        """记一条注入历史。

        ## 与 recent 的区别

        * `recent` 是**集合语义**：同一条只保留一份，重排到最前；
          它回答"我常用哪些"。
        * `history` 是**流水语义**：同一条可以连续出现多次；
          它回答"我最近依次用了什么"，支持"刚才那个再来一次"。

        所以不能合并——用户可能连着三次插入同一个颜文字，
        recent 里只有一条，但历史里是三条，这个区别有意义。
        """
        text = str(text or "")
        if not text:
            return
        # 与 recent 同一套自净规则：不在库里的条目一律不落盘。
        # 否则脏数据（测试残留、插件停用后消失的条目）会一直留在历史里。
        if not self._accepts(text):
            return
        self.history.insert(0, {"text": text, "ts": time.time(),
                                "source": str(source)})
        if len(self.history) > self.max_history:
            del self.history[self.max_history:]
        self._schedule_save()

    def last_injected(self, skip=0):
        """取最近一次注入的文本；没有则返回空串。

        :param skip: 跳过几条（0=最近一条，1=倒数第二条…）
        """
        if 0 <= skip < len(self.history):
            return self.history[skip].get("text", "")
        return ""

    def recent_injected(self, n=20):
        """最近的注入记录（含时间与来源），供"历史"面板展示。"""
        return list(self.history[:n])

    def clear_history(self):
        self.history = []
        self._schedule_save()
        self.changed.emit()

    def summarize_habits(self):
        """给界面用的一句话总结：我在什么时候爱用什么。

        返回 [(bucket, [(text, count), ...]), ...]，按桶内次数降序。
        没有数据时返回空列表。
        """
        out = []
        label = {name: name for name, _lo, _hi in HOUR_BUCKETS}
        for bucket, m in self.hour_usage.items():
            if not m:
                continue
            top = sorted(m.items(), key=lambda kv: kv[1], reverse=True)[:3]
            out.append((label.get(bucket, bucket), top))
        return out

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
