"""Fast interactive previews for the Adjustments panel.

Pillow's ImageEnhance.* filters are blends between the image and a fixed
"degenerate" image (black / mean-gray / grayscale / smoothed copy). Those
degenerates don't depend on slider values, so we precompute them once per
preview image; each slider tick then runs only a few C-level blends + LUT
lookups (~5-10x faster than rebuilding the chain). The commit path still uses
the exact original implementation for full quality.
"""
from __future__ import annotations

from PIL import Image, ImageFilter, ImageStat

from .image_ops import apply_temperature


def prepare_adjust_cache(base: Image.Image) -> dict:
    """Precompute everything that does not depend on slider values."""
    base = base.convert("RGB")
    gray = base.convert("L").convert("RGB")               # saturation degenerate
    mean_l = int(ImageStat.Stat(base.convert("L")).mean[0])
    mean = Image.new("RGB", base.size, (mean_l,) * 3)     # contrast degenerate
    h, s, v = base.convert("HSV").split()                 # cached hue source
    return {
        "base": base,
        "black": Image.new("RGB", base.size, (0, 0, 0)),  # brightness degenerate
        "gray": gray,
        "mean": mean,
        "smooth": base.filter(ImageFilter.SMOOTH),        # sharpness degenerate
        "h": h, "s": s, "v": v,
    }


def render_adjust(cache: dict, brightness: int = 100, contrast: int = 100,
                  saturation: int = 100, hue: int = 0, temperature: int = 0,
                  sharpness: int = 100) -> Image.Image:
    """One cheap preview render. Mirrors the order of apply_adjustments."""
    cur = cache["base"]

    if hue % 360:
        d = int(round(hue / 360.0 * 255))
        h = cache["h"].point(lambda x: (x + d) % 256)     # 256-entry LUT
        cur = Image.merge("HSV", (h, cache["s"], cache["v"])).convert("RGB")

    if temperature:
        cur = apply_temperature(cur, temperature)

    if brightness != 100:
        cur = Image.blend(cache["black"], cur, brightness / 100)
    if contrast != 100:
        cur = Image.blend(cache["mean"], cur, contrast / 100)
    if saturation != 100:
        cur = Image.blend(cache["gray"], cur, saturation / 100)
    if sharpness != 100:
        cur = Image.blend(cache["smooth"], cur, sharpness / 100)
    return cur
