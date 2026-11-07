"""主页上的「问候卡片」：按当前时段显示一句问候。

**为什么放在主程序而不是插件里**：这张卡片是内置功能，与「统计行」「快捷入口」
同级，不依赖任何测试功能开关。插件版（`plugins/home_greeting/`）保留下来
作为反面教材，见 `docs/PLUGIN_GUIDE.md`。

时段划分按中文习惯（不是简单的上/下午二分）：

    05:00-10:59  上午     11:00-12:59  中午
    13:00-17:59  下午     18:00-04:59  晚上 / 凌晨

卡片刻意复用宿主的 objectName（`Card` / `HomeTitle` / `BodyText`），
这样它自动继承全局 QSS，深浅色切换、圆角、边框都不用写一行样式。
"""
import datetime

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout

from ui.pages._scaffold import body_text

# 时段 -> (emoji, 副标题提示)
_PERIODS = (
    (5, "凌晨", "🌙", "这么晚还在忙，记得休息一下。"),
    (11, "上午", "☀️", "新的一天，来点颜文字放松心情吧。"),
    (13, "中午", "🍚", "午饭时间到了，别忘了吃饭。"),
    (18, "下午", "🌤️", "泡杯茶，继续加油。"),
    (24, "晚上", "🌙", "今天辛苦了，早点休息。"),
)


def current_period(now=None):
    """返回 (时段名, emoji, 提示语)。便于测试注入固定时间。"""
    hour = (now or datetime.datetime.now()).hour
    for upper, name, emoji, tip in _PERIODS:
        if hour < upper:
            return name, emoji, tip
    return "晚上", "🌙", "今天辛苦了，早点休息。"


class GreetingCard(QFrame):
    """主页顶部的时间问候卡片。

    `refresh()` 按当前时间重绘文案；主页每次切到前台都会调用它
    （见 `HomePage.refresh_stats`）。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Card")
        self._build()
        self.refresh()

    def _build(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(16)

        self._emoji = QLabel()
        self._emoji.setObjectName("HomeTitle")
        self._emoji.setFixedWidth(40)
        self._emoji.setAlignment(Qt.AlignCenter)
        root.addWidget(self._emoji)

        box = QVBoxLayout()
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(4)

        self._title = QLabel()
        self._title.setObjectName("HomeTitle")
        box.addWidget(self._title)

        self._sub = body_text()
        self._sub.setWordWrap(True)
        box.addWidget(self._sub)

        root.addLayout(box, 1)

    def refresh(self, now=None):
        """按当前（或指定）时间刷新问候语。"""
        now = now or datetime.datetime.now()
        period, emoji, tip = current_period(now)
        self._emoji.setText(emoji)
        self._title.setText("%s好" % period)
        self._sub.setText("%s · %s" % (now.strftime("%H:%M"), tip))

    # 便于测试断言
    def texts(self):
        return self._emoji.text(), self._title.text(), self._sub.text()
