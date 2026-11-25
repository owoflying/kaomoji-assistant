"""自绘透明容器：可靠地"什么都不画"，让底部内容透出来。

## 为什么需要这个类

Qt 里让一个容器透明，理论上只要：

    w.setAttribute(Qt.WA_StyledBackground, True)
    w.setStyleSheet("QWidget#名字{background:transparent;}")

**但实测在无边框主窗口的子容器上不生效**：标题栏 `QWidget#TitleBar`
明明设了上面两条，渲染出来仍是 palette 的 Window 色（深色主题下
`#6d7073`），横跨整个标题栏宽度。

补充尝试过、也无效的手段：

* `w.setAutoFillBackground(False)`
* `w.paintEvent = lambda _e: None`（直接覆盖实例方法）
* 把样式表选择器改成 `QWidget#TitleBar`、`.TitleBar`、纯 `QWidget`

推测与主窗口的 `WA_TranslucentBackground` + `setMask()` + DWM 设置
叠加后的绘制路径有关（未逐一验证）。

## 做法

改成**真正的自绘控件**：继承 QWidget、显式实现 `paintEvent` 且
只做 `pass`。Qt 对这种"有自己的 paintEvent 的控件"不会再走
palette 自动填充路径。

本项目里 `MaterialCard`（ui/pages/_scaffold.py）就是自绘控件，
它能正常显示背景，说明这条路可靠。

## 用法

    bar = TransparentHost(height=40, object_name="TitleBar")
    layout = QHBoxLayout(bar)

子控件（按钮/标签）照常添加，容器本身完全透明。
"""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget


class TransparentHost(QWidget):
    """完全透明的容器（自绘，不画任何东西）。

    :param height: 固定高度；None 表示不固定
    :param object_name: 便于 QSS 定位与调试
    """

    def __init__(self, parent=None, height=None, object_name=""):
        super().__init__(parent)
        if object_name:
            self.setObjectName(object_name)
        if height is not None:
            self.setFixedHeight(int(height))
        # 不设背景、不自动填充；paintEvent 里什么都不画
        self.setAutoFillBackground(False)
        self.setAttribute(Qt.WA_NoSystemBackground, True)

    def paintEvent(self, _e):
        """刻意什么都不画 —— 让窗口 paintEvent 画的背景透出来。"""
        return
