"""组合式颜文字：把"积木"拼成自定义颜文字。

## 为什么需要

库里的颜文字是固定字符串。用户想要 `(◕‿◕)` 的"得意"版、或者
`(づ｡◕‿‿◕｡)づ` 里换个眼睛，只能自己手打——而这正是颜文字的
乐趣所在，也是最容易卡住的地方（符号难记、对不齐）。

## 积木模型

颜文字拆成三个可换的部分 + 一对括号：

    (   ◕   ‿   ◕   )
    ↑   ↑   ↑   ↑   ↑
   左   左眼  嘴   右眼  右

大多数颜文字符合 `(眼 嘴 眼)` 这个结构，但不是全部：

    (=^･ω･^=)      猫脸，两侧是 =^ 和 ^=
    (ノಠ益ಠ)ノ彡┻━┻  带"手"和"桌子"
    ╰(*°▽°*)╯      带装饰符号

所以积木不是强制的——只提供"快速拼出一个常规颜文字"的捷径，
拼出来的结果就是普通字符串，存进"我的颜文字"后与手打的无异。

## 设计取舍

**不做语法解析**。试图自动把任意颜文字拆成积木，边界情况无穷无尽
（嵌套括号、多重符号、Unicode 组合字符）。这里只提供：
  * 一份精选的积木词表（左括号 / 眼睛 / 嘴 / 右括号 / 装饰）；
  * 一个纯函数 `compose()` 把它们拼起来。

用户看到的是几个下拉框，选完就得到结果，不满意直接手改。
"""

def _merge(*lists):
    """合并去重并保持首次出现的顺序。"""
    out = []
    seen = set()
    for seq in lists:
        for x in seq:
            if x not in seen:
                seen.add(x)
                out.append(x)
    return out


#: 经人工挑选的协调组合，供「随机」使用。
#:
#: 每条是 (左括号, 眼睛, 嘴, 右括号, 装饰)。挑选原则：
#:   * 带手臂的括号配 `)ノ` 这种右侧；
#:   * `=`/`^` 这类窄眼睛配宽嘴（`‿` `▽`），不配 `ᴥ`；
#:   * 装饰只在圆形括号上用，带手臂的加装饰会显得乱。
GOOD_COMBOS = [
    ("(", "◕", "‿", ")", ""),
    ("(", "◕", "‿", ")", "♡"),
    ("(", "•", "‿", ")", ""),
    ("(", "･", "ω", ")", ""),
    ("(", "•̀", "ω", ")", ""),
    ("(", "°", "▽", ")", ""),
    ("(", "＾", "▽", ")", ""),
    ("(", "^", "‿", ")", ""),
    ("(", "◠", "◡", ")", ""),
    ("(", "◡", "‿", ")", ""),
    ("(", "ᴗ", "‿", ")", "✧"),
    ("(", "✧", "‿", ")", ""),
    ("(", "★", "‿", ")", ""),
    ("(", "T", "﹏", ")", ""),
    ("(", "ಠ", "益", ")", ""),
    ("(", "⊙", "﹏", ")", ""),
    ("(", "◕", "◡", ")", "♪"),
    ("ヽ(", "・", "∀", ")ﾉ", ""),
    ("ヽ(", "°", "▽", ")ノ", ""),
    ("ヾ(", "＾", "∇", ")", ""),
    ("＼(", "≧", "▽", ")／", ""),
    ("╰(", "*", "°", ")╯", ""),
    ("(", "◕", "‿", ")づ", ""),
    ("(", "´", "ω", ")ﻭ", ""),
    ("(", "•̀", "ᴗ", ")و", "✧"),
    ("(*", "´", "∀", ")", "*"),
    ("(", "๑", "˃ᴗ˂", ")ﻭ", ""),
    ("(", "✿", "‿", ")", ""),
]

def _parts_from_good_combos():
    """从 GOOD_COMBOS 里抽出各位置出现过的部件。"""
    left, eye, mouth, right, decor = [], [], [], [], []
    for l, e, m, r, d in GOOD_COMBOS:
        left.append(l)
        eye.append(e)
        mouth.append(m)
        right.append(r)
        if d and d not in decor:
            decor.append(d)
    return left, eye, mouth, right, decor


_GL, _GE, _GM, _GR, _GD = _parts_from_good_combos()

#: 下拉框实际展示的候选 = 精选组合里出现过的 + 手动补充的。
#:
#: 先放"实战用过"的部件，用户翻下拉时先看到靠谱的；
#: 再放其余候选，供想折腾的人挑。
LEFT = _merge(_GL, [
    "(", "（", "ヽ(", "ヾ(", "＼(", "┌(", "╰(", "（´", "(*",
])

