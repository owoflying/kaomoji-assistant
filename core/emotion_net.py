"""极小的情绪分类神经网络：纯 Python、零依赖、可在用户机器上增量训练。

## 为什么是"小"而不是"大"

实测结论（见开发记录）：

    参数量 2311，权重约 18KB，单次推理 0.47ms（关键词匹配 0.08ms）

0.47ms 对候选条弹出（本身几十毫秒）完全无感。所以"神经网络会很慢"
这个担心在**这个规模**下不成立——慢的是大模型，不是神经网络本身。

## 它比关键词匹配强在哪

关键词匹配看的是"句子里有没有某个词"，看不到**词的组合与顺序**：

    今天一点都不开心   →  关键词判"开心"（错，人知道是不开心）
    我不开心           →  关键词判"开心"（错）
    开心吗？其实一点都不 →  关键词判"开心"（错）

网络把整句压成一个向量再判断，能学到「否定词 + 情绪词」这种组合模式。
这是"整体思考"而非"逐字匹配"。

## 特征设计（关键）

纯字符词袋会丢掉"不"和"开心"的先后关系，所以特征包含三部分：

    1. **字符 unigram**：单字频次（平滑后）
    2. **字符 bigram**：相邻两字（"不开""开心"）——捕捉局部组合
    3. **位置标记**：句尾是否问号、是否含否定词、句长档位

bigram 是让网络理解否定的关键：「不开心」会被编码成 `不开` + `开心` 两个
特征，网络可以学到 `不开` 这个特征的负权重。

## 网络结构

    input(特征维度) -> hidden(24, relu) -> out(7, softmax)

单隐层足够：这类任务的特征空间不大，层数多了反而过拟合。
"""
import json
import math
import os
import random

#: 情绪分类（与 kaomoji.json 的 category 名一致）。
#:
#: ⚠ 第 8 类 `NONE` 是**"不该弹窗"**，不是一种情绪。
#:
#: 早期只有 7 类时，网络被迫把所有句子判成某个情绪——实测闲聊句
#: 「代码跑通了」判开心 0.92、「收到」判惊讶 0.99，误报率 94.7%。
#: 加一个显式的"无情绪"类之后，网络才有能力说"这句不用弹"。
CLASSES = ("开心", "伤心", "生气", "惊讶", "喜欢", "思考", "疲惫")

#: 内部类别表（含"无情绪"，仅用于训练与推理，不对外暴露）
NET_CLASSES = CLASSES + ("__NONE__",)

#: "无情绪"在 NET_CLASSES 里的下标
NONE_INDEX = len(CLASSES)

#: 供 UI / 统计使用的类别名（不含内部类）
EMOTIONS = CLASSES

#: 特征维度组成
UNIGRAM_BUCKETS = 80      # 单字哈希桶
BIGRAM_BUCKETS = 128      # 双字哈希桶
EXTRA_FEATURES = 8        # 手工特征（否定/问号/长度等）
#: 转折位置特征：把句子按转折词切成前后两段，分别编码。
#:
#: **为什么必须有它**：转折句「虽然很累但是很开心」的正确情绪在后半句，
#: 但 unigram/bigram 都是**无序频次统计**，网络看到的只是"累"和"开心"
#: 各出现一次，根本不知道谁在后面。实测转折类准确率长期卡在 0~33%，
#: 数据加到 1900 条也没用——瓶颈在特征不在数据量。
#:
#: 这里显式给出「转折词之后那段」的 unigram 特征，网络就能直接学到
#: "后半段的情绪词才是答案"。
TURN_TAIL_BUCKETS = 64
INPUT_DIM = (UNIGRAM_BUCKETS + BIGRAM_BUCKETS + EXTRA_FEATURES
             + TURN_TAIL_BUCKETS)

#: 隐层宽度
HIDDEN = 20

#: 模型文件版本（结构变化时递增，旧模型自动作废）
MODEL_VERSION = 1

#: 否定词：出现时降低对应情绪权重
NEGATION_WORDS = ("不", "没", "别", "无", "非", "未")

#: 转折词：后半句通常主导情绪
TURN_WORDS = ("但是", "但", "不过", "然而", "可是", "却", "其实")

#: 用于切分"尾段"的转折词。按长度降序，保证「但是」先于「但」匹配。
_TURN_CUT_WORDS = ("但是", "不过", "然而", "可是", "结果", "然后",
                   "但", "却", "可")


def chars_of(s):
    """取非空白字符列表。"""
    return [c for c in str(s or "") if not c.isspace()]


