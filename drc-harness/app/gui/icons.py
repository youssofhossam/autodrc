"""Small, dependency-free line icons drawn with QPainter.

Using painted icons keeps the application self-contained and guarantees that
the icon set matches the dark theme on every platform (no missing themed icon
fallbacks).
"""

from __future__ import annotations

from functools import lru_cache

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap

from app.gui.theme import COLORS


@lru_cache(maxsize=256)
def icon(name: str, color: str = COLORS["text_muted"], size: int = 18) -> QIcon:
    dpr = 2
    pixmap = QPixmap(size * dpr, size * dpr)
    pixmap.setDevicePixelRatio(dpr)
    pixmap.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    _draw(painter, name, QColor(color), float(size))
    painter.end()

    return QIcon(pixmap)


def logo_pixmap(size: int = 32) -> QPixmap:
    """The application mark: an accent tile with a four-point spark."""

    dpr = 2
    pixmap = QPixmap(size * dpr, size * dpr)
    pixmap.setDevicePixelRatio(dpr)
    pixmap.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

    tile = QRectF(0.5, 0.5, size - 1.0, size - 1.0)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(COLORS["accent"]))
    painter.drawRoundedRect(tile, size * 0.24, size * 0.24)

    cx = cy = size / 2.0
    outer = size * 0.27
    inner = size * 0.075
    spark = QPainterPath()
    spark.moveTo(cx, cy - outer)
    spark.lineTo(cx + inner, cy - inner)
    spark.lineTo(cx + outer, cy)
    spark.lineTo(cx + inner, cy + inner)
    spark.lineTo(cx, cy + outer)
    spark.lineTo(cx - inner, cy + inner)
    spark.lineTo(cx - outer, cy)
    spark.lineTo(cx - inner, cy - inner)
    spark.closeSubpath()

    painter.setBrush(QColor("#10131a"))
    painter.drawPath(spark)
    painter.end()
    return pixmap


def app_icon() -> QIcon:
    result = QIcon()
    for size in (16, 24, 32, 48, 64, 128):
        result.addPixmap(logo_pixmap(size))
    return result


