"""Картинки установщика: installer/art/wizard-*.bmp (боковой баннер) и small-*.bmp (значок)."""
from pathlib import Path

from PIL import Image
from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QGuiApplication, QImage, QLinearGradient, QPainter, QRadialGradient

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "installer" / "art"
ICON = ROOT / "assets" / "icon.png"


def qimage_to_bmp(img: QImage, path: Path) -> None:
    tmp = path.with_suffix(".png")
    img.save(str(tmp))
    Image.open(tmp).convert("RGB").save(path)
    tmp.unlink()


def wizard(scale: float) -> QImage:
    w, h = round(164 * scale), round(314 * scale)
    img = QImage(w, h, QImage.Format.Format_ARGB32)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    bg = QLinearGradient(0, 0, 0, h)
    bg.setColorAt(0, QColor("#2c2a26"))
    bg.setColorAt(1, QColor("#1a1917"))
    p.fillRect(0, 0, w, h, bg)
    # шахматка внизу, тающая кверху
    cell = w / 6
    rows = int(h * 0.45 / cell) + 1
    for r in range(rows):
        y = h - (r + 1) * cell
        alpha = int(34 * (1 - r / rows))
        for c in range(6):
            if (r + c) % 2 == 0:
                p.fillRect(QRectF(c * cell, y, cell, cell), QColor(129, 182, 76, alpha))
    glow = QRadialGradient(w / 2, h * 0.3, w * 0.75)
    glow.setColorAt(0, QColor(129, 182, 76, 70))
    glow.setColorAt(1, QColor(129, 182, 76, 0))
    p.fillRect(0, 0, w, h, glow)
    icon = QImage(str(ICON))
    s = w * 0.56
    p.drawImage(QRectF((w - s) / 2, h * 0.3 - s / 2, s, s), icon)
    f = QFont("Segoe UI")
    f.setPixelSize(round(25 * scale))
    f.setBold(True)
    p.setFont(f)
    p.setPen(QColor("white"))
    p.drawText(QRectF(0, h * 0.3 + s / 2 + 10 * scale, w, 34 * scale), Qt.AlignmentFlag.AlignCenter, "Chessium")
    f.setPixelSize(round(11 * scale))
    f.setBold(False)
    p.setFont(f)
    p.setPen(QColor("#a7a49f"))
    p.drawText(QRectF(8 * scale, h * 0.3 + s / 2 + 42 * scale, w - 16 * scale, 34 * scale),
               Qt.AlignmentFlag.AlignHCenter | Qt.TextFlag.TextWordWrap, "анализ шахматных партий\nс экрана")
    p.end()
    return img


def small(size: int) -> QImage:
    img = QImage(size, size, QImage.Format.Format_ARGB32)
    img.fill(QColor("#2b2a28"))
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    p.drawImage(QRectF(0, 0, size, size), QImage(str(ICON)))
    p.end()
    return img


def main() -> None:
    app = QGuiApplication([])
    OUT.mkdir(parents=True, exist_ok=True)
    for scale, tag in ((1, "100"), (1.5, "150"), (2, "200"), (2.5, "250")):
        qimage_to_bmp(wizard(scale), OUT / f"wizard-{tag}.bmp")
    for size in (55, 83, 110, 138):
        qimage_to_bmp(small(size), OUT / f"small-{size}.bmp")
    print("ok", OUT)


if __name__ == "__main__":
    main()
