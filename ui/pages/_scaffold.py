"""页面公共骨架：页面外边距、页标题、卡片、分区标题。

7 个设置页此前各自抄了一遍「页面根布局 + PageTitle + Card + SectionTitle」，
外边距（36/28）与卡片内边距（16/14）全靠手抄保持一致，改一版视觉要翻 7 个文件。
抽到这里之后，各页只描述自己的内容结构，视觉参数只有一处权威来源。

刻意保持为普通函数而不是基类：这些页面各自继承 QWidget、生命周期不同，
套一层基类只会增加 MRO 与 __init__ 协作的复杂度，收益远不如直接组合。
"""
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout

# 页面四周留白与区块间距（统一窗口内容区）
PAGE_MARGIN = (36, 28, 36, 28)
PAGE_SPACING = 22

# 卡片内边距与行间距
CARD_MARGIN = (16, 14, 16, 14)
CARD_SPACING = 12


def page_layout(widget, spacing=PAGE_SPACING, margins=PAGE_MARGIN):
    """给页面挂上标准根布局并返回它。"""
    root = QVBoxLayout(widget)
    root.setContentsMargins(*margins)
    root.setSpacing(spacing)
    return root


def page_title(text):
    """页面主标题（QSS: PageTitle）。"""
    lb = QLabel(text)
    lb.setObjectName("PageTitle")
    return lb


def section_title(text):
    """区块小标题（QSS: SectionTitle）。"""
    lb = QLabel(text)
    lb.setObjectName("SectionTitle")
    return lb


def card(parent=None):
    """标准卡片容器（QSS: Card）。"""
    frame = QFrame(parent)
    frame.setObjectName("Card")
    return frame


def card_layout(card_widget, margins=CARD_MARGIN, spacing=CARD_SPACING,
                horizontal=False):
    """给卡片挂上标准内边距并返回布局。"""
    lay = QHBoxLayout(card_widget) if horizontal else QVBoxLayout(card_widget)
    lay.setContentsMargins(*margins)
    lay.setSpacing(spacing)
    return lay


def card_title(text):
    """卡片内的小标题（QSS: CardTitle）。"""
    lb = QLabel(text)
    lb.setObjectName("CardTitle")
    return lb


def caption(text=""):
    """次要说明文字（QSS: Caption）。"""
    lb = QLabel(text)
    lb.setObjectName("Caption")
    return lb


def body_text(text=""):
    """正文（QSS: BodyText）。"""
    lb = QLabel(text)
    lb.setObjectName("BodyText")
    return lb