def _draw(painter: QPainter, name: str, color: QColor, size: float) -> None:
    if name == "logo":
        painter.drawPixmap(0, 0, logo_pixmap(int(size)))
        return

    pen = QPen(color)
    pen.setWidthF(max(1.4, size / 13.0))
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)

    s = size
    m = s * 0.2

    if name == "plus":
        painter.drawLine(QPointF(m, s / 2), QPointF(s - m, s / 2))
        painter.drawLine(QPointF(s / 2, m), QPointF(s / 2, s - m))
    elif name == "send":
        painter.drawLine(QPointF(s / 2, s - m), QPointF(s / 2, m))
        painter.drawLine(QPointF(s / 2, m), QPointF(m + s * 0.12, m + s * 0.22))
        painter.drawLine(QPointF(s / 2, m), QPointF(s - m - s * 0.12, m + s * 0.22))
    elif name in {"chevron-down", "chevron-right", "chevron-left"}:
        half = s * 0.18
        if name == "chevron-down":
            painter.drawLine(QPointF(s / 2 - half, s / 2 - half / 2), QPointF(s / 2, s / 2 + half / 2))
            painter.drawLine(QPointF(s / 2, s / 2 + half / 2), QPointF(s / 2 + half, s / 2 - half / 2))
        elif name == "chevron-right":
            painter.drawLine(QPointF(s / 2 - half / 2, s / 2 - half), QPointF(s / 2 + half / 2, s / 2))
            painter.drawLine(QPointF(s / 2 + half / 2, s / 2), QPointF(s / 2 - half / 2, s / 2 + half))
        else:
            painter.drawLine(QPointF(s / 2 + half / 2, s / 2 - half), QPointF(s / 2 - half / 2, s / 2))
            painter.drawLine(QPointF(s / 2 - half / 2, s / 2), QPointF(s / 2 + half / 2, s / 2 + half))
    elif name == "settings":
        xs = (s * 0.34, s * 0.68, s * 0.46)
        for y, knob_x in zip((s * 0.3, s * 0.5, s * 0.7), xs, strict=True):
            painter.drawLine(QPointF(m, y), QPointF(s - m, y))
            painter.setBrush(color)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(QPointF(knob_x, y), s * 0.07, s * 0.07)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
    elif name == "sidebar":
        painter.drawRoundedRect(QRectF(m * 0.75, m, s - m * 1.5, s - m * 2), 2.5, 2.5)
        painter.drawLine(QPointF(s * 0.4, m), QPointF(s * 0.4, s - m))
    elif name == "copy":
        painter.drawRoundedRect(QRectF(m, m + s * 0.1, s * 0.48, s * 0.52), 2.0, 2.0)
        painter.drawRoundedRect(QRectF(s * 0.35, m * 0.7, s * 0.48, s * 0.52), 2.0, 2.0)
    elif name == "check":
        painter.drawLine(QPointF(m, s * 0.52), QPointF(s * 0.43, s * 0.72))
        painter.drawLine(QPointF(s * 0.43, s * 0.72), QPointF(s - m, s * 0.3))
    elif name == "close":
        painter.drawLine(QPointF(m, m), QPointF(s - m, s - m))
        painter.drawLine(QPointF(s - m, m), QPointF(m, s - m))
    elif name == "trash":
        painter.drawLine(QPointF(m, s * 0.3), QPointF(s - m, s * 0.3))
        painter.drawLine(QPointF(s * 0.4, s * 0.3), QPointF(s * 0.44, m * 0.8))
        painter.drawLine(QPointF(s * 0.6, s * 0.3), QPointF(s * 0.56, m * 0.8))
        painter.drawLine(QPointF(s * 0.44, m * 0.8), QPointF(s * 0.56, m * 0.8))
        painter.drawRoundedRect(QRectF(s * 0.3, s * 0.3, s * 0.4, s * 0.5), 2.0, 2.0)
    elif name == "edit":
        painter.drawLine(QPointF(m + s * 0.05, s - m - s * 0.05), QPointF(s - m - s * 0.12, m + s * 0.12))
        painter.drawLine(QPointF(s - m - s * 0.12, m + s * 0.12), QPointF(s - m, m))
        painter.drawLine(QPointF(s - m, m), QPointF(s - m - s * 0.22, m + s * 0.08))
        painter.drawLine(QPointF(m + s * 0.05, s - m - s * 0.05), QPointF(m + s * 0.24, s - m - s * 0.14))
    elif name == "retry":
        painter.drawArc(QRectF(m, m, s - 2 * m, s - 2 * m), 35 * 16, 285 * 16)
        painter.drawLine(QPointF(s * 0.78, s * 0.22), QPointF(s * 0.78, s * 0.42))
        painter.drawLine(QPointF(s * 0.78, s * 0.22), QPointF(s * 0.58, s * 0.25))
    elif name == "alert":
        circle = QRectF(m, m, s - 2 * m, s - 2 * m)
        painter.drawEllipse(circle)
        painter.drawLine(QPointF(s / 2, s * 0.35), QPointF(s / 2, s * 0.56))
        painter.setBrush(color)
        painter.drawEllipse(QPointF(s / 2, s * 0.7), s * 0.035, s * 0.035)
    elif name == "stop":
        painter.setBrush(color)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(
            QRectF(s * 0.3, s * 0.3, s * 0.4, s * 0.4), s * 0.08, s * 0.08
        )
    elif name == "spark":
        cx = cy = s / 2
        outer = s * 0.28
        inner = s * 0.08
        path = QPainterPath()
        path.moveTo(cx, cy - outer)
        path.lineTo(cx + inner, cy - inner)
        path.lineTo(cx + outer, cy)
        path.lineTo(cx + inner, cy + inner)
        path.lineTo(cx, cy + outer)
        path.lineTo(cx - inner, cy + inner)
        path.lineTo(cx - outer, cy)
        path.lineTo(cx - inner, cy - inner)
        path.closeSubpath()
        painter.setBrush(color)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawPath(path)
    else:  # pragma: no cover - programming error
        painter.drawEllipse(QRectF(m, m, s - 2 * m, s - 2 * m))
