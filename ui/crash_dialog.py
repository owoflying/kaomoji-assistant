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
                 parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setWindowFlags(
            self.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)
        self._summary = summary
        self._detail = detail
        self._report_fn = report_fn
        self._expanded = False

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

        tip = QLabel("程序没有退出，其余功能仍可继续使用。"
                     "如果反复出现，可以把报告发给开发者。")
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


def show_crash(summary, detail, parent=None):
    """崩溃弹窗（由 `core.crash_guard` 的回调调用）。"""
    try:
        from core import crash_guard

        dlg = CrashDialog(summary, detail, title="程序遇到了一个问题",
                          report_fn=crash_guard.last_crash_report,
                          parent=parent)
        dlg.show()
        dlg.raise_()
        return dlg
    except Exception:
        return None


def show_error(summary, detail, parent=None):
    """开发者模式下的即时报错弹窗。"""
    try:
        dlg = CrashDialog(summary, detail, title="运行时错误", parent=parent)
        dlg.show()
        dlg.raise_()
        return dlg
    except Exception:
        return None
