"""依次浮现：让窗口里的元素**逐块**淡入。

## 为什么要"逐块"而不是整体淡入

整体淡入（整个窗口从 0 到 1）只是"变亮"，没有层次，看久了像卡顿。
真实的动效通常是**错峰出现**：标题先到、说明跟上、内容块再跟上，
每块之间隔 60~80ms。这样眼睛会自然按顺序扫过内容，
观感是"铺开"而不是"亮起"。

## 为什么这里可以用 QGraphicsOpacityEffect

主窗口切页时我们**拒绝**用它——那是几百个控件的整页，每帧离屏合成
实测卡 200ms+。但欢迎界面只有几块小容器，离屏成本可忽略，
用它比自绘简单，也完全不影响布局。
"""
from PySide6.QtCore import QEasingCurve, QPropertyAnimation, QTimer
from PySide6.QtWidgets import QGraphicsOpacityEffect


def stagger_reveal(widgets, *, delay_step=70, duration=320, start_delay=0):
    """让一组控件依次淡入。

    :param widgets: 控件列表（按出现顺序）
    :param delay_step: 相邻两块之间的间隔（ms）
    :param duration: 单块动画时长（ms）
    :param start_delay: 整体延迟（ms）
    :return: 动画对象列表 —— 调用方要 hold 住，否则可能被 GC 掉导致动画中断
    """
    anims = []
    for i, w in enumerate(widgets):
        if w is None:
            continue
        try:
            eff = QGraphicsOpacityEffect(w)
            eff.setOpacity(0.0)
            w.setGraphicsEffect(eff)
        except Exception:
            continue

        anim = QPropertyAnimation(eff, b"opacity")
        anim.setDuration(duration)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.OutCubic)
        anims.append(anim)

        delay = int(start_delay + i * delay_step)
        # 每块单独定一个 Timer：用 singleShot 保持调用顺序清晰，
        # 且不依赖前一块是否完成（错峰是靠时间差，不是靠串联）
        QTimer.singleShot(delay, anim.start)
    return anims


def reveal_window(dialog, *, fade_ms=220):
    """窗口整体入场：从透明淡入。

    ## 为什么用 windowOpacity

    顶层窗口的 `windowOpacity` 走系统合成的全局 alpha，
    比给整个 QDialog 挂离屏效果便宜得多，也不会让内容画出边界。

    ⚠ 必须在 `show()` **之前**把透明度归零，否则会先闪现一帧不透明窗口。
    """
    try:
        dialog.setWindowOpacity(0.0)
    except Exception:
        return None
    anim = QPropertyAnimation(dialog, b"windowOpacity", dialog)
    anim.setDuration(fade_ms)
    anim.setStartValue(0.0)
    anim.setEndValue(1.0)
    anim.setEasingCurve(QEasingCurve.OutCubic)
    # 挂到窗口上，防止被 Python GC 回收导致动画中断
    dialog._reveal_anim = anim
    anim.start()
    return anim


def install_button_hover(root):
    """给窗口里的按钮装上平滑悬停过渡（复用现有实现）。"""
    try:
        from ui.hover_feedback import install_hover_faders

        return install_hover_faders(root)
    except Exception:
        return 0
