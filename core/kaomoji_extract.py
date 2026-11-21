"""从任意文本中识别颜文字。

## 为什么需要单独的识别器

现有的 `kaomoji_data._is_valid_kaomoji()` 是**校验**用的：
判断"这一整条字符串是不是合法颜文字"，判据是"有没有连续两个汉字"。
它假设输入**本身就是一条颜文字**。

但这里要处理的是聊天记录：

    "今天好累啊 (´-ω-`) 想睡觉"
    "哈哈哈哈 ヽ(≧∇≦)ノ 笑死"
    "嗯…(￣▽￣; ) 你说的对"

要从这种混合文本里**抽出**颜文字片段。判据完全不同：

* 不能要求"整段没有连续汉字"——整句都是中文；
* 需要一个**边界划分**：哪里开始是颜文字、哪里结束；
* 要知道"括号里包着的那坨符号"才是候选。

## 识别策略

按优先级从高到低：

1. **括号包裹式**：`(...)`、`（...）`、`[...]`、`{...}` 包起来的一段，
   里面**非中日韩字符占比足够高**就算颜文字。
   覆盖绝大多数情况（`(´-ω-`)`、`ヽ(≧∇≦)ノ`）。

2. **括号外带装饰的**：括号前后紧跟的手臂/装饰符号
   （`ヽ`、`ノ`、`ﾉ`、`✦`、`♡`…），一并纳入。
   这是 `ヽ(≧∇≦)ノ` 这类"括号不是完整整体"的情况。

3. **无括号式**：形如 ``¯\\_(ツ)_/¯``、`(╯°□°)╯︵ ┻━┻` 这种。
   这条规则容易误判，所以要求**至少 3 个非文字符号**才认。

## 一个重要的取舍

**宁可漏，不可错**。抽取错了会让用户收藏一堆垃圾（比如把"(笑)"、
"(真的吗)"这种普通括号内容当成颜文字），比漏掉几条烦人得多。

所以每条规则都设了保守的阈值，并且对中文内容特别小心：
括号里如果以汉字为主，直接排除。
"""

#: 中日韩统一表意文字范围
def _is_cjk(ch):
    cp = ord(ch)
    return (0x4E00 <= cp <= 0x9FFF
            or 0x3400 <= cp <= 0x4DBF
            or 0xF900 <= cp <= 0xFAFF
            or 0x3040 <= cp <= 0x30FF       # 平假名 / 片假名
            or 0xAC00 <= cp <= 0xD7AF)      # 谚文

#: 颜文字常用的装饰符号（括号外的手臂、星星、心等）
#:
#: 只收**确实出现在颜文字里**的。不要收常见的标点（。，！？），
#: 那会把普通句子的标点也吸进来。
_DECO = set(
    "ヽヾノﾉゝゞ尐✨✧✦★☆♡❤♪～~"
    "┬┴┻━︵︶⌒╯╰╮╭ᕤʕʔᐠᐟᓄᓂ"
    "¯\\_/─━┃┏┓┗┛┣┫┳┻╋"
    # 阿拉伯字母常被借来当"手臂"：`(๑•̀ㅂ•́)و✧`、`(๑˃ᴗ˂)ﻭ`
    "وﻭز"
)

#: 括号对
_PAIRS = {"(": ")", "（": "）", "[": "]", "{": "}", "《": "》",
          "〈": "〉"}

#: 判定"这段符号像不像颜文字"的最低非 CJK 占比
_MIN_SYMBOL_RATIO = 0.5
#: 括号内最少字符数（`(A)` 这种太短的不算）
_MIN_INNER_LEN = 3
#: 无括号式：至少要有这么多非文字符号
_MIN_BARE_SYMBOLS = 3


def _symbol_ratio(s):
    """非中日韩字符的占比（0~1）。"""
    if not s:
        return 0.0
    nc = sum(1 for ch in s if not _is_cjk(ch) and not ch.isspace())
    total = sum(1 for ch in s if not ch.isspace())
    return nc / total if total else 0.0


def _looks_like_kaomoji(inner):
    """括号里包着的这一段，像不像颜文字。"""
    if len(inner) < _MIN_INNER_LEN:
        return False
    if not inner.strip():
        return False
    # 以汉字为主 → 是"（笑）""（真的吗）"这类普通括号，排除
    if _symbol_ratio(inner) < _MIN_SYMBOL_RATIO:
        return False
    # 纯字母数字 → 排除 "(abc123)"、"(hello world)"
    if all(ch.isalnum() for ch in inner if not ch.isspace()):
        return False
    # 至少要有一个**非 ASCII 的可视符号**。
    #
    # ⚠ 阈值不能用 0x2000：颜文字常用的 `ᴗ`(0x1D17)、`˃`(0x02C3)、
    # `๑`(0x0E51) 都在它下面。用 0x00FF（拉丁补充之外）才合适，
    # 这样英文括号内容被排除、颜文字符号则能通过。
    def _is_symbol_char(ch):
        if ch in "()[]{}<>-_=+*/\\|~^":
            return True
        cp = ord(ch)
        if cp <= 0xFF:
            return False
        # 拉丁扩展（`café` 里的 é、`naïve` 里的 ï）不算颜文字符号，
        # 否则 "(café)" 这种也会被当成表情
        if 0x100 <= cp <= 0x24F:
            return False
        if 0x0300 <= cp <= 0x036F:      # 组合变音符
            return False
        return True

    has_symbol = any(_is_symbol_char(ch) for ch in inner)
    if not has_symbol:
        return False
    return True


def extract(text, max_items=20):
    """从一段文本里抽出所有颜文字。

    :return: 去重后的列表，按出现顺序。抽不到时返回空列表。

    >>> extract("今天好累 (´-ω-`) 想睡觉")
    ['(´-ω-`)']
    >>> extract("哈哈哈哈 ヽ(≧∇≦)ノ 笑死")
    ['ヽ(≧∇≦)ノ']
    >>> extract("（真的吗）")
    []
    """
    s = str(text or "")
    if not s.strip():
        return []

    out = []
    seen = set()

    def _add(cand):
        cand = cand.strip()
        if not cand or cand in seen:
            return
        if len(cand) > 60:      # 太长的一定不是颜文字
            return
        seen.add(cand)
        out.append(cand)

    # ---------- 1 & 2：括号包裹 + 邻近装饰 ----------
    i = 0
    n = len(s)
    while i < n:
        ch = s[i]
        close = _PAIRS.get(ch)
        if close is not None:
            j = s.find(close, i + 1)
            if j > i:
                inner = s[i + 1:j]
                if _looks_like_kaomoji(inner):
                    # 向前/向后吸收紧邻的装饰符号
                    start = i
                    while start > 0 and s[start - 1] in _DECO:
                        start -= 1
                    end = j + 1
                    while end < n and s[end] in _DECO:
                        end += 1
                    _add(s[start:end])
                i = j + 1
                continue
        i += 1

    # ---------- 3：无括号式（保守） ----------
    # 只在没抽到东西时尝试，避免把括号式的结果再拆一遍
    if not out:
        for tok in s.split():
            if len(tok) < _MIN_INNER_LEN:
                continue
            dc = sum(1 for c in tok if c in _DECO)
            if dc >= _MIN_BARE_SYMBOLS and _symbol_ratio(tok) >= 0.6:
                _add(tok)
                if len(out) >= max_items:
                    break

    return out[:max_items]


def dedupe_against(items, existing):
    """去掉已经存在于 `existing` 里的条目（避免推荐用户已收藏的）。"""
    have = set(str(x) for x in (existing or []))
    return [t for t in items if str(t) not in have]
