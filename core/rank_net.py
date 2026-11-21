"""推荐排序网络：学「在什么情境下我会选哪条颜文字」。

## 为什么这里值得用神经网络

现有的 `UserState.score()` 是一条**线性可加**公式：

    score = a·频率 + b·新近度 + c·时段偏好 + d·收藏

它表达不了**交叉项**，而交叉项恰恰是真实偏好的大部分：

1. **时段 × 历史**：深夜刚发过一条发泄类，下一句多半还是发泄类；
   早上刚发过问候，下一句更可能是正经内容。同一个"刚用过"信号，
   在不同时段指向相反方向。

2. **新近度 × 频率**：只用过 1 次、且就在刚才 → 极可能再用；
   用过 50 次、但上次是三天前 → 反而可能想换换。
   线性公式里 1×频率 + 1×新近度 无法区分这两种情况。

3. **饱和效应**：连着三次用同一个，第四次往往想换。
   "连用次数"是个独立的非线性信号。

4. **类别上下文**：刚用了"开心"类，紧接着更可能还是"开心"类
   （情绪是连贯的），但连用三次后又会跳出。

四条都是线性模型的结构性盲区。

## 结构

沿用 `core/emotion_net.py` 的思路（同样零依赖、纯 Python）：

    输入 → 隐层(ReLU) → 输出(softmax)

但这里**不预测"哪一条"**，而是预测"这一条该不该排前面"——
因为条目会变（用户随时新增自定义颜文字），输出层固定成"条数"
会立刻失效。

所以做成**打分器**：输入是一组情境特征，输出 0~1 的分数。
训练信号来自"用户最终选了哪条"：选中的打 1，同屏落选的打 0。

## 冷启动

网络权重初始为小随机值，输出接近 0.5，几乎不影响排序；
靠 `UserState.score()` 的线性公式兜底。用满 30 次后网络权重
才逐步压过公式（见 `blend_weight()`）。

这样新用户完全感受不到网络的存在，老用户才逐渐受益。
"""

import json
import os

#: 输入维度：见 `features()`
INPUT_DIM = 22
#: 隐层宽度。小是为了快——每次唤起要算几十条，
#: 且要在主线程里算（候选条弹出不能卡）。
HIDDEN = 12
#: 学到多少次开始真正采用网络输出
WARMUP = 30


def _relu(x):
    return x if x > 0.0 else 0.0


