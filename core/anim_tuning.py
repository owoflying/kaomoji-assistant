"""动画时长与缓动曲线的**统一规范**。

## 为什么需要统一

原来各处的 `setEasingCurve(QEasingCurve.OutCubic)` 是随手写的，
结果用户反馈：

    "动画太长了显得很卡，而且不是非线性动画不够丝滑"

两个问题都能追溯到曲线选择：

### 1. `OutCubic` 的"前重后轻"看起来像卡住

`OutCubic` = `1-(1-t)^3`，在 t=0.3 时已经走完 **66%**。
于是观感是"瞬间跳到 2/3，然后慢吞吞拖完剩下 1/3"——
最后那段慢速移动被人眼读成"卡住不动了"。

### 2. 时长本身偏长

180ms 的纯淡入在桌面应用里偏长。Windows / macOS 的系统级
过渡多在 **100~150ms** 区间。超过 150ms 会开始有"等它做完"的感觉。

## 规范

    PAGE     切页淡入      130ms   OutQuint
    HOVER    悬停高光       90ms   OutQuad
    PICKER   候选条弹出    110ms   OutQuint
    THEME    换主题        220ms   InOutCubic（有遮罩，需更长）
    TOGGLE   开关滑动      110ms   OutQuint

**`OutQuint`** = `1-(1-t)^5`：t=0.3 时走完 83%，比 OutCubic 更"干脆"，
末段收敛更快，不会有拖尾感。这是当前 Material / Fluent 系统
推荐的小元素动效曲线。

**`OutQuad`** = `1-(1-t)^2`：比 OutQuint 柔和，适合短距离的
亮度渐变（悬停）——用太陡的曲线反而像"闪一下"。

**`InOutCubic`**：两端都缓，适合有明确"进入-停留-退出"结构的
长过渡（换主题的遮罩）。

## 怎么用

    from core.anim_settings import DURATION, EASING

    anim.setDuration(DURATION["page"])
    anim.setEasingCurve(EASING["page"])

不要在调用处写魔法数字——这里改一次，全局生效。
"""
from PySide6.QtCore import QEasingCurve

#: 各类动画的时长（ms）
DURATION = {
    "page": 130,      # 切页淡入
    "hover": 90,      # 悬停高光
    "picker": 110,    # 候选条弹出/收起
    "theme": 220,     # 换主题（带遮罩，需要更长）
    "toggle": 110,    # 开关滑动
}

#: 各类动画的缓动曲线
EASING = {
    # 小元素出现：干脆、无拖尾
    "page": QEasingCurve.OutQuint,
    "picker": QEasingCurve.OutQuint,
    "toggle": QEasingCurve.OutQuint,
    # 亮度渐变：柔和，太陡会像"闪一下"
    "hover": QEasingCurve.OutQuad,
    # 长过渡：两端都缓
    "theme": QEasingCurve.InOutCubic,
}


def apply(anim, kind):
    """给一个 QPropertyAnimation 套上规范里的时长与曲线。

    :param anim: QPropertyAnimation
    :param kind: `page` / `hover` / `picker` / `theme` / `toggle`
    :return: anim（便于链式调用）
    """
    try:
        anim.setDuration(DURATION.get(kind, 130))
        anim.setEasingCurve(EASING.get(kind, QEasingCurve.OutQuint))
    except Exception:
        pass
    return anim


def duration(kind, default=130):
    """取某类动画的时长。"""
    return DURATION.get(kind, default)


def easing(kind):
    """取某类动画的曲线。"""
    return EASING.get(kind, QEasingCurve.OutQuint)
