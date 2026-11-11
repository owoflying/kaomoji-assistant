"""训练情绪识别小网络，产出随包的初始模型。

## 训练数据从哪来

没有人工标注语料，所以用**模板 + 词表**合成。关键是模板要覆盖
关键词匹配搞不定的结构：

    肯定：我很{情绪词}
    否定：我一点也不{情绪词}          -> 标签取**否定后**的情绪
    转折：虽然{情绪A}但是{情绪B}      -> 取后半句
    疑问：{事情}怎么办？              -> 思考
    程度：太{情绪词}了 / 有点{情绪词}

这样训练出来的网络学到的是「否定词 + 情绪词」的组合模式，
而不是记住单个词——这正是它比关键词匹配强的地方。

## 用法

    .venv\\Scripts\\python.exe tools/train_emotion_model.py

产出 `data/emotion_model.json`（约 200KB 的文本，压缩后更小）。
改模板或词表后重跑即可。
"""
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from core import emotion_net as en  # noqa: E402

random.seed(20260802)

#: 每个情绪的核心表达词（与关键词表同源，但用于**造句**而非匹配）
WORDS = {
    "开心": ["开心", "高兴", "愉快", "快乐", "兴奋", "激动", "满足", "幸福",
             "美滋滋", "爽", "嗨", "雀跃"],
    "伤心": ["难过", "伤心", "悲伤", "低落", "委屈", "心碎", "失落", "沮丧",
             "难受", "郁闷", "扎心", "崩溃"],
    "生气": ["生气", "愤怒", "恼火", "火大", "不爽", "窝火", "气愤", "烦",
             "暴躁", "抓狂", "无语", "郁闷"],
    "惊讶": ["惊讶", "震惊", "吃惊", "意外", "惊奇", "错愕", "傻眼", "呆住",
             "没想到", "不敢信"],
    "喜欢": ["喜欢", "心动", "爱慕", "中意", "欣赏", "着迷", "迷恋", "倾心",
             "偏爱", "稀罕"],
    "思考": ["疑惑", "困惑", "纳闷", "纠结", "想不通", "费解", "搞不懂",
             "不明白"],
    "疲惫": ["累", "疲倦", "疲惫", "困", "乏力", "无力", "疲劳", "撑不住",
             "精疲力尽", "虚脱"],
}

#: 否定词
NEG = ["不", "没", "一点都不", "并不", "根本不", "完全不", "从来没有"]

#: 程度词
DEGREE = ["很", "非常", "特别", "有点", "稍微", "实在", "真的", "超级"]


def _phrase(w, degree=True):
    """造一个情绪短语。"""
    if degree and random.random() < 0.6:
        return random.choice(DEGREE) + w
    return w


def gen_positive():
    """肯定句：{我/今天} {程度} {情绪词}。标签就是该情绪。"""
    out = []
    for emo, ws in WORDS.items():
        for w in ws:
            for _ in range(6):
                p = _phrase(w)
                tpl = random.choice([
                    "我%s" % p,
                    "今天%s%s" % (p, w[-1] if len(w) == 1 else ""),
                    "感觉%s%s" % (p, "啊" if random.random() < 0.5 else ""),
                    "%s真的%s" % (p, "受不了" if emo == "疲惫" else ""),
                    "好%s呀" % p,
                    "%s%s！" % (p, "了" if random.random() < 0.5 else ""),
                    "真的%s" % p,
                    "我现在%s" % p,
                ])
                out.append((tpl.strip(), emo))
    return out


def gen_negated():
    """否定句：{否定词} {情绪词}。标签是**相反**的情绪。

    "我不开心" -> 伤心（而不是开心）
    这是关键词匹配必然出错、而网络能学会的核心场景。
    """
    # 情绪 -> 否定后的情绪（用生活里最自然的对应）
    opposite = {
        "开心": "伤心", "伤心": "开心", "生气": "开心", "惊讶": "思考",
        "喜欢": "生气", "思考": "开心", "疲惫": "开心",
    }
    out = []
    for emo, ws in WORDS.items():
        tgt = opposite.get(emo, "思考")
        for w in ws:
            for _ in range(6):
                neg = random.choice(NEG)
                tpl = random.choice([
                    "我%s%s" % (neg, w),
                    "%s有点%s%s" % ("今天", neg, w),
                    "一点也不%s" % w,
                    "其实%s那么%s" % (neg, w),
                    "%s%s起来" % (neg, w),
                    "我真的%s%s" % (neg, w),
                ])
                out.append((tpl, tgt))
    return out


