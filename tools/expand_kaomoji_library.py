"""扩充内置颜文字库。

## 为什么要做

原来的库只有 109 条 / 10 个分类，实际用起来很快搜不到想要的。
功能做得再花，库不够用就是白搭。

## 数据结构（向后兼容）

保持 `{"categories": [{"name": ..., "items": [str, ...]}]}` 不变——
插件、用户配置、旧版本都能继续读。

额外增加**可选的**元数据字段，读不到就用默认值：

    {
      "name": "开心",
      ;; 分类级：搜索别名，用于拼音/英文命中
      "aliases": ["kx", "happy", "joy", "smile"],
      ;; 分类级：场景标签，用于"按场景推荐"
      "scenes": ["greeting", "celebrate"],

      "items": [
        "(＾▽＾)",                       ;; 纯字符串也继续支持
        {"text": "(๑˃ᴗ˂)ﻭ",
         "aliases": ["cheer", "win"],   ;; 该条的别名
         "scenes": ["celebrate"],       ;; 该条的场景
         "pinyin": "jiayou"}            ;; 可选：该条的拼音
      ]
    }

`KaomojiData.load()` 会把两种写法都归一成字符串列表（保持旧行为），
元数据单独存一份索引供搜索/推荐使用。

## 用法

    python tools/expand_kaomoji_library.py
"""

import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

DATA = os.path.join(ROOT, "data", "kaomoji.json")


# ====================================================================
# 新的分类内容
#
# 每条带 aliases（搜索别名）与 scenes（场景）。
# 场景是跨分类的：同一个"打招呼"场景会横跨开心/喜欢/动物等分类。
# ====================================================================

def _k(text, aliases=None, scenes=None):
    """构造一个带元数据的条目。"""
    d = {"text": text}
    if aliases:
        d["aliases"] = aliases
    if scenes:
        d["scenes"] = scenes
    return d


