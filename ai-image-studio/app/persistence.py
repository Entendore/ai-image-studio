"""Safe file I/O and window-state persistence."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

from PIL import Image, ImageOps
from PySide6.QtCore import QByteArray, QSettings
from PySide6.QtWidgets import QMainWindow, QSplitter

from .errors import UserError

_ALLOWED_SAVE = {".png": "PNG", ".jpg": "JPEG", ".jpeg": "JPEG",
                 ".webp": "WEBP", ".bmp": "BMP"}


def open_image(path: str) -> Image.Image:
    """Open defensively: friendly errors, EXIF rotation, sane color mode."""
    try:
        img = Image.open(path)
        img.load()
    except Image.DecompressionBombError as e:
        raise UserError("This image is too large to open safely.") from e
    except PermissionError as e:
        raise UserError(f"Cannot read '{path}': permission denied.") from e
    except FileNotFoundError as e:
        raise UserError(f"File not found: {path}") from e
    except Exception as e:
        raise UserError(f"Could not open image '{path}': {e}") from e
    try:
        img = ImageOps.exif_transpose(img)
    except Exception:
        pass
    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGBA")
    return img


def atomic_save_image(img: Image.Image, path: str, quality: int = 95):
    """Write to a temp file, then os.replace() — crash-safe saves."""
    p = Path(path)
    fmt = _ALLOWED_SAVE.get(p.suffix.lower())
    if fmt is None:
        raise UserError(f"Unsupported output format: {p.suffix or '(no extension)'}")
    save_img = img
    if fmt == "JPEG":
        if save_img.mode == "RGBA":
            bg = Image.new("RGB", save_img.size, (255, 255, 255))
            bg.paste(save_img, mask=save_img.getchannel("A"))
            save_img = bg
        else:
            save_img = save_img.convert("RGB")
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=".aiis-", suffix=".tmp")
    os.close(fd)
    try:
        if fmt in ("JPEG", "WEBP"):
            save_img.save(tmp, fmt, quality=quality)
        else:
            save_img.save(tmp, fmt)
        os.replace(tmp, p)
    except PermissionError as e:
        raise UserError(f"Cannot write '{path}': permission denied.") from e
    except Exception as e:
        raise UserError(f"Could not save '{path}': {e}") from e
    finally:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass


def save_window(win: QMainWindow, settings: QSettings):
    settings.setValue("ui/geometry", win.saveGeometry())


def restore_window(win: QMainWindow, settings: QSettings) -> bool:
    geo = settings.value("ui/geometry")
    if geo is None:
        return False
    if isinstance(geo, str):
        geo = QByteArray.fromBase64(geo.encode())
    elif isinstance(geo, (bytes, bytearray)):
        geo = QByteArray(bytes(geo))
    if geo.isNull():
        return False
    return win.restoreGeometry(geo)


def save_splitter(splitter: QSplitter, settings: QSettings, key: str):
    settings.setValue(f"ui/{key}", splitter.sizes())


def restore_splitter(splitter: QSplitter, settings: QSettings, key: str):
    val = settings.value(f"ui/{key}")
    if not val:
        return
    try:
        if isinstance(val, str):
            sizes = [int(v) for v in val.split(",")]
        else:
            sizes = [int(v) for v in val]
        if len(sizes) == len(splitter.sizes()) and sum(sizes) > 0:
            splitter.setSizes(sizes)
    except (TypeError, ValueError):
        pass
