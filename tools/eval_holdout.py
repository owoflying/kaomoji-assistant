"""泛化验证：用**从未参与优化**的句子测真实水平。

## 血的教训

第 5 轮结束时：
  * 评测集（137 句，我照着改规则的）→ 97%
  * 第一批泛化（30 句，我又照着改了词表）→ 100%
  * 第二批泛化（30 句，又改了）→ 70%
  * **第三批（26 句，完全没碰过）→ 46%**

结论：**只要某批句子被我拿来改进过，它就不再是测试集了**。
所以这个文件里的句子会不断轮换——测完就得换新的，
或者干脆把它们收进训练集（`train_emotion_model.REAL_EXPRESSIONS`），
然后写新句子。

## 用法

    .venv\\Scripts\\python.exe tools/eval_holdout.py

输出准确率 / 漏判 / 误报，以及每个错误的具体来源层。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

from core import emotion_classifier  # noqa: E402

#: **当前这一批**留出集。用完之后记得换掉（见模块说明）。
#:
#: 上一批（可算熬出头了 / 眼眶发热 / 什么破玩意儿 …）已经收进
#: `train_emotion_model.REAL_EXPRESSIONS`，不能再用。这是新的一批。
HOLDOUT = [
    # 开心
    ("这会儿心情正好", "开心"),
    ("事办妥了", "开心"),
    ("总算圆满了", "开心"),
    ("这波不亏", "开心"),
    # 伤心
    ("心里凉了半截", "伤心"),
    ("挺失落的", "伤心"),
    ("委屈得说不出话", "伤心"),
    # 生气
    ("简直无法理喻", "生气"),
    ("越说越上火", "生气"),
    ("真够可以的", "生气"),
    # 惊讶
    ("这不太可能", "惊讶"),
    ("真是活见鬼", "惊讶"),
    ("我没眼花吧", "惊讶"),
    # 喜欢
    ("看着就舒服", "喜欢"),
    ("挺对我口味", "喜欢"),
    ("越看越中意", "喜欢"),
    # 思考
    ("有点想不明白", "思考"),
    ("这个得捋捋", "思考"),
    ("逻辑上说不通", "思考"),
    # 疲惫
    ("一点劲都提不上来", "疲惫"),
    ("乏得厉害", "疲惫"),
    ("整个人都懈了", "疲惫"),
    # 不该弹
    ("我改完发你", None),
    ("这个模块谁负责", None),
    ("先跑个测试", None),
    ("我看下日志", None),
    ("下周再说吧", None),
    ("需要重启吗", None),
    # ---- 扩充：28 句噪声太大（±8%），加到 60 句 ----
    ("今天出门挺顺", "开心"),
    ("这事儿成了", "开心"),
    ("心里美", "开心"),
    ("心情相当可以", "开心"),
    ("乐了一阵", "开心"),
    ("胸口发闷", "伤心"),
    ("有点撑不下去", "伤心"),
    ("被晾在一边", "伤心"),
    ("心里不是滋味", "伤心"),
    ("鼻子有点酸", "伤心"),
    ("火一下就上来了", "生气"),
    ("真让人来气", "生气"),
    ("这操作真无语", "生气"),
    ("想发火", "生气"),
    ("越看越气", "生气"),
    ("这不合常理", "惊讶"),
    ("真没料到", "惊讶"),
    ("我懵了", "惊讶"),
    ("太意外了", "惊讶"),
    ("有点出乎意料", "惊讶"),
    ("越看越爱", "喜欢"),
    ("挺看得上的", "喜欢"),
    ("这东西省心", "喜欢"),
    ("挺讨喜", "喜欢"),
    ("合我口味", "喜欢"),
    ("有点绕", "思考"),
    ("这里得想清楚", "思考"),
    ("逻辑没理顺", "思考"),
    ("还得再看看", "思考"),
    ("有点不明白", "思考"),
    ("眼皮直打架", "疲惫"),
    ("脑袋发木", "疲惫"),
    ("浑身发软", "疲惫"),
    ("实在熬不动", "疲惫"),
    ("提不起精神", "疲惫"),
    ("这个改一下", None),
    ("我先看看再说", None),
    ("等下我确认", None),
    ("这份要不要留", None),
    ("按之前的来", None),
    ("能加个过滤吗", None),
    ("大概什么时候", None),
    ("先这样吧", None),
]


def main():
    if not emotion_classifier.shared().has_net:
        print("模型未加载")
        return 1

    clf = emotion_classifier.EmotionClassifier()
    ok = miss = fp = 0
    should = sum(1 for _t, e in HOLDOUT if e is not None)
    errors = []

    for text, expect in HOLDOUT:
        r = clf.detect(text)
        got = r[0] if r else None
        if got == expect:
            ok += 1
        else:
            errors.append((text, expect, got, clf.last_source or "-"))
            if expect is not None and got is None:
                miss += 1
            if expect is None and got is not None:
                fp += 1

    total = len(HOLDOUT)
    print("=== 留出集（%d 句，未参与优化）" % total)
    if errors:
        print()
        for text, exp, got, src in errors:
            print("  ERR %-18s 期望=%-5s 实际=%-6s [%s]"
                  % (text, exp or "不弹", got or "不弹", src))

    print()
    print("  准确率: %.1f%% (%d/%d)" % (100.0 * ok / total, ok, total))
    print("  漏判  : %.1f%% (%d/%d)"
          % (100.0 * miss / should, miss, should))
    print("  误报  : %.1f%% (%d/%d)"
          % (100.0 * fp / (total - should), fp, total - should))
    print()
    print("  ⚠ 测完这批就失效了——要改进的话，把它收进")
    print("    train_emotion_model.REAL_EXPRESSIONS，然后写新的留出集。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
