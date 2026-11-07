"""界面修改的记录与自动回滚（插件的"后悔药"）。

**核心问题**：插件改了内置界面（换文字、藏控件、插新控件、甚至替换整页渲染），
停用/卸载/崩溃之后界面必须**原样复原**。否则用户会看到一个残缺的界面，
而罪魁祸首已经不在插件列表里了。

**做法**：所有修改都不直接落到控件上，而是经过 `UiMutator`：

    before = 读取原值  ->  记录  ->  施加修改

每条记录（`Change`）都带一个 `undo()` 闭包。回滚 = 倒序执行所有 undo。
这样无论插件改了文字、隐藏了控件、插了子控件还是替换了渲染，都能还原。

## 三类修改

1. **属性修改**：setText / setVisible / setStyleSheet / setEnabled / 图标…
   记录原值即可还原。
2. **结构修改**：插入子控件、移除内置控件。
   插入的记录"该控件 + 父布局 + 索引"，undo 时摘出并销毁；
   移除的记录控件本身，undo 时按原索引插回。
3. **渲染替换**：把某个页面/容器的渲染交给插件工厂。
   记录原 widget 与它的父容器，undo 时换回并销毁插件产物。

## 为什么不直接在控件上改

因为"改了一半崩溃"是最难排查的状态。所有 undo 收集在栈里、按插件隔离，
`PluginManager._detach` 调用 `rollback(plugin_id)` 一次性还原，
配合 try/except 保证单条 undo 失败不影响其它条。
"""
import weakref


def _alive(widget):
    """Qt 对象是否仍有效。"""
    if widget is None:
        return False
    try:
        from shiboken6 import isValid
        return isValid(widget)
    except Exception:
        try:
            return bool(widget)
        except RuntimeError:
            return False


class Change:
    """一条可回滚的界面修改记录。"""

    __slots__ = ("plugin_id", "ui_id", "kind", "undo", "_done")

    def __init__(self, plugin_id, ui_id, kind, undo):
        self.plugin_id = plugin_id
        self.ui_id = ui_id
        self.kind = kind
        self.undo = undo          # () -> None
        self._done = False

    def revert(self):
        """执行回滚；重复调用无副作用。返回是否真正执行了 undo。"""
        if self._done:
            return False
        self._done = True
        try:
            self.undo()
        except Exception:
            return False
        return True

    def __repr__(self):
        return "<Change %s %s.%s>" % (self.plugin_id, self.ui_id, self.kind)


