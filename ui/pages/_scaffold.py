"""页面公共骨架：页面外边距、页标题、卡片、分区标题。

7 个设置页此前各自抄了一遍「页面根布局 + PageTitle + Card + SectionTitle」，
外边距（36/28）与卡片内边距（16/14）全靠手抄保持一致，改一版视觉要翻 7 个文件。
抽到这里之后，各页只描述自己的内容结构，视觉参数只有一处权威来源。

刻意保持为普通函数而不是基类：这些页面各自继承 QWidget、生命周期不同，
套一层基类只会增加 MRO 与 __init__ 协作的复杂度，收益远不如直接组合。
"""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QScrollArea, QVBoxLayout, QWidget,
)

# 页面四周留白与区块间距（统一窗口内容区）
PAGE_MARGIN = (36, 28, 36, 28)
PAGE_SPACING = 22

# 卡片内边距与行间距
CARD_MARGIN = (16, 14, 16, 14)
CARD_SPACING = 12


def page_layout(widget, spacing=PAGE_SPACING, margins=PAGE_MARGIN,
                scroll=True):
    """给页面挂上标准根布局并返回**内容布局**。

    :param scroll: 是否把内容包进滚动区（默认 True）

    ## 为什么默认要滚动

    窗口最小高度是 640px，而主页内容实测需要 **668px**——直接超出 28px，
    底部「使用提示」卡片被硬生生裁掉，文字显示不全。

    设置页早就用 `QScrollArea` 解决了这个问题（它的注释写着"修复此前
    被压成细线"），但主页/词库/搜索/快捷短语几个页面漏了这层。
    统一在骨架层加，所有页面都受益，也避免以后新页面再踩一次。

    ## 返回值说明

    返回的仍然是**内容布局**（可直接 `addWidget`），滚动区只是包在外层。
    页面若需要访问滚动区本身（比如设置页要控制滚动位置），传
    `scroll=False` 自己建。

    ## 为什么横向不滚动

    窗口有最小宽度 900px，各页内容都按这个宽度设计。允许横向滚动会
    让用户能把内容拖出可视区，体验更差——所以固定 `ScrollBarAlwaysOff`，
    靠 `setWidgetResizable(True)` 让内容自适应宽度。
    """
    if not scroll:
        root = QVBoxLayout(widget)
        root.setContentsMargins(*margins)
        root.setSpacing(spacing)
        return root

    outer = QVBoxLayout(widget)
    outer.setContentsMargins(0, 0, 0, 0)
    outer.setSpacing(0)

    area = QScrollArea()
    area.setObjectName("PageScroll")
    area.setWidgetResizable(True)
    area.setFrameShape(QFrame.NoFrame)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    area.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
    area.setStyleSheet("background:transparent;border:none;")
    outer.addWidget(area, 1)

    body = QWidget()
    body.setObjectName("PageScrollBody")
    body.setStyleSheet("background:transparent;")
    area.setWidget(body)

    root = QVBoxLayout(body)
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
    """标准卡片容器（QSS: Card）。

    ## ⚠ 必须 `WA_StyledBackground`

    `QFrame` 默认**不绘制 QSS 里的 `background`**——只有设了
    `WA_StyledBackground` 才会真的画背景色/圆角。少了这一行，
    `QFrame#Card {{ background: ... }}` 形同虚设，卡片只剩一条边框，
    内容看起来是"飘"在页面上的（用户反馈过"卡片完全看不见"）。

    实测：加这个属性前后，卡片区域的像素从不透明背景变成纯白。
    """
    frame = QFrame(parent)
    frame.setObjectName("Card")
    frame.setAttribute(Qt.WA_StyledBackground, True)
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