def _hash32(s):
    """稳定的 32 位哈希（不用内置 hash，因为它每个进程随机化）。"""
    h = 2166136261
    for ch in s:
        h ^= ord(ch)
        h = (h * 16777619) & 0xFFFFFFFF
    return h


def _bucket(s, n):
    return _hash32(s) % n


def vectorize(text):
    """把整句编码成定长特征向量（"整体思考"的输入）。

    返回 list[float]，长度 = INPUT_DIM。
    """
    v = [0.0] * INPUT_DIM
    s = str(text or "")
    # 去掉空白，但保留标点（问号对判断"思考"有用）
    chars = [c for c in s if not c.isspace()]
    if not chars:
        return v

    n = float(len(chars))

    # 1) unigram
    for c in chars:
        v[_bucket("u:" + c, UNIGRAM_BUCKETS)] += 1.0 / n

    # 2) bigram（相邻两字）：让网络看到「不开」「开心」这类组合
    if len(chars) >= 2:
        m = float(len(chars) - 1)
        for i in range(len(chars) - 1):
            bg = chars[i] + chars[i + 1]
            v[UNIGRAM_BUCKETS + _bucket("b:" + bg, BIGRAM_BUCKETS)] += 1.0 / m

    # 3) 手工特征
    base = UNIGRAM_BUCKETS + BIGRAM_BUCKETS
    neg_count = sum(s.count(w) for w in NEGATION_WORDS)
    turn_count = sum(s.count(w) for w in TURN_WORDS)
    v[base + 0] = 1.0 if neg_count else 0.0
    v[base + 1] = min(1.0, neg_count / 2.0)
    v[base + 2] = 1.0 if turn_count else 0.0
    v[base + 3] = 1.0 if s.rstrip().endswith(("？", "?")) else 0.0
    v[base + 4] = min(1.0, len(chars) / 20.0)
    # 否定词是否出现在情绪词**之前**（"不开心" vs "开心不起来"）
    first_neg = min((s.find(w) for w in NEGATION_WORDS if w in s), default=-1)
    v[base + 5] = 1.0 if first_neg >= 0 and first_neg < len(s) / 2 else 0.0
    # 感叹号（强情绪）
    v[base + 6] = 1.0 if ("！" in s or "!" in s) else 0.0
    # 重复标点（"！！！"更强烈）
    v[base + 7] = 1.0 if ("！！" in s or "!!" in s) else 0.0

    # 4) 转折尾段：转折词之后那部分的 unigram
    #
    # 这一步是纯词袋做不到的——它显式区分"转折前"和"转折后"，
    # 让网络能学到后半句才决定整体情绪。
    tail_start = -1
    for tw in _TURN_CUT_WORDS:
        idx = s.rfind(tw)
        if idx >= 0:
            pos_after = idx + len(tw)
            if pos_after > tail_start:
                tail_start = pos_after
    if tail_start >= 0:
        tail = chars_of(s[tail_start:])
        if tail:
            m = float(len(tail))
            off = UNIGRAM_BUCKETS + BIGRAM_BUCKETS + EXTRA_FEATURES
            for c in tail:
                v[off + _bucket("t:" + c, TURN_TAIL_BUCKETS)] += 1.0 / m
    return v


def _relu(x):
    return x if x > 0.0 else 0.0


def _softmax(logits):
    m = max(logits) if logits else 0.0
    ex = [math.exp(x - m) for x in logits]
    tot = sum(ex) or 1.0
    return [x / tot for x in ex]


