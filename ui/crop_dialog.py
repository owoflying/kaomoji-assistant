"""背景图裁切对话框：拖拽选区，所见即所得。

## 为什么要有它

原本裁切只能填四个百分比数字（左/上/宽/高）。用户的原话是：

    "只给数字很难用"

确实——要"从这张壁纸里取右下角那块好看的"，得先目测猜比例，
填完再看效果，反复试。这是机器的工作，不是人的。

## 做法

一个"选头像"式的界面：

    ┌─────────────────────────────────┐
    │                                 │
    │        背景图（缩放显示）         │
    │        ┌───────────┐            │
    │        │  可拖拽选框 │            │
    │        └───────────┘            │
    │                                 │
    └─────────────────────────────────┘
    [重置]                      [取消] [确定]

* 在图上**拖动**画一个新框
* 拖框内部 = 移动；拖四角/四边 = 缩放
* 右侧实时预览"候选条实际会显示成什么样"

结果以相对比例 (x, y, w, h) 返回，与已有配置语义一致。
"""
from PySide6.QtCore import QPoint, QRect, QSize, Qt
from PySide6.QtGui import (
    QColor, QImage, QPainter, QPen, QPixmap,
)
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
)

#: 选框的握把半径（像素）
_GRIP = 8


class CropCanvas(QWidget):
    """可拖拽选区的图片画布。

    :param image: QImage 原图
    :param crop: 初始裁切 (x, y, w, h) 相对比例
    """

    def __init__(self, image, crop=(0.0, 0.0, 1.0, 1.0), parent=None):
        super().__init__(parent)
        self._image = image
        self._crop = self._normalize(crop)
        self._drag = None          # "new" / "move" / "resize"
        self._grip = None          # 缩放时抓住的角
        self._start = QPoint()
        self._start_crop = None
        self.setMinimumSize(360, 260)
        self.setMouseTracking(True)
        self.setCursor(Qt.CrossCursor)

    # ---------------------------------------------------------- 几何
    def _display_rect(self):
        """图片在画布里的实际绘制矩形（保持比例、居中、留边）。"""
        if self._image is None or self._image.isNull():
            return QRect(0, 0, self.width(), self.height())
        iw, ih = self._image.width(), self._image.height()
        if iw <= 0 or ih <= 0:
            return QRect(0, 0, self.width(), self.height())
        margin = 12
        aw = max(1, self.width() - 2 * margin)
        ah = max(1, self.height() - 2 * margin)
        scale = min(aw / iw, ah / ih)
        w = int(iw * scale)
        h = int(ih * scale)
        x = margin + (aw - w) // 2
        y = margin + (ah - h) // 2
        return QRect(x, y, w, h)

    def _crop_to_px(self):
        """相对裁切 → 画布上的像素矩形。"""
        d = self._display_rect()
        x, y, w, h = self._crop
        return QRect(int(d.x() + x * d.width()),
                     int(d.y() + y * d.height()),
                     max(1, int(w * d.width())),
                     max(1, int(h * d.height())))

    def _px_to_crop(self, rect):
        """画布像素矩形 → 相对比例，并夹在图片范围内。"""
        d = self._display_rect()
        if d.width() <= 0 or d.height() <= 0:
            return (0.0, 0.0, 1.0, 1.0)
        r = rect.intersected(d)
        if r.width() < 8 or r.height() < 8:
            return self._crop
        return (max(0.0, (r.x() - d.x()) / d.width()),
                max(0.0, (r.y() - d.y()) / d.height()),
                min(1.0, r.width() / d.width()),
                min(1.0, r.height() / d.height()))

    @staticmethod
    def _normalize(crop):
        try:
            x, y, w, h = [float(v) for v in crop]
        except Exception:
            return (0.0, 0.0, 1.0, 1.0)
        if w <= 0 or h <= 0:
            return (0.0, 0.0, 1.0, 1.0)
        x = max(0.0, min(1.0, x))
        y = max(0.0, min(1.0, y))
        w = max(0.05, min(1.0 - x, w))
        h = max(0.05, min(1.0 - y, h))
        return (x, y, w, h)

    def crop(self):
        return self._crop

    def set_crop(self, crop):
        self._crop = self._normalize(crop)
        self.update()

    def reset(self):
        self._crop = (0.0, 0.0, 1.0, 1.0)
        self.update()

    def current_pixmap(self):
        """按当前裁切从原图取出的那块（给右侧预览用）。"""
        if self._image is None or self._image.isNull():
            return QPixmap()
        x, y, w, h = self._crop
        iw, ih = self._image.width(), self._image.height()
        return QPixmap.fromImage(self._image.copy(
            QRect(int(x * iw), int(y * ih),
                  max(1, int(w * iw)), max(1, int(h * ih)))))

    # ---------------------------------------------------------- 交互
    def _grip_at(self, pos):
        """判断点是否落在某个缩放握把上。"""
        r = self._crop_to_px()
        pts = {
            "tl": r.topLeft(), "tr": r.topRight(),
            "bl": r.bottomLeft(), "br": r.bottomRight(),
        }
        for name, p in pts.items():
            if (pos - p).manhattanLength() <= _GRIP * 2:
                return name
        return None

    def mousePressEvent(self, e):
        if e.button() != Qt.LeftButton:
            return
        pos = e.pos()
        self._start = pos
        self._start_crop = self._crop
        grip = self._grip_at(pos)
        if grip is not None:
            self._drag, self._grip = "resize", grip
            self.setCursor(Qt.SizeFDiagCursor if grip in ("tl", "br")
                           else Qt.SizeBDiagCursor)
        elif self._crop_to_px().contains(pos):
            self._drag, self._grip = "move", None
            self.setCursor(Qt.SizeAllCursor)
        else:
            # 在空白处拖动 = 画新框
            self._drag, self._grip = "new", None
            self._crop = self._px_to_crop(QRect(pos, QSize(1, 1)))
            self.update()

    def mouseMoveEvent(self, e):
        pos = e.pos()
        if self._drag is None:
            # 悬停时光标提示可拖拽区域
            if self._grip_at(pos) is not None:
                self.setCursor(Qt.PointingHandCursor)
            elif self._crop_to_px().contains(pos):
                self.setCursor(Qt.SizeAllCursor)
            else:
                self.setCursor(Qt.CrossCursor)
            return

        d = self._display_rect()
        if self._drag == "new":
            self._crop = self._px_to_crop(
                QRect(self._start, pos).normalized())
        elif self._drag == "move":
            dx = (pos.x() - self._start.x()) / max(1, d.width())
            dy = (pos.y() - self._start.y()) / max(1, d.height())
            x, y, w, h = self._start_crop
            nx = max(0.0, min(1.0 - w, x + dx))
            ny = max(0.0, min(1.0 - h, y + dy))
            self._crop = (nx, ny, w, h)
        elif self._drag == "resize":
            r = self._crop_to_px()
            x1, y1, x2, y2 = r.left(), r.top(), r.right(), r.bottom()
            if "l" in self._grip:
                x1 = pos.x()
            if "r" in self._grip:
                x2 = pos.x()
            if "t" in self._grip:
                y1 = pos.y()
            if "b" in self._grip:
                y2 = pos.y()
            self._crop = self._px_to_crop(
                QRect(QPoint(x1, y1), QPoint(x2, y2)).normalized())
        self.update()

    def mouseReleaseEvent(self, e):
        self._drag = None
        self._grip = None
        self.setCursor(Qt.CrossCursor)
        # 通知外部刷新预览
        cb = getattr(self, "on_crop_changed", None)
        if callable(cb):
            try:
                cb(self._crop)
            except Exception:
                pass

    # ---------------------------------------------------------- 绘制
    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)
        p.fillRect(self.rect(), QColor(self.palette().window().color()))

        if self._image is None or self._image.isNull():
            p.end()
            return
        d = self._display_rect()
        p.drawImage(d, self._image)

        # 裁切框外的区域压暗，视觉上"只保留选中部分"
        sel = self._crop_to_px()
        mask = QColor(0, 0, 0, 120)
        p.fillRect(QRect(d.x(), d.y(), d.width(), sel.top() - d.y()), mask)
        p.fillRect(QRect(d.x(), sel.bottom() + 1, d.width(),
                         d.bottom() - sel.bottom()), mask)
        p.fillRect(QRect(d.x(), sel.top(), sel.left() - d.x(),
                         sel.height()), mask)
        p.fillRect(QRect(sel.right() + 1, sel.top(),
                         d.right() - sel.right(), sel.height()), mask)

        # 选框 + 四角握把
        p.setPen(QPen(QColor(255, 255, 255, 230), 2))
        p.drawRect(sel)
        p.setPen(QPen(QColor(0, 0, 0, 90), 1))
        p.drawRect(sel.adjusted(1, 1, -1, -1))
        p.setBrush(QColor(255, 255, 255, 240))
        p.setPen(QPen(QColor(0, 0, 0, 120), 1))
        for pt in (sel.topLeft(), sel.topRight(),
                   sel.bottomLeft(), sel.bottomRight()):
            p.drawRect(QRect(pt.x() - 4, pt.y() - 4, 8, 8))
        p.end()