LIBRARY = [
    # ---------------------------------------------------------- 基础情绪
    {
        "name": "开心",
        "aliases": ["kx", "happy", "joy", "smile", "glad"],
        "scenes": ["greeting", "celebrate"],
        "items": [
            _k("(＾▽＾)", ["happy", "smile", "yay"], ["greeting"]),
            _k("(◕‿◕)", ["smile", "sweet"]),
            _k("(｡･ω･｡)", ["soft", "cute"]),
            _k("(๑˃ᴗ˂)ﻭ", ["cheer", "win", "yes"], ["celebrate"]),
            _k("(＾ω＾)", ["smile"]),
            _k("ヽ(・∀・)ﾉ", ["yay", "hello", "hi"], ["greeting"]),
            _k("(≧▽≦)", ["laugh", "lol", "haha"]),
            _k("(*≧ω≦*)", ["excited", "happy"]),
            _k("(•‿•)", ["smile", "polite"]),
            _k("(ᵔᴥᵔ)", ["cute", "smile"]),
            _k("(＾◡＾)", ["smile", "warm"]),
            _k("٩(◕‿◕)۶", ["yay", "celebrate"], ["celebrate"]),
            _k("(ﾉ◕ヮ◕)ﾉ*:･ﾟ✧", ["magic", "sparkle", "send"], ["celebrate"]),
            _k("ヾ(≧▽≦*)o", ["haha", "laugh"]),
            _k("(✿◠‿◠)", ["flower", "gentle"]),
            _k("(„• ᴗ •„)", ["shy", "happy"]),
            _k("(๑>◡<๑)", ["love", "happy"]),
            _k("(⌒▽⌒)", ["grin", "bigsmile"]),
            _k("o(≧▽≦)o", ["yay", "excited"]),
            _k("(＊❛ᴗ❛＊)", ["blush", "happy"]),
        ],
    },
    {
        "name": "伤心",
        "aliases": ["sx", "sad", "cry", "unhappy", "blue"],
        "scenes": ["comfort"],
        "items": [
            _k("(╥﹏╥)", ["cry", "tears", "sob"]),
            _k("(ノ﹏ヽ)", ["cry", "weep"]),
            _k("(｡•́︿•̀｡)", ["sad", "down"]),
            _k("(╯︵╰,)", ["cry", "grief"]),
            _k("(っ˘̩╭╮˘̩)っ", ["hug", "comfort"], ["comfort"]),
            _k("(ಥ﹏ಥ)", ["cry", "tears"]),
            _k("(´;ω;`)", ["sob", "cry"]),
            _k("(个_个)", ["cry", "sad"]),
            _k("｡ﾟ(ﾟ´ω`ﾟ)ﾟ｡", ["wail", "cry"]),
            _k("(´-ω-`)", ["sigh", "tired"]),
            _k("(´。＿。｀)", ["blank", "down"]),
            _k("(╥_╥)", ["cry", "sad"]),
            _k("(ノдヽ)", ["cry", "despair"]),
            _k("(´；ω；`)", ["cry", "tears"]),
            _k("( >д<)", ["pain", "ouch"]),
        ],
    },
    {
        "name": "生气",
        "aliases": ["sq", "angry", "mad", "rage"],
        "scenes": [],
        "items": [
            _k("(＃`Д´)", ["angry", "furious"]),
            _k("(╬ Ò﹏Ó)", ["rage", "mad"]),
            _k("(ノಠ益ಠ)ノ彡┻━┻", ["tableflip", "rage", "flip"]),
            _k("(｀Д´)", ["angry", "mad"]),
            _k("(・`ω´・)", ["annoyed", "hmp"]),
            _k("(￣ヘ￣)", ["annoyed", "sigh"]),
            _k("(╯°□°）╯︵ ┻━┻", ["tableflip", "flip"]),
            _k("(¬_¬)", ["sideeye", "unimpressed"]),
            _k("(눈_눈)", ["glare", "sus"]),
            _k("(҂`_´)", ["angry", "cursing"]),
            _k("(￣ω￣;)", ["sweat", "awkward"]),
            _k("(≖_≖ )", ["sideeye", "doubt"]),
        ],
    },
    {
        "name": "惊讶",
        "aliases": ["jy", "surprise", "shock", "wow", "omg"],
        "scenes": [],
        "items": [
            _k("(°ロ°)", ["shock", "surprised"]),
            _k("(⊙_⊙)", ["surprise", "stare"]),
            _k("Σ(°△°|||)︴", ["shock", "omg"]),
            _k("(ﾟдﾟ)", ["shock", "what"]),
            _k("(⊙o⊙)", ["wow", "surprised"]),
            _k("(゜ロ゜)", ["shock", "gasp"]),
            _k("Σ(っ °Д °;)っ", ["omg", "panic"]),
            _k("(°◇°)", ["surprise", "gasp"]),
            _k("(๑°ㅁ°๑)", ["shock", "wow"]),
            _k("(☉_☉)", ["stare", "surprise"]),
            _k("(°ω°;)", ["nervous", "shock"]),
            _k("(」゜ロ゜)」", ["panic", "run"]),
            _k("(⊙﹏⊙)", ["confused", "shock"]),
            _k("(*ﾟﾛﾟ)", ["wow", "surprise"]),
        ],
    },
    {
        "name": "喜欢",
        "aliases": ["xh", "love", "heart", "like", "adore"],
        "scenes": ["greeting", "comfort"],
        "items": [
            _k("(♥ω♥*)", ["love", "heart"]),
            _k("(｡♥‿♥｡)", ["love", "inlove"]),
            _k("(◍•ᴗ•◍)❤", ["love", "heart"]),
            _k("( ´ ▽ ` ).｡ｏ♡", ["love", "sweet"]),
            _k("♡(˃͈ દ ˂͈ ༶ )", ["love", "kiss"]),
            _k("(´,,•ω•,,)♡", ["love", "shy"]),
            _k("(⁄ ⁄•⁄ω⁄•⁄ ⁄)", ["blush", "shy"]),
            _k("(*/ω＼*)", ["shy", "blush"]),
            _k("(灬º‿º灬)♡", ["love", "warm"]),
            _k("(っ˘ω˘ς )", ["cuddle", "love"], ["comfort"]),
            _k("(♡˙︶˙♡)", ["love", "happy"]),
            _k("(◕‿◕)♡", ["love", "smile"]),
            _k("(づ｡◕‿‿◕｡)づ", ["hug", "comfort"], ["comfort"]),
            _k("(ღ˘⌣˘ღ)", ["love", "peaceful"]),
        ],
    },
    {
        "name": "害羞",
        "aliases": ["hx", "shy", "blush", "embarrass"],
        "scenes": [],
        "items": [
            _k("(⁄ ⁄•⁄ω⁄•⁄ ⁄)⁄", ["shy", "blush"]),
            _k("(*/▽＼*)", ["shy"]),
            _k("(〃∀〃)", ["blush", "shy"]),
            _k("(´-ω-`)ゞ", ["embarrassed", "oops"]),
            _k("(⁄˘⁄ ⁄ ω⁄ ⁄ ˘⁄)", ["shy", "flustered"]),
            _k("(๑•́ ₃ •̀๑)", ["pout", "cute"]),
            _k("(〃ω〃)", ["shy", "blush"]),
            _k("(〃▽〃)", ["shy", "happy"]),
            _k("( ›´ω`‹ )", ["shy", "guilty"]),
            _k("(๑´ㅂ`๑)", ["shy", "cute"]),
        ],
    },
    {
        "name": "得意",
        "aliases": ["dy", "proud", "smug", "cool", "confident"],
        "scenes": ["celebrate"],
        "items": [
            _k("(￣▽￣)ノ", ["cool", "bye"], ["farewell"]),
            _k("(⌐■_■)", ["cool", "dealwithit"]),
            _k("(・ω<)★", ["wink", "cheeky"]),
            _k("(￣ω￣)", ["smug", "calm"]),
            _k("(˘ω˘)", ["peaceful", "calm"]),
            _k("(￣∀￣)", ["smug", "sly"]),
            _k("(｡•̀ᴗ-)✧", ["confident", "sparkle"]),
            _k("(๑˘︶˘๑)", ["relaxed", "cool"]),
            _k("( •_•)>⌐■-■", ["sunglasses", "cool"]),
            _k("( ´ ▽ ` )ﾉ", ["bye", "wave"], ["farewell"]),
            _k("(≖ᴗ≖)", ["smirk", "knowing"]),
            _k("(๑•̀ㅂ•́)و✧", ["determined", "fight", "yes"],
               ["celebrate"]),
        ],
    },
    {
        "name": "无语",
        "aliases": ["wy", "speechless", "facepalm", "meh", "whatever"],
        "scenes": [],
        "items": [
            _k("(￣_￣)", ["speechless", "meh"]),
            _k("(・_・;)", ["awkward", "sweat"]),
            _k("(-_-;)", ["awkward", "sweat"]),
            _k("(￣ヘ￣;)", ["annoyed", "sigh"]),
            _k("(´_ゝ`)", ["deadpan", "meh"]),
            _k("(・_・;)", ["speechless"]),
            _k("(ー_ーゞ", ["meh", "sweat"]),
            _k("(￣ー￣)", ["smug", "knowing"]),
            _k("(._.)", ["blank", "meh"]),
            _k("(´･_･`)", ["tired", "meh"]),
        ],
    },
    {
        "name": "思考",
        "aliases": ["sk", "think", "hmm", "wonder", "confused"],
        "scenes": [],
        "items": [
            _k("(・∀・)?", ["question", "hmm"]),
            _k("(￣～￣)", ["thinking", "hmm"]),
            _k("(´・ω・`)", ["confused", "hmm"]),
            _k("(・・)", ["confused", "what"]),
            _k("(？ω？)", ["confused", "curious"]),
            _k("(・_・?", ["question", "huh"]),
            _k("(°ー°〃)", ["thinking", "curious"]),
            _k("(-ω-)", ["pondering", "hmm"]),
            _k("(￣ ￣|||)", ["doubt", "sweat"]),
        ],
    },
    {
        "name": "疲惫",
        "aliases": ["pb", "tired", "sleepy", "exhausted", "drained"],
        "scenes": ["night"],
        "items": [
            _k("(=_=)", ["tired", "sleepy"]),
            _k("(－_－) zzZ", ["sleep", "sleepy"], ["night"]),
            _k("(￣o￣) zzZZzz", ["sleep"], ["night"]),
            _k("(´-﹏-`)", ["exhausted"]),
            _k("(~_~;)", ["sweat", "tired"]),
            _k("(－‸＃)", ["annoyed", "tired"]),
            _k("(¯﹃¯)", ["drool", "hungry"]),
            _k("(︶︹︺)", ["sigh", "down"]),
            _k("(◞‸◟)", ["sigh", "sad"]),
        ],
    },
    {
        "name": "动作",
        "aliases": ["dz", "action", "gesture"],
        "scenes": ["farewell", "greeting"],
        "items": [
            _k("(ノ°Д°)?~~~", ["run", "escape"]),
            _k("ヽ(￣ω￣(￣ω￣〃)ゝ", ["walk", "pair"]),
            _k("(－ω－)φ", ["write", "note"]),
            _k("(╯°□°)╯︵ ┻━┻", ["tableflip", "flip"]),
            _k("┬─┬ノ( º _ ºノ)", ["unflip", "calm"]),
            _k("(๑•̀ㅂ•́)ゞ", ["salute", "yes"]),
            _k("o(*°▽°*)o", ["cheer", "wave"]),
            _k("(づ￣ ³￣)づ", ["kiss", "hug"], ["comfort"]),
            _k("(☞ﾟヮﾟ)☞", ["point", "this"]),
            _k("☜(ﾟヮﾟ☜)", ["point", "that"]),
            _k("(ノ^_^)ノ", ["wave", "bye"], ["farewell"]),
        ],
    },
    {
        "name": "动物",
        "aliases": ["dw", "animal", "cat", "dog", "bear"],
        "scenes": ["greeting"],
        "items": [
            _k("(=^･ω･^=)", ["cat", "meow"]),
            _k("ฅ^•ﻌ•^ฅ", ["cat", "paw"]),
            _k("(=ↀωↀ=)", ["cat"]),
            _k("ʕ•ᴥ•ʔ", ["bear"]),
            _k("ʕ￫ᴥ￩ʔ", ["bear", "confused"]),
            _k("(=･ｪ･=)", ["dog", "puppy"]),
            _k("U・ᴥ・U", ["dog", "puppy"]),
            _k("(•ө•)♡", ["bird", "love"]),
            _k("(´･ω･`)", ["mouse", "small"]),
            _k("/ᐠ｡ꞈ｡ᐟ\\", ["cat", "cute"]),
        ],
    },
    {
        "name": "符号",
        "aliases": ["fh", "symbol", "deco", "kaomoji"],
        "scenes": [],
        "items": [
            _k("✧･ﾟ: *✧･ﾟ:*", ["sparkle", "magic"]),
            _k("☆*:.｡.o(≧▽≦)o.｡.:*☆", ["sparkle", "star"],
               ["celebrate"]),
            _k("ﾟ+｡:.ﾟヽ(*´∀`)ﾉﾟ.:｡+ﾟ", ["sparkle", "celebration"],
               ["celebrate"]),
            _k("・‥…━━━☆", ["star", "line"]),
            _k("╰(*°▽°*)╯", ["cheer", "hooray"], ["celebrate"]),
            _k("(*￣▽￣)ノ~~", ["point", "hey"]),
            _k("☁️✧⁺⸜(●˙▾˙●)⸝⁺✧", ["cloud", "dream"]),
        ],
    },
    # ---------------------------------------------------------- 新增分类
    {
        "name": "加油",
        "aliases": ["jy2", "cheer", "fight", "ganbatte", "support"],
        "scenes": ["support", "celebrate"],
        "items": [
            _k("(๑•̀ㅂ•́)و✧", ["fight", "determined"], ["support"]),
            _k("٩(๑•̀ω•́๑)۶", ["fight", "go"], ["support"]),
            _k("o(｀ω´*)o", ["fight", "determined"], ["support"]),
            _k("(ง •̀_•́)ง", ["fight", "punch", "go"], ["support"]),
            _k("ᕦ(ò_óˇ)ᕤ", ["strong", "fight"], ["support"]),
            _k("💪(•̀ᴗ•́)و", ["strong", "muscle"], ["support"]),
            _k("(๑˃̵ᴗ˂̵)و", ["yes", "go"], ["support"]),
            _k("٩(ˊᗜˋ*)و", ["go", "cheer"], ["support"]),
            _k("(•̀ᴗ•́)و", ["go", "ok"], ["support"]),
            _k("ヽ(•̀ω•́ )ゝ", ["salute", "ready"], ["support"]),
        ],
    },
    {
        "name": "告别",
        "aliases": ["gb", "bye", "farewell", "goodbye", "leave"],
        "scenes": ["farewell"],
        "items": [
            _k("(´・ω・)ﾉ", ["bye", "wave"]),
            _k("(・ω・)ノ", ["bye", "hi"]),
            _k("(￣▽￣)ﾉ", ["bye", "cool"]),
            _k("ノシ", ["bye", "wave"]),
            _k("(´ω｀)ノ", ["bye"]),
            _k("( ^_^ )/", ["bye", "wave"]),
            _k("(；ω；)ﾉ", ["bye", "sad"]),
            _k("(｡･ω･)ﾉﾞ", ["bye", "cute"]),
            _k("ﾉｼ", ["bye", "wave"]),
            _k("(´∀｀)ﾉ", ["bye", "happy"]),
        ],
    },
    {
        "name": "道歉",
        "aliases": ["qq", "sorry", "apologize", "regret"],
        "scenes": ["apology"],
        "items": [
            _k("(｡•́︿•̀｡)ゞ", ["sorry", "oops"], ["apology"]),
            _k("m(_ _)m", ["sorry", "bow"], ["apology"]),
            _k("(_ _*)ゞ", ["sorry", "bow"], ["apology"]),
            _k("(｡-人-｡)", ["sorry", "pray"], ["apology"]),
            _k("(´-ω-`;)ゞ", ["oops", "sorry"], ["apology"]),
            _k("(>_<)", ["oops", "sorry"], ["apology"]),
            _k("(๑•́ ₃ •̀๑)", ["pout", "sorry"], ["apology"]),
            _k("人(_ _*)", ["bow", "sorry"], ["apology"]),
        ],
    },
    {
        "name": "感谢",
        "aliases": ["gx", "thanks", "thank", "thx"],
        "scenes": ["gratitude"],
        "items": [
            _k("(｡◕‿◕｡)❤", ["thanks", "love"], ["gratitude"]),
            _k("(*´▽`人)", ["thanks", "pray"], ["gratitude"]),
            _k("(๑˃ᴗ˂)ﻭ", ["thanks", "yes"], ["gratitude"]),
            _k("(◍•ᴗ•◍)♡", ["thanks", "love"], ["gratitude"]),
            _k("(´｡• ᵕ •｡`)", ["thanks", "warm"], ["gratitude"]),
            _k("(*´∀｀*)人(*´∀｀*)", ["thanks", "mutual"],
               ["gratitude"]),
            _k("(人´∀｀)．☆", ["thanks", "bow"], ["gratitude"]),
        ],
    },
    {
        "name": "庆祝",
        "aliases": ["qz", "celebrate", "congrats", "party", "yay"],
        "scenes": ["celebrate"],
        "items": [
            _k("٩(◕‿◕)۶", ["yay", "party"]),
            _k("ヽ(°〇°)ﾉ", ["wow", "party"]),
            _k("☆ﾟ.*･｡ﾟヽ(✿ﾟ▽ﾟ)ノﾟ｡･*.ﾟ☆", ["party", "confetti"]),
            _k("(๑˃̵ᴗ˂̵)ﻭ", ["yes", "win"]),
            _k("＼(≧▽≦)／", ["yay", "hooray"]),
            _k("(*≧∀≦*)", ["excited", "celebrate"]),
            _k("✧*｡٩(ˊᗜˋ*)و✧*｡", ["sparkle", "win"]),
            _k("🎉ヽ(・∀・)ﾉ🎉", ["party", "confetti"]),
        ],
    },
    {
        "name": "打招呼",
        "aliases": ["zhaohu", "greet", "hello", "hi", "morning"],
        "scenes": ["greeting"],
        "items": [
            _k("(*ﾟ▽ﾟ)ﾉ", ["hi", "hello"]),
            _k("(｡･ω･)ﾉﾞ", ["hi", "cute"]),
            _k("ヾ(＾∇＾)", ["hello", "hi"]),
            _k("( ´ ▽ ` )ﾉ", ["hi", "wave"]),
            _k("ヽ(´▽`)/", ["hi", "happy"]),
            _k("(◕ᴗ◕✿)", ["hi", "gentle"]),
            _k("(｡>﹏<｡)ﾉ", ["hi", "shy"]),
            _k("＼(＾▽＾)／", ["hi", "yay"]),
            _k("(ノ・∀・)ノ", ["hi", "wave"]),
            _k("(◍•ᴗ•◍)ﾉ", ["hi", "friendly"]),
        ],
    },
    {
        "name": "拥抱",
        "aliases": ["yb", "hug", "cuddle", "comfort"],
        "scenes": ["comfort"],
        "items": [
            _k("(づ｡◕‿‿◕｡)づ", ["hug"]),
            _k("(っ˘ω˘ς )", ["cuddle", "hug"]),
            _k("(つ≧▽≦)つ", ["hug", "happy"]),
            _k("(づ￣ ³￣)づ", ["kiss", "hug"]),
            _k("ʕっ•ᴥ•ʔっ", ["bearhug", "hug"]),
            _k("(っ´▽`)っ", ["hug", "warm"]),
            _k("( *´꒳`*)੭⁾⁾", ["hug", "squeeze"]),
            _k("(づ●─●)づ", ["hug"]),
            _k("(っ・ω・)っ", ["hug", "support"]),
        ],
    },
    {
        "name": "晚安",
        "aliases": ["wa", "night", "sleep", "goodnight"],
        "scenes": ["night"],
        "items": [
            _k("(´-ω-)｡O○☆ﾟ･:,｡*", ["goodnight", "sleep"]),
            _k("(－_－) zzZ", ["sleep", "tired"]),
            _k("寝る(。-ω-)zzz", ["sleep", "night"]),
            _k("(つω-`)", ["sleepy", "night"]),
            _k("( ˘ω˘ )", ["sleep", "calm"]),
            _k("(*´꒳`*)〜☆", ["goodnight", "sweet"]),
            _k("(´△｀) zzZ", ["sleep"]),
        ],
    },
]