class EmotionNet:
    """单隐层情绪分类器。权重全在 list 里，便于 JSON 序列化。"""

    def __init__(self, w1=None, b1=None, w2=None, b2=None, seed=7):
        self.n_in = INPUT_DIM
        self.n_hidden = HIDDEN
        self.n_out = len(NET_CLASSES)
        if w1 is None:
            rnd = random.Random(seed)
            s1 = 1.0 / math.sqrt(self.n_in)
            self.w1 = [[rnd.uniform(-s1, s1) for _ in range(self.n_in)]
                       for _ in range(self.n_hidden)]
            self.b1 = [0.0] * self.n_hidden
            s2 = 1.0 / math.sqrt(self.n_hidden)
            self.w2 = [[rnd.uniform(-s2, s2) for _ in range(self.n_hidden)]
                       for _ in range(self.n_out)]
            self.b2 = [0.0] * self.n_out
        else:
            self.w1, self.b1, self.w2, self.b2 = w1, b1, w2, b2

    # ---------- 推理 ----------
    def forward(self, x):
        """返回 (probabilities, hidden)。"""
        hid = [0.0] * self.n_hidden
        for i in range(self.n_hidden):
            wi = self.w1[i]
            s = self.b1[i]
            for j in range(self.n_in):
                xj = x[j]
                if xj:
                    s += wi[j] * xj
            hid[i] = s if s > 0.0 else 0.0
        logits = [0.0] * self.n_out
        for k in range(self.n_out):
            wk = self.w2[k]
            s = self.b2[k]
            for i in range(self.n_hidden):
                hi = hid[i]
                if hi:
                    s += wk[i] * hi
            logits[k] = s
        return _softmax(logits), hid

    def predict(self, text):
        """返回 (emotion, confidence)；emotion 为 None 表示"不该弹窗"。

        两种情况返回 None：
          * 网络判定为 `__NONE__`（这句没有情绪）——这是加第 8 类之后的主力路径；
          * 最高概率低于阈值（网络没把握）。

        阈值 0.45：宁可不出手，也不要乱弹——自动弹出打扰用户是有代价的。
        """
        probs, _ = self.forward(vectorize(text))
        k = max(range(self.n_out), key=lambda i: probs[i])
        conf = probs[k]
        if k == NONE_INDEX:
            # 判成"无情绪"：不再看置信度，直接表示不弹
            return None, conf
        if conf < 0.45:
            return None, conf
        return NET_CLASSES[k], conf

    # ---------- 训练 ----------
    def train_one(self, text, target_index, lr=0.05):
        """单样本 SGD（在线学习）：返回损失。

        在线学习是关键——用户每选一次颜文字，就等于给了一次"分类是对的"
        的反馈，可以立刻更新权重，网络会越来越贴合这个用户的表达习惯。
        """
        x = vectorize(text)
        probs, hid = self.forward(x)

        # 交叉熵对 logits 的梯度：probs - onehot
        dlogit = list(probs)
        dlogit[target_index] -= 1.0

        loss = -math.log(max(1e-9, probs[target_index]))

        # 反向传播到 w2/b2
        dw2 = [[0.0] * self.n_hidden for _ in range(self.n_out)]
        for k in range(self.n_out):
            g = dlogit[k]
            if not g:
                continue
            for i in range(self.n_hidden):
                dw2[k][i] = g * hid[i]

        # 到隐层
        dhid = [0.0] * self.n_hidden
        for i in range(self.n_hidden):
            s = 0.0
            for k in range(self.n_out):
                s += dlogit[k] * self.w2[k][i]
            dhid[i] = s if hid[i] > 0.0 else 0.0

        # 反向传播到 w1/b1
        for i in range(self.n_hidden):
            g = dhid[i]
            wi = self.w1[i]
            if not g:
                # 隐层未激活时只更新 bias（保持简单）
                continue
            for j in range(self.n_in):
                xj = x[j]
                if xj:
                    wi[j] -= lr * g * xj
            self.b1[i] -= lr * g

        for k in range(self.n_out):
            g = dlogit[k]
            if not g:
                continue
            wk = self.w2[k]
            for i in range(self.n_hidden):
                wk[i] -= lr * dw2[k][i]
            self.b2[k] -= lr * g

        return loss

    # ---------- 序列化 ----------
    def to_dict(self):
        return {
            "version": MODEL_VERSION,
            "classes": list(NET_CLASSES),
            "n_in": self.n_in,
            "n_hidden": self.n_hidden,
            "trained": getattr(self, "trained_samples", 0),
            "w1": self.w1, "b1": self.b1,
            "w2": self.w2, "b2": self.b2,
        }

    @classmethod
    def from_dict(cls, d):
        """从 dict 恢复；版本或形状不匹配时返回 None（由调用方回退）。"""
        try:
            if not isinstance(d, dict):
                return None
            if int(d.get("version", -1)) != MODEL_VERSION:
                return None
            if int(d.get("n_in", -1)) != INPUT_DIM:
                return None
            if int(d.get("n_hidden", -1)) != HIDDEN:
                return None
            if list(d.get("classes") or []) != list(NET_CLASSES):
                return None
            net = cls(w1=d["w1"], b1=d["b1"], w2=d["w2"], b2=d["b2"])
            net.trained_samples = int(d.get("trained", 0) or 0)
            return net
        except Exception:
            return None

    def save(self, path):
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.to_dict(), f, ensure_ascii=False)
            os.replace(tmp, path)
            return True
        except Exception:
            return False

    @classmethod
    def load(cls, path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return cls.from_dict(json.load(f))
        except Exception:
            return None


def class_index(emotion):
    """情绪名 -> 网络输出下标；未知返回 None。

    支持 `"__NONE__"`（训练"无情绪"样本时用）。
    """
    if not emotion:
        return None
    try:
        return NET_CLASSES.index(emotion)
    except ValueError:
        return None
