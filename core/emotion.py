"""情绪识别：关键词匹配 -> 颜文字分类名。

参考「情绪颜文字输入法」文章的核心思路：持续收集用户输入，用轻量的关键词
匹配判定当前情绪，从而自动推荐对应分类的颜文字。这里只做关键词匹配
（速度极快、零依赖），不引入任何模型。

映射到的分类名需与 data/kaomoji.json 中的 category.name 完全一致。
"""
from typing import Optional

# 情绪关键词库（与现有颜文字分类对齐）。命中顺序即优先级（靠前的先匹配）。
EMOTION_KEYWORDS = {
    "开心": [
        "开心", "高兴", "哈哈", "哈哈哈", "嘻嘻", "笑", "可爱", "棒", "太棒",
        "666", "么么", "愉悦", "欢乐", "兴奋", "耶", "庆祝", "happy", "haha",
        "笑死", "笑不活了", "爷青回", "哈哈哈哈", "乐", "美滋滋", "爽",
    ],
    "伤心": [
        "伤心", "难过", "哭", "呜呜", "悲伤", "泪", "想哭", "委屈", "心碎",
        "郁闷", "低落", "sad", "tt",
        "破防", "破大防", "裂开", "蚌埠", "绷不住", "哭死", "破防了",
    ],
    "生气": [
        "生气", "愤怒", "火大", "讨厌", "烦", "气死", "可恶", "无语", "恨",
        "怒", "angry",
        "典中典", "老六", "红温", "服了", "无语子", "栓Q", "离谱",
    ],
    "惊讶": [
        "惊讶", "震惊", "哇塞", "天哪", "卧槽", "不敢相信", "居然", "竟然",
        "我的天", "厉害", "牛", "amazing", "wow",
        "神了", "封神", "yyds", "炸裂", "绝了", "牛逼", "泰裤辣",
        "起飞", "太强了", "牛哇", "绝绝",
    ],
    "喜欢": [
        "喜欢", "爱", "心动", "萌", "爱了", "中意", "稀饭", "宝贝", "亲",
        "抱抱", "love",
        "绝绝子", "awsl", "爱了爱了", "prpr", "舔屏", "妈耶",
    ],
    "思考": [
        "？", "?", "为什么", "怎么", "什么", "如何", "吗", "咋", "是不是",
        "思考", "想想", "考虑", "琢磨", "寻思", "盘算", "沉思", "纠结",
        "why", "hmm", "think",
        "u1s1", "yysy", "有一说一", "细说", "懂的都懂", "退退退",
    ],
    "疲惫": [
        "力竭", "精疲力尽", "疲惫", "累了", "好累", "好困", "困了", "犯困",
        "想睡", "睡了", "躺平", "emo", "麻了", "顶不住", "撑不住", "摆烂",
        "累死", "乏", "困死",
    ],
}


def detect(text: str) -> Optional[str]:
    """在一段输入文本里检测情绪，返回分类名；未命中返回 None。

    按 EMOTION_KEYWORDS 的声明顺序取第一个命中项（保留旧行为，供外部调用）。
    """
    if not text:
        return None
    for emotion, keywords in EMOTION_KEYWORDS.items():
        for kw in keywords:
            if kw in text:
                return emotion
    return None


def detect_last(text: str):
    """返回文本中「最靠右」命中的情绪及其位置：(emotion, kw, pos)；未命中返回 None。

    自动弹出场景必须用它而不是 detect()：用户刚敲下的字在最右边，
    若按声明顺序取第一个命中，会出现「前面打过『开心』后，再打『为什么』
    仍然一直推荐开心」的粘滞感。
    """
    if not text:
        return None
    best = None  # (pos, kw_len, emotion, kw)
    for emotion, keywords in EMOTION_KEYWORDS.items():
        for kw in keywords:
            pos = text.rfind(kw)
            if pos < 0:
                continue
            # 位置更靠右者优先；位置相同时取更长的关键词（如「哈哈哈」优于「哈哈」）
            if best is None or (pos, len(kw)) > (best[0], best[1]):
                best = (pos, len(kw), emotion, kw)
    if best is None:
        return None
    return best[2], best[3], best[0]
