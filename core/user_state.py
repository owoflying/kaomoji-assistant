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

#: 时段偏好生效的最小样本量。
#:
#: 某个时段一共用过不到这么多次时，不启用时段加成——
#: 样本太少，"最高频"可能只是"今天早上顺手用了一次"，
#: 拿它去影响排序纯属噪声。见 `hour_affinity` 的说明。
HOUR_MIN_BUCKET_SAMPLES = 8

#: 时段环境加成权重：推荐时如果条目属于当前时段场景，额外加分。
WEIGHT_SCENE = 0.8

#: 网络分数换算到「线性分」的量纲系数。
#:
#: 线性分的大致范围：freq ≈ 0~2.3，rec ≈ 0~1，hour ≈ 0~1，
#: 收藏 +2.5 —— 所以常见分值在 0~4 之间。
#: 网络输出是 0~1，乘 2.0 后能与之同量级地竞争，
#: 但不会一上来就把线性信号压死（那会让老用户的肌肉记忆失效）。
NET_SCALE = 2.0

#: 正样本学习率。压低到 0.03 是刻意的——偏好要"多次一致的选择"
#: 才形成，一次点击不该立刻改变排序。
LEARN_LR_POS = 0.03
#: 负样本学习率：比正样本更低，因为"没被选"的信号更弱。
LEARN_LR_NEG = 0.02
#: 负样本目标值。不是 0——没被选可能只是这次不合适，不代表讨厌。
NEGATIVE_TARGET = 0.15

