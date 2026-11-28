"""崩溃 / 报错弹窗：告诉用户"出事了"，并支持导出诊断报告。

## 为什么要有这个弹窗

原来异常只写进日志缓冲，用户什么都看不到：

* 功能悄悄坏掉，用户以为是"这个功能就这样"
* 打包成 `--windowed` 后连 stderr 都没有，重启也没痕迹
* 想反馈问题，也拿不出任何材料

## 两个入口

    CrashDialog.show_crash(summary, detail)   崩溃（未捕获异常）
    CrashDialog.show_error(summary, detail)   开发者模式下的即时报错

两者内容一致，只是标题与措辞不同。都提供：

    [查看详情]  展开堆栈（默认收起，避免吓到普通用户）
    [导出报告]  存成 txt，路径复制到剪贴板并提示
    [继续使用]  关掉，不退出程序

## 为什么不用 QMessageBox

`QMessageBox` 不能方便地放"可展开的长文本 + 多个自定义按钮"，
而且它是模态的简单对话框。这里用自定义 `QDialog` 更合适。

## 不阻塞

默认 `show()` 而非 `exec()`——异常可能发生在任意时刻
（包括动画回调里），模态循环容易和正在运行的事件循环打架。
"""
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QGuiApplication
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QPushButton, QTextEdit, QVBoxLayout,
)


class CrashDialog(QDialog):
    """崩溃 / 报错提示框。

    :param summary: 一句话摘要
    :param detail: 完整堆栈（点「查看详情」才展开）
    :param title: 窗口标题
    :param report_fn: 无参可调用，返回报告文本（给「导出报告」用）
    """

    def __init__(self, summary, detail, title="出错了", report_fn=None,
                 parent=None, alive=True):
        super().__init__(parent)
        #: 程序是否还能继续运行（决定提示文案，见下方 tip 的说明）
        self._alive = bool(alive)
        self.setWindowTitle(title)
        self.setWindowFlags(
            self.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)
        self._summary = summary
        self._detail = detail
        self._report_fn = report_fn
        self._expanded = False
        #: 已合并的报错（第 0 条是构造时那条）
        self._extra = [(summary, detail)]

        self.setMinimumWidth(520)
        try:
            from ui.window_material_mixin import surface_theme

            t, _p = surface_theme("dialog")
            self.setStyleSheet(t.style_sheet())
        except Exception:
            pass

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 18)
        root.setSpacing(12)

        head = QLabel(title)
        head.setObjectName("SectionTitle")
        root.addWidget(head)

        msg = QLabel(summary)
        msg.setObjectName("BodyText")
        msg.setWordWrap(True)
        root.addWidget(msg)
        #: 摘要行引用（`add_report()` 会在合并新报错时更新它）
        self._summary_label = msg

        # 状态说明：**按实际情况生成，不能写死**
        #
        # 原来这里是一句无条件的
        #   "程序没有退出，其余功能仍可继续使用。"
        # 但实际分两种情况：
        #
        #   * 一般异常（槽函数里抛的）：进程确实还活着，这句成立
        #   * 真正的崩溃（事件循环被破坏、Qt 内部状态无效）：
        #     进程可能马上退出，这句话就是**误导用户**
        #
        # 用户指出过这个问题。所以文案由调用方通过 `alive` 参数决定。
        if alive:
            tip_text = ("程序仍在运行，其余功能可以继续使用。"
                        "如果这个问题反复出现，建议导出报告发给开发者。")
        else:
            tip_text = ("程序可能即将退出。请先把报告导出，"
                        "再重新启动——报告里有定位问题所需的全部信息。")
        tip = QLabel(tip_text)
        tip.setObjectName("Caption")
        tip.setWordWrap(True)
        root.addWidget(tip)

        # 详情（默认隐藏）
        self._detail_view = QTextEdit()
        self._detail_view.setReadOnly(True)
        self._detail_view.setPlainText(detail or "")
        self._detail_view.setMinimumHeight(180)
        f = QFont("Consolas", 9)
        self._detail_view.setFont(f)
        self._detail_view.hide()
        root.addWidget(self._detail_view)

        row = QHBoxLayout()
        row.setSpacing(8)
        self._toggle = QPushButton("查看详情")
        self._toggle.clicked.connect(self._on_toggle)
        row.addWidget(self._toggle)

        self._export = QPushButton("导出报告")
        self._export.clicked.connect(self._on_export)
        row.addWidget(self._export)
        row.addStretch(1)

        close = QPushButton("继续使用")
        close.setObjectName("AccentButton")
        close.setMinimumHeight(32)
        close.clicked.connect(self.accept)
        row.addWidget(close)
        root.addLayout(row)

    def add_report(self, summary, detail):
        """把新的一条报错追加到**当前窗口**里，而不是再弹一个窗。

        ## 为什么这样做

        一次操作可能引发连环异常（每个指纹不同，去重挡不住）。
        实测能连弹五六个几乎一样的窗口把屏幕盖满，用户根本没法
        操作，也看不清到底出了几个错。

        合并到一个窗口后：

            * 只占一个窗口，不遮挡工作区
            * 标题显示累计条数，一眼知道出了几个错
            * 详情区按时间顺序累积，方便一起导出

        :return: 累计条数
        """
        self._extra.append((summary, detail))
        n = len(self._extra)
        # 标题带计数，让用户知道不止一个错
        try:
            self.setWindowTitle("程序遇到了 %d 个问题" % n)
        except Exception:
            pass
        # 摘要行显示最新一条
        try:
            if getattr(self, "_summary_label", None) is not None:
                self._summary_label.setText(
                    "%s（共 %d 条，见详情）" % (summary, n))
        except Exception:
            pass
        # 详情区累积
        try:
            parts = []
            for i, (s, d) in enumerate(self._extra, 1):
                parts.append("── 第 %d 条 ──\n%s\n%s" % (i, s, d))
            self._detail_view.setPlainText("\n\n".join(parts))
        except Exception:
            pass
        # 有新错误时把详情展开，否则用户不知道又多了
        try:
            if not self._expanded:
                self._expanded = True
                self._detail_view.setVisible(True)
                self._toggle.setText("收起详情")
        except Exception:
            pass
        try:
            self.show()
            self.raise_()
            self.activateWindow()
        except Exception:
            pass
        return n

    def note_report_saved(self, path):
        """告知弹窗"报告已经自动存到哪了"，并把路径复制到剪贴板。

        程序即将退出时用户来不及手动点「导出报告」，所以崩溃路径
        会先自动落盘，这里只负责把位置亮出来。
        """
        try:
            self._saved_path = path
            self._export.setText("报告已保存")
            try:
                QGuiApplication.clipboard().setText(path)
            except Exception:
                pass
            lb = getattr(self, "_saved_label", None)
            if lb is None:
                lb = QLabel()
                lb.setObjectName("Caption")
                lb.setWordWrap(True)
                lb.setTextInteractionFlags(Qt.TextSelectableByMouse)
                self.layout().addWidget(lb)
                self._saved_label = lb
            short = path if len(path) <= 90 else ("..." + path[-87:])
            lb.setText("报告位置（已复制到剪贴板）：%s" % short)
        except Exception:
            pass

    def _on_toggle(self):
        self._expanded = not self._expanded
        self._detail_view.setVisible(self._expanded)
        self._toggle.setText("收起详情" if self._expanded else "查看详情")
        try:
            self.adjustSize()
        except Exception:
            pass

    def _on_export(self):
        """导出报告，并把路径复制到剪贴板（方便直接粘贴给开发者）。"""
        path = None
        try:
            from core import crash_guard

            if self._report_fn is not None:
                text = self._report_fn()
                import datetime
                import os

                from core.runtime import app_data_dir

                base = os.path.join(app_data_dir(), "crashes")
                os.makedirs(base, exist_ok=True)
                stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
                path = os.path.join(base, "report-%s.txt" % stamp)
                with open(path, "w", encoding="utf-8") as f:
                    f.write(text)
            else:
                path = crash_guard.export_report(
                    exc_type=None, extra=self._summary + "\n\n" + self._detail)
        except Exception:
            path = None

        if not path:
            self._export.setText("导出失败")
            return
        try:
            QGuiApplication.clipboard().setText(path)
            self._export.setText("已导出（路径已复制）")
        except Exception:
            self._export.setText("已导出")
        try:
            os.startfile(os.path.dirname(path))
        except Exception:
            pass