def gen_turn():
    """转折句：虽然{A}但是{B}。标签取**后半句** B 的情绪。"""
    out = []
    emos = list(WORDS)
    for _ in range(600):
        a, b = random.sample(emos, 2)
        wa = random.choice(WORDS[a])
        wb = random.choice(WORDS[b])
        tpl = random.choice([
            "虽然%s但是%s" % (_phrase(wa), _phrase(wb)),
            "%s，不过%s" % (_phrase(wa), _phrase(wb)),
            "本来%s，后来%s" % (_phrase(wa), _phrase(wb)),
            "%s可是%s" % (_phrase(wa), _phrase(wb)),
        ])
        out.append((tpl, b))
    return out


def gen_question():
    """疑问句 -> 思考。"""
    out = []
    stems = ["为什么会这样", "这是怎么回事", "该怎么办", "到底行不行",
             "你说呢", "这样对吗", "什么时候能好", "为什么会这样呢",
             "谁说的", "真的假的", "能不能行", "怎么办才好"]
    for s in stems:
        for _ in range(8):
            out.append((random.choice(["", "嗯", "我问问，"]) + s
                        + random.choice(["？", "?", "呢？"]), "思考"))
    return out


def gen_neutral():
    """无情绪句：标签设为「思考」是错的。

    网络没有"其他"类，所以这类句子只能靠置信度阈值过滤——
    训练时**不给**标签，避免教它瞎猜。这里返回空。
    """
    return []


def gen_short():
    """短句 / 口头禅（常见高频表达）。"""
    data = [
        ("哈哈", "开心"), ("哈哈哈哈", "开心"), ("笑死", "开心"),
        ("笑死我了", "开心"), ("嘻嘻", "开心"), ("耶", "开心"),
        ("呜呜", "伤心"), ("呜呜呜", "伤心"), ("想哭", "伤心"),
        ("哭了", "伤心"), ("破防了", "伤心"),
        ("无语", "生气"), ("服了", "生气"), ("烦死了", "生气"),
        ("气死了", "生气"), ("离谱", "生气"),
        ("哇塞", "惊讶"), ("天哪", "惊讶"), ("卧槽", "惊讶"),
        ("我的天", "惊讶"), ("绝了", "惊讶"), ("居然", "惊讶"),
        ("好喜欢", "喜欢"), ("爱了", "喜欢"), ("心动", "喜欢"),
        ("好可爱", "喜欢"), ("awsl", "喜欢"),
        ("为什么", "思考"), ("为什么呢", "思考"), ("搞不懂", "思考"),
        ("想不通", "思考"), ("怎么回事", "思考"),
        ("好累", "疲惫"), ("累了", "疲惫"), ("困死了", "疲惫"),
        ("好困", "疲惫"), ("躺平", "疲惫"), ("emo了", "疲惫"),
    ]
    return data


def gen_intensive():
    """强化易混场景：同一个词在不同语境下情绪不同。

    实测发现两处易错：
      * "我真的好累" 被判成开心（"好"这个字在别处常伴随"好开心"）
      * "虽然很累但是很开心" 被判成喜欢（转折结构没学稳）

    这里针对性地多造这类句子，让网络把"好"的字面情绪和"好+形容词"
    的结构区分开。
    """
    out = []

    # "好X" 结构：把「好」+ 各种情绪的搭配都覆盖到
    #
    # ⚠ 重复次数要克制。实测给 8 次时这一块暴涨到 1876 条，
    # 把 444 条否定样本淹没，网络退化成"看到情绪词就是那个情绪"，
    # 否定句全部判错。给 2 次即可——它只是补充，不该主导。
    for emo, ws in WORDS.items():
        for w in ws:
            for _ in range(2):
                tpl = random.choice([
                    "真的好%s" % w,
                    "好%s啊" % w,
                    "我%s%s" % ("好", w),
                    "今天好%s" % w,
                    "感觉好%s" % w,
                ])
                out.append((tpl, emo))

    # 转折：加强"但是/不过/可是"后半句权重
    emos = list(WORDS)
    for _ in range(400):
        a, b = random.sample(emos, 2)
        wa = random.choice(WORDS[a])
        wb = random.choice(WORDS[b])
        tpl = random.choice([
            "虽然%s但是%s" % (wa, wb),
            "虽然很%s，但真的很%s" % (wa, wb),
            "一开始%s，但是%s" % (wa, wb),
            "不得不说，%s但是%s" % (wa, wb),
            "%s归%s，但还是%s" % (wa, wa, wb),
        ])
        out.append((tpl, b))

    # 感叹 / 强化
    for emo, ws in WORDS.items():
        for w in ws[:4]:
            out.append(("太%s了！！" % w, emo))
            out.append(("%s死我了" % w, emo))
            out.append(("真的%s到不行" % w, emo))
    return out


