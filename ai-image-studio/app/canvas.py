"""Zoomable image canvas: inpaint mask painting, crop tool, display cache
for very large images, and dirty-region repaints for smooth interaction."""
from PIL import Image, ImageDraw, ImageOps
from PySide6.QtCore import QPointF, QRect, QRectF, QSizeF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QPainter, QPen, QPixmap, QRegion
from PySide6.QtWidgets import QWidget

from .utils import pil_to_pixmap

MASK_ALPHA = 150
MASK_COLOR = QColor(255, 64, 64, MASK_ALPHA)
HANDLE = 5  # half-size of crop handles in widget px


class ImageCanvas(QWidget):
    TOOL_BRUSH, TOOL_ERASER, TOOL_PAN, TOOL_CROP = 0, 1, 2, 3
    _OPPOSITE = (3, 2, 1, 0)
    DISPLAY_CAP = 4096     # max painted resolution; full-res data is kept

    imageChanged = Signal()
    maskChanged = Signal()
    strokeStarted = Signal()
    zoomChanged = Signal(float)
    fileDropped = Signal(str)
    cropChanged = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setAcceptDrops(True)
        self.setFocusPolicy(Qt.ClickFocus)
        self._image = None
        self._pixmap = None
        self._mask = None
        self._overlay = None
        self._disp_scale = 1.0
        self._zoom = 1.0
        self._offset = QPointF(0, 0)
        self._tool = self.TOOL_BRUSH
        self._brush = 48
        self.paint_enabled = True
        self.show_mask = True
        self._bg = QColor("#141518")
        self._ph = QColor("#7c7f86")
        self._painting = self._erasing = self._panning = False
        self._last_pt = None
        self._pan_start = QPointF()
        self._pan_offset = QPointF()
        self._pending_fit = False
        self._crop_rect = None
        self._crop_mode = None
        self._crop_anchor = None
        self._crop_grab = None
        self.setMinimumSize(320, 240)
        self.setCursor(Qt.CrossCursor)

    # ------------------------------------------------------------------ image
    def setImage(self, img, reset_mask=True):
        if img is None:
            self._image = self._pixmap = self._mask = self._overlay = None
            self._crop_rect = None
            self.cropChanged.emit(False)
            self.imageChanged.emit()
            self.update()
            return
        self._image = img.copy()
        w, h = self._image.size
        if max(w, h) > self.DISPLAY_CAP:      # display cache for huge images
            s = self.DISPLAY_CAP / max(w, h)
            disp = self._image.resize(
                (max(1, int(w * s)), max(1, int(h * s))),
                Image.Resampling.LANCZOS)
        else:
            disp = self._image
        self._disp_scale = disp.width / self._image.width
        self._pixmap = pil_to_pixmap(disp)
        if reset_mask or self._mask is None:
            self._reset_mask()
        if self._crop_rect is not None:
            self._crop_rect = self._crop_rect.intersected(
                QRectF(0, 0, self._image.width, self._image.height))
            if self._crop_rect.isEmpty():
                self._crop_rect = None
                self.cropChanged.emit(False)
        if self.isVisible():
            self.fit()
        else:
            self._pending_fit = True
        self.imageChanged.emit()
        self.update()

    def image(self):
        return self._image

    def current(self):
        return (self._image.copy() if self._image else None,
                self._mask.copy() if self._mask else None)

    # ------------------------------------------------------------------- mask
    def _reset_mask(self):
        self._mask = Image.new("L", self._image.size, 0)
        ow = max(1, round(self._image.width * self._disp_scale))
        oh = max(1, round(self._image.height * self._disp_scale))
        self._overlay = QPixmap(ow, oh)
        self._overlay.fill(Qt.transparent)
        self.maskChanged.emit()

    def setMask(self, mask):
        if self._image is None or mask is None:
            return
        if mask.size != self._image.size:
            mask = mask.resize(self._image.size)
        self._mask = mask.convert("L")
        dmask = self._mask
        if self._disp_scale != 1.0:
            dmask = self._mask.resize(
                (max(1, round(self._mask.width * self._disp_scale)),
                 max(1, round(self._mask.height * self._disp_scale))),
                Image.Resampling.NEAREST)
        rgba = Image.new("RGBA", dmask.size, (0, 0, 0, 0))
        red = Image.new("RGBA", dmask.size, (255, 64, 64, MASK_ALPHA))
        solid = dmask.point(lambda v: 255 if v > 0 else 0)
        self._overlay = pil_to_pixmap(Image.composite(red, rgba, solid))
        self.maskChanged.emit()
        self.update()

    def mask(self):
        return self._mask

    def hasMaskPaint(self):
        return self._mask is not None and self._mask.getbbox() is not None

    def clearMask(self):
        if self._mask is not None:
            self._reset_mask()
            self.update()

    def invertMask(self):
        if self._mask is not None:
            self.setMask(ImageOps.invert(self._mask))

    # ------------------------------------------------------------------- view
    def _view_scale(self) -> float:
        """Widget pixels per image pixel (zoom × display scale)."""
        return self._zoom * self._disp_scale

    def zoom(self):
        return self._zoom

    def fit(self):
        if self._pixmap is None:
            return
        aw, ah = max(50, self.width() - 32), max(50, self.height() - 32)
        self._zoom = min(aw / self._pixmap.width(), ah / self._pixmap.height())
        self._offset = QPointF(
            (self.width() - self._pixmap.width() * self._zoom) / 2,
            (self.height() - self._pixmap.height() * self._zoom) / 2)
        self.zoomChanged.emit(self._zoom)
        self.update()

    def zoomBy(self, factor):
        if self._pixmap is not None:
            self._zoom_at(self._zoom * factor,
                          QPointF(self.width() / 2, self.height() / 2))

    def _zoom_at(self, z, anchor: QPointF):
        s = self._view_scale()
        img = QPointF((anchor.x() - self._offset.x()) / s,
                      (anchor.y() - self._offset.y()) / s)
        self._zoom = max(0.03, min(16.0, z))
        s = self._view_scale()
        self._offset = QPointF(anchor.x() - img.x() * s,
                               anchor.y() - img.y() * s)
        self.zoomChanged.emit(self._zoom)
        self.update()

    # ------------------------------------------------------------------ tools
    def tool(self):
        return self._tool

    def setTool(self, tool):
        if tool == self.TOOL_CROP:
            if self._image is None:
                return
            if self._crop_rect is None:
                w, h = self._image.size
                self._crop_rect = QRectF(w * 0.08, h * 0.08, w * 0.84, h * 0.84)
                self.cropChanged.emit(True)
        elif self._crop_rect is not None:
            self._crop_rect = None
            self.cropChanged.emit(False)
        self._tool = tool
        self.setCursor(Qt.OpenHandCursor if tool == self.TOOL_PAN
                       else Qt.CrossCursor)
        self.update()

    def setBrushSize(self, v):
        self._brush = int(v)

    def setThemeColors(self, bg: str, placeholder: str = None):
        self._bg = QColor(bg)
        if placeholder:
            self._ph = QColor(placeholder)
        self.update()

    # ------------------------------------------------------------------- crop
    def hasCrop(self):
        return self._crop_rect is not None

    def cancelCrop(self):
        if self._crop_rect is not None:
            self._crop_rect = None
            self.cropChanged.emit(False)
            self.update()

    def applyCrop(self):
        if self._image is None or self._crop_rect is None:
            return None
        r = self._crop_rect
        x1 = int(round(max(0.0, r.left())))
        y1 = int(round(max(0.0, r.top())))
        x2 = int(round(min(float(self._image.width), r.right())))
        y2 = int(round(min(float(self._image.height), r.bottom())))
        x2, y2 = max(x2, x1 + 1), max(y2, y1 + 1)
        img = self._image.crop((x1, y1, x2, y2))
        mask = (self._mask.crop((x1, y1, x2, y2))
                if self._mask is not None else None)
        self._crop_rect = None
        self.cropChanged.emit(False)
        self.update()
        return img, mask

    def _crop_widget_rect(self):
        r, s = self._crop_rect, self._view_scale()
        return QRectF(self._offset.x() + r.left() * s,
                      self._offset.y() + r.top() * s,
                      r.width() * s, r.height() * s)

    @staticmethod
    def _handle_points(wr):
        return [wr.topLeft(), wr.topRight(), wr.bottomLeft(), wr.bottomRight()]

    def _handle_at(self, pos):
        if self._crop_rect is None:
            return None
        for i, hp in enumerate(self._handle_points(self._crop_widget_rect())):
            if abs(pos.x() - hp.x()) <= HANDLE + 2 and \
                    abs(pos.y() - hp.y()) <= HANDLE + 2:
                return i
        return None

    def _crop_dirty(self, *rects) -> QRegion:
        region = QRegion()
        for r in rects:
            if r is None:
                continue
            region += QRegion(int(r.left()) - HANDLE - 30,
                              int(r.top()) - HANDLE - 30,
                              int(r.width()) + 2 * (HANDLE + 30),
                              int(r.height()) + 2 * (HANDLE + 30))
        return region.intersected(self.rect())

    def _crop_press(self, pos):
        old = self._crop_widget_rect() if self._crop_rect else None
        img = self._image_pos(pos)
        h = self._handle_at(pos)
        if h is not None:
            wr = self._crop_widget_rect()
            self._crop_anchor = self._image_pos(
                self._handle_points(wr)[self._OPPOSITE[h]])
            self._crop_mode = "resize"
        elif self._crop_rect.contains(img):
            self._crop_mode = "move"
            self._crop_grab = QPointF(img.x() - self._crop_rect.left(),
                                      img.y() - self._crop_rect.top())
        else:
            self._crop_mode = "new"
            self._crop_anchor = img
            self._crop_rect = QRectF(img, img)
        new = self._crop_widget_rect() if self._crop_rect else None
        self.update(self._crop_dirty(old, new))

    def _crop_move(self, pos):
        old = self._crop_widget_rect()
        img = self._image_pos(pos)
        if self._crop_mode in ("new", "resize"):
            a = self._crop_anchor
            x1, y1 = min(a.x(), img.x()), min(a.y(), img.y())
            x2, y2 = max(a.x(), img.x()), max(a.y(), img.y())
            self._crop_rect = QRectF(x1, y1, max(8, x2 - x1), max(8, y2 - y1))
        else:
            r = self._crop_rect
            nx = max(0.0, min(self._image.width - r.width(),
                              img.x() - self._crop_grab.x()))
            ny = max(0.0, min(self._image.height - r.height(),
                              img.y() - self._crop_grab.y()))
            self._crop_rect = QRectF(nx, ny, r.width(), r.height())
        self.update(self._crop_dirty(old, self._crop_widget_rect()))

    # ----------------------------------------------------------------- events
    def wheelEvent(self, e):
        if self._pixmap is None:
            return
        factor = 1.25 if e.angleDelta().y() > 0 else 0.8
        self._zoom_at(self._zoom * factor, e.position())

    def mousePressEvent(self, e):
        if self._pixmap is None:
            return
        if e.button() in (Qt.MiddleButton, Qt.RightButton) or \
                (e.button() == Qt.LeftButton and self._tool == self.TOOL_PAN):
            self._panning = True
            self._pan_start = e.position()
            self._pan_offset = self._offset
            self.setCursor(Qt.ClosedHandCursor)
        elif e.button() == Qt.LeftButton and \
                self._tool in (self.TOOL_BRUSH, self.TOOL_ERASER) and \
                self.paint_enabled:
            pt = self._to_image(e.position())
            self.strokeStarted.emit()
            self._painting = True
            self._erasing = self._tool == self.TOOL_ERASER
            self._last_pt = pt
            if pt:
                self._stroke(pt, pt)
        elif e.button() == Qt.LeftButton and self._tool == self.TOOL_CROP:
            self._crop_press(e.position())

    def mouseMoveEvent(self, e):
        if self._panning:
            self._offset = self._pan_offset + (e.position() - self._pan_start)
            self.update()
        elif self._painting:
            pt = self._to_image(e.position())
            if pt and self._last_pt:
                self._stroke(self._last_pt, pt)
                self._last_pt = pt
        elif self._tool == self.TOOL_CROP and self._crop_mode:
            self._crop_move(e.position())

    def mouseReleaseEvent(self, e):
        if self._panning:
            self._panning = False
            self.setCursor(Qt.OpenHandCursor if self._tool == self.TOOL_PAN
                           else Qt.CrossCursor)
        if self._painting:
            self._painting = False
            self._last_pt = None
            self.maskChanged.emit()
        if self._crop_mode:
            self._crop_mode = None
            self._crop_anchor = self._crop_grab = None
            self.update()
            self.cropChanged.emit(self._crop_rect is not None)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self._pending_fit:
            self._pending_fit = False
            self.fit()

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        for url in e.mimeData().urls():
            path = url.toLocalFile()
            if path:
                self.fileDropped.emit(path)
                break

    # -------------------------------------------------------------- painting
    def _to_image(self, pos: QPointF):
        if self._image is None:
            return None
        s = self._view_scale()
        x = (pos.x() - self._offset.x()) / s
        y = (pos.y() - self._offset.y()) / s
        return (max(0.0, min(self._image.width() - 1, x)),
                max(0.0, min(self._image.height() - 1, y)))

    def _image_pos(self, pos: QPointF) -> QPointF:
        s = self._view_scale()
        x = (pos.x() - self._offset.x()) / s
        y = (pos.y() - self._offset.y()) / s
        if self._image is None:
            return QPointF(x, y)
        return QPointF(max(0.0, min(float(self._image.width), x)),
                       max(0.0, min(float(self._image.height), y)))

    def _stroke_dirty(self, p0, p1) -> QRect:
        s = self._view_scale()
        pad = (self._brush / 2) * s + 6
        xs = [p0[0] * s + self._offset.x(), p1[0] * s + self._offset.x()]
        ys = [p0[1] * s + self._offset.y(), p1[1] * s + self._offset.y()]
        rect = QRect(int(min(xs) - pad), int(min(ys) - pad),
                     int(max(xs) - min(xs) + 2 * pad) + 1,
                     int(max(ys) - min(ys) + 2 * pad) + 1)
        return rect.intersected(self.rect())

    def _stroke(self, p0, p1):
        s = self._disp_scale
        w = max(2, int(self._brush))
        wp = max(1, int(round(w * s)))           # width in display pixels
        col = QColor(0, 0, 0) if self._erasing else MASK_COLOR
        p = QPainter(self._overlay)
        if self._erasing:
            p.setCompositionMode(QPainter.CompositionMode_Clear)
        q0 = QPointF(p0[0] * s, p0[1] * s)
        q1 = QPointF(p1[0] * s, p1[1] * s)
        if p0 == p1:
            p.setPen(Qt.NoPen)
            p.setBrush(QBrush(col))
            p.drawEllipse(q0, wp / 2, wp / 2)
        else:
            p.setPen(QPen(col, wp, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            p.drawLine(q0, q1)
        p.end()
        d = ImageDraw.Draw(self._mask)           # mask stays full-resolution
        val = 0 if self._erasing else 255
        r = w / 2
        d.line([p0, p1], fill=val, width=w)
        for (x, y) in (p0, p1):
            d.ellipse((x - r, y - r, x + r, y + r), fill=val)
        self.update(self._stroke_dirty(p0, p1))

    def paintEvent(self, ev):
        p = QPainter(self)
        p.fillRect(self.rect(), self._bg)
        if self._pixmap is None:
            p.setPen(self._ph)
            p.drawText(self.rect(), Qt.AlignCenter,
                       "Open an image (Ctrl+O), drag & drop a file,\n"
                       "or send a result from the Generate tab.")
            return
        target = QRectF(self._offset,
                        QSizeF(self._pixmap.width() * self._zoom,
                               self._pixmap.height() * self._zoom))
        p.setRenderHint(QPainter.SmoothPixmapTransform, self._zoom < 3)
        p.drawPixmap(target, self._pixmap, QRectF(self._pixmap.rect()))
        if self.show_mask and self._overlay is not None and \
                self._tool != self.TOOL_CROP:
            p.drawPixmap(target, self._overlay, QRectF(self._overlay.rect()))
        p.setPen(QPen(QColor("#3c3d41"), 1))
        p.setBrush(Qt.NoBrush)
        p.drawRect(target)

        if self._tool == self.TOOL_CROP and self._crop_rect is not None:
            wr = self._crop_widget_rect()
            p.fillRect(self.rect(), QColor(0, 0, 0, 120))
            p.drawPixmap(wr, self._pixmap, QRectF(self._crop_rect))
            p.setPen(QPen(QColor("#ffffff"), 1))
            p.setBrush(Qt.NoBrush)
            p.drawRect(wr)
            p.setBrush(QBrush(QColor("#ffffff")))
            for hp in self._handle_points(wr):
                p.drawRect(QRectF(hp.x() - HANDLE, hp.y() - HANDLE,
                                  HANDLE * 2, HANDLE * 2))
            p.setBrush(Qt.NoBrush)
            p.setPen(QColor("#ffffff"))
            label = f"{int(round(self._crop_rect.width()))} × " \
                    f"{int(round(self._crop_rect.height()))} px"
            ly = wr.top() - 20
            if ly < 4:
                ly = wr.bottom() + 18
            p.drawText(QPointF(wr.left() + 2, ly), label)