EYES = _merge(_GE, [
    "◕", "◔", "•", "･", "°", "^", "`", "＞", "＜", "= =", "T", "u",
    "ಠ", "✧", "★", "☉", "⊙", "o", "O", "ᴗ", "ᵕ", "•̀", "•́", "◠", "◡",
])

MOUTHS = _merge(_GM, [
    "‿", "ω", "ᵕ", "ᴗ", "v", "▽", "∀", "ヮ", "益", "﹏", "－", "_",
    "ε", "з", "o", "O", "へ", "◡", "△", "ᴥ", "ᗜ", "✕",
])

RIGHT = _merge(_GR, [
    ")", "）", ")ノ", ")ﾉ", ")ゞ", ")و", ")ﻭ", ")づ", ")╯",
])

DECOR = _merge(_GD, [
    "", "♡", "❤", "✧", "☆", "♪", "～", "ゞ", "✿", "★", "❀", "！", "~",
])

#: 眼睛的镜像替换表（左右翻转的写法）
_MIRROR = {
    "•̀": "•́", "•́": "•̀", "＞": "＜", "＜": "＞",
    "◕": "◕", "◔": "◔", "ᵕ": "ᵕ", "ᴗ": "ᴗ",
}


def mirror_eye(eye):
    """给出左眼对应的右眼写法。

    大多数眼睛左右对称，直接用；少数需要翻转（`•̀` ↔ `•́`、`＞` ↔ `＜`）。
    """
    return _MIRROR.get(eye, eye)


def compose(left="(", eye="◕", mouth="‿", right=")", decor="",
            mirror=True):
    """把积木拼成一个颜文字。

    :param mirror: 右眼是否用左眼的镜像（`False` 则左右相同）
    :return: 拼好的字符串

    >>> compose("(", "◕", "‿", ")")
    '(◕‿◕)'
    >>> compose("(", "•̀", "ω", ")")
    '(•̀ω•́)'
    """
    le = str(eye)
    re_ = mirror_eye(le) if mirror else le
    return "%s%s%s%s%s%s" % (left, le, mouth, re_, right, decor)


def parts_of(text):
    """尝试把一个颜文字拆回积木（能拆就拆，拆不了返回 None）。

    只处理最典型的 `(眼 嘴 眼)` 结构。用于「编辑已有的自定义颜文字」：
    能拆就预填下拉框，拆不了就让用户直接改文本。

    识别方法：从两端剥掉括号/装饰，再看中间是否形如 `眼 + 嘴 + 眼`
    （且左右眼互为镜像）。
    """
    s = str(text or "").strip()
    if not s:
        return None
    # 先剥装饰
    decor = ""
    for d in sorted(DECOR, key=len, reverse=True):
        if d and s.endswith(d):
            decor = d
            s = s[: -len(d)]
            break
    # 再剥括号
    left = ""
    for L in sorted(LEFT, key=len, reverse=True):
        if s.startswith(L):
            left = L
            s = s[len(L):]
            break
    right = ""
    for R in sorted(RIGHT, key=len, reverse=True):
        if s.endswith(R):
            right = R
            s = s[: -len(R)]
            break
    if not left or not right or not s:
        return None
    # 中间应当是「眼 嘴 眼」：嘴是已知积木之一，且两侧剩余互为镜像
    for m in sorted(MOUTHS, key=len, reverse=True):
        idx = s.find(m)
        while idx >= 0:
            le, re_ = s[:idx], s[idx + len(m):]
            if le and re_ and (mirror_eye(le) == re_ or le == re_):
                return {"left": left, "eye": le, "mouth": m,
                        "right": right, "decor": decor}
            idx = s.find(m, idx + 1)
    return None


def random_parts(rng=None):
    """随机组合一套积木（"来一个随机颜文字"按钮用）。

    ## 为什么不是纯随机拼

    五个位置各随机取一个，拼出来的结果**大多不好看**：
    眼睛用 `= =`、嘴用 `ᴥ`、左括号带 `´`，凑在一起是
    `（´= =ᴥ= =)❀` 这种东西。

    原因是积木之间有协调性——`=` 类眼睛配 `‿` 好看，配 `ᴥ` 就怪。
    所以这里改成**从已知好看的组合里随机挑一套**，再用它去填下拉框。
    下拉框仍然允许用户改成任意组合，只是"随机"按钮保证给出可用的结果。
    """
    import random as _random

    r = rng or _random
    # 过滤掉"眼睛和嘴是同一个符号"的组合——拼出来像 `(ᴗᴗᴗ)`，
    # 分不清哪是眼睛哪是嘴，看着像卡住了。
    candidates = [c for c in GOOD_COMBOS if c[1] != c[2]]
    left, eye, mouth, right, decor = r.choice(candidates or GOOD_COMBOS)
    return {"left": left, "eye": eye, "mouth": mouth,
            "right": right, "decor": decor}
