"""使用分析页：把 UserState 里记的账变成看得懂的东西。

## 这张页回答什么

用户平时不会有意识地"统计自己"，但会想知道：

    * 我最常用哪几个颜文字？
    * 我一天里什么时候用得最多？
    * 最近用得比之前多还是少？
    * 我到底用过多少次？

数据一直都在（`core/user_state.py` 里存着 usage / hour_usage / history），
只是没展示。这个页面只做展示，不改任何数据。

## 数据不足时怎么办

新装用户只有几条记录，画出来全是 0 看起来像坏了。所以：
少于 `MIN_SAMPLES`（5 次）时，不画图，改成一句提示 +
当前已有记录数。避免"用一张空图表劝退新用户"。
"""
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from core import usage_stats as US
from ui.pages._scaffold import (
    body_text, caption, card, card_layout, page_layout, page_title,
    section_title,
)
from ui.widgets.charts import BarChart, HeatBar, Sparkline


class _StatTile(QWidget):
    """一个大数字 + 一句说明的小方块。"""

    def __init__(self, label, theme=None, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        self.value = QLabel("0")
        self.value.setObjectName("StatValue")
        self.caption = caption(label)
        lay.addWidget(self.value)
        lay.addWidget(self.caption)

    def set_value(self, text):
        self.value.setText(str(text))


class UsageStatsPage(QWidget):
    """使用分析页。

    :param state: `UserState` 实例（只读使用）
    :param theme: 当前主题
    """

    def __init__(self, state, theme, parent=None):
        super().__init__(parent)
        self._state = state
        self._theme = theme
        self._build()
        self.refresh()

    # ------------------------------------------------------------ 构建
    def _build(self):
        root = page_layout(self)
        root.addWidget(page_title("使用分析"))

        # ---- 概览 ----
        root.addWidget(section_title("概览"))
        c = card()
        lay = card_layout(c, margins=(20, 16, 20, 16), spacing=10)
        row = QHBoxLayout()
        row.setSpacing(28)
        self.t_total = _StatTile("累计上屏次数")
        self.t_today = _StatTile("今天使用")
        self.t_recent = _StatTile("最近使用过")
        self.t_fav = _StatTile("收藏")
        for t in (self.t_total, self.t_today, self.t_recent, self.t_fav):
            row.addWidget(t)
        row.addStretch(1)
        lay.addLayout(row)
        self.hint = caption("")
        self.hint.setWordWrap(True)
        lay.addWidget(self.hint)
        root.addWidget(c)

        # ---- 最常用 ----
        root.addWidget(section_title("最常用"))
        c = card()
        lay = card_layout(c, margins=(20, 16, 20, 16), spacing=8)
        self.top_chart = BarChart(theme=self._theme, label_width=150,
                                  min_height=30)
        lay.addWidget(self.top_chart)
        root.addWidget(c)

        # ---- 时段分布 ----
        root.addWidget(section_title("使用时段"))
        c = card()
        lay = card_layout(c, margins=(20, 16, 20, 16), spacing=8)
        self.heat = HeatBar(theme=self._theme, height=48)
        lay.addWidget(self.heat)
        self.peak_label = body_text("")
        lay.addWidget(self.peak_label)
        root.addWidget(c)

        # ---- 近期趋势 ----
        root.addWidget(section_title("最近 14 天"))
        c = card()
        lay = card_layout(c, margins=(20, 16, 20, 16), spacing=8)
        self.spark = Sparkline(theme=self._theme, height=88)
        lay.addWidget(self.spark)
        self.trend_label = body_text("")
        lay.addWidget(self.trend_label)
        root.addWidget(c)
        root.addStretch(1)

    # ------------------------------------------------------------ 刷新
    def refresh(self):
        """重新取数并更新所有控件。"""
        try:
            sm = US.summary(self._state)
        except Exception:
            return

        # ---- 概览 ----
        self.t_total.set_value(sm["total"])
        daily = sm["daily"]
        self.t_today.set_value(daily[-1][1] if daily else 0)
        self.t_recent.set_value(len(sm["recent"]))
        self.t_fav.set_value(len(sm["favorites"]))

        if not sm["has_data"]:
            self.hint.setText(
                "目前记录还少（累计 %d 次，至少 %d 次才会生成图表）。"
                "多用几次颜文字，这里就会出现你的使用画像。"
                % (sm["total"], sm["min_samples"]))
        else:
            self.hint.setText("")

        # ---- 最常用 ----
        rows = [(text, cnt) for text, cnt in sm["top"]]
        self.top_chart.setVisible(bool(rows))
        if rows:
            self.top_chart.set_data(rows)

        # ---- 时段 ----
        dist = [(label, ratio, cnt) for _b, label, cnt, ratio
                in sm["hour_dist"]]
        self.heat.setVisible(bool(dist))
        if dist:
            self.heat.set_data(dist)
        bucket, label, cnt = sm["peak"]
        if bucket:
            self.peak_label.setText(
                "你最常在「%s」使用颜文字（%d 次）。" % (label, cnt))
        else:
            self.peak_label.setText("还没有足够的时段数据。")

        # ---- 趋势 ----
        values = [c for _d, c in daily]
        labels = [d.strftime("%m-%d") for d, _c in daily] if daily else []
        self.spark.set_data(values, labels)

        trend = sm["trend"]
        ratio = sm["trend_ratio"]
        if trend == "up":
            txt = "最近一周明显比之前多（约 %.1f 倍）" % ratio \
                if ratio < 900 else "最近一周开始用了"
        elif trend == "down":
            txt = "最近一周比之前少（约 %.0f%%）" % (ratio * 100)
        elif trend == "flat":
            txt = "最近两周的使用量基本持平"
        else:
            txt = "数据还不够，多记录几天就能看出趋势"
        self.trend_label.setText(txt)

    # ------------------------------------------------------------ 主题
    def set_theme(self, theme_obj):
        self._theme = theme_obj
        for w in (self.top_chart, self.heat, self.spark):
            try:
                w.set_theme(theme_obj)
            except Exception:
                pass
        self.update()
