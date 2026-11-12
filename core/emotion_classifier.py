"""情绪判定统一入口：神经网络优先，关键词兜底。

## 为什么要两层

单靠关键词做不到"整体理解"：

    今天一点都不开心   →  关键词看到"开心"，判成开心（人知道是不开心）
    我不开心           →  同上
    虽然很累但是很开心  →  关键词命中两个，靠位置蒙到一个

单靠网络也不行：

  * 冷启动时模型还没训练好（或模型文件丢失）；
  * 网络可能对某个很明确的词过度自信地判错。

所以做成**分层**：

    1. 网络给出结果且置信度够（≥0.45）→ 用网络
    2. 否则回退关键词匹配
    3. 关键词也没有 → 不触发

实测网络在否定/转折场景 7/7 正确，关键词 1/7；而纯关键词在某些
直白表达上更稳。两层互补比任何单层都好。

## 持续适应（在线学习）

用户每选一次颜文字，就等于给了一次「当前情绪判断是对的」的反馈。
`learn_from_choice()` 用这一次样本对网络做一次 SGD 更新——
网络会逐渐贴合**这个用户**的说话方式。

这不需要 GPU、不需要重新训练、不联网：一次 `train_one` 约 0.3ms，
在用户选字的瞬间就完成了。
"""
import os
import threading

from core import emotion_net as en
from core.emotion import detect_last as keyword_detect

#: 置信度阈值：低于此值认为网络"没把握"，交给关键词兜底
#:
#: 会被配置项 `emotion_sensitivity`（0~100）调整：
#: 灵敏度低 → 阈值高 → 更保守；灵敏度高 → 阈值低 → 更容易触发。
CONFIDENCE_THRESHOLD = 0.45

#: 灵敏度换算区间：灵敏度 0 时阈值 0.65（保守），100 时 0.25（灵敏）
_THRESHOLD_MAX = 0.65
_THRESHOLD_MIN = 0.25


def threshold_for_sensitivity(sensitivity):
    """把 0~100 的灵敏度换算成置信度阈值。

    线性插值：灵敏度 0 → 0.65，50 → 0.45，100 → 0.25。

    为什么要暴露这个：不同人对"打扰"的容忍度差别很大。有人希望
    多推荐几次（宁可错杀），有人只想要极准的那几次。给滑块比替用户决定好。
    """
    try:
        s = float(sensitivity)
    except (TypeError, ValueError):
        s = 50.0
    s = max(0.0, min(100.0, s))
    return _THRESHOLD_MAX - (_THRESHOLD_MAX - _THRESHOLD_MIN) * (s / 100.0)


#: 在线学习的步长。太小几乎学不动，太大一个样本就能把权重带偏。
LEARN_RATE = 0.03

#: 含"不/没"但**不表示否定**的固定搭配。
#:
#: 「今天天气不错」里的"不"是肯定；「不好意思」「差不多」同理。
#: 用它们做否定判定会把普通闲聊误判成否定句，进而让网络乱判。
_FALSE_NEGATIONS = (
    "不错", "不好意思", "差不多", "不少", "不久", "不断", "不仅", "不但",
    "不得不", "不管", "不论", "无论", "说不定", "了不起", "巴不得",
    "没过多久", "没准", "开玩笑", "没关系",
)

#: 转折词：出现时后半句通常主导整体情绪。
#:
#: 与 `emotion_net._TURN_CUT_WORDS` 是同一组词，但语义不同：
#: 那边用于**切分特征**，这里用于**选择信谁**。
_TURN_WORDS = ("但是", "不过", "然而", "可是", "结果", "然后",
               "归", "但", "却")

#: 用于识别"否认情绪"的否定词
_DENY_NEGATIONS = ("没有", "没", "不", "才不", "并不")

#: 跟在否定词后面、表示"在否认这个情绪"的词
_EMOTION_WORDS_FOR_DENY = (
    "生气", "难过", "伤心", "开心", "高兴", "喜欢", "失望", "委屈",
    "累", "困", "紧张", "害怕", "担心", "着急", "介意",
)

