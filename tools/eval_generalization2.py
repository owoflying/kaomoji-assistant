"""第二批泛化验证：又一批**全新**句子，检验 100% 是否只是巧合。

第一批发泛化集在补完词表后到了 30/30，但那 30 句已经"看过"了
（我是照着它们的失败去补的词表）。严格来说它们也变成了训练数据。

这里再造 30 句完全没出现过的，且**刻意避开**前面补过的词，
看真实泛化能力。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

from core import emotion_classifier  # noqa: E402

FRESH = [
    # 开心（换一种说法）
    ("今天过得挺顺", "开心"),
    ("总算松了口气", "开心"),
    ("这单成了", "开心"),
    ("心情飞扬", "开心"),
    # 伤心
    ("有点想不通人情冷暖", "伤心"),
    ("鼻子一酸", "伤心"),
    ("心里发酸", "伤心"),
    ("这结局太虐了", "伤心"),
    # 生气
    ("真是忍无可忍", "生气"),
    ("气不打一处来", "生气"),
    ("这操作看不懂", "生气"),
    ("简直莫名其妙", "生气"),
    # 惊讶
    ("差点没认出他", "惊讶"),
    ("我眼镜碎了", "惊讶"),
    ("竟然真成了", "惊讶"),
    # 喜欢
    ("一眼就相中了", "喜欢"),
    ("这东西真上头", "喜欢"),
    ("太合心意了", "喜欢"),
    # 思考
    ("这里我绕不出来", "思考"),
    ("这条逻辑不通", "思考"),
    ("我还得再想想", "思考"),
    # 疲惫
    ("浑身没劲", "疲惫"),
    ("眼皮打架", "疲惫"),
    ("这周连轴转", "疲惫"),
    # 不该弹
    ("我把配置改了", None),
    ("等下同步给你", None),
    ("这个再跑一遍", None),
    ("有问题随时说", None),
    ("接口返回什么", None),
    ("我先离线一会", None),
]


def main():
    clf = emotion_classifier.EmotionClassifier()
    ok = miss = fp = 0
    should = sum(1 for _t, e in FRESH if e is not None)
    print("=== 第二批全新句子")
    print()
    for text, expect in FRESH:
        r = clf.detect(text)
        got = r[0] if r else None
        good = got == expect
        ok += good
        if expect is not None and got is None:
            miss += 1
        if expect is None and got is not None:
            fp += 1
        if not good:
            print("  ERR %-18s 期望=%-5s 实际=%-6s [%s]"
                  % (text, expect or "不弹", got or "不弹",
                     clf.last_source or "-"))

    total = len(FRESH)
    print()
    print("=== 第二批泛化")
    print("  准确率: %.1f%% (%d/%d)" % (100.0 * ok / total, ok, total))
    print("  漏判  : %.1f%% (%d/%d)"
          % (100.0 * miss / should, miss, should))
    print("  误报  : %.1f%% (%d/%d)"
          % (100.0 * fp / (total - should), fp, total - should))


if __name__ == "__main__":
    main()
