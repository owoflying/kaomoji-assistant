"""评估工具索引：哪个脚本该在什么时候跑。

    python tools/eval_run.py            跑全部（推荐）
    python tools/eval_run.py --quick    只跑评测集（快，防退化）

## 三个数据集的定位（别搞混）

| 数据集 | 文件 | 是否参与优化 | 什么时候看 |
|--------|------|------------|-----------|
| **评测集** | eval_corpus.py（137 句）| **是** | 防退化：改完不能比之前差 |
| **留出集** | eval_holdout4.py（68 句）| **否** | 真实泛化：这才是能对外说的数字 |
| 历史泛化集 | eval_generalization*.py | 是 | 保留作回归，不作结论 |

## 血泪教训

第 5 轮时评测集 97%、留出集只有 46%。差距全部来自"对着测试集改代码"。

**只有留出集的数字可信**。评测集的作用是保证"改了 A 别弄坏 B"，
不是用来证明效果好。

## 正确的改进循环

    1. 跑留出集，收集失败样本
    2. 把它们收进 train_emotion_model.REAL_EXPRESSIONS（当**训练数据**）
    3. 重训（train_emotion_model.py）
    4. 换一批**全新的**留出集，再测

千万不要"照着留出集的失败改规则分支"——那样它立刻就不再是留出集。
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PY = sys.executable


def _run(script, title):
    print()
    print("=" * 60)
    print(title)
    print("=" * 60)
    path = os.path.join(HERE, script)
    if not os.path.exists(path):
        print("  （脚本不存在，跳过）")
        return None
    r = subprocess.run([PY, path], cwd=ROOT, capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    out = (r.stdout or "") + (r.stderr or "")
    # 只打印关键行，避免刷屏
    for line in out.splitlines():
        if any(k in line for k in ("准确率", "漏判", "误报", "ERR", "===")):
            print(line)
    return out


def main():
    quick = "--quick" in sys.argv

    _run("eval_emotion.py", "评测集（防退化，已参与优化）")
    if not quick:
        _run("eval_holdout4.py", "留出集第 4 批（未参与优化 —— 看这个）")

    print()
    print("=" * 60)
    print("说明")
    print("=" * 60)
    print("  评测集：确认没有改坏既有能力（它参与过优化，数字偏高是正常的）")
    if not quick:
        print("  留出集：真实泛化水平，对外只报这个")
        print("  改完要改进的话：把留出集失败句收进 REAL_EXPRESSIONS 再训，")
        print("  然后写新的一批留出集（见本文件顶部说明）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
