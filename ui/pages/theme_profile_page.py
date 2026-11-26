"""主题包页：主配置 + 各界面自动沿用 + 单界面覆盖。

## 这张页面解决什么

用户的原话：

    "最好实现一个完整主题包功能，有一个主配置，其他界面自动沿用。
     也可以单独为某个界面添加自定义单独配置。"

以及

    "一些独立窗口还是没有主题功能"

根因是之前每个窗口各存一套配置键，加窗口就要加键，最后大部分窗口
干脆没接。现在所有窗口都通过 `core.theme_profile` 从**同一份主配置**
解析外观，这张页只管：

1. 告知用户"哪些界面正在沿用主配置"
2. 允许把某个界面切成"独立配置"，并单独调它的参数
3. 一键恢复沿用
"""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QPushButton, QSlider, QWidget,
)

from core.theme_profile import (
    SURFACES, clear_override, has_override, resolve, set_override,
)
from ui.fluent_combobox import FluentComboBox
from ui.pages._scaffold import (
    card, card_layout, page_layout, page_title, section_title,
)
from ui.toggle_switch import ToggleSwitch

#: 覆盖层里允许单独调的字段 → (显示名, 控件类型, 范围)
_OVERRIDE_FIELDS = (
    ("material_enabled", "启用材质", "bool", None),
    ("material_type", "材质效果", "choice", ("none", "blur", "frosted", "fog")),
    ("material_strength", "材质强度", "range", (0, 100, "%d%%")),
    ("panel_alpha", "整窗不透明度", "range", (0, 100, "%d%%")),
    ("image_opacity", "背景图不透明度", "range", (0, 100, "%d%%")),
)