class UiMutator:
    """按插件隔离的修改收集器：施加修改的同时记录回滚动作。

    用法（插件侧通过 api.mutate() 拿到它）::

        m = mutator
        m.set_text("home.greeting.title", "你好")
        m.set_visible("settings.some_row", False)
        m.insert("home.greeting", my_widget, index=0)
        m.rollback()          # 也可由宿主在卸载时自动调用

    所有方法在目标不存在时**静默返回 False**，绝不抛异常——插件代码里
    对"界面改了、元素没了"这种情形应该有合理默认行为，而不是崩溃。
    """

    def __init__(self, plugin_id, registry, on_change=None):
        self.plugin_id = plugin_id
        self._registry = registry
        #: 每次记录新修改时回调（宿主可用于"插件改了什么"的日志/审计）
        self._on_change = on_change
        self._changes = []

    # ---------- 内部 ----------
    def _target(self, ui_id):
        return self._registry.resolve(ui_id) if self._registry is not None else None

    def _record(self, ui_id, kind, undo):
        ch = Change(self.plugin_id, str(ui_id), kind, undo)
        self._changes.append(ch)
        if callable(self._on_change):
            try:
                self._on_change(ch)
            except Exception:
                pass
        return ch

    # ---------- 属性修改 ----------
    def set_text(self, ui_id, text):
        """改 QLabel / QPushButton 等控件的文字。"""
        w = self._target(ui_id)
        if w is None or not hasattr(w, "text") or not hasattr(w, "setText"):
            return False
        try:
            original = w.text()
        except Exception:
            return False
        w.setText(str(text))
        ref = weakref.ref(w)

        def _undo():
            obj = ref()
            if _alive(obj):
                obj.setText(original)

        self._record(ui_id, "text", _undo)
        return True

    def set_visible(self, ui_id, visible):
        """显示/隐藏一个控件（隐藏内置控件是常见需求）。"""
        w = self._target(ui_id)
        if w is None or not hasattr(w, "setVisible"):
            return False
        # 记 isHidden()（控件自身意愿）而非 isVisible()（受父级影响）
        was_hidden = bool(w.isHidden())
        w.setVisible(bool(visible))
        ref = weakref.ref(w)

        def _undo():
            obj = ref()
            if not _alive(obj):
                return
            # 用显式隐藏状态还原：isVisible 受父级影响，isHidden 才是控件自身意愿
            obj.setVisible(not was_hidden)

        self._record(ui_id, "visible", _undo)
        return True

    def set_style(self, ui_id, qss, append=True):
        """设置控件样式表。

        :param append: True=在原样式后追加（推荐，保留宿主级联）；
                       False=整体替换（会切断祖先 QSS，慎用）。
        """
        w = self._target(ui_id)
        if w is None or not hasattr(w, "setStyleSheet"):
            return False
        try:
            original = w.styleSheet()
        except Exception:
            return False
        new = (original + "\n" + str(qss)) if append else str(qss)
        w.setStyleSheet(new)
        ref = weakref.ref(w)

        def _undo():
            obj = ref()
            if _alive(obj):
                obj.setStyleSheet(original)

        self._record(ui_id, "style", _undo)
        return True

    def set_enabled(self, ui_id, enabled):
        """启用/禁用控件。"""
        w = self._target(ui_id)
        if w is None or not hasattr(w, "setEnabled"):
            return False
        original = bool(w.isEnabled())
        w.setEnabled(bool(enabled))
        ref = weakref.ref(w)

        def _undo():
            obj = ref()
            if _alive(obj):
                obj.setEnabled(original)

        self._record(ui_id, "enabled", _undo)
        return True

    def set_property(self, ui_id, name, value):
        """设置任意 Qt 属性（含 dynamic property，配合 QSS 选择器使用）。"""
        w = self._target(ui_id)
        if w is None or not hasattr(w, "setProperty"):
            return False
        original = w.property(name)
        w.setProperty(name, value)
        ref = weakref.ref(w)

        def _undo():
            obj = ref()
            if _alive(obj):
                obj.setProperty(name, original)

        self._record(ui_id, "property:%s" % name, _undo)
        return True

    # ---------- 结构修改 ----------
    def insert(self, ui_id, widget, index=-1):
        """把一个控件插入到目标容器的布局里（目标通常是某个卡片/行容器）。"""
        target = self._target(ui_id)
        if target is None or widget is None:
            return False
        lay = target.layout() if hasattr(target, "layout") else None
        if lay is None:
            return False
        if index is None or index < 0 or index > lay.count():
            index = lay.count()
        lay.insertWidget(index, widget)

        def _undo():
            # 摘出并销毁：先 hide 避免 setParent(None) 时变成可见顶层窗口
            try:
                widget.hide()
                parent_lay = widget.parentWidget().layout() if widget.parentWidget() else None
                if parent_lay is not None:
                    parent_lay.removeWidget(widget)
            except RuntimeError:
                return
            try:
                widget.setParent(None)
                widget.deleteLater()
            except RuntimeError:
                pass

        self._record(ui_id, "insert", _undo)
        return True

    def remove(self, ui_id):
        """把内置控件从布局里摘掉（可回滚：记住原位置，undo 时插回）。"""
        w = self._target(ui_id)
        if w is None:
            return False
        parent = w.parentWidget()
        lay = parent.layout() if parent is not None and hasattr(parent, "layout") else None
        if lay is None:
            return False
        try:
            index = lay.indexOf(w)
        except Exception:
            return False
        if index < 0:
            return False
        lay.removeWidget(w)
        try:
            w.hide()                    # 不销毁，undo 要还回去
            w.setParent(None)
        except RuntimeError:
            return False
        ref = weakref.ref(w)
        parent_ref = weakref.ref(parent)

        def _undo():
            obj, par = ref(), parent_ref()
            if not _alive(obj) or not _alive(par):
                return
            play = par.layout()
            if play is None:
                return
            obj.setParent(par)
            play.insertWidget(min(index, play.count()), obj)
            obj.show()

        self._record(ui_id, "remove", _undo)
        return True

    # ---------- 渲染替换 ----------
    def replace_render(self, ui_id, factory):
        """把目标容器**内部的内容**换成插件 factory 的产物。

        ⚠ 语义澄清：替换的是"容器里装的东西"，不是"容器本身在父布局里的位置"。
        - 想换掉欢迎卡片**的内容**  → ``replace_render("home.welcome", f)``
        - 想换掉**整个欢迎卡片**（位置也被占） → 先用 ``remove`` 再 ``insert``

        :param factory: ``factory(parent) -> QWidget``
        """
        container = self._target(ui_id)
        if container is None or not callable(factory):
            return False
        lay = container.layout() if hasattr(container, "layout") else None
        if lay is None:
            return False

        new_widget = factory(container)
        if new_widget is None:
            return False

        # 摘下原件（不销毁，undo 要还）
        #
        # ⚠ 必须**倒序** takeAt 而非 removeWidget：removeWidget 只摘控件、留下
        # 空的布局项（itemAt 返回 None），容器里会残留"看不见的占位"。
        # takeAt 连布局项一起摘走，容器才真正被清空。
        removed_items = []
        while lay.count():
            removed_items.append(lay.takeAt(0))
        for item in removed_items:
            w = item.widget() if item is not None else None
            if w is None:
                continue            # spacer / 嵌套布局：已被 takeAt 摘走，无需隐藏
            try:
                w.hide()
                w.setParent(None)
            except RuntimeError:
                pass
        lay.addWidget(new_widget)

        new_ref = weakref.ref(new_widget)

        def _undo():
            nw = new_ref()
            if _alive(nw):
                try:
                    nw.hide()
                    lay.removeWidget(nw)
                    nw.setParent(None)
                    nw.deleteLater()
                except RuntimeError:
                    pass
            # 按原顺序、原类型重建布局项（控件直接插回；嵌套布局整体插回；
            # spacer 用 addStretch 近似还原，视觉上等价）
            for item in removed_items:
                if item is None:
                    continue
                w = item.widget()
                if w is not None:
                    if not _alive(w):
                        continue
                    try:
                        w.setParent(container)
                        lay.addWidget(w)
                        w.show()
                    except RuntimeError:
                        pass
                    continue
                sub = item.layout()
                if sub is not None:
                    lay.addLayout(sub)
                    continue
                if item.spacerItem() is not None:
                    lay.addStretch(1)

        self._record(ui_id, "replace_render", _undo)
        return True

    def swap_widget(self, ui_id, factory):
        """把目标控件**整个**替换成插件产物（连它在父布局里的位置一起接管）。

        与 `replace_render` 的区别：
            replace_render  换「容器里的内容」，容器本身还在
            swap_widget     换「控件本身」，位置与父容器不变

        适合"我觉得欢迎卡片不好看，整个换掉"。回滚时销毁产物、把原控件插回原位。
        """
        target = self._target(ui_id)
        if target is None or not callable(factory):
            return False
        parent = target.parentWidget()
        if parent is None:
            return False
        lay = parent.layout() if hasattr(parent, "layout") else None
        if lay is None:
            return False
        try:
            index = lay.indexOf(target)
        except Exception:
            return False
        if index < 0:
            return False

        new_widget = factory(parent)
        if new_widget is None:
            return False

        # 摘掉原控件（不销毁）
        try:
            lay.removeWidget(target)
            target.hide()
            target.setParent(None)
        except RuntimeError:
            return False
        lay.insertWidget(index, new_widget)

        tgt_ref = weakref.ref(target)
        new_ref = weakref.ref(new_widget)
        par_ref = weakref.ref(parent)

        def _undo():
            par, orig, nw = par_ref(), tgt_ref(), new_ref()
            play = par.layout() if _alive(par) else None
            if play is None:
                return
            if _alive(nw):
                try:
                    nw.hide()
                    play.removeWidget(nw)
                    nw.setParent(None)
                    nw.deleteLater()
                except RuntimeError:
                    pass
            if _alive(orig):
                try:
                    orig.setParent(par)
                    play.insertWidget(min(index, play.count()), orig)
                    orig.show()
                except RuntimeError:
                    pass

        self._record(ui_id, "swap_widget", _undo)
        return True

    # ---------- 回滚 ----------
    def pending(self):
        """尚未回滚的修改条数。"""
        return sum(1 for c in self._changes if not c._done)

    def rollback(self):
        """倒序回滚全部修改；返回成功回滚的条数。

        倒序是必须的：后施加的修改可能依赖先施加的（例如先插容器再往容器里放控件）。
        """
        ok = 0
        for ch in reversed(self._changes):
            if ch.revert():
                ok += 1
        self._changes = []
        return ok

    def __repr__(self):
        return "<UiMutator %s pending=%d>" % (self.plugin_id, self.pending())


class MutatorHub:
    """按插件 id 管理 UiMutator，供宿主统一回滚。"""

    def __init__(self, registry, on_change=None):
        self._registry = registry
        self._on_change = on_change
        self._mutators = {}

    def for_plugin(self, plugin_id):
        """取（或创建）某插件的修改器。"""
        m = self._mutators.get(plugin_id)
        if m is None:
            m = UiMutator(plugin_id, self._registry, self._on_change)
            self._mutators[plugin_id] = m
        return m

    def rollback(self, plugin_id):
        """回滚某插件的全部修改（卸载/停用/崩溃时由宿主调用）。"""
        m = self._mutators.pop(plugin_id, None)
        return m.rollback() if m is not None else 0

    def rollback_all(self):
        """回滚所有插件的修改（应用退出前兜底）。"""
        total = 0
        for pid in list(self._mutators):
            total += self.rollback(pid)
        return total

    def pending(self, plugin_id=None):
        if plugin_id is not None:
            m = self._mutators.get(plugin_id)
            return m.pending() if m else 0
        return sum(m.pending() for m in self._mutators.values())
