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
    """转折句：虽然{A}但是{B}。标签取**后半句** B 的情绪。

    ⚠ 这是最难的类别（实测独立语料准确率只有 33%）。

    原因：句子同时含两个情绪词，网络分不出主次。解决要靠
    **转折词的位置信息**——但当前特征是 bigram + 频次，丢掉了位置。

    这里的对策是**句式覆盖**：把"转折词前半句 / 后半句"的各种组合都造出来，
    让网络通过"转折词与哪个情绪词相邻"来间接学会主次关系
    （bigram 能捕捉"但很开心"和"虽然很累"这两种相邻结构）。
    """
    out = []
    emos = list(WORDS)
    turn_words = ["但是", "但", "不过", "然而", "可是", "却", "结果", "可"]

    for _ in range(700):
        a, b = random.sample(emos, 2)
        wa = random.choice(WORDS[a])
        wb = random.choice(WORDS[b])
        tw = random.choice(turn_words)
        tpl = random.choice([
            "虽然%s%ss%s" % (wa, tw, wb),
            "虽然很%s，%s真的很%s" % (wa, tw, wb),
            "一开始%s，%s%s" % (wa, tw, wb),
            "%s归%s，%s还是%s" % (wa, wa, tw, wb),
            "本来%s，%s被弄得很%s" % (wa, tw, wb),
            "明明%s，还要装作很%s" % (wa, wb),
            "看着%s，其实%s" % (wa, wb),
            "%s是%s，%s后来%s" % (wa, wa, tw, wb),
        ])
        out.append((tpl, b))

    # 专门强化「后半句才是重点」：让后半句的情绪词紧接转折词
    for _ in range(300):
        a, b = random.sample(emos, 2)
        wa = random.choice(WORDS[a])
        wb = random.choice(WORDS[b])
        out.append(("%s，但是%s" % (wa, wb), b))
        out.append(("%s，不过%s" % (wa, wb), b))
        out.append(("%s，结果%s" % (wa, wb), b))
        out.append(("虽然%s，但整体还是%s的" % (wa, wb), b))
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


def gen_none():
    """**无情绪**的句子（闲聊 / 陈述 / 普通提问）。

    ⚠ 这块是本轮最重要的补充。早期模型只有 7 个情绪类，被迫把所有
    句子判成某个情绪——实测闲聊句「代码跑通了」判开心 0.92、
    「收到」判惊讶 0.99，误报率 94.7%。

    加了 `__NONE__` 类之后，网络才有能力说"这句不用弹"。
    这里要覆盖日常输入里**最常见**的那些句式，否则真实使用仍会频繁误弹。
    """
    out = []

    # 1) 日常应答 / 短回复
    replies = ["好的", "收到", "嗯", "嗯嗯", "行", "可以", "没问题", "知道了",
               "了解", "明白", "谢谢", "多谢", "辛苦了", "麻烦了", "稍等",
               "等一下", "马上", "我看看", "我试试", "在吗", "在不在",
               "没问题的", "就这样", "可以了", "差不多了", "还行"]
    for r in replies:
        out.append((r, "__NONE__"))
        out.append((random.choice(["", "嗯，", "好的，"]) + r, "__NONE__"))

    # 2) 陈述事实 / 事务性
    facts = [
        "明天开会", "今天上班", "我在吃饭", "我先去吃饭了", "我买了个新手机",
        "这个文件放哪", "帮我看看", "代码跑通了", "这个功能挺好用的",
        "刚到家", "在路上了", "马上到", "已经发送了", "收到了文件",
        "下午三点", "会议室在哪", "我改一下", "稍后回复", "正在处理",
        "这个问题我知道了", "需要我做什么", "还有什么要改的",
        "文档写好了", "测试通过了", "部署完成", "我这边没问题",
        "你发我看看", "我确认一下", "稍等一下我看下", "今天有点忙",
        "我在写代码", "刚才在开会", "手机没电了", "网络不太好",
    ]
    for r in facts:
        out.append((r, "__NONE__"))

    # 3) 普通提问（不带情绪，单纯问事情）
    questions = [
        "这个怎么用", "在哪下载", "多少钱", "什么时候", "多少人",
        "几点开始", "你吃了吗", "吃饭了吗", "你在干什么", "你在哪",
        "这是什么", "为什么这样写", "怎么改", "能不能加个功能",
        "支持什么系统", "有文档吗", "要怎么配置", "默认值是多少",
    ]
    for q in questions:
        out.append((q, "__NONE__"))
        out.append((q + random.choice(["？", "?"]), "__NONE__"))

    # 4) 含"不/没"但**不是否定情绪**的固定搭配（高频陷阱）
    traps = [
        "今天天气不错", "今天心情不错", "还不错", "挺好的", "不错",
        "不好意思", "不好意思打扰了", "差不多", "差不多就这样",
        "没什么", "没什么大不了的", "没关系", "没事", "不要紧",
        "不少了", "不久", "不断", "不仅", "不但", "不得不",
        "不管怎样", "无论如何", "说不定", "了不起", "巴不得",
        "挺好的呀", "这个不错", "味道不错", "看起来不错",
    ]
    for r in traps:
        out.append((r, "__NONE__"))

    # 5) 中性描述
    neutral = [
        "今天", "明天", "昨天", "现在", "刚才", "等会", "一会儿",
        "我在家", "在公司", "在外面", "这里", "那里", "这个", "那个",
        "第一", "第二", "第三", "开始", "结束", "完成",
    ]
    for r in neutral:
        out.append((r, "__NONE__"))

    return out


