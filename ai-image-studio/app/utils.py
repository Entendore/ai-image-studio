"""Small shared helpers."""
import os
from pathlib import Path

from PIL import Image
from PySide6.QtGui import QImage, QPixmap


def default_output_dir() -> str:
    env = os.environ.get("AIIS_OUTPUT_DIR", "").strip()
    d = Path(env) if env else Path.home() / "Pictures" / "ai-image-studio"
    d.mkdir(parents=True, exist_ok=True)
    return str(d)


def pil_to_pixmap(img: Image.Image) -> QPixmap:
    """Convert PIL → QPixmap with a single deep copy (fromImage does the copy)."""
    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGBA")
    if img.mode == "RGBA":
        fmt, bpl = QImage.Format_RGBA8888, img.width * 4
    else:
        fmt, bpl = QImage.Format_RGB888, img.width * 3
    return QPixmap.fromImage(
        QImage(img.tobytes(), img.width, img.height, bpl, fmt))


def pil_thumbnail_pixmap(img: Image.Image, size: int = 104) -> QPixmap:
    """Cheap thumbnails: downscale in PIL first, convert only the small image."""
    t = img if img.mode in ("RGB", "RGBA") else img.convert("RGB")
    t = t.copy()
    t.thumbnail((size, size), Image.Resampling.BILINEAR)
    return pil_to_pixmap(t)
