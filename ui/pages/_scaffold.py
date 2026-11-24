"""页面公共骨架：页面外边距、页标题、卡片、分区标题。

7 个设置页此前各自抄了一遍「页面根布局 + PageTitle + Card + SectionTitle」，
外边距（36/28）与卡片内边距（16/14）全靠手抄保持一致，改一版视觉要翻 7 个文件。
抽到这里之后，各页只描述自己的内容结构，视觉参数只有一处权威来源。

刻意保持为普通函数而不是基类：这些页面各自继承 QWidget、生命周期不同，
套一层基类只会增加 MRO 与 __init__ 协作的复杂度，收益远不如直接组合。
"""
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
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
    # ⚠ ID 选择器：无选择器会级联到滚动区内部所有控件，把卡片白底清掉
    area.setStyleSheet(
        "QScrollArea#PageScroll{background:transparent;border:none;}")
    outer.addWidget(area, 1)

    body = QWidget()
    body.setObjectName("PageScrollBody")
    # ⚠ 必须用 ID 选择器。
    #
    # 没有选择器的 `background:transparent;` 会级联到 body 的**所有子孙**，
    # 把卡片（QFrame#Card）的白底也清掉。QSS 里卡片规则明明存在却看不见
    # ——用户反馈的"根本就没有卡片"就是这个原因。
    body.setStyleSheet("QWidget#PageScrollBody{background:transparent;}")
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


class MaterialCard(QFrame):
    """卡片容器：支持背景材质（磨砂/雾化）自绘。

    ## 为什么需要自绘

    `QFrame` 靠 QSS 画 background，做不出"磨砂颗粒""雾化叠层"这类效果。
    背景材质开启时，卡片要自己画一层带材质的半透明底，才能和窗口的
    背景图/毛玻璃呼应起来。

    ## 材质关闭时退回纯 QSS

    材质关着（或没有全局材质上下文）时，`paintEvent` 什么都不做，
    完全由 QSS 的 `QFrame#Card` 规则绘制——保证默认外观零变化、
    也零额外开销。

    ## 上下文从哪来

    卡片不持有配置（它是通用控件，页面各处都在用）。材质参数通过
    `material_context()` 从**窗口**上取——窗口有 `_background_material`
    / material 配置，卡片只读。
    """

    def __init__(self, parent=None, object_name="Card"):
        super().__init__(parent)
        self.setObjectName(object_name)
        self.setAttribute(Qt.WA_StyledBackground, True)
        #: 材质缓存：(QPixmap, key)
        self._mat_cache = None
        self._mat_key = None

    # ---------------------------------------------------------- 上下文
    def _material_ctx(self):
        """取当前的 (material_type, strength, enabled)。取不到就当关闭。"""
        if self._mat_cache is None and self._mat_key == "off":
            return None
        w = self.window()
        fn = getattr(w, "_material_paint_params", None)
        if fn is None:
            return None
        try:
            return fn()
        except Exception:
            return None

    # ---------------------------------------------------------- 绘制
    def paintEvent(self, e):
        ctx = self._material_ctx()
        if ctx is None:
            # 材质关闭：交给 QSS（父类会画 background）
            super().paintEvent(e)
            return

        mat_type, strength, card_color, radius = ctx
        key = (self.width(), self.height(), mat_type, strength, card_color)
        if self._mat_cache is None or self._mat_key != key:
            from PySide6.QtCore import QSize

            from ui.window_material import render_inner_surface

            self._mat_cache = render_inner_surface(
                QSize(max(1, self.width()), max(1, self.height())),
                material=mat_type, strength=strength, base_color=card_color)
            self._mat_key = key

        from PySide6.QtCore import QRectF
        from PySide6.QtGui import QPainter, QPainterPath, QPen

        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        path = QPainterPath()
        rect = QRectF(self.rect())
        path.addRoundedRect(rect, radius, radius)
        p.setClipPath(path)
        p.drawPixmap(0, 0, self._mat_cache)
        p.setClipping(False)
        # 边框跟着主题（材质底是半透明的，没边框会和背景糊在一起）
        try:
            t = getattr(self.window(), "theme", None)
            if t is not None:
                pen = QPen(QColor(t.card_border))
                pen.setWidth(1)
                p.setPen(pen)
                p.setBrush(Qt.NoBrush)
                p.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5),
                                  radius, radius)
        except Exception:
            pass
        p.end()


def card(parent=None):
    """标准卡片容器（QSS: Card + 可选背景材质）。

    ## ⚠ 必须 `WA_StyledBackground`

    `QFrame` 默认**不绘制 QSS 里的 `background`**——只有设了
    `WA_StyledBackground` 才会真的画背景色/圆角。少了这一行，
    `QFrame#Card {{ background: ... }}` 形同虚设，卡片只剩一条边框，
    内容看起来是"飘"在页面上的（用户反馈过"卡片完全看不见"）。

    实测：加这个属性前后，卡片区域的像素从不透明背景变成纯白。
    """
    return MaterialCard(parent)


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
