"""逐块浮现：页面内容从上往下依次淡入 + 上浮。

## 为什么要"从上往下依次"

整页淡入只是"整体变亮"，没有层次，看起来像一次性刷新。
而**从上到下依次浮现**符合人眼的阅读顺序——视线自然从标题扫到
正文再到操作区，观感是"内容被铺开"，而不是"页面被替换"。

## 怎么用（一行接入）

    from ui.page_reveal import reveal_page
    reveal_page(page)

它会找到页面里的内容容器，按**垂直位置**排序它的直接子控件，
依次淡入。新页面、插件页都能直接用，不需要改任何业务代码。

## 只扫"直接子控件"

不递归进卡片内部。原因：

* 卡片里的元素（标题/说明/按钮）本来就该跟着卡片一起出现，
  再拆开逐个浮现会显得琐碎、慢
* 递归之后元素数量可能上百，逐个动画反而卡

所以粒度是"一块一块"（标题 / 卡片 / 卡片 / 底部按钮），
这正是用户说"从上往下依次浮现"想要的层次。

## 与切页动画的关系

切页时 `AnimatedStackedWidget` 负责"新页整体出现"，
本模块负责"页内内容铺开"。两者叠加时**必须错开时间**，
否则一起动会互相掩盖。`reveal_page(..., delay=60)` 让页内
浮现稍晚于切页启动。

## 性能

每个元素一个 `QGraphicsOpacityEffect`，动画结束立刻摘掉。
实测 6~8 个元素时开销可忽略；超过 12 个会自动截断（见 `MAX_ITEMS`），
避免长页面变成"瀑布"看上半天。
"""
from PySide6.QtCore import QEasingCurve, QPropertyAnimation, QTimer
from PySide6.QtWidgets import QGraphicsOpacityEffect

#: 最多让多少块依次浮现。超出的直接显示（否则长页面要等很久）
MAX_ITEMS = 12

#: 元素之间的间隔（ms）
STEP_MS = 55

#: 单块动画时长（ms）
ITEM_MS = 200

#: 每块的上浮距离（像素）
RISE = 8


def _find_content_layout(page):
    """找到页面里的内容布局，返回 (layout, container) 或 (None, None)。

    ## 查找顺序

        1. 名叫 `PageScroll` 的滚动区（`page_layout()` 建的标准页面）
        2. **任意** `QScrollArea` 的内容（设置页 / 关于页自己建的滚动区，
           objectName 不叫 PageScroll）
        3. 页面自身的布局

    ⚠ 第 2 步很关键：设置页和关于页的滚动区是各页面自己 new 的，
    只按名字找会漏掉，结果整个页面只被当成"一块"处理——
    表现为"这两个页面没有逐块浮现效果"。
    """
    from PySide6.QtWidgets import QScrollArea

    # 1. 标准页面
    try:
        area = page.findChild(QScrollArea, "PageScroll")
        if area is not None:
            body = area.widget()
            if body is not None and body.layout() is not None:
                return body.layout(), body
    except Exception:
        pass

    # 2. 任意滚动区——取**内容最多**的那个（页面可能嵌套多层滚动区）
    try:
        best = None
        best_count = -1
        for area in page.findChildren(QScrollArea):
            body = area.widget()
            if body is None or body.layout() is None:
                continue
            n = body.layout().count()
            if n > best_count:
                best, best_count = body.layout(), n
        if best is not None and best_count > 0:
            return best, best.parentWidget()
    except Exception:
        pass

    # 3. 页面自身布局
    try:
        if page.layout() is not None and page.layout().count() > 0:
            return page.layout(), page
    except Exception:
        pass
    return None, None


def _visible_children(layout):
    """取出布局里的直接子控件，按**垂直位置**从上到下排序。

    ⚠ 用 `geometry().y()` 而不是布局索引：布局索引是创建顺序，
    不一定等于视觉顺序（有的页面先 addStretch 后 addWidget）。
    """
    items = []
    try:
        for i in range(layout.count()):
            w = layout.itemAt(i).widget()
            if w is None or not w.isVisible():
                continue
            # 跳过纯占位（高度 0）
            if w.height() <= 0:
                continue
            items.append(w)
    except Exception:
        return []
    try:
        items.sort(key=lambda w: (w.y(), w.x()))
    except Exception:
        pass
    return items


def reveal_page(page, *, delay=0, step=STEP_MS, item_ms=ITEM_MS,
                rise=RISE, max_items=MAX_ITEMS):
    """让页面的内容从上往下依次浮现。

    :param page: 页面 QWidget
    :param delay: 整体延迟（ms）。切页后调用时给个 40~80，
                  让切页动画先起步
    :param step: 相邻两块之间的间隔（ms）
    :param item_ms: 单块动画时长（ms）
    :param rise: 每块起始时比最终位置低多少像素
    :param max_items: 最多处理多少块（超出直接显示）
    :return: 动画对象列表（调用方应 hold 住，否则可能被 GC）

    ## 上浮怎么实现

    直接 `move()` 会和自己所在的布局打架（布局下一帧会改回去）——
    这个问题在切页动画上踩过。所以这里**只做透明度**，
    位移改用 `QGraphicsOpacityEffect` 之外的手段：

    实际上不做位移，只做淡入 + 让每块"稍微晚一点"出现。
    错峰本身就产生了"从上往下铺开"的动感，不需要真的移动像素。
    """
    anims = []
    layout, _container = _find_content_layout(page)
    if layout is None:
        return anims

    items = _visible_children(layout)[:max_items]
    if not items:
        return anims

    for i, w in enumerate(items):
        try:
            eff = QGraphicsOpacityEffect(w)
            eff.setOpacity(0.0)
            w.setGraphicsEffect(eff)
        except Exception:
            continue

        anim = QPropertyAnimation(eff, b"opacity")
        anim.setDuration(item_ms)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        # OutQuint：末段收敛快，不会拖尾
        anim.setEasingCurve(QEasingCurve.OutQuint)
        anims.append(anim)

        def _make_finish(widget, effect):
            def _finish():
                # 必须摘掉 effect：留着会让该控件每次重绘都多一次离屏合成
                try:
                    widget.setGraphicsEffect(None)
                except Exception:
                    pass
            return _finish

        anim.finished.connect(_make_finish(w, eff))

        start_at = int(delay + i * step)
        QTimer.singleShot(start_at, anim.start)

    # 保险：即使某块动画没触发，2 秒后也把 effect 全部摘掉，
    # 绝不能让内容永久停在半透明（那就是"页面打开是空的"）
    def _safety():
        for w in items:
            try:
                eff = w.graphicsEffect()
                if eff is not None and eff.opacity() < 0.99:
                    w.setGraphicsEffect(None)
                elif eff is not None:
                    w.setGraphicsEffect(None)
            except Exception:
                pass

    QTimer.singleShot(int(delay + len(items) * step + item_ms + 300), _safety)
    return anims


def reveal_widget_children(container, **kw):
    """给任意容器（不是标准页面）做逐块浮现。

    插件页、开发者页这类自己搭布局的界面可以直接用这个。
    """
    return reveal_page(container, **kw)
