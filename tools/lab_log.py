"""炼丹记录：每次训练的实验配置与结果，避免重复踩坑。

格式：日期 | 数据量 | 特征 | 轮数 | 训练集 | 独立语料 | 双层协作 | 结论
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
LOG = os.path.join(ROOT, "docs", "emotion_lab_log.md")

HEADER = """# 情绪识别炼丹记录

每次改模型/数据/特征后，跑一次 `tools/eval_emotion.py` 并在此追加一行。
**只看独立语料与双层协作的数字**，训练集准确率没有意义（会背题）。

## 指标说明

- **独立语料**：`tools/eval_corpus.py`（75 句，不参与训练）
- **双层协作**：`emotion_classifier.detect()` 的实际表现 —— 这是用户感受到的
- **漏判**：应弹没弹（用户觉得功能没用）
- **误报**：不该弹却弹（用户觉得功能很烦）

## 实验记录

| 日期 | 数据量 | 输入维度 | 轮数 | 训练集 | 独立语料 | 双层准确率 | 漏判 | 误报 | 结论 |
|------|--------|----------|------|--------|----------|------------|------|------|------|
"""


def append(rows, note=""):
    lines = []
    for r in rows:
        lines.append("| %s | %s | %s | %s | %s | %s | %s | %s | %s | %s |" % (
            r.get("date", ""), r.get("samples", ""), r.get("dim", ""),
            r.get("epochs", ""), r.get("train_acc", ""), r.get("holdout", ""),
            r.get("two_layer", ""), r.get("miss", ""), r.get("fp", ""),
            r.get("note", "")))
    if note:
        lines.append("")
        lines.append(note)

    exists = os.path.exists(LOG)
    with open(LOG, "a", encoding="utf-8") as f:
        if not exists:
            f.write(HEADER)
        f.write("\n".join(lines) + "\n")
    print("已追加 %d 条记录到 %s" % (len(rows), LOG))


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--init":
        if not os.path.exists(LOG):
            os.makedirs(os.path.dirname(LOG), exist_ok=True)
            with open(LOG, "w", encoding="utf-8") as f:
                f.write(HEADER)
            print("已初始化", LOG)
        else:
            print("已存在", LOG)
    else:
        print("用法: python tools/lab_log.py --init")
