"""Рисует иконку приложения: assets/icon.png и assets/icon.ico."""
from pathlib import Path

from PIL import Image
from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QGuiApplication, QImage, QLinearGradient, QPainter, QPainterPath, QPen

ROOT = Path(__file__).resolve().parent.parent
SIZE = 512


def render() -> QImage:
    img = QImage(SIZE, SIZE, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    r = QRectF(16, 16, SIZE - 32, SIZE - 32)
    grad = QLinearGradient(r.topLeft(), r.bottomRight())
    grad.setColorAt(0, QColor("#95c75f"))
    grad.setColorAt(1, QColor("#4e7a2c"))
    path = QPainterPath()
    path.addRoundedRect(r, 104, 104)
    p.fillPath(path, grad)
    # клетки доски в углу — лёгкий узор
    p.setClipPath(path)
    cell = r.width() / 4
    for i in range(4):
        for j in range(4):
            if (i + j) % 2:
                p.fillRect(QRectF(r.left() + i * cell, r.top() + j * cell, cell, cell), QColor(255, 255, 255, 18))
    p.setClipping(False)
    # конь
    f = QFont("Segoe UI Symbol")
    f.setPixelSize(380)
    p.setFont(f)
    knight = "\u265e"
    shadow = QRectF(r).translated(0, 14)
    p.setPen(QColor(0, 0, 0, 70))
    p.drawText(shadow, Qt.AlignmentFlag.AlignCenter, knight)
    p.setPen(QColor("white"))
    p.drawText(r.translated(0, -4), Qt.AlignmentFlag.AlignCenter, knight)
    p.end()
    return img


def main() -> None:
    app = QGuiApplication([])
    out = ROOT / "assets"
    out.mkdir(exist_ok=True)
    png = out / "icon.png"
    render().save(str(png))
    im = Image.open(png).convert("RGBA")
    im.save(out / "icon.ico", sizes=[(s, s) for s in (16, 20, 24, 32, 40, 48, 64, 128, 256)])
    print("ok", png)


if __name__ == "__main__":
    main()