#: 时段分桶：把 24 小时压成 6 个有语义的桶。
#:
#: 不分 24 个桶是因为数据太稀疏——一天用不了几次，按小时统计
#: 每个桶平均不到 1 次，噪声比信号大。6 个桶足够区分
#: "清晨/上午/午后/傍晚/夜晚/深夜"这几种典型语境。
#: 时段定义统一放在 `core/time_buckets.py`——那里只依赖标准库，
#: 本模块和 `core/rank_net.py` 都引用它，不再是两者互相 import。
from core.time_buckets import HOUR_BUCKETS, hour_bucket  # noqa: E402,F401


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
        #: 推荐网络（`attach_rank_net` 装上才有；None 时 rank() 走纯线性公式）
        self.rank_net = None
        self.rank_data = None
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
        # 网络权重单独存（见 save_rank_net 的说明）
        self.save_rank_net()

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

        ## 两个刻意的修正

        1. **归一化而非直接用次数**：否则用得多的人所有项分数都高，
           时段偏好就淹没在总量里了。

        2. **最小样本门槛（`HOUR_MIN_BUCKET_SAMPLES`）**：时段内
           总样本太少时直接返回 0。

           不加这个会产生一个很蠢的行为：用户某天早上第一次用了
           某条，这一条就成了"早上最高频"，affinity = 1.0，
           立刻压到所有常用项前面。第一天用就被"学"成这样，
           用户会觉得推荐在乱跳。

           门槛之下宁可不用时段信号——它本来就是锦上添花。
        """
        b = bucket if bucket is not None else hour_bucket()
        m = self.hour_usage.get(b) or {}
        if not m:
            return 0.0
        # 总样本不够时不可信：1 条数据得出的"最高频"没有意义
        if sum(m.values()) < HOUR_MIN_BUCKET_SAMPLES:
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

        ## 神经网络加权（可选）

        如果调用方给 `self.rank_net` 装了网络（见 `attach_rank_net`），
        会把它给出的 0~1 分数按 `blend_weight()` 混进来：

            最终 = 线性分 + blend * 网络分 * NET_SCALE

        `blend` 在样本少时接近 0（完全等于原来的行为），
        攒够数据才逐步接手。详见 `core/rank_net.py` 的冷启动说明。
        """
        seq = [str(x) for x in items]
        net = getattr(self, "rank_net", None)
        if net is None:
            return sorted(seq, key=lambda t: -self.score(t, now))

        blend = net.blend_weight()
        try:
            from core.rank_net import features
        except Exception:
            return sorted(seq, key=lambda t: -self.score(t, now))

        data = getattr(self, "rank_data", None)
        bucket = hour_bucket()
        scored = []
        for i, t in enumerate(seq):
            base = self.score(t, now)
            if blend > 0.0:
                try:
                    fv = features(t, state=self, data=data,
                                  hour_bucket_name=bucket, now=now)
                    base += blend * net.predict(fv) * NET_SCALE
                except Exception:
                    pass
            scored.append((-base, i, t))
        scored.sort()
        return [t for _s, _i, t in scored]

    # ---------- 推荐理由（可解释性） ----------
    def explain(self, text, hour_bucket_name=None):
        """解释"这条为什么排在这个位置"，返回 [(标签, 说明), ...]。

        ## 为什么需要

        推荐排序是个黑盒：用户只看到顺序变了，不知道为什么。
        尤其加了神经网络之后，"它把我常用的挤下去了"这种感觉
        很容易让人烦躁却无从反馈。

        给出理由，至少让变化是**可理解**的。

        返回的标签是短词（给候选条角标用），说明是一句话（给统计页用）。
        只返回真正起作用的那几项——没命中就不列，避免噪音。
        """
        text = str(text or "")
        out = []
        if not text:
            return out

        count = self.usage.get(text, 0)
        if count >= 3:
            out.append(("常用", "用过 %d 次" % count))
        elif count > 0:
            out.append(("用过", "用过 %d 次" % count))

        if text in self.favorites:
            out.append(("收藏", "你收藏了这条"))

        # 新近度：在最近列表前几位
        try:
            idx = self.recent.index(text)
        except ValueError:
            idx = -1
        if 0 <= idx < 3:
            out.append(("刚用过", "最近第 %d 个" % (idx + 1)))

        # 连用：历史里连续出现
        run = 0
        for h in self.recent_injected(20):
            if h.get("text") == text:
                run += 1
            elif run > 0:
                break
        if run >= 2:
            out.append(("连用", "连续用了 %d 次" % run))

        # 时段偏好
        aff = self.hour_affinity(text, hour_bucket_name)
        if aff >= 0.6:
            out.append(("时段偏好", "这个时段常用"))

        # 网络贡献
        net = getattr(self, "rank_net", None)
        if net is not None:
            blend = net.blend_weight()
            if blend > 0.0:
                try:
                    from core.rank_net import features
                    fv = features(text, state=self,
                                  data=getattr(self, "rank_data", None),
                                  hour_bucket_name=hour_bucket_name)
                    p = net.predict(fv)
                    if p >= 0.65:
                        out.append(("学到了", "推断你在这种情境下会选它"))
                    elif p <= 0.2:
                        out.append(("少推荐", "推断你这次不太想要它"))
                except Exception:
                    pass
        return out

    def primary_reason(self, text, hour_bucket_name=None):
        """只取最靠前的那一条理由标签（候选条角标用）。"""
        reasons = self.explain(text, hour_bucket_name)
        return reasons[0][0] if reasons else ""

    # ---------- 推荐网络 ----------
    def attach_rank_net(self, net, data=None):
        """装上推荐网络（AppRuntime 启动时调用）。

        :param data: KaomojiData，提供分类归属与场景标签等特征
        """
        self.rank_net = net
        self.rank_data = data

    def detach_rank_net(self):
        self.rank_net = None
        self.rank_data = None

    def learn_selection(self, chosen, shown):
        """用户从候选里选中了一条——教网络「这种情境该选这条」。

        ## 训练信号

        * **正样本**：选中的那条，目标值 1.0。
        * **负样本**：同屏展示过但没被选的条目，目标值 0.0。

        只取**同屏展示过的**作为负样本，而不是整个库——
        用户没看到的条目不能算"他不要"，那是噪声。

        ## 三个防止「学得太死」的措施

        第一版实测有问题：连选 40 次同一条，网络就把它推到 0.95、
        其它全压到 0.02，用户想换口味得再练 40 次才扳得回来。
        真实的偏好不是这么刚性的。

        1. **负样本采样**：同屏可能几十条，只取 3 条。全喂进去
           会让负样本在数量上压倒正样本（1 正 : 39 负），
           几次之后就学会"什么都不选"。
        2. **负样本目标值不是 0 而是 0.15**：没被选可能只是这次不合适，
           不代表讨厌。留一点余地。
        3. **更低的学习率**：单次选择的影响有限，需要多次一致的选择
           才会改变排序——这才符合"偏好"的语义。
        """
        net = getattr(self, "rank_net", None)
        if net is None:
            return 0
        ch = str(chosen or "")
        if not ch:
            return 0
        try:
            from core.rank_net import features
        except Exception:
            return 0

        data = getattr(self, "rank_data", None)
        bucket = hour_bucket()
        trained = 0
        try:
            fv = features(ch, state=self, data=data, hour_bucket_name=bucket)
            net.train_one(fv, 1.0, lr=LEARN_LR_POS)
            trained += 1
        except Exception:
            pass

        # 负样本：从同屏的其它条目里均匀取 3 条
        negatives = [str(x) for x in (shown or []) if str(x) != ch]
        if negatives:
            step = max(1, len(negatives) // 3)
            for t in negatives[::step][:3]:
                try:
                    fv = features(t, state=self, data=data,
                                  hour_bucket_name=bucket)
                    net.train_one(fv, NEGATIVE_TARGET, lr=LEARN_LR_NEG)
                    trained += 1
                except Exception:
                    pass
        self._schedule_save()
        return trained

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

    def rank_net_path(self):
        """推荐网络权重的存放路径（与 user_state.json 同目录）。"""
        d = os.path.dirname(self.path) or "."
        return os.path.join(d, "rank_net.json")

    def save_rank_net(self):
        """把网络权重落盘。

        单独存一个文件而不是塞进 user_state.json：
        权重是几十个浮点数组，混在用户数据里既难读也难以手工清理
        （"推荐不准了想重置"只需删这个文件）。
        """
        net = getattr(self, "rank_net", None)
        if net is None:
            return
        try:
            net.save(self.rank_net_path())
        except Exception:
            pass

    def load_rank_net(self):
        """从磁盘读回网络权重；文件不存在则返回一个全新网络。"""
        try:
            from core.rank_net import RankNet
        except Exception:
            return None
        try:
            return RankNet.load(self.rank_net_path())
        except Exception:
            return None

    def reset_rank_net(self):
        """把网络重置成"没学过"的状态（用户抱怨推荐乱了时用）。"""
        try:
            from core.rank_net import RankNet
        except Exception:
            return
        old = getattr(self, "rank_net", None)
        data = getattr(self, "rank_data", None)
        self.rank_net = RankNet()
        self.rank_data = data
        # 顺手删掉磁盘上的旧权重，否则下次启动又读回来
        try:
            p = self.rank_net_path()
            if os.path.isfile(p):
                os.remove(p)
        except Exception:
            pass
        if old is not None:
            self._schedule_save()

    def export_data(self, path):
        """把**全部**使用记录导出成一个 JSON 文件。

        ## 与「保存使用统计」的区别

        那个是给人看的文本（排行榜）；这个是**完整可回灌的数据**：
        次数、最后使用时间、时段分布、注入流水、收藏、网络权重，
        导回去能原样恢复。

        用途：换机器迁移、备份、或者想分析自己的使用数据。

        :return: (成功, 消息)
        """
        net = getattr(self, "rank_net", None)
        payload = {
            "_format": "kaomoji-assistant-usage",
            "_version": 2,
            "exported_at": time.time(),
            "recent": self.recent,
            "favorites": self.favorites,
            "usage": self.usage,
            "last_used": self.last_used,
            "hour_usage": self.hour_usage,
            "history": self.history,
            "rank_net": net.to_dict() if net is not None else None,
        }
        try:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            return True, "已导出 %d 条使用记录" % len(self.usage)
        except Exception as e:
            return False, "导出失败：%r" % (e,)

    def import_data(self, path, merge=True):
        """从导出的 JSON 恢复使用记录。

        :param merge: True 时**累加**（次数相加、流水合并去重），
                      False 时整体替换。
                      默认累加：用户在另一台机器上也有使用记录时，
                      两边合并比直接覆盖更合理。

        :return: (成功, 消息)

        ⚠ **不认识的条目不导入**：导入前会用 `valid_items` 过滤，
        和载入本地文件同一套自净规则。
        """
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            return False, "读取失败：%r" % (e,)

        if not isinstance(data, dict) or "usage" not in data:
            return False, "这不像是使用记录的导出文件"

        try:
            if not merge:
                self.recent = []
                self.favorites = []
                self.usage = {}
                self.last_used = {}
                self.hour_usage = {}

            # 次数：累加（merge）或直接采用（replace，上面已清空）
            added = 0
            for k, v in (data.get("usage") or {}).items():
                try:
                    key, cnt = str(k), int(v)
                except (TypeError, ValueError):
                    continue
                if not self._accepts(key):
                    continue
                self.usage[key] = self.usage.get(key, 0) + cnt
                added += 1

            # 最后使用时间：取较新的那个
            for k, v in (data.get("last_used") or {}).items():
                try:
                    key, ts = str(k), float(v)
                except (TypeError, ValueError):
                    continue
                if not self._accepts(key):
                    continue
                if ts > self.last_used.get(key, 0.0):
                    self.last_used[key] = ts

            # 时段分布：逐层累加
            for bucket, m in (data.get("hour_usage") or {}).items():
                if not isinstance(m, dict):
                    continue
                cur = self.hour_usage.setdefault(str(bucket), {})
                for k, v in m.items():
                    try:
                        key, cnt = str(k), int(v)
                    except (TypeError, ValueError):
                        continue
                    if not self._accepts(key):
                        continue
                    cur[key] = cur.get(key, 0) + cnt

            # 收藏：合并去重，保持顺序
            for t in (data.get("favorites") or []):
                t = str(t)
                if self._accepts(t) and t not in self.favorites:
                    self.favorites.append(t)

            # 流水：合并后按时间倒序截断
            incoming = []
            for rec in (data.get("history") or []):
                if not isinstance(rec, dict):
                    continue
                t = str(rec.get("text") or "")
                if not self._accepts(t):
                    continue
                try:
                    ts = float(rec.get("ts") or 0)
                except (TypeError, ValueError):
                    ts = 0.0
                incoming.append({"text": t, "ts": ts,
                                 "source": str(rec.get("source") or "")})
            if incoming:
                merged = self.history + incoming
                merged.sort(key=lambda r: r.get("ts", 0), reverse=True)
                self.history = merged[: self.max_history]

            # 最近列表：合并去重
            for t in (data.get("recent") or []):
                t = str(t)
                if self._accepts(t) and t not in self.recent:
                    self.recent.append(t)
            self.recent = self.recent[: self.max_recent]

            # 网络权重：只有本地没有时可选择接管（合并训练毫无意义，
            # 两组权重相加等于破坏模型）
            nd = data.get("rank_net")
            cur_net = getattr(self, "rank_net", None)
            if nd and (cur_net is None or cur_net.samples == 0):
                try:
                    from core.rank_net import RankNet
                    self.rank_net = RankNet.from_dict(nd)
                except Exception:
                    pass

            self.save()
            return True, ("导入完成：%d 个条目，累计插入 %d 次"
                          % (added, self.total_inserts()))
        except Exception as e:
            return False, "导入失败：%r" % (e,)

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