def main():
    old = {}
    try:
        with io.open(DATA, encoding="utf-8") as f:
            old_data = json.load(f)
        for c in old_data.get("categories", []):
            old[c.get("name")] = list(c.get("items") or [])
    except Exception as e:
        print("读取原库失败：%r" % (e,))
        old_data = {}

    old_total = sum(len(v) for v in old.values())
    print("原库：%d 个分类 / %d 条" % (len(old), old_total))

    # 合并策略：新库为准，但把原库里有、新库没有的条目补进去（不丢数据）
    merged = []
    new_names = set()
    for cat in LIBRARY:
        name = cat["name"]
        new_names.add(name)
        items = list(cat["items"])
        existing_texts = {
            (it if isinstance(it, str) else it.get("text"))
            for it in items
        }
        for text in old.get(name, []):
            if text not in existing_texts:
                items.append(text)
                existing_texts.add(text)
        merged.append({
            "name": name,
            "aliases": cat["aliases"],
            "scenes": cat["scenes"],
            "items": items,
        })

    # 原库里独有的分类原样保留
    for name, items in old.items():
        if name not in new_names and items:
            merged.append({"name": name, "items": items})

    total = sum(len(c["items"]) for c in merged)
    with io.open(DATA, "w", encoding="utf-8") as f:
        json.dump({"categories": merged}, f, ensure_ascii=False, indent=2)

    print("新库：%d 个分类 / %d 条" % (len(merged), total))
    print()
    for c in merged:
        n = len(c["items"])
        aliases = ",".join(c.get("aliases") or []) or "-"
        scenes = ",".join(c.get("scenes") or []) or "-"
        print("  %-8s %3d 条  别名:%-28s 场景:%s" % (c["name"], n,
                                                    aliases, scenes))
    return 0


if __name__ == "__main__":
    sys.exit(main())