class CropDialog(QDialog):
    """裁切对话框：左边拖选框，右边实时预览。

    :param image_path: 图片路径
    :param crop: 初始裁切 (x, y, w, h) 相对比例
    """

    def __init__(self, image_path, crop=None, title="选择显示区域",
                 preview_size=(240, 90), parent=None,
                 preview_label="效果预览"):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self._result_crop = None

        img = QImage(image_path) if image_path else QImage()
        self.canvas = CropCanvas(img, crop or (0, 0, 1, 1))
        self.canvas.on_crop_changed = self._on_crop_changed

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        row = QHBoxLayout()
        row.setSpacing(14)
        row.addWidget(self.canvas, 3)

        # 右侧：预览 + 说明
        side = QVBoxLayout()
        side.setSpacing(8)
        side.addWidget(QLabel(preview_label))
        self.preview = QLabel()
        self.preview.setFixedSize(QSize(*preview_size))
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setStyleSheet(
            "border:1px solid rgba(128,128,128,0.4);")
        side.addWidget(self.preview)
        side.addStretch(1)
        tip = QLabel("在图上拖动可框选；\n拖动框内可移动；\n拖动四角可缩放。")
        tip.setWordWrap(True)
        side.addWidget(tip)
        sidew = QWidget()
        sidew.setLayout(side)
        row.addWidget(sidew, 1)
        root.addLayout(row, 1)

        btns = QHBoxLayout()
        self.reset_btn = QPushButton("重置")
        self.reset_btn.clicked.connect(self._reset)
        btns.addWidget(self.reset_btn)
        btns.addStretch(1)
        cancel = QPushButton("取消")
        cancel.clicked.connect(self.reject)
        ok = QPushButton("确定")
        ok.setDefault(True)
        ok.clicked.connect(self.accept)
        btns.addWidget(cancel)
        btns.addWidget(ok)
        root.addLayout(btns)

        self.resize(760, 520)
        self._on_crop_changed(self.canvas.crop())

    def _on_crop_changed(self, _crop):
        pm = self.canvas.current_pixmap()
        if pm.isNull():
            self.preview.clear()
            return
        self.preview.setPixmap(pm.scaled(
            self.preview.size(), Qt.KeepAspectRatio,
            Qt.SmoothTransformation))

    def _reset(self):
        self.canvas.reset()
        self._on_crop_changed(self.canvas.crop())

    def result_crop(self):
        """用户确定的裁切 (x, y, w, h) 相对比例。"""
        return self.canvas.crop()


def edit_crop(image_path, crop=None, parent=None, title="选择显示区域",
              preview_label="效果预览"):
    """弹出裁切对话框。

    :return: (x, y, w, h) 或 None（用户取消）
    """
    if not image_path:
        return None
    dlg = CropDialog(image_path, crop, title=title, parent=parent,
                     preview_label=preview_label)
    if dlg.exec() == QDialog.Accepted:
        return dlg.result_crop()
    return None