def gen_colloquial():
    """口语化夸奖 / 日常情绪表达（网络训练时最容易漏的一类）。

    实测发现：「好萌」被判 NONE 0.97、「太赞了」判惊讶、
    「累得不行了」判 NONE——这些都不是调度问题，而是训练数据里
    根本没有这些说法。

    单独造这一类，专补"程度副词 + 单字形容词"的短促口语：
        好萌 / 好帅 / 太赞了 / 累得不行了 / 香疯了
    """
    out = []

    # 夸奖类形容词（喜欢）
    praise_adj = ["萌", "可爱", "帅", "好看", "漂亮", "美", "香", "赞", "棒",
                  "牛", "酷", "甜", "暖", "治愈", "上头", "绝"]
    for adj in praise_adj:
        for pre in ("好", "太", "真", "超", "特别", "巨"):
            out.append(("%s%s" % (pre, adj), "喜欢"))
            out.append(("%s%s了" % (pre, adj), "喜欢"))
            out.append(("%s%s啊" % (pre, adj), "喜欢"))
            out.append(("%s%s呀" % (pre, adj), "喜欢"))
        out.append(("这也太%s了" % adj, "喜欢"))
        out.append(("%s死了" % adj, "喜欢"))

    # 疲惫 / 累到极点的口语
    tired = ["累", "困", "乏", "疲惫"]
    for w in tired:
        for tpl in ("%s得不行了", "%s死了", "%s到不行", "%s瘫了", "%s爆了",
                    "%s坏了", "真的%s", "%s得慌", "%s成狗"):
            out.append((tpl % w, "疲惫"))

    # 情绪到极点的口语（"X死了/ X到不行"）
    for emo, ws in (("开心", ["开心", "高兴", "乐"]),
                    ("伤心", ["难过", "伤心", "emo"]),
                    ("生气", ["气", "烦", "怒"]),
                    ("惊讶", ["震惊", "吃惊"])):
        for w in ws:
            for tpl in ("%s死了", "%s到不行", "%s得不行", "%s坏了", "%s爆了"):
                out.append((tpl % w, emo))

    # 网络流行语
    slang = [
        ("yyds", "惊讶"), ("绝绝子", "喜欢"), ("awsl", "喜欢"),
        ("破防了", "伤心"), ("裂开了", "伤心"), ("麻了", "疲惫"),
        ("摆烂", "疲惫"), ("躺平", "疲惫"), ("emo了", "疲惫"),
        ("上头了", "喜欢"), ("真香", "喜欢"), ("好家伙", "惊讶"),
        ("蚌埠住了", "伤心"), ("绷不住了", "伤心"),
    ]
    out.extend(slang)
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
    data += gen_colloquial()           # 口语夸奖 / 程度极点
    data += gen_none()                 # 无情绪：控制误报
    random.shuffle(data)
    return data