class RankNet:
    """把「情境特征」映射成「该条应排多前」的分数（0~1）。"""

    def __init__(self, w1=None, b1=None, w2=None, b2=None, seed=11):
        import random

        rnd = random.Random(seed)
        # He 初始化：隐层用 sqrt(2/n) 量级的随机权重
        if w1 is None:
            self.w1 = [[rnd.gauss(0, (2.0 / INPUT_DIM) ** 0.5)
                        for _ in range(INPUT_DIM)] for _ in range(HIDDEN)]
        else:
            self.w1 = [list(row) for row in w1]
        if b1 is None:
            self.b1 = [0.0] * HIDDEN
        else:
            self.b1 = list(b1)
        if w2 is None:
            self.w2 = [rnd.gauss(0, (2.0 / HIDDEN) ** 0.5)
                       for _ in range(HIDDEN)]
        else:
            self.w2 = list(w2)
        if b2 is None:
            self.b2 = 0.0
        else:
            self.b2 = float(b2)
        #: 训练样本计数：用于冷启动融合权重
        self.samples = 0

    # ------------------------------------------------------------ 前向
    def _forward(self, x):
        """返回 (隐层激活, 输出分数)。"""
        h = []
        for i in range(HIDDEN):
            row = self.w1[i]
            s = self.b1[i]
            for k in range(INPUT_DIM):
                s += row[k] * x[k]
            h.append(_relu(s))
        z = self.b2
        for i in range(HIDDEN):
            z += self.w2[i] * h[i]
        # sigmoid：输出 0~1，可直接当"排序权重"
        if z > 40:
            out = 1.0
        elif z < -40:
            out = 0.0
        else:
            out = 1.0 / (1.0 + 2.718281828459045 ** (-z))
        return h, out

    def predict(self, x):
        """给一个特征向量打分（0~1）。"""
        if len(x) != INPUT_DIM:
            return 0.5
        return self._forward(x)[1]

    # ------------------------------------------------------------ 训练
    def train_one(self, x, target, lr=0.03):
        """单样本在线学习。

        :param target: 0~1。选中的打 1.0，同屏落选的打 0.15。
        :param lr: 学习率。默认 0.03 是试出来的：

            太大（0.08）：连选几次同一条就把它推到 0.95、其它压到 0.02，
            用户想换口味得再练几十次才扳得回来——那不像"偏好"，像"上瘾"。

            太小：一个月都学不出东西。

            0.03 下，需要 20~30 次一致的选择才能让排序明显改变，
            这个节奏和"形成习惯"接近。
        """
        if len(x) != INPUT_DIM:
            return 0.0
        h, out = self._forward(x)
        err = out - target
        # 交叉熵 + sigmoid 的梯度就是 (out - target)
        g = err * lr

        # 输出层
        self.b2 -= g
        for i in range(HIDDEN):
            self.w2[i] -= g * h[i]

        # 隐层（ReLU 的导数：激活>0 才回传）
        for i in range(HIDDEN):
            if h[i] <= 0.0:
                continue
            gi = g * self.w2[i]
            row = self.w1[i]
            for k in range(INPUT_DIM):
                row[k] -= gi * x[k]
            self.b1[i] -= gi

        self.samples += 1
        return abs(err)

    def blend_weight(self):
        """网络输出在最终排序里占多大比重（0~1）。

        冷启动策略：样本少时几乎不用网络，靠线性公式兜底。
        理由是网络权重一开始是随机的，用它会**打乱**本来好好的排序；
        用户完全感受不到它的存在，等数据够了才慢慢接手。
        """
        if self.samples <= 0:
            return 0.0
        if self.samples >= WARMUP * 4:
            return 0.75
        # WARMUP 之前几乎为 0，之后线性爬升到 0.75
        return 0.75 * min(1.0, self.samples / float(WARMUP * 4))

    # ------------------------------------------------------------ 持久化
    def to_dict(self):
        return {"w1": self.w1, "b1": self.b1, "w2": self.w2,
                "b2": self.b2, "samples": self.samples}

    @classmethod
    def from_dict(cls, d):
        try:
            net = cls(w1=d.get("w1"), b1=d.get("b1"),
                      w2=d.get("w2"), b2=d.get("b2"))
            net.samples = int(d.get("samples") or 0)
            return net
        except Exception:
            return cls()

    def save(self, path):
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self.to_dict(), f)
        except Exception:
            pass

    @classmethod
    def load(cls, path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return cls.from_dict(json.load(f))
        except Exception:
            return cls()


#: 情境特征。每条 `features()` 都会现场算。
#:
#: 索引说明（共 INPUT_DIM=22 维）：
#:   0  该条在本时段的偏好（0~1，已归一化）
#:   1  总体频率（log 压缩后归一到 ~0..1）
#:   2  新近度（最近列表位次反比）
#:   3  是否收藏
#:   4  是否在最近 3 条里
#:   5  是否就是上一条（连用信号）
#:   6  连续使用次数（同一条连着用了几次，0~1）
#:   7  该条所属分类是否与上一条相同
#:   8  该条所属分类在本时段的总偏好
#:   9  库内该分类的条目数（长尾修正：大类天然更可能被选）
#:   10 当前小时（归一化 0~1，给网络自己学时段规律）
#:   11 是否周末
#:   12 今天已用次数（归一化）
#:   13 该条在最近 10 条里出现几次（0~1）
#:   14 距上次使用该条的时间（天，log 压缩）
#:   15 该条是否用户自定义（自定义的往往更贴身）
#:   16 该条是否有场景标签
#:   17 当前时段场景是否包含该条
#:   18 会话内已用次数（归一化）
#:   19 是否周六日 + 深夜（交叉项，人工喂给网络）
#:   20 频率 × 新近度（交叉项）
#:   21 时段偏好 × 新近度（交叉项）
N_FEATURES = INPUT_DIM


def _log1p_norm(x, cap=3.0):
    """log(1+x) 后压到 0~1。"""
    import math

    if x <= 0:
        return 0.0
    return min(1.0, math.log1p(x) / cap)


def features(text, *, state, data=None, hour_bucket_name=None,
             now=None):
    """把一条颜文字在"当前情境"下的各种信号编成定长向量。

    :param state: UserState 实例（提供使用统计、最近列表、收藏）
    :param data:  KaomojiData 实例（提供分类归属、场景标签）；
                  为 None 时不计算分类相关特征
    :param hour_bucket_name: 指定时段桶（测试用，默认按当前时间）
    :param now: 指定当前时间戳（测试用）

    返回值长度恒为 INPUT_DIM；任何一项算不出来都填 0，
    绝不抛异常——候选条弹出路径上不能因为特征工程出错而崩。
    """
    import time as _time

    from core.user_state import hour_bucket

    v = [0.0] * INPUT_DIM
    text = str(text or "")
    if not text or state is None:
        return v

    try:
        ts_now = float(now) if now is not None else _time.time()
        bucket = hour_bucket_name or hour_bucket(ts_now)

        # ---- 0~3：与现有线性公式重合的基础信号 ----
        aff = state.hour_affinity(text, bucket)
        v[0] = aff

        cnt = state.usage.get(text, 0)
        freq = _log1p_norm(cnt)
        v[1] = freq

        try:
            idx = state.recent.index(text)
        except ValueError:
            idx = -1
        rec = 1.0 / (1.0 + idx / 6.0) if idx >= 0 else 0.0
        v[2] = rec

        v[3] = 1.0 if text in state.favorites else 0.0

        # ---- 4~7：上下文（上一条、连用、同分类）----
        recent = list(state.recent)
        v[4] = 1.0 if text in recent[:3] else 0.0

        last = state.last_injected()
        v[5] = 1.0 if (last and text == last) else 0.0

        # 连用：从历史最前面数，连续多少条是同一个
        run = 0
        for h in state.recent_injected(20):
            if h.get("text") == text:
                run += 1
            elif run > 0:
                break
        v[6] = min(1.0, run / 3.0)

        # 与上一条同分类？
        same_cat = 0.0
        if data is not None and last:
            try:
                ca = set(data.categories_of(text))
                cl = set(data.categories_of(last))
                if ca and cl and (ca & cl):
                    same_cat = 1.0
            except Exception:
                pass
        v[7] = same_cat

        # ---- 8~9：分类层面的信号 ----
        if data is not None:
            try:
                cats = data.categories_of(text)
                if cats:
                    # 该分类在当前时段的总偏好
                    best = 0.0
                    for other in data.get_items(cats[0]):
                        best = max(best, state.hour_affinity(other, bucket))
                    v[8] = best
                    # 分类规模：大类天然更容易被选中（长尾修正）
                    v[9] = min(1.0, len(data.get_items(cats[0])) / 40.0)
            except Exception:
                pass

        # ---- 10~12：时间信息（让网络自己学时段规律）----
        import datetime

        dt = datetime.datetime.fromtimestamp(ts_now)
        v[10] = dt.hour / 23.0
        v[11] = 1.0 if dt.weekday() >= 5 else 0.0

        today = dt.date()
        today_count = 0
        for h in state.recent_injected(state.max_history):
            try:
                hd = datetime.datetime.fromtimestamp(
                    float(h.get("ts") or 0)).date()
            except Exception:
                continue
            if hd == today:
                today_count += 1
        v[12] = min(1.0, today_count / 20.0)

        # ---- 13~14：近期重复度与沉默时长 ----
        rec10 = [h.get("text") for h in state.recent_injected(10)]
        v[13] = min(1.0, rec10.count(text) / 5.0)

        last_ts = state.last_used.get(text)
        if last_ts:
            days = max(0.0, (ts_now - float(last_ts)) / 86400.0)
            v[14] = _log1p_norm(days, cap=2.0)

        # ---- 15~17：条目本身的性质 ----
        # 是否用户自定义：自定义清单在 UserKaomoji 里，这里只看
        # "不在内置库"这个近似判据——调用方可通过 data 判断
        if data is not None:
            try:
                v[15] = 0.0 if text in set(data.get_items()) else 1.0
                im = data.meta.get(text) or {}
                v[16] = 1.0 if im.get("scenes") else 0.0
                scene = state.current_scene()
                if scene and scene in [s.lower()
                                       for s in im.get("scenes", [])]:
                    v[17] = 1.0
            except Exception:
                pass

        # ---- 18：会话内用量 ----
        v[18] = min(1.0, len(state.history) / 30.0)

        # ---- 19~21：交叉项（线性公式表达不了的，显式喂给网络）----
        v[19] = 1.0 if (v[11] and bucket == "night") else 0.0
        v[20] = freq * rec
        v[21] = aff * rec
    except Exception:
        # 特征工程永远不该让候选条失效
        pass
    return v
