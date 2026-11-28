"""时段工具：把时间映射成"清晨/上午/夜晚…"这类桶。

## 为什么单独一个模块

`core/rank_net.py` 需要按"一天里的哪个时段"构造特征，
`core/user_state.py` 需要按时段统计使用习惯——两边都要这一套定义。

原来定义在 `user_state` 里，`rank_net` 反向 import 它，形成循环：

    rank_net   → user_state.hour_bucket
    user_state → rank_net.features

两个模块在功能上其实是**平级**的（一个是模型、一个是数据），
谁 import 谁都不合适。把纯粹的"时间归类"抽出来，双方都依赖它，
依赖就变成了干净的树：

    time_buckets  ←  rank_net
    time_buckets  ←  user_state
    time_buckets  →  （只依赖标准库）

## 边界

这里只做"时间 → 桶"的**纯计算**，不碰任何业务状态。
凡是需要读配置、写文件、访问网络的东西，都不该放进来。
"""
import datetime

#: 时段划分：(桶名, 起始小时, 结束小时)，闭区间。
#:
#: 最后一桶跨零点（23:00–04:59），所以判断函数要特殊处理。
HOUR_BUCKETS = (
    ("dawn", 5, 8),       # 05:00–08:59 清晨
    ("morning", 9, 11),   # 09:00–11:59 上午
    ("noon", 12, 13),     # 12:00–13:59 午后
    ("afternoon", 14, 17),  # 14:00–17:59 下午
    ("evening", 18, 22),  # 18:00–22:59 夜晚
    ("night", 23, 4),     # 23:00–04:59 深夜
)

#: 桶名集合（供遍历 / 校验）
BUCKET_NAMES = tuple(name for name, _lo, _hi in HOUR_BUCKETS)


def hour_bucket(ts=None):
    """把时间戳（默认现在）映射成时段桶名。

    :param ts: Unix 时间戳；None 表示当前时间
    :return: 桶名，取值见 `BUCKET_NAMES`
    """
    h = (datetime.datetime.fromtimestamp(ts) if ts is not None
         else datetime.datetime.now()).hour
    for name, lo, hi in HOUR_BUCKETS:
        if lo <= hi:
            if lo <= h <= hi:
                return name
        elif h >= lo or h <= hi:      # 跨零点的桶
            return name
    return "morning"
