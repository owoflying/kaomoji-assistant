"""评测情绪识别：分区准确率 + 漏判率 + 误报率。

    .venv\\Scripts\\python.exe tools/eval_emotion.py
    .venv\\Scripts\\python.exe tools/eval_emotion.py --net-only   # 只看网络
    .venv\\Scripts\\python.exe tools/eval_emotion.py --kw-only    # 只看关键词

## 三个指标，哪个都不能牺牲

    * **准确率**：判对的 / 总数
    * **漏判率**：本应弹窗却没弹 —— 用户觉得"这功能没用"
    * **误报率**：本不该弹却弹了 —— 用户觉得"这功能好烦"

调参时最忌讳只盯准确率：把阈值调高能减少误报，但漏判会暴涨。
所以三个数一起看。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from eval_corpus import CASES, by_category  # noqa: E402


def _load(mode):
    if mode == "kw":
        from core.emotion import detect_last
        return lambda t: detect_last(t)
    if mode == "net":
        from core import emotion_net as en
        net = en.EmotionNet.load(os.path.join(ROOT, "data", "emotion_model.json"))
        if net is None:
            print("找不到模型文件")
            return None

        def _f(t):
            e, c = net.predict(t)
            return (e, "", 0) if e else None
        return _f
    from core import emotion_classifier
    clf = emotion_classifier.EmotionClassifier()
    return lambda t: clf.detect(t) if clf.has_net else _load("kw")(t)


def evaluate(mode="both", verbose=True):
    fn = _load(mode)
    if fn is None:
        return None

    groups = by_category()
    rows = []
    should_trigger = 0
    actually_triggered = 0
    correct = 0
    missed = 0
    false_pos = 0

    for cat, cases in groups.items():
        c_total = c_correct = c_miss = c_fp = 0
        for text, expect in cases:
            hit = fn(text)
            got = hit[0] if hit else None
            c_total += 1
            if expect is not None:
                should_trigger += 1
                if got is None:
                    c_miss += 1
                    missed += 1
                elif got == expect:
                    c_correct += 1
                    correct += 1
            else:
                if got is not None:
                    c_fp += 1
                    false_pos += 1
                else:
                    c_correct += 1
                    correct += 1
            if got is not None:
                actually_triggered += 1
            if verbose and got != expect:
                mark = "漏" if (expect and not got) else ("误" if not expect else "错")
                print("    [%s] %-22s 期望=%-4s 实际=%s"
                      % (mark, text, expect or "不弹", got or "不弹"))
        rows.append((cat, c_total, c_correct, c_miss, c_fp))

    total = len(CASES)
    if verbose:
        print()
        print("=== 分类别")
        print("  %-12s %4s %6s %6s %6s" % ("类别", "总数", "准确率", "漏判", "误报"))
        for cat, n, ok, miss, fp in rows:
            print("  %-12s %4d %5.0f%% %6d %6d"
                  % (cat, n, 100.0 * ok / max(1, n), miss, fp))

    print()
    print("=== 总体（%s）" % {"both": "双层协作", "net": "仅网络",
                            "kw": "仅关键词"}[mode])
    print("  总准确率  : %.1f%%  (%d/%d)"
          % (100.0 * correct / total, correct, total))
    print("  漏判率    : %.1f%%  (%d/%d 应弹的没弹)"
          % (100.0 * missed / max(1, should_trigger), missed, should_trigger))
    print("  误报率    : %.1f%%  (%d/%d 不该弹却弹)"
          % (100.0 * false_pos / max(1, total - should_trigger),
             false_pos, total - should_trigger))
    return {
        "acc": 100.0 * correct / total,
        "miss": 100.0 * missed / max(1, should_trigger),
        "fp": 100.0 * false_pos / max(1, total - should_trigger),
        "correct": correct, "total": total,
        "missed": missed, "should": should_trigger,
        "false_pos": false_pos,
    }


def main():
    args = sys.argv[1:]
    if "--net-only" in args:
        return evaluate("net")
    if "--kw-only" in args:
        return evaluate("kw")
    if "--compare" in args:
        print("=" * 60)
        print("仅关键词")
        print("=" * 60)
        evaluate("kw", verbose=False)
        print()
        print("=" * 60)
        print("仅网络")
        print("=" * 60)
        evaluate("net", verbose=False)
        print()
        print("=" * 60)
        print("双层协作")
        print("=" * 60)
        return evaluate("both", verbose=True)
    return evaluate("both", verbose=True)


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