def gen_negation_intensive():
    """**重点强化否定**：这是网络相对关键词匹配的核心优势，必须学扎实。

    前面 `gen_negated` 只有 444 条，而其它模板动辄上千条，
    数据一多就会被淹没（实测网络退化成"看到情绪词就判那个情绪"）。
    这里用更多句式把否定场景堆到足够权重。
    """
    opposite = {
        "开心": "伤心", "伤心": "开心", "生气": "开心", "惊讶": "思考",
        "喜欢": "生气", "思考": "开心", "疲惫": "开心",
    }
    negs = ["不", "没", "一点都不", "并不", "根本不", "完全不",
            "不太", "不怎么", "毫不", "算不上"]
    #: 只取每个情绪的**代表性**词，避免样本爆炸
    #:
    #: 纯 Python 的在线 SGD 每条约 0.2ms，样本量上到 1 万就要跑好几小时。
    #: 取前 5 个词 × 4 种否定 × 4 个句式 ≈ 每情绪 80 条，总计约 560 条，
    #: 加上 gen_negated 的 444 条，否定场景占比约 15%，足够学会。
    out = []
    for emo, ws in WORDS.items():
        tgt = opposite.get(emo, "思考")
        for w in ws[:5]:
            for neg in negs[:4]:
                for tpl in (
                    "我%s%s" % (neg, w),
                    "今天%s%s" % (neg, w),
                    "%s那么%s" % (neg, w),
                    "一点也不%s吧" % w,
                ):
                    out.append((tpl, tgt))
    return out


def build_dataset():
    data = []
    data += gen_positive()
    data += gen_negated()
    data += gen_negation_intensive()   # 否定是核心优势，重点强化
    data += gen_turn()
    data += gen_question()
    data += gen_short()
    data += gen_intensive()
    random.shuffle(data)
    return data


def train(epochs=40, lr=0.08):
    import time

    data = build_dataset()
    print("训练样本: %d 条" % len(data))
    net = en.EmotionNet()
    print("参数量: %d" % (net.n_in * net.n_hidden + net.n_hidden
                          + net.n_hidden * net.n_out + net.n_out))
    print("预计耗时: 约 %.0f 秒（纯 Python SGD，每条 ~0.3ms）"
          % (len(data) * epochs * 0.0003 * 2))

    t_start = time.perf_counter()
    for ep in range(epochs):
        random.shuffle(data)
        total_loss = 0.0
        correct = 0
        for text, emo in data:
            idx = en.class_index(emo)
            if idx is None:
                continue
            loss = net.train_one(text, idx, lr=lr)
            total_loss += loss
            probs, _ = net.forward(en.vectorize(text))
            if max(range(len(probs)), key=lambda i: probs[i]) == idx:
                correct += 1
        acc = correct / max(1, len(data))
        if (ep + 1) % 5 == 0 or ep == 0:
            done = time.perf_counter() - t_start
            print("  epoch %2d  loss=%.3f  acc=%.3f  (%.0fs)"
                  % (ep + 1, total_loss / len(data), acc, done),
                  flush=True)

    net.trained_samples = len(data)
    return net, data


def evaluate(net, data):
    """在训练集上评估（同时抽查几个关键词失败的场景）。"""
    correct = 0
    for text, emo in data:
        got, conf = net.predict(text)
        if got == emo:
            correct += 1
    print()
    print("训练集准确率: %.1f%%" % (100.0 * correct / max(1, len(data))))

    print()
    print("=== 关键词匹配搞不定的场景")
    cases = [
        ("今天一点都不开心", "不是开心"),
        ("我不开心", "不是开心"),
        ("开心吗？其实一点都不", "不是开心"),
        ("我没有不高兴", "不是生气"),
        ("虽然很累但是很开心", "开心"),
        ("我真的好累", "疲惫"),
        ("这个怎么办？", "思考"),
    ]
    from core.emotion import detect_last
    ok = 0
    for text, expect in cases:
        got, conf = net.predict(text)
        kw = detect_last(text)
        kw_emo = kw[0] if kw else "不触发"
        if expect.startswith("不是"):
            passed = got != expect[2:]
        else:
            passed = got == expect
        ok += 1 if passed else 0
        print("  %s %-22s 网络=%-6s(%.2f)  关键词=%-6s  期望=%s"
              % ("✓" if passed else "✗", text, got or "None", conf, kw_emo, expect))
    print("  网络正确: %d/%d" % (ok, len(cases)))


def main():
    net, data = train()
    evaluate(net, data)

    out = os.path.join(ROOT, "data", "emotion_model.json")
    if net.save(out):
        size = os.path.getsize(out)
        print()
        print("模型已保存: %s (%.0f KB)" % (out, size / 1024.0))
    else:
        print("保存失败")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
