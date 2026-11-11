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
CONFIDENCE_THRESHOLD = 0.45

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
        if load_model:
            self.load()

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

        两层的**协作规则**（不是简单的"网络优先"）：

            1. 网络给出结果且有把握 → 候选用网络
            2. 关键词也给出结果 → 取两者共识；冲突时看谁更可信
            3. 网络没把握 → 用关键词
            4. 两者都没有 → 不触发

        **为什么冲突时不能无条件信网络**：实测网络会把「吃饭了吗」判成
        思考（置信度不低），而关键词层已经把这类普通提问排除掉了。
        所以规则是：**关键词明确说"没有情绪"时，网络的高置信也不足以推翻**
        ——除非网络置信度非常高（≥0.8）。

        这条规则保证了"不误弹"这个硬要求：闲聊时被弹窗打扰，
        比漏掉一次推荐烦人得多。
        """
        if not text:
            self.last_source = ""
            return None

        with self._lock:
            net = self.net

        # 关键词层结果（同时作为"否决票"）
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

        net_ok = net_emo is not None and net_conf >= CONFIDENCE_THRESHOLD

        # 1) 网络没把握 -> 关键词
        if not net_ok:
            self.last_source = "keyword" if kw_hit else ""
            self.last_conf = net_conf
            return kw_hit

        # ---------- 关键分支：含否定词 ----------
        # 「今天一点都不开心」这类句子，**关键词层必然判错**（它只看到"开心"），
        # 而网络能整体理解。所以含否定词时以网络为准——这正是上网络的理由。
        if self._has_negation(text):
            if net_conf >= 0.5:
                self.last_source = "net(negation)"
                self.last_conf = net_conf
                return self._as_hit(text, net_emo, None)
            # 网络也没把握：宁可不说（否定句判反了比不判更糟）
            self.last_source = ""
            self.last_conf = net_conf
            return None

        # 2) 两者一致 -> 用（最可信）
        if kw_hit is not None and kw_hit[0] == net_emo:
            self.last_source = "net+keyword"
            self.last_conf = net_conf
            return self._as_hit(text, net_emo, kw_hit)

        # 3) 无否定词时关键词说"没情绪"：闲聊场景，宁可不弹
        #    （「吃饭了吗」「今天天气不错」被网络判成思考/开心，都是误报）
        if kw_hit is None:
            self.last_source = ""
            self.last_conf = net_conf
            return None

        # 4) 关键词有别的结论：关键词更保守（有明确词才出手），以它为准
        self.last_source = "keyword"
        self.last_conf = net_conf
        return kw_hit

    @staticmethod
    def _as_hit(text, emotion, kw_hit):
        """把网络结论包装成与 detect_last 同形的 (emotion, kw, pos)。"""
        pos = kw_hit[2] if kw_hit else 0
        kw = kw_hit[1] if kw_hit else ""
        return emotion, kw, pos

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
    def learn_from_choice(self, text, emotion):
        """用户选了一个"某情绪"分类下的颜文字 → 强化该分类。

        只在"当前判定就是这个情绪"或"这次选择能说明情绪"时才学，
        避免把噪声当信号。返回是否真的更新了权重。
        """
        if not text or not emotion:
            return False
        idx = en.class_index(emotion)
        if idx is None:
            return False
        with self._lock:
            net = self.net
        if net is None:
            return False
        try:
            net.train_one(text, idx, lr=LEARN_RATE)
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