class ThemeProfilePage(QWidget):
    """主题包页。

    :param config_provider: 无参可调用，返回当前配置 dict
    :param on_apply: 无参回调——改了之后需要落盘 + 重绘
    :param theme: 当前主题（用于控件配色）
    """

    def __init__(self, config_provider, theme, on_apply=None, parent=None):
        super().__init__(parent)
        self._config_provider = config_provider
        self._theme = theme
        self._on_apply = on_apply
        self._loading = False
        self._surface = "main"          # 当前正在编辑哪个界面
        self._rows = {}
        self._build()
        self.refresh()

    # ---------------------------------------------------------- 构建
    def _build(self):
        root = page_layout(self)
        root.addWidget(page_title("主题包"))

        hint = QLabel(
            "所有界面默认沿用「主配置」。只有当你为某个界面打开"
            "「独立配置」时，它才会不再跟随主配置。")
        hint.setObjectName("BodyText")
        hint.setWordWrap(True)
        root.addWidget(hint)

        # ---- 各界面一览 ----
        root.addWidget(section_title("界面列表"))
        self._list_card = card()
        self._list_layout = card_layout(self._list_card)
        root.addWidget(self._list_card)

        # ---- 选中界面的配置 ----
        root.addWidget(section_title("单独配置"))
        self._detail_card = card()
        self._detail_layout = card_layout(self._detail_card)
        self._detail_title = QLabel("")
        self._detail_title.setObjectName("CardTitle")
        self._detail_layout.addWidget(self._detail_title)

        self._follow_check = ToggleSwitch(self._theme)
        self._follow_check.setToolTip(
            "关闭 = 该界面完整沿用主配置；\n"
            "打开 = 为该界面单独设置（只覆盖下面这些项）。")
        self._follow_check.toggled.connect(self._on_follow_toggled)
        self._add_row(self._detail_layout, "独立配置", self._follow_check)

        for key, name, kind, spec in _OVERRIDE_FIELDS:
            self._rows[key] = self._make_control(key, name, kind, spec)
            self._add_row(self._detail_layout, name, self._rows[key])
        root.addWidget(self._detail_card)
        root.addStretch(1)

    def _make_control(self, key, name, kind, spec):
        """按字段类型造控件，返回容器 widget。"""
        if kind == "bool":
            w = ToggleSwitch(self._theme)
            w.toggled.connect(lambda v, k=key: self._push(k, bool(v)))
            return w
        if kind == "choice":
            from ui.window_material import material_choices

            cb = FluentComboBox(self._theme)
            for mid, label in material_choices():
                cb.addItem(label, mid)
            cb.setMinimumWidth(120)
            cb.currentIndexChanged.connect(
                lambda *_a, k=key, c=cb: self._push(k, c.currentData()))
            return cb
        # range
        lo, hi, fmt = spec
        row = QWidget()
        lay = QHBoxLayout(row)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        sl = QSlider(Qt.Horizontal)
        sl.setRange(lo, hi)
        sl.setFixedWidth(140)
        val = QLabel("")
        val.setFixedWidth(44)
        sl.valueChanged.connect(lambda v: val.setText(fmt % v))
        sl.sliderReleased.connect(
            lambda *_a, k=key, s=sl: self._push(k, s.value()))
        lay.addWidget(sl)
        lay.addWidget(val)
        row._slider = sl
        row._value = val
        row._fmt = fmt
        return row

    def _add_row(self, layout, label, w):
        row = QHBoxLayout()
        row.setContentsMargins(0, 6, 0, 6)
        row.setSpacing(12)
        row.setAlignment(Qt.AlignVCenter)
        lb = QLabel(label)
        lb.setObjectName("BodyText")
        row.addWidget(lb)
        row.addStretch(1)
        row.addWidget(w)
        holder = QWidget()
        holder.setLayout(row)
        holder.setMinimumHeight(36)
        layout.addWidget(holder)

    # ---------------------------------------------------------- 刷新
    def refresh(self):
        """重建界面列表 + 回填当前选中界面的配置。"""
        cfg = self._config_provider() or {}
        self._loading = True
        try:
            # 清空列表行
            while self._list_layout.count():
                item = self._list_layout.takeAt(0)
                w = item.widget()
                if w is not None:
                    w.setParent(None)
                    w.deleteLater()
            for surface, label in SURFACES.items():
                self._list_layout.addWidget(
                    self._make_list_row(surface, label, cfg))
        finally:
            self._loading = False
        self._refresh_detail()

    def _make_list_row(self, surface, label, cfg):
        """一行：界面名 + 状态（沿用主配置 / 独立配置） + 编辑按钮。"""
        row = QHBoxLayout()
        row.setContentsMargins(0, 6, 0, 6)
        row.setSpacing(10)
        name = QLabel(label)
        name.setObjectName("BodyText")
        name.setFixedWidth(110)
        row.addWidget(name)

        if surface == "main":
            state = QLabel("主配置")
            state.setObjectName("Caption")
        elif has_override(cfg, surface):
            state = QLabel("独立配置")
            state.setObjectName("Caption")
        else:
            state = QLabel("沿用主配置")
            state.setObjectName("Caption")
        row.addWidget(state)
        row.addStretch(1)

        if surface != "main":
            btn = QPushButton("编辑")
            btn.setFixedWidth(72)
            btn.clicked.connect(
                lambda *_a, s=surface: self._select(s))
            row.addWidget(btn)

        holder = QWidget()
        holder.setLayout(row)
        holder.setMinimumHeight(36)
        return holder

    def _select(self, surface):
        self._surface = surface
        self._refresh_detail()

    def _refresh_detail(self):
        cfg = self._config_provider() or {}
        surface = self._surface
        self._detail_title.setText(
            "正在编辑：%s" % SURFACES.get(surface, surface))
        params = resolve(cfg, surface)
        own = has_override(cfg, surface)

        self._loading = True
        try:
            # 主窗口不能关掉"沿用"（它就是主配置本身）
            is_main = surface == "main"
            self._follow_check.setEnabled(not is_main)
            self._follow_check.setChecked(is_main or own)
            for key, name, kind, spec in _OVERRIDE_FIELDS:
                w = self._rows[key]
                # 主窗口永远可编辑；其它界面要开了"独立配置"才可编辑
                editable = is_main or own
                w.setEnabled(editable)
                val = params.get(key)
                if kind == "bool":
                    w.setChecked(bool(val))
                elif kind == "choice":
                    idx = w.findData(val)
                    w.setCurrentIndex(idx if idx >= 0 else 0)
                else:
                    sl = getattr(w, "_slider", None)
                    if sl is not None:
                        if key == "panel_alpha":
                            v = int(round(float(val or 0) * 100))
                        else:
                            v = int(val or 0)
                        sl.setValue(max(sl.minimum(), min(sl.maximum(), v)))
                        lbl = getattr(w, "_value", None)
                        if lbl is not None:
                            lbl.setText(w._fmt % sl.value())
        finally:
            self._loading = False

    # ---------------------------------------------------------- 写入
    def _push(self, key, value):
        """改一项：主窗口写主配置，其它界面写它的覆盖层。"""
        if self._loading:
            return
        if self._surface == "main":
            self._set_profile_field(key, value)
        else:
            set_override(self._config_provider(), self._surface, {key: value})
        self._apply_and_refresh()

    def _set_profile_field(self, key, value):
        """主窗口的改动写进主配置（theme_profile）。"""
        from core.theme_profile import get_profile, set_profile

        cfg = self._config_provider()
        prof = get_profile(cfg)
        prof[key] = value
        set_profile(cfg, prof)

    def _on_follow_toggled(self, follow):
        """开关「独立配置」：关 = 清掉覆盖层，回到沿用主配置。"""
        if self._loading or self._surface == "main":
            return
        cfg = self._config_provider()
        if follow:
            # 打开独立配置：把当前生效值写成一份覆盖，作为起点
            params = resolve(cfg, self._surface)
            patch = {k: params.get(k) for k, _, _, _ in _OVERRIDE_FIELDS}
            set_override(cfg, self._surface, patch)
        else:
            clear_override(cfg, self._surface)
        self._apply_and_refresh()

    def _apply_and_refresh(self):
        if self._on_apply is not None:
            try:
                self._on_apply()
            except Exception:
                pass
        self.refresh()

    # ---------------------------------------------------------- 主题
    def set_theme(self, theme_obj):
        self._theme = theme_obj
        from ui.fluent_combobox import FluentComboBox as _CB

        for c in self.findChildren(_CB):
            try:
                c.set_theme(theme_obj)
            except Exception:
                pass
        for t in self.findChildren(ToggleSwitch):
            try:
                t.update_theme(theme_obj)
            except Exception:
                pass
        self.update()