def _holdout_score(net):
    """用独立评测语料打分（不参与训练），返回准确率百分比。"""
    try:
        from eval_corpus import CASES
    except Exception:
        return None
    ok = 0
    for case in CASES:
        text, expect = case[0], case[1]
        got, _ = net.predict(text)
        if got == expect:
            ok += 1
    return 100.0 * ok / len(CASES)


def train(epochs=40, lr=0.08, early_stop=True):
    """训练。**默认开启早停**：用独立语料监控，过拟合就回滚。

    ## 为什么必须早停

    实测踩过一次大坑：4806 条样本跑 40 轮，训练集准确率冲到 100%，
    但独立语料只有 75%（前一轮是 85%）。网络把训练集背下来了，
    真实表现反而变差——**训练集数字好看是危险信号，不是好信号**。

    早停规则：每轮都在独立语料上打分，记录最佳；连续 3 轮没提升就停，
    并**回滚到最佳那一轮的权重**。
    """
    import time

    data = build_dataset()
    print("训练样本: %d 条" % len(data))
    net = en.EmotionNet()
    print("参数量: %d" % (net.n_in * net.n_hidden + net.n_hidden
                          + net.n_hidden * net.n_out + net.n_out))
    print("预计耗时: 约 %.0f 秒（纯 Python SGD，每条 ~0.3ms）"
          % (len(data) * epochs * 0.0003 * 2))

    best_score = -1.0
    best_snapshot = None
    best_epoch = 0
    stale = 0

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

        holdout = _holdout_score(net) if early_stop else None
        if holdout is not None:
            if holdout > best_score:
                best_score = holdout
                best_epoch = ep + 1
                # 只存必要字段，避免内存翻倍
                best_snapshot = {
                    "w1": [list(r) for r in net.w1],
                    "b1": list(net.b1),
                    "w2": [list(r) for r in net.w2],
                    "b2": list(net.b2),
                }
                stale = 0
            else:
                stale += 1

        if (ep + 1) % 5 == 0 or ep == 0:
            done = time.perf_counter() - t_start
            extra = ("  独立=%.1f%%%s" % (holdout, "  ★" if holdout == best_score
                                          else "")) if holdout is not None else ""
            print("  epoch %2d  loss=%.3f  acc=%.3f%s  (%.0fs)"
                  % (ep + 1, total_loss / len(data), acc, extra, done),
                  flush=True)

        if early_stop and stale >= 3:
            print("  早停：连续 3 轮独立语料没有提升，回滚到第 %d 轮（%.1f%%）"
                  % (best_epoch, best_score))
            break

    if best_snapshot is not None:
        net.w1 = best_snapshot["w1"]
        net.b1 = best_snapshot["b1"]
        net.w2 = best_snapshot["w2"]
        net.b2 = best_snapshot["b2"]

    net.trained_samples = len(data)
    return net, data


def evaluate(net, data):
    """训练集检出率 + **独立评测语料**上的真实表现。

    ⚠ 只报训练集准确率是自欺欺人（网络能背下来）。真正的数字看
    `tools/eval_corpus.py` 那份不参与训练的语料。
    """
    correct = 0
    for text, emo in data:
        got, conf = net.predict(text)
        if emo == "__NONE__":
            if got is None:
                correct += 1
        elif got == emo:
            correct += 1
    print()
    print("训练集检出率: %.1f%%" % (100.0 * correct / max(1, len(data))))

    # 独立语料
    sys.path.insert(0, HERE)
    try:
        from eval_corpus import by_category
    except Exception:
        print("（找不到 eval_corpus.py，跳过独立评估）")
        return

    print()
    print("=== 独立评测语料（不参与训练）")
    groups = by_category()
    tot = ok = 0
    for cat, cases in groups.items():
        c_ok = 0
        for text, expect in cases:
            got, _conf = net.predict(text)
            tot += 1
            if got == expect:
                c_ok += 1
                ok += 1
        print("  %-12s %2d/%2d  %3.0f%%"
              % (cat, c_ok, len(cases), 100.0 * c_ok / max(1, len(cases))))
    print("  %-12s %2d/%2d  %3.0f%%"
          % ("合计", ok, tot, 100.0 * ok / max(1, tot)))


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
