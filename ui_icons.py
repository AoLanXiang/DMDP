"""界面图标：全部用 QPainter 画出来，不依赖任何图片文件或 emoji 字体。

原来界面拿 emoji（🎮 📁 ⬇ 🦆 ⚗ …）当图标，打包到没有 emoji 字体的环境里
会渲染成白色方块（"白花花一片"）。这里改成矢量绘制，任何 Windows 都能画出来。
"""

from PyQt6.QtCore import Qt, QPointF, QRectF
from PyQt6.QtGui import (QPainter, QPixmap, QColor, QPen, QBrush, QPainterPath,
                         QPolygonF, QFont)

_ICON_CACHE = {}


def _color(c):
    return QColor(c) if isinstance(c, str) else c


def _paint_duck(p: QPainter, s: float):
    """鸭子头（项目 Logo）"""
    body = QColor("#FFC53D")
    p.setBrush(QBrush(body))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawEllipse(QRectF(s * 0.30, s * 0.34, s * 0.44, s * 0.42))
    p.drawEllipse(QRectF(s * 0.38, s * 0.16, s * 0.34, s * 0.34))
    # 嘴
    p.setBrush(QBrush(QColor("#FF8A00")))
    path = QPainterPath()
    path.moveTo(s * 0.70, s * 0.32)
    path.lineTo(s * 0.90, s * 0.37)
    path.lineTo(s * 0.70, s * 0.42)
    p.drawPath(path)
    # 眼睛
    p.setBrush(QBrush(QColor("#2C2C2C")))
    p.drawEllipse(QRectF(s * 0.52, s * 0.26, s * 0.07, s * 0.07))


def _paint_gamepad(p: QPainter, s: float, c: QColor):
    p.setPen(QPen(c, s * 0.075, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    p.setBrush(Qt.BrushStyle.NoBrush)
    path = QPainterPath()
    path.addRoundedRect(QRectF(s * 0.12, s * 0.30, s * 0.76, s * 0.42), s * 0.16, s * 0.16)
    p.drawPath(path)
    # 十字键
    p.drawLine(QPointF(s * 0.32, s * 0.42), QPointF(s * 0.32, s * 0.60))
    p.drawLine(QPointF(s * 0.23, s * 0.51), QPointF(s * 0.41, s * 0.51))
    # 右摇杆点
    p.setBrush(QBrush(c))
    p.drawEllipse(QRectF(s * 0.62, s * 0.46, s * 0.10, s * 0.10))


def _paint_folder(p: QPainter, s: float, c: QColor):
    p.setPen(QPen(c, s * 0.07))
    p.setBrush(QBrush(c.lighter(160)))
    path = QPainterPath()
    path.moveTo(s * 0.12, s * 0.34)
    path.lineTo(s * 0.42, s * 0.34)
    path.lineTo(s * 0.50, s * 0.26)
    path.lineTo(s * 0.86, s * 0.26)
    path.lineTo(s * 0.86, s * 0.74)
    path.lineTo(s * 0.12, s * 0.74)
    path.closeSubpath()
    p.drawPath(path)


def _paint_star(p: QPainter, s: float, c: QColor):
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(c))
    star = QPainterPath()
    cx = cy = s / 2
    for i in range(10):
        ang = -90 + i * 36
        r = s * 0.40 if i % 2 == 0 else s * 0.17
        x = cx + r * __import__("math").cos(__import__("math").radians(ang))
        y = cy + r * __import__("math").sin(__import__("math").radians(ang))
        star.lineTo(x, y) if i else star.moveTo(x, y)
    star.closeSubpath()
    p.drawPath(star)


def _paint_list(p: QPainter, s: float, c: QColor):
    p.setPen(QPen(c, s * 0.075))
    for i, y in enumerate((0.28, 0.5, 0.72)):
        p.drawLine(QPointF(s * 0.40, s * y), QPointF(s * 0.85, s * y))
        p.setBrush(QBrush(c))
        p.drawEllipse(QRectF(s * 0.15, s * y - s * 0.045, s * 0.09, s * 0.09))


def _paint_gear(p: QPainter, s: float, c: QColor):
    import math
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(c))
    cx = cy = s / 2
    for i in range(8):
        ang = math.radians(i * 45)
        w = s * 0.09
        x = cx + s * 0.30 * math.cos(ang)
        y = cy + s * 0.30 * math.sin(ang)
        p.save()
        p.translate(x, y)
        p.rotate(i * 45)
        p.drawRect(QRectF(-w / 2, -s * 0.13, w, s * 0.26))
        p.restore()
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(QPen(c, s * 0.07))
    p.drawEllipse(QRectF(s * 0.24, s * 0.24, s * 0.52, s * 0.52))
    p.drawEllipse(QRectF(s * 0.40, s * 0.40, s * 0.20, s * 0.20))


