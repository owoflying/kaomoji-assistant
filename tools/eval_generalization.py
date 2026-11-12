"""泛化验证：用**全新**的句子测，检查是否过拟合了评测集。

## 为什么必须做这一步

前面几轮优化顺序是"看错题 → 改规则"，很容易变成对着答案改代码。
96.4% 可能只是"评测集拟合度"，不是真实水平。

这里临时造一批**没参与任何训练、也没参与规则调试**的句子，
看实际表现。如果差距很大，说明规则调过头了。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

from core import emotion_classifier  # noqa: E402

# 全新句子（写这个文件时才想出来的，之前的语料里没有）
FRESH = [
    ("今天心情特别好", "开心"),
    ("笑死我了哈哈哈", "开心"),
    ("总算是搞定了", "开心"),
    ("这波赚了", "开心"),
    ("心里空落落的", "伤心"),
    ("感觉被抛弃了", "伤心"),
    ("越想越难受", "伤心"),
    ("气到想砸键盘", "生气"),
    ("这人怎么这么离谱", "生气"),
    ("真让人火大", "生气"),
    ("我眼睛没花吧", "惊讶"),
    ("居然真给我抽中了", "惊讶"),
    ("这也太巧了吧", "惊讶"),
    ("越看越顺眼", "喜欢"),
    ("这配色真对我胃口", "喜欢"),
    ("搞不清这个逻辑", "思考"),
    ("这部分我没弄明白", "思考"),
    ("到底哪出问题了", "思考"),
    ("困得脑袋发沉", "疲惫"),
    ("实在提不起劲", "疲惫"),
    # 不该弹的
    ("我先看看情况", None),
    ("需要我做什么", None),
    ("这个功能没做好", None),
    ("再改一版", None),
    ("好的我马上", None),
    ("下午两点", None),
    ("文档在共享盘", None),
    ("你先发我", None),
    ("这个我不熟悉", None),
    ("稍微等一下", None),
]


def main():
    clf = emotion_classifier.EmotionClassifier()
    ok = miss = fp = 0
    should = sum(1 for _t, e in FRESH if e is not None)
    print("=== 全新句子（未参与训练/调规则）")
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
        mark = "OK " if good else "ERR"
        print("  %s %-18s 期望=%-5s 实际=%-6s [%s]"
              % (mark, text, expect or "不弹", got or "不弹",
                 clf.last_source or "-"))

    total = len(FRESH)
    print()
    print("=== 泛化结果")
    print("  准确率: %.1f%% (%d/%d)" % (100.0 * ok / total, ok, total))
    print("  漏判  : %.1f%% (%d/%d)"
          % (100.0 * miss / should, miss, should))
    print("  误报  : %.1f%% (%d/%d)"
          % (100.0 * fp / (total - should), fp, total - should))
    print()
    print("  （对比：评测集 96.4%）")
    gap = 96.4 - 100.0 * ok / total
    if gap > 10:
        print("  ⚠ 差距 %.1f 个百分点，规则可能过拟合了评测集" % gap)
    elif gap > 5:
        print("  差距 %.1f 个百分点，可接受" % gap)
    else:
        print("  差距 %.1f 个百分点，泛化良好" % gap)


if __name__ == "__main__":
    main()
