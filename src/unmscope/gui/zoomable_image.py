"""A small, fixed-viewport image widget that can be zoomed and panned.

Built for the Stack Projections MIP thumbnails (user, 2026-09-23: "The
stack projections should also be zoomable") -- they used to be a plain,
fixed-size QLabel showing the projection scaled to fit the box, with no way
to inspect detail without saving the TIFF and opening it elsewhere.

No QGraphicsView: a QLabel inside a QScrollArea. Wheel events rescale the
label's pixmap around the current zoom level; the QScrollArea supplies the
fixed-size viewport and the scrollbars, and a click-drag on the viewport
walks them (a small hand-rolled pan, since QScrollArea has no drag-to-pan of
its own). Double-click resets to the fit-to-box zoom level.
"""
from __future__ import annotations

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QLabel, QScrollArea, QWidget


class ZoomableImageView(QScrollArea):
    MIN_ZOOM = 1.0
    MAX_ZOOM = 8.0
    STEP = 1.15

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWidgetResizable(False)
        self.setAlignment(Qt.AlignCenter)
        self.setFrameShape(QScrollArea.NoFrame)
        self._label = QLabel()
        self._label.setAlignment(Qt.AlignCenter)
        # Inherits the QScrollArea's own background-color stylesheet
        # (letterboxing around a KeepAspectRatio-scaled pixmap must match,
        # not show through as the platform's default widget background).
        self._label.setStyleSheet("background: transparent;")
        self.setWidget(self._label)
        self._source: QPixmap | None = None
        self._zoom = 1.0
        self._dragging = False
        self._drag_start = QPoint()
        self._scroll_start = (0, 0)
        self.viewport().setCursor(Qt.OpenHandCursor)

    def set_pixmap(self, pixmap: QPixmap) -> None:
        """New base image, fit to the viewport (matches the previous plain-
        QLabel behaviour); zoom resets to 1.0 (fit)."""
        self._source = pixmap
        self._zoom = self.MIN_ZOOM
        self._apply()

    def reset_zoom(self) -> None:
        self._zoom = self.MIN_ZOOM
        self._apply()

    def pixmap(self) -> QPixmap:
        """The currently displayed (fit/zoomed) pixmap -- QLabel-compatible
        accessor for callers that only need to check something was drawn."""
        return self._label.pixmap()

    def _apply(self) -> None:
        if self._source is None or self._source.isNull():
            return
        fit = self.viewport().size()
        base = self._source.scaled(fit, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        if self._zoom > 1.0:
            base = base.scaled(int(base.width() * self._zoom), int(base.height() * self._zoom),
                               Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self._label.setPixmap(base)
        self._label.resize(base.size())

    def wheelEvent(self, event) -> None:
        if self._source is None:
            super().wheelEvent(event)
            return
        old_zoom = self._zoom
        factor = self.STEP if event.angleDelta().y() > 0 else (1.0 / self.STEP)
        self._zoom = min(self.MAX_ZOOM, max(self.MIN_ZOOM, self._zoom * factor))
        if self._zoom != old_zoom:
            self._apply()
        event.accept()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton and self._zoom > self.MIN_ZOOM:
            self._dragging = True
            self._drag_start = event.pos()
            self._scroll_start = (self.horizontalScrollBar().value(), self.verticalScrollBar().value())
            self.viewport().setCursor(Qt.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._dragging:
            delta = event.pos() - self._drag_start
            self.horizontalScrollBar().setValue(self._scroll_start[0] - delta.x())
            self.verticalScrollBar().setValue(self._scroll_start[1] - delta.y())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self._dragging:
            self._dragging = False
            self.viewport().setCursor(Qt.OpenHandCursor)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        self.reset_zoom()
        event.accept()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._apply()