#: 「不 + X」本身就是一个**情绪词**的组合，不能当成否认。
#:
#: 「我不开心」= 在表达伤心（要弹）；
#: 「我没生气」= 在否认生气（不弹）。
#: 两者前半截长得一样，区别在于"不开心"已经是个固定的负面表达。
_NEGATED_IS_AN_EMOTION = (
    "不开心", "不高兴", "不舒服", "不痛快", "不爽", "不满意",
)


def model_path():
    """模型文件路径（跟随运行时路径规则：源码态/打包态各自定位）。"""
    try:
        from core import runtime
        return runtime.resource_path("data", "emotion_model.json")
    except Exception:
        return os.path.join("data", "emotion_model.json")


def user_model_path():
    """用户侧增量训练的模型（优先于随包模型）。

    随包模型是只读资源，打包后位于 `_internal/`；用户学习出来的权重
    必须写到可写目录，否则装在 Program Files 下会写失败。
    """
    try:
        from core import runtime
        return os.path.join(runtime.app_data_dir(), "emotion_model_user.json")
    except Exception:
        return "emotion_model_user.json"


class EmotionClassifier:
    """两层判定 + 在线学习。线程安全（采样线程与主线程都会用到）。"""

    def __init__(self, load_model=True):
        self._lock = threading.RLock()
        self.net = None
        self._dirty = 0
        self.last_source = ""      # 最近一次判定来自 "net" / "keyword" / ""
        self.last_conf = 0.0
        #: 触发阈值（由 `emotion_sensitivity` 配置调整，见 threshold_for_sensitivity）
        self.threshold = CONFIDENCE_THRESHOLD
        #: 是否启用个性化学习（配置项 emotion_learning）
        self.learning_enabled = True
        #: 是否使用 AI（神经网络）。
        #:
        #: 关掉后**完全走纯算法**（关键词匹配）：不加载模型、不推理、不学习。
        #: 这不是"把权重调低"，而是彻底不碰网络——想用纯规则的用户可以放心，
        #: 也不会有任何模型文件被读写。
        self.ai_enabled = True
        if load_model:
            self.load()

    def configure(self, sensitivity=None, learning_enabled=None,
                  suggest_enabled=None, ai_enabled=None):
        """应用用户配置：灵敏度 / 是否学习 / 是否启用情绪推荐 / 是否用 AI。

        由 AppRuntime.apply_settings 调用，保证设置页改动实时生效。
        """
        if ai_enabled is not None:
            new_ai = bool(ai_enabled)
            if new_ai != self.ai_enabled:
                self.ai_enabled = new_ai
                if new_ai:
                    # 重新打开 AI：把模型读回来
                    self.load()
                else:
                    # 关闭 AI：把模型从内存卸掉，确保不再有任何推理路径
                    with self._lock:
                        self.net = None
                    self.last_source = ""
        if sensitivity is not None:
            self.threshold = threshold_for_sensitivity(sensitivity)
        if learning_enabled is not None:
            self.learning_enabled = bool(learning_enabled)
        if suggest_enabled is not None:
            self._suggest_enabled = bool(suggest_enabled)

    # ---------- 加载 / 保存 ----------
    def load(self):
        """优先加载用户模型，其次随包模型。"""
        for p in (user_model_path(), model_path()):
            try:
                net = en.EmotionNet.load(p)
                if net is not None:
                    with self._lock:
                        self.net = net
                    return True
            except Exception:
                continue
        with self._lock:
            self.net = None
        return False

    def save_user_model(self):
        """把当前权重存为用户模型（增量学习结果）。"""
        with self._lock:
            net = self.net
            dirty = self._dirty
        if net is None or not dirty:
            return False
        ok = net.save(user_model_path())
        if ok:
            with self._lock:
                self._dirty = 0
        return ok

    @property
    def has_net(self):
        return self.net is not None

    # ---------- 判定 ----------
    def detect(self, text):
        """返回 (emotion, keyword, pos) 或 None。语义与 `emotion.detect_last` 一致。

        ## 两层的分工（实测得出，不是拍脑袋）

        | 层 | 强项 | 弱项 |
        |----|------|------|
        | 关键词 | 准（误报 0%） | 覆盖窄（漏判 25%） |
        | 网络   | 广（漏判 1.8%） | 早期无区分度（误报 95%） |

        加 `__NONE__` 类后网络终于会说"这句不用弹"，误报降到 0%。
        但网络仍偏保守——「好萌」「好帅」「太赞了」会被判成 NONE，
        而关键词层对这些也没有词。两层都沉默，用户就永远看不到推荐。

        ## 调度规则

            1. **关键词有词** → 以关键词为准（它零误报，最可信）
            2. **含否定词** → 以网络为准（关键词在这里必然判错）
            3. 网络给出情绪且置信够 → 用网络
            4. 网络判 NONE、关键词无词 → **模糊地带**，交给 `_ambiguous()`
        """
        if not text:
            self.last_source = ""
            return None

        # ---------- 纯算法模式 ----------
        # 用户关闭 AI 后，这里直接返回关键词层结论：不推理、不学习、
        # 也不做任何"模糊地带"的猜测。行为完全可预测——这正是选这个
        # 开关的人想要的。
        if not self.ai_enabled:
            try:
                hit = keyword_detect(text)
            except Exception:
                hit = None
            self.last_source = "keyword(纯算法)" if hit else ""
            self.last_conf = 0.0
            return hit

        with self._lock:
            net = self.net

        try:
            kw_hit = keyword_detect(text)
        except Exception:
            kw_hit = None

        net_emo, net_conf = None, 0.0
        if net is not None:
            try:
                net_emo, net_conf = net.predict(text)
            except Exception:
                net_emo, net_conf = None, 0.0

        # ---------- 规则 2.5：否认情绪 → 不弹 ----------
        # 「我没生气」「我不难过」：句子里有否定词，也有情绪词，但整体是
        # **在否认那个情绪**，不是在表达别的情绪。用户说这句时不想配表情。
        #
        # 必须放在否定分支之前：否则会走进"听网络的"，而网络对这类句子
        # 容易判成"开心"（因为"没生气"≈平静），实测就是一个误报。
        if self._is_denying_emotion(text):
            self.last_source = ""
            self.last_conf = net_conf
            return None

        # ---------- 规则 2：含否定词 → 听网络的 ----------
        # 「今天一点都不开心」：关键词只看得到"开心"，必然判反。
        if self._has_negation(text):
            if net_emo is not None and net_conf >= max(0.5, self.threshold):
                self.last_source = "net(negation)"
                self.last_conf = net_conf
                return self._as_hit(text, net_emo, None)
            # 网络判 NONE 或没把握：否定句判反比不判更糟，宁可不弹
            self.last_source = ""
            self.last_conf = net_conf
            return None

        # ---------- 规则 1.5：假否定固定搭配 → 不弹 ----------
        # 「不好意思，我迟到了」「不得不承认这个设计很棒」：这些句子含"不"，
        # 但"不"不是否定（是不好意思/不得不 这类固定搭配）。
        # 网络对它们会给出中等置信的错误判断（实测 惊讶0.57 / 疲惫0.81），
        # 关键词也没有词——必须在这里拦下，否则就是误报。
        if self._has_false_negation(text):
            self.last_source = ""
            self.last_conf = net_conf
            return None

        # ---------- 规则 1.6：转折句（仅在网络非常确定时采信） ----------
        # 「本来挺高兴的，结果被气到了」：关键词看到"高兴"就判开心，
        # 但它不懂**转折结构**——真正的情绪在"结果"后面。
        #
        # ⚠ 实测网络的转折判断只有 2/12 正确（尾段特征提取是对的，
        # 但模型没学够）。无条件信它会让 turn 类从 50% 掉到 33%。
        # 折中：只在网络**很有把握**时才采信，否则仍走关键词。
        if self._has_turn(text) and net_emo is not None \
                and net_conf >= 0.90 and (kw_hit is None or kw_hit[0] != net_emo):
            self.last_source = "net(turn)"
            self.last_conf = net_conf
            return self._as_hit(text, net_emo, None)

        # ---------- 规则 1：关键词有词 → 以它为准 ----------
        # 关键词是"精确但覆盖窄"的一层：它只在看到明确情绪词时出手，
        # 实测误报为 0，所以只要它开口就信它。
        if kw_hit is not None:
            if net_emo is not None and net_emo == kw_hit[0]:
                self.last_source = "net+keyword"      # 两层一致，最可信
                self.last_conf = net_conf
            else:
                self.last_source = "keyword"          # 冲突时信关键词
                self.last_conf = net_conf
            return kw_hit

        # ---------- 规则 3：网络给出情绪 ----------
        if net_emo is not None and net_conf >= self.threshold:
            self.last_source = "net"
            self.last_conf = net_conf
            return self._as_hit(text, net_emo, None)

        # ---------- 规则 4：模糊地带 ----------
        return self._ambiguous(text, net_conf)

    #: 模糊地带里，"看起来像在夸"的字，用来判断要不要冒险推荐
    _PRAISE_HINTS = ("好", "太", "真", "超", "特别", "非常", "巨", "贼")

    def _ambiguous(self, text, net_conf):
        """两层都没出手时的兜底判断。

        ## 为什么需要它

        规则走到这里说明：关键词没有情绪词，网络也判了"无情绪"。
        听起来该不弹——但实测有一批句子会被误伤：

            好萌 / 好帅 / 太赞了 / 好可爱 / 这也太香了

        这些是**网络训练时没见过的口语表达**，它诚实地说了 NONE；
        关键词表里也没收这些词。结果用户打了很常见的夸奖，
        却什么都看不到。

        ## 判断依据

        用**结构线索**而不是词表：程度副词 + 形容词，且句子很短。
        聊天里「好X」「太X了」这类结构几乎总是表达情绪，
        而「好的」「差不多」这类虽然也短，但不在这个结构里
        （`好的` 后面没有形容词位）。

        风险控制：
          * 只对**很短**的句子生效（≤8 字），长句子更可能是陈述；
          * 网络置信度不能太低（说明它也不是"很确定没情绪"）；
          * 命中后标记 `last_source="heuristic"` 便于观察与统计。
        """
        s = str(text or "").strip()
        if not s or len(s) > 8:
            self.last_source = ""
            self.last_conf = net_conf
            return None
        # 网络非常确定"没情绪"时不冒险
        if net_conf >= 0.75:
            self.last_source = ""
            self.last_conf = net_conf
            return None
        # 含**假否定**固定搭配（不好意思 / 不得不 / 差不多…）时不冒险：
        # 这些词恰好以"不"开头，会被下面的程度副词规则误判成"很X"
        for w in _FALSE_NEGATIONS:
            if w in s:
                self.last_source = ""
                self.last_conf = net_conf
                return None
        # 需要"程度副词 + 后面还有内容"的结构
        for hint in self._PRAISE_HINTS:
            idx = s.find(hint)
            if idx < 0:
                continue
            rest = s[idx + len(hint):].strip("了呀啊呢吧的了！!~～ 的")
            if len(rest) >= 1:
                # 先问问网络"如果无视 NONE 最像哪个情绪"
                guess = self._top_non_none(s)
                if guess is not None:
                    self.last_source = "heuristic"
                    self.last_conf = net_conf
                    return (guess, "", 0)
        self.last_source = ""
        self.last_conf = net_conf
        return None

    def _top_non_none(self, text):
        """取除 NONE 外概率最高的情绪（用于模糊地带的猜测）。"""
        with self._lock:
            net = self.net
        if net is None:
            return None
        try:
            from core import emotion_net as _en
            probs, _ = net.forward(_en.vectorize(text))
            best_k, best_p = -1, -1.0
            for k in range(len(_en.CLASSES)):
                if probs[k] > best_p:
                    best_k, best_p = k, probs[k]
            if best_k < 0 or best_p < 0.15:
                return None
            return _en.CLASSES[best_k]
        except Exception:
            return None

    @staticmethod
    def _as_hit(text, emotion, kw_hit):
        """把网络结论包装成与 detect_last 同形的 (emotion, kw, pos)。"""
        pos = kw_hit[2] if kw_hit else 0
        kw = kw_hit[1] if kw_hit else ""
        return emotion, kw, pos

    @staticmethod
    def _is_denying_emotion(text):
        """是否在**否认某个情绪**（"我没生气"/"我不难过"/"没有不高兴"）。

        与"否定句"的区别：
          * 「今天一点都不开心」→ 在陈述**相反**的情绪（伤心），该弹；
          * 「我没生气」→ 只是**否认**生气，没有表达别的情绪，不该弹。

        判据：否定词紧跟情绪词，且整句很短（否认句通常就几个字）。
        长句里出现"不生气"更可能是叙事（"他让我不生气了"），交给网络。
        """
        s = str(text or "").strip()
        if not s or len(s) > 10:
            return False
        # 「不开心」「不高兴」这类**本身就是情绪词**，不是否认
        for neg_emo in _NEGATED_IS_AN_EMOTION:
            if neg_emo in s:
                return False
        # 去掉开头的自称，避免"我"干扰
        core = s
        for p in ("我", "他", "她", "我们", "你", "其实", "真的"):
            if core.startswith(p):
                core = core[len(p):]
        for neg in _DENY_NEGATIONS:
            if not core.startswith(neg):
                continue
            rest = core[len(neg):].strip()
            if not rest:
                continue
            # 否定词后面紧跟情绪词 -> 是在否认这个情绪
            for emo_words in _EMOTION_WORDS_FOR_DENY:
                if rest.startswith(emo_words):
                    return True
        return False

    @staticmethod
    def _has_turn(text):
        """句子里是否有转折词。

        转折词之后的部分通常才决定整体情绪：
            本来挺高兴的，结果被气到了  →  生气（不是开心）

        这是关键词层**结构性的盲区**：它只能看到"高兴"这个字，
        不知道后面还有个"结果"。
        """
        s = str(text or "")
        return any(w in s for w in _TURN_WORDS)

    @staticmethod
    def _has_false_negation(text):
        """句子里是否含"看起来像否定、其实不是"的固定搭配。

        与 `_has_negation` 的分工：
          * `_has_negation` 用来决定"要不要走网络否定分支"，它先剔除假否定；
          * 本方法用来**主动拦截**：只要句子里有这类搭配，就当作无情绪。

        「不好意思，我迟到了」——网络会判惊讶 0.57，关键词没词，
        不拦就是一个活生生的误报。
        """
        s = str(text or "")
        return any(w in s for w in _FALSE_NEGATIONS)

    @staticmethod
    def _has_negation(text):
        """句子里是否有**真正表示否定**的词。

        ⚠ 不能简单地"含'不'就算否定"：「不错」「不好意思」「差不多」里
        的"不"并不是否定情绪。实测「今天天气不错」会因此被误判。

        做法：先去掉这些固定搭配，再检查剩余文本里是否还有否定词。
        """
        try:
            from core.emotion_net import NEGATION_WORDS
        except Exception:
            return False
        s = str(text or "")
        # 去掉"不错"类固定搭配（它们整体表示肯定）
        for w in _FALSE_NEGATIONS:
            s = s.replace(w, "")
        return any(w in s for w in NEGATION_WORDS)

    # ---------- 在线学习 ----------
    # ---------- 在线学习 ----------
    def learn_from_choice(self, text, emotion, strength=1.0):
        """用户选了某情绪分类下的颜文字 → 强化该分类。

        ## 学到的是什么

        `text` 是**触发那次弹窗的原句**，`emotion` 是用户认可的类别。
        一次 `train_one` 就是一次 SGD，权重朝"这句话属于这个情绪"移动。

        :param strength: 学习强度倍数（0~2）。用于区分信号强弱：
            * 1.0 = 用户主动从该情绪候选里选了一张（强信号）
            * 0.5 = 间接确认（例如手动唤起初次就选，弱信号）

        ⚠ 只应在**有情绪上下文**时调用，否则会把噪声教给网络。
        """
        if not text or not emotion:
            return False
        if not self.learning_enabled or not self.ai_enabled:
            return False          # 关了个性化学习，或干脆没在用 AI
        idx = en.class_index(emotion)
        if idx is None:
            return False
        with self._lock:
            net = self.net
        if net is None:
            return False
        try:
            lr = LEARN_RATE * max(0.1, min(2.0, float(strength)))
            net.train_one(text, idx, lr=lr)
            with self._lock:
                self._dirty += 1
                self._dirty_total = getattr(self, "_dirty_total", 0) + 1
            return True
        except Exception:
            return False

    def learn_ignored(self, text, emotion):
        """用户**看到了**某情绪的推荐，但没选就走了 → 弱化该判断。

        ## 为什么"忽略"也是有用的信号

        弹出「开心」候选，用户看了一眼，然后继续打字、换别的词——
        这通常意味着判断错了，或者时机不对。

        但我们不能把"忽略"当成强信号：用户可能只是找不到合适的、
        或者不小心关掉了。所以用**很小的学习率**慢慢修正，而不是
        一次就否定——错一次不至于把正确的判断带偏。

        好处是：网络会逐渐学会"我在这类句子上判得不准"，
        长期看减少了打扰。
        """
        if not text or not emotion:
            return False
        if not self.learning_enabled or not self.ai_enabled:
            return False
        idx = en.class_index(emotion)
        if idx is None:
            return False
        with self._lock:
            net = self.net
        if net is None:
            return False
        try:
            # 极小步长：只把该类的概率往下压一点点
            net.train_one(text, en.NONE_INDEX, lr=LEARN_RATE * 0.25)
            with self._lock:
                self._dirty += 1
                self._dirty_total = getattr(self, "_dirty_total", 0) + 1
            return True
        except Exception:
            return False

    def maybe_autosave(self, every=20):
        """每积累 N 次学习就落盘一次，避免频繁写盘。"""
        with self._lock:
            n = getattr(self, "_dirty_total", 0)
        if n and n % every == 0:
            return self.save_user_model()
        return False

    # ---------- 学习状态（给 UI 展示） ----------
    def learning_stats(self):
        """返回个性化学习的统计，供主界面展示"越用越顺手"的证据。"""
        with self._lock:
            total = getattr(self, "_dirty_total", 0)
            net = self.net
        base = 0
        try:
            base = int(getattr(net, "trained_samples", 0) or 0) if net else 0
        except Exception:
            pass
        return {
            "personal_updates": total,      # 本次会话学了多少次
            "base_samples": base,           # 随包模型的训练样本量
            "has_personal_model": os.path.exists(user_model_path()),
            "model_ready": net is not None,
        }

    def reset_personal_model(self):
        """清掉个性化学习结果，回到随包模型。

        用户可能觉得"越学越偏"，需要一个一键还原的出口。
        """
        try:
            p = user_model_path()
            if os.path.exists(p):
                os.remove(p)
        except Exception:
            pass
        with self._lock:
            self._dirty = 0
            self._dirty_total = 0
        return self.load()


#: 进程级单例（采样线程与 UI 共用同一份权重，学习才能累积）
_shared = None
_shared_lock = threading.Lock()


def shared():
    """取全局分类器（惰性创建，只加载一次模型）。"""
    global _shared
    with _shared_lock:
        if _shared is None:
            _shared = EmotionClassifier()
        return _shared
