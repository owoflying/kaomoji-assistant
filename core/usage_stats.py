"""使用分析：从 UserState 的原始数据算出可展示的统计。

## 为什么单独一个模块

`UserState` 里已经存了足够多的原始数据：

    usage          {颜文字: 使用次数}
    last_used      {颜文字: 最后使用时间戳}
    hour_usage     {时段桶: {颜文字: 次数}}
    history        [{text, ts, source}, ...]  按时间倒序
    recent         最近用过的（去重）
    favorites      收藏

但这些只是"记账"。要回答用户真正关心的问题——

    * 我最常用的是哪几个？
    * 我一天里什么时候用得最多？
    * 我的使用在变多还是变少？
    * 推荐准不准？

——还需要一层聚合。放在这里而不是塞进 UserState，是因为：

1. `UserState` 的职责是**存储**，加统计会让它继续膨胀（已经 34KB）
2. 纯函数好测试，不需要 Qt
3. UI 换一版展示方式不用动数据层

## 设计原则：数据不足时说"数据不足"

新用户只有几条记录，这时候画个热力图只会误导。所以每个统计函数
都会同时返回 `has_data` 之类的信号，让 UI 决定是展示还是提示
"再多用几次就能看到统计了"。
"""
import datetime

#: 时段桶 → 中文显示名
BUCKET_LABELS = {
    "dawn": "清晨",
    "morning": "上午",
    "noon": "午后",
    "afternoon": "下午",
    "evening": "夜晚",
    "night": "深夜",
}

#: 时段桶的展示顺序（按一天的自然顺序，不是 HOUR_BUCKETS 的定义顺序）
BUCKET_ORDER = ("dawn", "morning", "noon", "afternoon", "evening", "night")

#: 少于这么多条记录就认为"数据不足"
MIN_SAMPLES = 5


def _ts_to_date(ts):
    """时间戳 → 日期（本地时区）。"""
    try:
        return datetime.datetime.fromtimestamp(float(ts)).date()
    except Exception:
        return None


def top_items(state, n=10):
    """最常用的颜文字。

    :return: `[(text, count), ...]`，按次数降序；没有任何记录时返回空列表

    ## 为什么用 `usage` 而不是 `history`

    `history` 有长度上限（`max_history`，默认 100 条），早期记录会被挤掉。
    而 `usage` 是累计计数，不会因为时间推移而丢失——
    "历史最常用"要看它。
    """
    try:
        usage = getattr(state, "usage", None) or {}
    except Exception:
        return []
    items = []
    for text, cnt in usage.items():
        try:
            c = int(cnt)
        except Exception:
            continue
        if c > 0:
            items.append((str(text), c))
    items.sort(key=lambda x: (-x[1], x[0]))
    return items[:max(1, int(n))]


def hour_distribution(state):
    """按时段统计使用次数。

    :return: `[(bucket, label, count, ratio), ...]`，按一天的自然顺序

    `ratio` 是占比 0~1，供 UI 画条形图。全部为 0 时 ratio 也是 0。

    ## 为什么用 `hour_usage` 而不是 `history`

    同上：`history` 会丢老数据，`hour_usage` 是累计的。
    """
    try:
        hu = getattr(state, "hour_usage", None) or {}
    except Exception:
        hu = {}

    counts = {}
    for bucket in BUCKET_ORDER:
        per = hu.get(bucket) or {}
        total = 0
        for cnt in per.values():
            try:
                total += int(cnt)
            except Exception:
                continue
        counts[bucket] = total

    grand = sum(counts.values())
    out = []
    for bucket in BUCKET_ORDER:
        c = counts.get(bucket, 0)
        out.append((bucket, BUCKET_LABELS.get(bucket, bucket), c,
                    (c / grand) if grand else 0.0))
    return out


def peak_bucket(state):
    """使用最多的时段。

    :return: `(bucket, label, count)`；没有记录时返回 `(None, "", 0)`
    """
    dist = hour_distribution(state)
    best = max(dist, key=lambda x: x[2]) if dist else None
    if best is None or best[2] <= 0:
        return (None, "", 0)
    return (best[0], best[1], best[2])


def daily_counts(state, days=14):
    """最近 N 天每天的使用次数。

    :return: `[(date, count), ...]`，从旧到新，**包含没有使用的日子（0）**

    ## 为什么用 `history`

    这里要的是时间序列，`usage` 只有总次数没有时间信息（`last_used`
    也只有最后一次）。只有 `history` 带逐条时间戳。

    代价是受 `max_history` 上限影响——用得特别多的用户，早期记录会被
    挤掉。这可以接受：近期的趋势本来就是最有用的。

    ## 为什么补齐 0

    不补 0 的话，两天没用会画成"连续"的，趋势看起来比实际更好。
    """
    try:
        hist = getattr(state, "history", None) or []
    except Exception:
        hist = []

    days = max(1, int(days))
    today = datetime.date.today()
    buckets = {}
    for i in range(days):
        d = today - datetime.timedelta(days=days - 1 - i)
        buckets[d] = 0

    for rec in hist:
        try:
            d = _ts_to_date(rec.get("ts"))
        except Exception:
            continue
        if d in buckets:
            buckets[d] += 1

    return [(d, buckets[d]) for d in sorted(buckets)]


def activity_trend(state, days=14):
    """近期活跃度趋势：后半段日均 vs 前半段日均。

    :return: `(ratio, direction)`，`direction` ∈ {"up", "down", "flat", "none"}

    ratio = 后半段日均 / 前半段日均。前半段为 0 时：
    后半段也 0 → "none"；否则 → 无穷大，统一记为 999（UI 只需知道"涨了"）。
    """
    data = daily_counts(state, days)
    if len(data) < 4:
        return (1.0, "none")
    mid = len(data) // 2
    first = [c for _d, c in data[:mid]]
    second = [c for _d, c in data[mid:]]

    def _avg(xs):
        return (sum(xs) / len(xs)) if xs else 0.0

    a, b = _avg(first), _avg(second)
    total = sum(c for _d, c in data)
    if total == 0:
        return (1.0, "none")
    if a == 0:
        return (999.0 if b > 0 else 1.0, "up" if b > 0 else "none")
    r = b / a
    if r >= 1.15:
        return (r, "up")
    if r <= 0.87:
        return (r, "down")
    return (r, "flat")


def summary(state):
    """给 UI 用的一份完整统计快照。

    :return: dict，含各分项 + `has_data` 判断
    """
    try:
        total = int(getattr(state, "total_inserts", lambda: 0)() or 0)
    except Exception:
        total = 0
    try:
        recent = list(getattr(state, "recent", None) or [])
    except Exception:
        recent = []
    try:
        favs = list(getattr(state, "favorites", None) or [])
    except Exception:
        favs = []
    try:
        hist_len = len(getattr(state, "history", None) or [])
    except Exception:
        hist_len = 0

    top = top_items(state, 10)
    dist = hour_distribution(state)
    peak = peak_bucket(state)
    daily = daily_counts(state, 14)
    ratio, direction = activity_trend(state, 14)

    return {
        "total": total,
        "recent": recent,
        "favorites": favs,
        "history_len": hist_len,
        "top": top,
        "hour_dist": dist,
        "peak": peak,
        "daily": daily,
        "trend_ratio": ratio,
        "trend": direction,
        # 少于这个数就提示"多记录几次再看"
        "has_data": total >= MIN_SAMPLES,
        "min_samples": MIN_SAMPLES,
    }