def _paint_check(p: QPainter, s: float, c: QColor):
    p.setPen(QPen(c, s * 0.11, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawLine(QPointF(s * 0.22, s * 0.52), QPointF(s * 0.42, s * 0.72))
    p.drawLine(QPointF(s * 0.42, s * 0.72), QPointF(s * 0.80, s * 0.28))


def _paint_flask(p: QPainter, s: float, c: QColor):
    p.setPen(QPen(c, s * 0.07))
    p.setBrush(QBrush(c.lighter(150)))
    path = QPainterPath()
    path.moveTo(s * 0.40, s * 0.18)
    path.lineTo(s * 0.60, s * 0.18)
    path.lineTo(s * 0.60, s * 0.40)
    path.lineTo(s * 0.80, s * 0.82)
    path.lineTo(s * 0.20, s * 0.82)
    path.lineTo(s * 0.40, s * 0.40)
    path.closeSubpath()
    p.drawPath(path)


def _paint_download(p: QPainter, s: float, c: QColor):
    p.setPen(QPen(c, s * 0.075))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawLine(QPointF(s * 0.50, s * 0.16), QPointF(s * 0.50, s * 0.62))
    p.setBrush(QBrush(c))
    p.setPen(Qt.PenStyle.NoPen)
    tri = QPolygonF([QPointF(s * 0.34, s * 0.50), QPointF(s * 0.66, s * 0.50), QPointF(s * 0.50, s * 0.72)])
    p.drawPolygon(tri)
    p.setPen(QPen(c, s * 0.075))
    p.drawLine(QPointF(s * 0.22, s * 0.86), QPointF(s * 0.78, s * 0.86))


def _paint_refresh(p: QPainter, s: float, c: QColor):
    p.setPen(QPen(c, s * 0.075))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawArc(QRectF(s * 0.22, s * 0.22, s * 0.56, s * 0.56), 30 * 16, 300 * 16)
    p.setBrush(QBrush(c))
    p.setPen(Qt.PenStyle.NoPen)
    tri = QPolygonF([QPointF(s * 0.72, s * 0.16), QPointF(s * 0.88, s * 0.30), QPointF(s * 0.68, s * 0.34)])
    p.drawPolygon(tri)


def _paint_close(p: QPainter, s: float, c: QColor):
    p.setPen(QPen(c, s * 0.09, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    p.drawLine(QPointF(s * 0.28, s * 0.28), QPointF(s * 0.72, s * 0.72))
    p.drawLine(QPointF(s * 0.72, s * 0.28), QPointF(s * 0.28, s * 0.72))


def _paint_globe(p: QPainter, s: float, c: QColor):
    p.setPen(QPen(c, s * 0.07))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawEllipse(QRectF(s * 0.18, s * 0.18, s * 0.64, s * 0.64))
    p.drawEllipse(QRectF(s * 0.36, s * 0.18, s * 0.28, s * 0.64))
    p.drawLine(QPointF(s * 0.18, s * 0.50), QPointF(s * 0.82, s * 0.50))


def _paint_java(p: QPainter, s: float, c: QColor):
    """咖啡杯"""
    p.setPen(QPen(c, s * 0.07))
    p.setBrush(QBrush(c.lighter(170)))
    p.drawRoundedRect(QRectF(s * 0.18, s * 0.36, s * 0.44, s * 0.34), s * 0.06, s * 0.06)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawArc(QRectF(s * 0.60, s * 0.42, s * 0.22, s * 0.20), -90 * 16, 180 * 16)
    p.setPen(QPen(c.lighter(130), s * 0.05))
    p.drawLine(QPointF(s * 0.30, s * 0.14), QPointF(s * 0.30, s * 0.28))
    p.drawLine(QPointF(s * 0.46, s * 0.14), QPointF(s * 0.46, s * 0.28))


def _paint_wrench(p: QPainter, s: float, c: QColor):
    p.setPen(QPen(c, s * 0.09, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    p.drawLine(QPointF(s * 0.34, s * 0.66), QPointF(s * 0.72, s * 0.28))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(c))
    p.drawEllipse(QRectF(s * 0.60, s * 0.12, s * 0.28, s * 0.28))
    p.drawEllipse(QRectF(s * 0.12, s * 0.60, s * 0.28, s * 0.28))


def _paint_dots(p: QPainter, s: float, c: QColor):
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(c))
    for i in range(3):
        p.drawEllipse(QRectF(s * (0.18 + i * 0.24), s * 0.42, s * 0.16, s * 0.16))


def _paint_play(p: QPainter, s: float, c: QColor):
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(c))
    p.drawPolygon(QPolygonF([QPointF(s * 0.30, s * 0.18), QPointF(s * 0.80, s * 0.50), QPointF(s * 0.30, s * 0.82)]))


def _paint_trash(p: QPainter, s: float, c: QColor):
    p.setPen(QPen(c, s * 0.07))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawLine(QPointF(s * 0.22, s * 0.30), QPointF(s * 0.78, s * 0.30))
    p.drawLine(QPointF(s * 0.40, s * 0.30), QPointF(s * 0.44, s * 0.18))
    p.drawLine(QPointF(s * 0.60, s * 0.30), QPointF(s * 0.56, s * 0.18))
    p.drawLine(QPointF(s * 0.30, s * 0.30), QPointF(s * 0.36, s * 0.84))
    p.drawLine(QPointF(s * 0.70, s * 0.30), QPointF(s * 0.64, s * 0.84))
    p.drawLine(QPointF(s * 0.36, s * 0.84), QPointF(s * 0.64, s * 0.84))


def _paint_box(p: QPainter, s: float, c: QColor):
    p.setPen(QPen(c, s * 0.07))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawRect(QRectF(s * 0.26, s * 0.28, s * 0.48, s * 0.44))


DRAWERS = {
    "box": _paint_box,
    "duck": _paint_duck,
    "gamepad": _paint_gamepad,
    "folder": _paint_folder,
    "star": _paint_star,
    "list": _paint_list,
    "gear": _paint_gear,
    "check": _paint_check,
    "flask": _paint_flask,
    "download": _paint_download,
    "refresh": _paint_refresh,
    "close": _paint_close,
    "globe": _paint_globe,
    "java": _paint_java,
    "wrench": _paint_wrench,
    "dots": _paint_dots,
    "play": _paint_play,
    "trash": _paint_trash,
}


def icon_pixmap(name: str, size: int = 18, color: str = "#4A90D9") -> QPixmap:
    """按名字画一张透明底图标，带缓存。"""
    key = (name, size, color)
    cached = _ICON_CACHE.get(key)
    if cached is not None:
        return cached

    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    drawer = DRAWERS.get(name)
    if drawer:
        try:
            if name == "duck":
                drawer(p, float(size))
            else:
                drawer(p, float(size), _color(color))
        except Exception:  # noqa: BLE001  图标画不出来也不能崩界面
            pass
    p.end()
    _ICON_CACHE[key] = pm
    return pm