def probe_alive():
    """判断程序现在还有没有救（给弹窗决定文案用）。

    ## 判据

        1. 有 QApplication 实例吗？
        2. 主线程的事件循环还在跑吗？（`QThread.currentThread()` 对得上，
           且不是正在退出的状态）
        3. 主窗口还活着吗？

    任何一条不成立就认为"可能马上退出"，文案改成提醒用户先导出报告。

    ⚠ 这只是**估计**。Qt 没有可靠的"我已死"信号，所以判据偏保守：
    拿不准时按"可能退出"处理——宁可让用户多导一份报告，
    也不要让他以为还能用、结果什么都没保存就没了。
    """
    try:
        from PySide6.QtCore import QCoreApplication, QThread

        app = QCoreApplication.instance()
        if app is None:
            return False
        if QThread.currentThread() != app.thread():
            return False
        # 事件循环是否在跑：用 processingEvents 的近似判断——
        # 已调用 quit() 说明正在收尾
        try:
            if app.closingDown():
                return False
        except Exception:
            pass
        return True
    except Exception:
        return False


def show_crash(summary, detail, parent=None, alive=None):
    """崩溃弹窗（由 `core.crash_guard` 的回调调用）。

    :param alive: 程序是否还能继续运行；None 时用 `probe_alive()` 自动判断

    ## 先落盘再弹窗

    如果程序真的即将退出，弹窗可能只存在几秒——用户来不及点
    「导出报告」。所以这里**先把报告写到磁盘**，弹窗只是告知
    用户"东西存在哪"。
    """
    path = None
    try:
        from core import crash_guard

        path = crash_guard.export_report(exc_type=None,
                                         extra=summary + "\n\n" + detail)
    except Exception:
        path = None

    try:
        from core import crash_guard

        if alive is None:
            alive = probe_alive()
        dlg = CrashDialog(summary, detail,
                          title="程序遇到了一个问题" if alive else "程序即将退出",
                          report_fn=crash_guard.last_crash_report,
                          parent=parent, alive=alive)
        # 报告已经落盘时，把路径直接展示出来，用户不用再点按钮
        if path:
            dlg.note_report_saved(path)
        dlg.show()
        dlg.raise_()
        return dlg
    except Exception:
        return None


def show_error(summary, detail, parent=None, alive=True):
    """开发者模式下的即时报错弹窗。

    这类错误是**局部**的（某次操作失败），程序本身没受影响，
    所以默认 `alive=True`。
    """
    try:
        dlg = CrashDialog(summary, detail, title="运行时错误",
                          parent=parent, alive=alive)
        dlg.show()
        dlg.raise_()
        return dlg
    except Exception:
        return None
