"""外观页：主题、透明度、背景材质的统一入口。

## 为什么要独立成页

原来这些散在「设置」页的「外观」和「背景材质」两个分组里，和热键、
输入、推荐、系统等十几项挤在一起，一屏放不下。加了背景材质之后：

* 用户找不到「面板不透明度」了（被材质那 6 行挤到屏幕外）；
* 外观相关的改动互相冲突（详见 `ui/appearance.py` 的说明）。

现在外观独立成页，结构清晰：

    主题        明暗 / 配色方案 / 编辑自定义主题
    透明度      面板 / 候选条
    背景材质    开关 / 效果 / 强度 / 背景图 / 图片透明度 / 暗化

## 唯一入口

所有读写都走 `AppearanceController`（见 ui/appearance.py），
不直接碰 config。这样"改 A 把 B 带歪"从机制上被杜绝。
"""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QPushButton, QSlider, QWidget,
)

from ui.fluent_combobox import FluentComboBox
from ui.pages._scaffold import (
    card, card_layout, page_layout, page_title, section_title,
)
from ui.toggle_switch import ToggleSwitch


class AppearancePage(QWidget):
    """外观设置页。

    :param controller: `AppearanceController`（唯一入口）
    :param theme: 当前主题（用于控件配色）
    :param on_apply: 无参回调——外观变了、需要落盘 + 重绘时调用
    """

    #: 页面重建时需要显式清空的控件属性。
    #:
    #: 这些都是"按界面能力决定建不建"的控件。重建时必须置 None——
    #: 否则旧的 Python 引用还挂着，`getattr` 依然能拿到已销毁的控件，
    #: 既让能力判断失效，又会在操作时抛 RuntimeError。
    _DYNAMIC_ATTRS = (
        "_follow_check",
        "theme_combo", "preset_combo", "edit_preset_btn",
        "panel_alpha_slider", "panel_alpha_slider_value",
        "material_check", "material_combo",
        "material_strength_slider", "material_strength_slider_value",
        "material_saturate_slider", "material_saturate_slider_value",
        "material_brightness_slider", "material_brightness_slider_value",
        "material_dim_slider", "material_dim_slider_value",
        "material_image_btn", "material_image_clear",
        "material_image_label",
        "main_crop_btn", "main_crop_reset", "main_crop_label",
    )

    def __init__(self, controller, theme, on_apply=None, parent=None):
        super().__init__(parent)
        self._ctrl = controller
        self._theme = theme
        self._on_apply = on_apply
        self._loading = False
        self._surface_combo = None
        self._build()

    # ------------------------------------------------------------ 构建
    def _build(self):
        """按**表面能力表**渲染界面。

        ## 为什么不一次性把所有控件都建出来再隐藏

        隐藏的控件仍然存在，用户切界面时状态会互相干扰（改过的值残留在
        别的界面上），代码里也要到处判断"这个控件在这个界面该不该用"。
        现在改成：**每个区块按能力决定建不建**，切换界面时重建。

        ## 重建的代价

        控件数量在几十个量级，重建 + 重新布局实测 <15ms，远低于感知阈值。
        而换来的是"界面里出现的每个控件都真的对当前界面有效"——
        这正是之前"候选条也显示裁切选项"这类问题的反面。
        """
        # ⚠ `page_layout()` 返回的是**滚动区里的内容布局**，不是
        # `self.layout()`（那是最外层的滚动区容器）。
        #
        # 之前重建时拿 `self.layout()` 当根布局去清空和追加，于是新控件
        # 被加到滚动区**外层**、没有滚动区的尺寸约束，所有行全被压到
        # 同一点（实测所有控件 y=6、宽 34）——用户看到的就是
        # "按钮全挤在一起"。
        self._root = page_layout(self)
        self._anim_checks = {}
        self._rebuild_body()

    def _rebuild_body(self):
        """按当前表面的能力重建整个页面内容。"""
        # 清空旧内容（用**内容布局**，不是 self.layout()）
        root = getattr(self, "_root", None)
        if root is None:
            self._root = root = page_layout(self)
        while root.count():
            item = root.takeAt(0)
            w = item.widget() if item is not None else None
            if w is not None:
                w.hide()
                w.setParent(None)
                w.deleteLater()

        # ⚠ 必须同时清掉这些**属性引用**。
        #
        # `deleteLater()` 是异步的，而且即使控件被销毁，`self.theme_combo`
        # 这个属性仍然指向那个对象（Python 引用还在）。于是
        # `getattr(self, "theme_combo")` 依然能拿到旧控件，
        # 判断"这个界面有没有该控件"就永远是"有"。
        #
        # 实测就踩到了：切到「对话框」后页面上确实不再显示材质控件，
        # 但 `hasattr` 仍为 True，导致 `_sync_enabled` 去操作已销毁的
        # 控件（RuntimeError），而且能力驱动的判断完全失效。
        for attr in self._DYNAMIC_ATTRS:
            try:
                setattr(self, attr, None)
            except Exception:
                pass

        from core.surface_spec import (
            ACCENT_FIELDS, CROP_FIELDS, MATERIAL_FIELDS, THEME_FIELDS, spec,
        )

        surface = self._current_surface()
        sp = spec(surface)
        root.addWidget(page_title("外观"))

        # ---- 作用于哪个界面（永远显示，它是页面的主语）----
        root.addWidget(section_title("应用于"))
        c = card()
        lay = card_layout(c)
        self._add_row(lay, "界面", self._surface_row())
        if surface != "main":
            self._add_row(lay, "独立配置", self._follow_check_row())
            self._hint(lay, "打开后，这个界面将有自己的一套设置，"
                            "不再跟随主窗口。关闭则完整沿用主窗口。")
        else:
            self._follow_check = None
            self._hint(lay, "主窗口是其余界面的默认外观来源。"
                            "想让某个界面不一样，在上面选它并打开「独立配置」。")
        if sp.note:
            self._hint(lay, sp.note)
        root.addWidget(c)

        # ---- 主题（所有界面都支持明暗；配色看能力）----
        if sp.supports_any(THEME_FIELDS):
            root.addWidget(section_title("主题"))
            c = card()
            lay = card_layout(c)
            self._add_row(lay, "主题", self._theme_combo())
            self._add_row(lay, "配色方案", self._preset_row())
            if sp.supports_any(ACCENT_FIELDS):
                self._hint(lay, "「自定义」配色可以用右侧的「编辑…」调整颜色；"
                                "强调色也在编辑器里设置。")
            else:
                self._hint(lay, "此界面的强调色跟随主窗口。")
            root.addWidget(c)

        # ---- 透明度 ----
        if sp.supports("panel_alpha"):
            root.addWidget(section_title("透明度"))
            c = card()
            lay = card_layout(c)
            self._add_row(lay, "面板不透明度", self._panel_alpha_row())
            self._hint(lay, "整窗透明度：拉到 100% 完全不透明，"
                            "拉低会透出桌面和背景图。")
            root.addWidget(c)

        # ---- 背景材质 ----
        if sp.supports_any(MATERIAL_FIELDS):
            root.addWidget(section_title("背景材质"))
            c = card()
            lay = card_layout(c)
            self._add_row(lay, "启用背景材质", self._material_toggle())
            into = ""
            if sp.own_image:
                into = "；可用「选择图片」为本界面单独指定背景图，" \
                       "不设置则沿用主窗口那张。"
            else:
                into = "；此界面不提供独立背景图，始终沿用主窗口那张。"
            self._hint(lay, "设置了背景图时，面板会自动让位给背景图"
                            "（始终看得见）；没设背景图时显示主题实色面板。"
                            + into)
            self._add_row(lay, "材质效果", self._material_combo())
            self._add_row(lay, "材质强度", self._material_strength_row())
            self._add_row(lay, "玻璃饱和度", self._material_saturate_row())
            self._add_row(lay, "玻璃亮度", self._material_brightness_row())
            if sp.own_image:
                self._add_row(lay, "背景图", self._material_image_row())
            # 裁切只在支持的界面上出现
            if sp.supports_all(CROP_FIELDS):
                self._add_row(lay, "显示图片的一部分", self._main_crop_row())
            self._add_row(lay, "背景暗化/提亮", self._material_dim_row())
            root.addWidget(c)

        # ---- 动画（全局设置，与界面无关，永远显示）----
        root.addWidget(section_title("动画"))
        c = card()
        lay = card_layout(c)
        from core.anim_settings import ANIMATIONS

        for name, key, label, desc in ANIMATIONS:
            chk = self._anim_toggle(name, key)
            self._anim_checks[name] = chk
            self._add_row(lay, label, chk)
        self._add_row(lay, "一键全开/全关", self._anim_all_row())
        self._hint(lay, "关掉只是让动画变成瞬间完成，功能本身不受影响。"
                        "低配机器或远程桌面下全关能降低 CPU 占用。"
                        "（首次启动的欢迎页动画不受这里控制）")
        root.addWidget(c)
        root.addStretch(1)

        # 回填当前值
        self.refresh_from_controller()

    # ---------- 动画开关 ----------
    def _anim_toggle(self, name, key):
        """单个动画开关。"""
        chk = ToggleSwitch(self._theme)
        chk.toggled.connect(lambda v, k=key: self._push_anim(k, v))
        return chk

    def _anim_all_row(self):
        row = QHBoxLayout()
        btn = QPushButton("全部开启")
        btn.clicked.connect(lambda: self._set_all_anims(True))
        row.addWidget(btn)
        btn2 = QPushButton("全部关闭")
        btn2.clicked.connect(lambda: self._set_all_anims(False))
        row.addWidget(btn2)
        row.addStretch(1)
        return row

    def _set_all_anims(self, value):
        """一键开关所有动画。"""
        if self._loading:
            return
        from core.anim_settings import set_all

        cfg = self._ctrl.config()
        set_all(cfg, value)
        self.refresh_from_controller()
        if self._on_apply is not None:
            try:
                self._on_apply(dict(cfg))
            except Exception:
                pass

    def _push_anim(self, key, value):
        """改一个动画开关。

        ⚠ 动画开关不属于外观主题包，它管的是交互行为，
        所以直接写配置、不走 _push（后者会把未知键丢掉）。
        """
        if self._loading:
            return
        cfg = self._ctrl.config()
        cfg[key] = bool(value)
        if self._on_apply is not None:
            try:
                self._on_apply(dict(cfg))
            except Exception:
                pass

    # ---------- 界面切换 ----------
    def _surface_row(self):
        """选择当前要编辑哪个界面。"""
        from core.theme_profile import SURFACES

        self._surface_combo = FluentComboBox(self._theme)
        for key, label in SURFACES.items():
            self._surface_combo.addItem(label, key)
        self._surface_combo.setMinimumWidth(150)
        self._surface_combo.currentIndexChanged.connect(
            lambda *_a: self._on_surface_changed())
        return self._surface_combo

    def _follow_check_row(self):
        """是否给当前界面单独配置（关闭 = 沿用主窗口）。"""
        self._follow_check = ToggleSwitch(self._theme)
        self._follow_check.setToolTip(
            "关闭 = 该界面完整沿用主窗口的外观；\n"
            "打开 = 为该界面单独设置。")
        self._follow_check.toggled.connect(self._on_follow_toggled)
        return self._follow_check

    def _current_params(self):
        """当前选中界面**实际生效**的外观参数（主配置 + 该界面覆盖）。

        ⚠ 不要用 `self._ctrl.state()`：controller 只认平铺的旧键，
        而实际生效值是主题包按界面解析出来的。两者在"候选条独立配置"
        时会不一致。
        """
        try:
            from core.theme_profile import resolve

            return resolve(self._ctrl.config(), self._current_surface())
        except Exception:
            return {}

    def _on_surface_changed(self):
        """切换目标界面：**整页重建**。

        不同界面的能力不同（候选条没有裁切、对话框没有材质），
        重建才能保证"看到的每个控件都对当前界面有效"，
        也不用在十几个控件上到处判断"该不该显示"。

        ## ⚠ 必须先把目标界面记下来

        重建会**新建** `_surface_combo`。如果直接把新 combo 的
        `currentData()` 当依据，它此刻还是初始值（main），
        于是"切到候选条"实际又渲染回主窗口——自己把自己覆盖掉。

        实测就踩到了：切到 picker 后 `_current_surface()` 仍是 main，
        给 picker 设的独立配置全写进了主配置。
        """
        if self._loading:
            return
        try:
            target = self._surface_combo.currentData() or "main"
        except Exception:
            target = "main"
        self._pending_surface = target
        self._rebuild_body()
        self._pending_surface = None

    def _current_surface(self):
        """当前选中的界面。

        重建过程中（`_pending_surface` 已设置）优先用它——
        那时 `_surface_combo` 可能还没建好或仍是初始值。
        """
        pend = getattr(self, "_pending_surface", None)
        if pend:
            return pend
        try:
            return self._surface_combo.currentData() or "main"
        except Exception:
            return "main"

    def _on_follow_toggled(self, follow):
        """切「独立配置」：开 = 把当前生效值落成一份覆盖；关 = 删掉覆盖。

        ## ⚠ 字段必须全量复制，不能手写清单

        这里原先是手写的 8 个字段，漏了 8 个——包括 **theme_mode /
        theme_preset**（明暗与配色），于是打开独立配置后：

            在这个界面改深浅色 → patch 有 theme_mode
            → set_override 能写进去
            但覆盖层是**从漏了 theme_mode 的快照**建的

        更糟的是 `resolve()` 合并时，覆盖层里没有的键会回落到主配置，
        所以"打开独立配置 → 改深浅色 → 界面没反应"，
        用户完全看不出哪里错了。

        现在直接从 `DEFAULT_PROFILE` 全量复制：主题包加字段，
        这里自动跟上，不会再漏。
        """
        if self._loading:
            return
        from core.theme_profile import (
            DEFAULT_PROFILE, clear_override, resolve, set_override,
        )

        surface = self._current_surface()
        if surface == "main":
            return
        cfg = self._ctrl.config()
        if follow:
            p = resolve(cfg, surface)
            # 全量快照：把这个界面当前**实际生效**的每个字段都固化下来，
            # 之后改动任何一项都由覆盖层接管。
            snapshot = {}
            for key in DEFAULT_PROFILE:
                if key in p:
                    snapshot[key] = p[key]
            set_override(cfg, surface, snapshot)
        else:
            clear_override(cfg, surface)
        if self._on_apply is not None:
            try:
                self._on_apply(dict(cfg))
            except Exception:
                pass
        self.refresh_from_controller()

    # ---------- 候选条外观 ----------
    def _main_crop_row(self):
        """主窗口背景图的「只显示一部分」——用可视化裁切对话框。"""
        row = QHBoxLayout()
        self.main_crop_btn = QPushButton("选择显示区域…")
        self.main_crop_btn.setToolTip("打开裁切窗口，拖拽选图里的一块")
        self.main_crop_btn.clicked.connect(self._edit_main_crop)
        row.addWidget(self.main_crop_btn)
        self.main_crop_reset = QPushButton("整张")
        self.main_crop_reset.clicked.connect(self._reset_main_crop)
        row.addWidget(self.main_crop_reset)
        self.main_crop_label = QLabel("")
        self.main_crop_label.setObjectName("Caption")
        row.addWidget(self.main_crop_label)
        row.addStretch(1)
        return row

    def _edit_main_crop(self):
        """弹出可视化裁切框（拖拽选区，不是填数字）。"""
        from ui.crop_dialog import edit_crop
        from ui.window_material import SLOT_MAIN, background_path

        path = background_path(SLOT_MAIN)
        if not path:
            from PySide6.QtWidgets import QMessageBox

            QMessageBox.information(self, "裁切", "请先选择一张背景图。")
            return
        cur = self._current_params()
        crop = edit_crop(path, (cur.get("crop_x", 0.0), cur.get("crop_y", 0.0),
                                cur.get("crop_w", 0.0), cur.get("crop_h", 0.0)),
                         parent=self, title="主窗口背景图 - 选择显示区域",
                         preview_label="主窗口效果预览")
        if crop is None:
            return
        # crop 来自裁切对话框，是全图的相对比例；None/全 0 表示整张
        self._push(crop_x=crop[0], crop_y=crop[1],
                   crop_w=crop[2], crop_h=crop[3])
        self._refresh_main_crop_label()

    def _reset_main_crop(self):
        self._push(crop_x=0.0, crop_y=0.0, crop_w=0.0, crop_h=0.0)
        self._refresh_main_crop_label()

    def _refresh_main_crop_label(self):
        cur = self._current_params()
        try:
            cw = float(cur.get("crop_w", 0.0))
            ch = float(cur.get("crop_h", 0.0))
            if cw <= 0 or ch <= 0:
                self.main_crop_label.setText("整张")
            else:
                self.main_crop_label.setText(
                    "%d%% × %d%% @ (%d%%, %d%%)" % (
                        round(cw * 100), round(ch * 100),
                        round(float(cur.get("crop_x", 0.0)) * 100),
                        round(float(cur.get("crop_y", 0.0)) * 100)))
        except Exception:
            pass

    def _add_row(self, layout, label, widget_or_layout):
        """一行设置项：左标签 + 右控件（与设置页同样的视觉规格）。"""
        row = QHBoxLayout()
        row.setContentsMargins(0, 6, 0, 6)
        row.setSpacing(12)
        row.setAlignment(Qt.AlignVCenter)
        lb = QLabel(label)
        lb.setObjectName("BodyText")
        row.addWidget(lb)
        row.addStretch(1)
        if isinstance(widget_or_layout, QWidget):
            row.addWidget(widget_or_layout)
        else:
            row.addLayout(widget_or_layout)
        container = QWidget()
        container.setLayout(row)
        container.setMinimumHeight(36)
        layout.addWidget(container)

    def _hint(self, layout, text):
        """分组底部的说明文字。"""
        lb = QLabel(text)
        lb.setObjectName("Caption")
        lb.setWordWrap(True)
        layout.addWidget(lb)

    # ------------------------------------------------------------ 控件
    def _theme_combo(self):
        self.theme_combo = FluentComboBox(self._theme)
        self.theme_combo.addItem("浅色", "light")
        self.theme_combo.addItem("深色", "dark")
        self.theme_combo.addItem("跟随系统", "system")
        self.theme_combo.currentIndexChanged.connect(
            lambda *_a: self._push(theme_mode=self.theme_combo.currentData()))
        self.theme_combo.setMinimumWidth(120)
        return self.theme_combo

    def _preset_row(self):
        from core.theme_presets import preset_choices

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        self.preset_combo = FluentComboBox(self._theme)
        for pid, name in preset_choices():
            self.preset_combo.addItem(name, pid)
        self.preset_combo.addItem("自定义", "custom")
        self.preset_combo.currentIndexChanged.connect(
            lambda *_a: self._on_preset_changed())
        self.preset_combo.setMinimumWidth(110)
        row.addWidget(self.preset_combo)

        self.edit_preset_btn = QPushButton("编辑…")
        self.edit_preset_btn.setToolTip("调整自定义主题的颜色")
        self.edit_preset_btn.clicked.connect(self._open_theme_editor)
        row.addWidget(self.edit_preset_btn)
        row.addStretch(1)
        return row

    def _slider_row(self, attr, config_key, lo, hi, fmt="%d%%",
                    on_change=None):
        """0~100 通用滑块行。"""
        row = QHBoxLayout()
        s = QSlider(Qt.Horizontal)
        s.setRange(lo, hi)
        s.setFixedWidth(140)
        val = QLabel("")
        val.setFixedWidth(44)
        s.valueChanged.connect(lambda v: val.setText(fmt % v))
        s.sliderReleased.connect(
            lambda *_a: (on_change or self._push_from_slider)(config_key, s))
        row.addWidget(s)
        row.addWidget(val)
        row.addStretch(1)
        setattr(self, attr, s)
        setattr(self, attr + "_value", val)
        return row

    def _panel_alpha_row(self):
        # 面板不透明度：0~100 映射到 0.0~1.0
        return self._slider_row(
            "panel_alpha_slider", "panel_alpha", 0, 100,
            on_change=lambda k, s: self._push(panel_alpha=s.value() / 100.0))

    def _material_toggle(self):
        self.material_check = ToggleSwitch(self._theme)
        self.material_check.setToolTip(
            "开启后可用自定义背景图和玻璃效果。\n"
            "关闭则使用纯色背景，最省资源。")
        self.material_check.toggled.connect(
            lambda *_a: self._push(material_enabled=self.material_check.isChecked()))
        return self.material_check

    def _material_combo(self):
        from ui.window_material import material_choices

        self.material_combo = FluentComboBox(self._theme)
        for mid, name in material_choices():
            self.material_combo.addItem(name, mid)
        self.material_combo.setToolTip(
            "毛玻璃：高斯模糊，背景隐约可见\n"
            "磨砂玻璃：轻微模糊 + 细颗粒\n"
            "雾化：半透明叠层，画面柔和")
        self.material_combo.currentIndexChanged.connect(
            lambda *_a: self._push(
                material_type=self.material_combo.currentData()))
        self.material_combo.setMinimumWidth(120)
        return self.material_combo

    def _material_strength_row(self):
        return self._slider_row(
            "material_strength_slider", "material_strength", 0, 100,
            on_change=lambda k, s: self._push(material_strength=s.value()))

    def _material_saturate_row(self):
        """玻璃饱和度 ×10（10 = 1.0x，30 = 3.0x）。

        这是"玻璃精致感"最关键的参数——纯模糊只会糊，提饱和才有通透感。
        """
        row = QHBoxLayout()
        s = QSlider(Qt.Horizontal)
        s.setRange(10, 30)
        s.setFixedWidth(140)
        val = QLabel("")
        val.setFixedWidth(44)
        s.valueChanged.connect(lambda v: val.setText("%.1fx" % (v / 10.0)))
        s.sliderReleased.connect(
            lambda *_a: self._push(material_saturate=s.value()))
        row.addWidget(s)
        row.addWidget(val)
        row.addStretch(1)
        self.material_saturate_slider = s
        self.material_saturate_slider_value = val
        return row

    def _material_brightness_row(self):
        """玻璃亮度 ×100（80 = 0.8x，130 = 1.3x）。"""
        row = QHBoxLayout()
        s = QSlider(Qt.Horizontal)
        s.setRange(80, 130)
        s.setFixedWidth(140)
        val = QLabel("")
        val.setFixedWidth(44)
        s.valueChanged.connect(lambda v: val.setText("%.2fx" % (v / 100.0)))
        s.sliderReleased.connect(
            lambda *_a: self._push(material_brightness=s.value()))
        row.addWidget(s)
        row.addWidget(val)
        row.addStretch(1)
        self.material_brightness_slider = s
        self.material_brightness_slider_value = val
        return row

    def _material_dim_row(self):
        return self._slider_row(
            "material_dim_slider", "material_dim", 0, 100,
            on_change=lambda k, s: self._push(material_dim=s.value()))

    def _material_image_row(self):
        row = QHBoxLayout()
        self.material_image_btn = QPushButton("选择图片…")
        self.material_image_btn.clicked.connect(self._pick_image)
        row.addWidget(self.material_image_btn)
        self.material_image_clear = QPushButton("清除")
        self.material_image_clear.clicked.connect(self._clear_image)
        row.addWidget(self.material_image_clear)
        self.material_image_label = QLabel("")
        self.material_image_label.setObjectName("Caption")
        row.addWidget(self.material_image_label)
        row.addStretch(1)
        return row

    # ------------------------------------------------------------ 交互
    def _push(self, **changes):
        """把改动应用到**当前选中的界面**。

        ## 两个去向

        * 选的是「主窗口」→ 写进主配置（theme_profile），
          所有**沿用主配置**的界面会一起跟着变
        * 选的是某个独立配置的界面 → 只写该界面的覆盖层，
          主窗口和其它界面不受影响

        ## 为什么不用 controller

        `AppearanceController` 是"主配置"的编辑器，只认识平铺的旧键。
        多界面覆盖是 `core.theme_profile` 的职责，两者语义不同，
        所以这里直接写主题包，再交给宿主落盘。
        """
        if self._loading:
            return
        from core.theme_profile import (
            get_profile, has_override, set_override, set_profile,
        )

        surface = self._current_surface()
        cfg = self._ctrl.config()

        # ---- 键名白名单：**自动来自主题包字段表**，不再手写 ----
        #
        # ## 这里曾经是一张手写的 alias 表，反复出事
        #
        # 手写表的致命问题：`core.theme_profile.DEFAULT_PROFILE` 加了新字段，
        # 但没人记得同步那张表 → 控件的改动走进 `_push` 后被静默丢弃，
        # 用户看到的是"点了没反应"。
        #
        # 已经踩过的例子：
        #   * 裁切四项（crop_x/y/w/h）漏了 → 「选择显示区域」点击无反应
        #   * theme_mode / theme_preset 漏了 → 换主题整个失效
        #
        # 现在改成**直接从主题包字段表取**：只要字段进了 DEFAULT_PROFILE，
        # 这里自动认识它。以后再也不用维护两处。
        from core.theme_profile import DEFAULT_PROFILE

        alias = {key: key for key in DEFAULT_PROFILE}
        patch = {}
        ignored = []
        for key, val in changes.items():
            if key in alias:
                patch[alias[key]] = val
            elif key in ("opacity",):
                continue        # 候选条透明度已合并到 panel_alpha
            else:
                ignored.append(key)

        # 有字段被丢弃时必须**留下痕迹**——静默丢弃是最难查的 bug 类型。
        if ignored:
            try:
                from core.applog import append_log

                append_log("warn", "appearance",
                           "_push 丢弃了未登记的字段：%r"
                           "（若这是外观选项，请加进 "
                           "core.theme_profile.DEFAULT_PROFILE）" % (ignored,))
            except Exception:
                pass

        if not patch:
            return

        # ⚠ 顺序很关键：**先拷快照，再改 config**。
        #
        # `cfg` 是宿主的 `self.config`（同一个 dict 对象）。宿主的
        # `apply_settings(new_cfg)` 是这么判断变化的：
        #
        #     old = dict(self.config)          # 拷当前值
        #     self.config.update(new_cfg)
        #     changed = {k for k ... if old.get(k) != v}
        #
        # 如果我们先 `set_profile(cfg, ...)` 再 `dict(cfg)`，那传出去的
        # `target` 里就是**新值**，宿主的 `old` 也是新值 → `changed` 恒为空
        # → 换肤分支不执行。
        #
        # 实测现象：下拉框显示"深色"、`config["theme_mode"]` 也变成了 dark，
        # 但界面一直停在浅色，`theme_ctrl.current()` 仍返回 light。
        #
        # 所以这里拷贝一份**改之前**的配置，改动只落在副本上，
        # 由宿主去统一落盘 + 广播。
        target = dict(cfg)
        if surface == "main":
            prof = get_profile(target)
            prof.update(patch)
            set_profile(target, prof)
        else:
            if not has_override(cfg, surface):
                return          # 未开独立配置时控件本就置灰，不该走到这
            set_override(target, surface, patch)
            # 单界面覆盖层也要同步到宿主的 config 里，否则宿主读旧值
            cfg["surface_overrides"] = target.get("surface_overrides")

        self._sync_enabled()
        if self._on_apply is None:
            return
        # 换材质种类时走过渡动画（明显视觉切换，需要遮掩重算停顿）
        if "material_type" in patch:
            animated = getattr(self, "_animated_apply", None)
            if animated is not None:
                animated(lambda: self._on_apply(target))
                self._refresh_after_apply()
                return
        self._on_apply(target)
        self._refresh_after_apply()

    def _refresh_after_apply(self):
        """应用改动后回填控件，让界面反映**真实生效值**。

        ## 为什么需要它

        `_on_apply` 最终由宿主异步处理（写盘 + 广播 + 重绘）。
        而先前那些 `refresh_from_controller()` 调用都发生在
        **配置还没更新**的时刻，于是读到的仍是旧值：

            点候选条 → 独立配置 → 选「深色」
            → `resolve(picker)` 已经是 dark
            → 但下拉框显示 light（回填时配置还没落）

        用户看到的是"选了深色，下拉框跳回浅色"——看起来像没保存。

        这里在应用之后再回填一次，保证界面与实际生效值一致。
        """
        try:
            self.refresh_from_controller()
        except Exception:
            pass

    def _push_from_slider(self, _key, _slider):
        # 默认路径不会走到（各滑块都自带 on_change）
        pass

    def _on_preset_changed(self):
        if self._loading:
            return
        self._sync_preset_btn()
        self._push(theme_preset=self.preset_combo.currentData() or "classic")

    def _sync_preset_btn(self):
        try:
            self.edit_preset_btn.setEnabled(
                self.preset_combo.currentData() == "custom")
        except Exception:
            pass

    def _sync_enabled(self):
        """按「当前界面 + 是否开独立配置 + 材质是否开启」联动置灰。

        ## 为什么用 _set_enabled 容错

        页面现在**按能力渲染**，不支持的控件根本没被创建
        （`self.material_combo` 可能不存在）。直接 `self.xxx.setEnabled()`
        会在切到「对话框」这类界面时抛 AttributeError。

        所以统一走一个"属性存在才操作"的小工具，而不是到处 try。
        """
        from core.theme_profile import has_override, resolve

        def _set(names, enabled):
            for n in names:
                w = getattr(self, n, None)
                if w is not None:
                    try:
                        w.setEnabled(enabled)
                    except Exception:
                        pass

        try:
            cfg = self._ctrl.config()
            surface = self._current_surface()
            own = surface == "main" or has_override(cfg, surface)
            params = resolve(cfg, surface)
            mat_on = own and bool(params.get("material_enabled", False))

            # 材质相关：关着就置灰
            _set(("material_combo", "material_strength_slider",
                  "material_saturate_slider", "material_brightness_slider",
                  "material_image_btn", "main_crop_btn", "main_crop_reset",
                  "material_dim_slider"), mat_on)

            # 「清除背景图」只在本界面**自己有图**时可用
            clear_btn = getattr(self, "material_image_clear", None)
            if clear_btn is not None:
                slot = self._own_image_slot()
                has_img = False
                if slot:
                    from ui.window_material import background_path

                    has_img = bool(background_path(slot))
                clear_btn.setEnabled(mat_on and has_img)

            # 主题相关：只有开了独立配置（或就是主窗口）才能改
            _set(("theme_combo", "preset_combo", "edit_preset_btn",
                  "panel_alpha_slider"), own)
        except Exception:
            pass

    # ------------------------------------------------------------ 背景图
    def _own_image_slot(self):
        """当前界面**自己的**背景图槽位。

        ## 为什么必须按表面取槽位

        原来 `copy_background(path)` / `clear_background()` 都用默认参数
        （`SLOT_MAIN`），于是给「托盘菜单」点「清除背景图」清掉的是
        **主窗口的图**——用户实测反馈过。

        :return: 槽位名；当前界面不支持独立图时返回 None
        """
        from core.surface_spec import spec

        sp = spec(self._current_surface())
        return sp.image_slot if sp.own_image else None

    def _pick_image(self):
        from PySide6.QtWidgets import QFileDialog, QMessageBox

        from ui.window_material import IMAGE_SUFFIXES, copy_background

        slot = self._own_image_slot()
        if not slot:
            return          # 该界面不支持独立背景图（控件本就该置灰）

        patterns = " ".join("*" + e for e in IMAGE_SUFFIXES)
        path, _ = QFileDialog.getOpenFileName(
            self, "选择背景图", "", "图片 (%s);;所有文件 (*.*)" % patterns)
        if not path:
            return
        ok, result = copy_background(path, slot=slot)
        if not ok:
            QMessageBox.warning(self, "背景图", result)
            return
        # 选了图就把材质打开，否则用户选完看不到任何变化
        self._material_check.setChecked(True)
        self._refresh_image_label()
        self._sync_enabled()
        # 图片本身不在配置里（存在数据目录），仍要通知重绘
        if self._on_apply is not None:
            self._on_apply()

    def _clear_image(self):
        """清除**当前界面自己**的背景图（绝不动主窗口的图）。"""
        from PySide6.QtWidgets import QMessageBox

        from ui.window_material import clear_background

        slot = self._own_image_slot()
        if not slot:
            return
        if slot == "background":
            # 主窗口的图是所有界面的默认来源，删了会影响一批界面，
            # 所以必须确认一次（其它界面各有自己的槽位，删了只影响自己）。
            box = QMessageBox(self)
            box.setWindowTitle("清除背景图")
            box.setIcon(QMessageBox.Question)
            box.setText("要清除主窗口的背景图吗？")
            box.setInformativeText(
                "沿用主窗口背景图的界面也会一起变成纯色面板。\n"
                "已经单独设置过背景图的界面不受影响。")
            yes = box.addButton("清除", QMessageBox.AcceptRole)
            box.addButton("取消", QMessageBox.RejectRole)
            box.exec()
            if box.clickedButton() is not yes:
                return

        if clear_background(slot):
            self._refresh_image_label()
            self._sync_enabled()
            if self._on_apply is not None:
                self._on_apply()

    def _refresh_image_label(self):
        import os

        from core.surface_spec import spec
        from ui.window_material import background_path

        surface = self._current_surface()
        sp = spec(surface)
        slot = self._own_image_slot()
        try:
            if slot:
                p = background_path(slot)
                if p:
                    self.material_image_label.setText(
                        "%s（本界面独立）" % os.path.basename(p))
                else:
                    self.material_image_label.setText(
                        "未设置（沿用主窗口）")
            else:
                # 不支持独立图 → 如实说明它跟随谁，而不是假装"没设置"
                self.material_image_label.setText(
                    "跟随主窗口（此界面不支持独立背景图）")
        except Exception:
            pass
        del sp

    def _open_theme_editor(self):
        """打开主题编辑器（自定义配色）。"""
        from ui.theme_editor import ThemeEditorDialog

        parent = self.window()
        cb = getattr(self, "_theme_preview_cb", None)

        def _preview(cfg, palette=None):
            if cb is not None:
                cb(cfg, palette)

        before = self._ctrl.state().theme_preset
        dlg = ThemeEditorDialog(self._ctrl.config(), on_preview=_preview,
                                parent=parent)
        if dlg.exec() != dlg.DialogCode.Accepted:
            if cb is not None:
                cb(self._ctrl.config(), None)
            # 取消：恢复原来的预设选择
            self._loading = True
            try:
                idx = self.preset_combo.findData(before)
                self.preset_combo.setCurrentIndex(idx if idx >= 0 else 0)
            finally:
                self._loading = False
            self._sync_preset_btn()
            return

        from core.theme_presets import save_user_theme

        ok, msg = save_user_theme(dlg.result_data())
        if not ok:
            return
        # 自定义主题的色板已经**写到磁盘**了。必须强制重载，
        # 不能只调 `_push(theme_preset="custom")`——
        # 若当前 preset 本来就是 custom，diff 为空、apply_settings 不触发，
        # 结果就是"编辑器里改了强调色，点了确定却什么都不发生"。
        # 这正是之前反复踩的"配置提前落盘、差异比对失效"坑。
        cb2 = getattr(self, "_force_reload_cb", None)
        if cb2 is not None:
            try:
                cb2()
            except Exception:
                pass
        if cb is not None:
            cb(self._ctrl.config(), None)
        self._loading = True
        try:
            idx = self.preset_combo.findData("custom")
            self.preset_combo.setCurrentIndex(idx if idx >= 0 else 0)
        finally:
            self._loading = False
        self._sync_preset_btn()
        self._push(theme_preset="custom")

    # ------------------------------------------------------------ 刷新
    def refresh_from_controller(self):
        """按**当前选中的界面**回填所有控件（不触发保存）。

        参数来自 `core.theme_profile.resolve(cfg, surface)`——
        主窗口读主配置，其它界面读"主配置 + 该界面覆盖"的合并结果。
        """
        from core.theme_profile import has_override, resolve

        cfg = self._ctrl.config()
        surface = self._current_surface()
        params = resolve(cfg, surface)
        is_main = surface == "main"
        own = is_main or has_override(cfg, surface)

        self._loading = True
        try:
            # 界面选择器与「独立配置」开关
            idx = self._surface_combo.findData(surface)
            if idx >= 0 and self._surface_combo.currentIndex() != idx:
                self._surface_combo.setCurrentIndex(idx)
            # 主窗口不显示这个开关（页面按能力渲染时它压根不存在）
            fc = getattr(self, "_follow_check", None)
            if fc is not None:
                fc.setEnabled(not is_main)
                fc.setChecked(False if is_main else own)

            # 主题 / 配色 / 材质效果（可能因为界面能力不同而没被创建）
            for combo_attr, val in (
                    ("theme_combo", params.get("theme_mode", "light")),
                    ("preset_combo", params.get("theme_preset", "classic")),
                    ("material_combo", params.get("material_type", "none"))):
                combo = getattr(self, combo_attr, None)
                if combo is None:
                    continue
                i2 = combo.findData(val)
                combo.setCurrentIndex(i2 if i2 >= 0 else 0)

            mat_chk = getattr(self, "material_check", None)
            if mat_chk is not None:
                mat_chk.setChecked(bool(
                    params.get("material_enabled", False)))

            # 滑块（单位不同，分别格式化）
            for attr, val, fmt in (
                    ("panel_alpha_slider",
                     int(round(float(params.get("panel_alpha", 0.92)) * 100)),
                     lambda v: "%d%%" % v),
                    ("material_strength_slider",
                     int(params.get("material_strength", 55)),
                     lambda v: "%d%%" % v),
                    ("material_saturate_slider",
                     int(params.get("material_saturate", 18)),
                     lambda v: "%.1fx" % (v / 10.0)),
                    ("material_brightness_slider",
                     int(params.get("material_brightness", 104)),
                     lambda v: "%.2fx" % (v / 100.0)),
                    ("material_dim_slider",
                     int(params.get("material_dim", 0)),
                     lambda v: "%d%%" % v)):
                s = getattr(self, attr, None)
                if s is not None:
                    s.setValue(max(s.minimum(), min(s.maximum(), val)))
                    lbl = getattr(self, attr + "_value", None)
                    if lbl is not None:
                        lbl.setText(fmt(s.value()))

            # 这两个只在对应控件存在时才有意义（按能力渲染）
            if getattr(self, "material_image_label", None) is not None:
                self._refresh_image_label()
            if getattr(self, "main_crop_btn", None) is not None:
                self._refresh_main_crop_label()
            # 动画开关（读原始配置，不属于主题包）
            try:
                from core.anim_settings import ANIMATIONS as _ANIMS

                for name, key, _label, _desc in _ANIMS:
                    chk = getattr(self, "_anim_checks", {}).get(name)
                    if chk is not None:
                        chk.setChecked(bool(cfg.get(key, True)))
            except Exception:
                pass
        finally:
            self._loading = False
        self._sync_preset_btn()
        self._sync_enabled()

    def set_theme(self, theme_obj):
        """换肤时同步控件配色。"""
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
