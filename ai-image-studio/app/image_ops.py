"""Pure-Pillow image operations: adjustments, filters, transforms, upscaling."""
from PIL import Image, ImageEnhance, ImageFilter, ImageOps

LANCZOS = Image.Resampling.LANCZOS

FILTERS = ["Original", "B&W", "Sepia", "Cool", "Warm", "Vintage",
           "Dramatic", "Fade", "Sharpen"]


def apply_temperature(img, t):
    """t in [-100, 100]; positive = warmer (more red, less blue)."""
    if t == 0:
        return img
    k = t / 100.0
    rl = [min(255, int(v * (1 + 0.25 * k))) for v in range(256)]
    bl = [min(255, max(0, int(v * (1 - 0.25 * k)))) for v in range(256)]
    r, g, b = img.convert("RGB").split()
    return Image.merge("RGB", (r.point(rl), g, b.point(bl)))


def apply_hue(img, deg):
    """Rotate hue by deg in [-180, 180]."""
    if deg % 360 == 0:
        return img
    h, s, v = img.convert("RGB").convert("HSV").split()
    d = int(round(deg / 360.0 * 255))
    h = h.point(lambda x: (x + d) % 256)
    return Image.merge("HSV", (h, s, v)).convert("RGB")


def apply_adjustments(img, brightness=100, contrast=100, saturation=100,
                      hue=0, temperature=0, sharpness=100):
    alpha = img.getchannel("A") if img.mode == "RGBA" else None
    out = apply_hue(img, hue)
    out = apply_temperature(out, temperature)
    if brightness != 100:
        out = ImageEnhance.Brightness(out).enhance(brightness / 100)
    if contrast != 100:
        out = ImageEnhance.Contrast(out).enhance(contrast / 100)
    if saturation != 100:
        out = ImageEnhance.Color(out).enhance(saturation / 100)
    if sharpness != 100:
        out = ImageEnhance.Sharpness(out).enhance(sharpness / 100)
    if alpha is not None:
        out = out.convert("RGBA")
        out.putalpha(alpha)
    return out


def vignette(img, strength=0.5):
    mask = Image.radial_gradient("L").resize(img.size)
    mask = mask.point(lambda v: int(v * strength))
    return Image.composite(Image.new("RGB", img.size, (0, 0, 0)), img, mask)


def apply_filter(img, name):
    img = img.convert("RGB")
    if name in ("", "Original"):
        return img
    if name == "B&W":
        return ImageOps.grayscale(img).convert("RGB")
    if name == "Sepia":
        return ImageOps.colorize(ImageOps.grayscale(img),
                                 black=(43, 26, 13), white=(255, 222, 179))
    if name == "Cool":
        return apply_temperature(img, -35)
    if name == "Warm":
        return apply_temperature(img, 30)
    if name == "Dramatic":
        out = ImageEnhance.Contrast(img).enhance(1.35)
        out = ImageEnhance.Color(out).enhance(1.15)
        return out.filter(ImageFilter.UnsharpMask(2, 70, 3))
    if name == "Fade":
        out = ImageEnhance.Contrast(img).enhance(0.82)
        out = ImageEnhance.Brightness(out).enhance(1.04)
        return out.point(lambda v: min(255, v + 14))
    if name == "Vintage":
        out = ImageEnhance.Color(img).enhance(0.65)
        out = ImageEnhance.Contrast(out).enhance(0.92)
        out = apply_temperature(out, 22)
        return vignette(out.point(lambda v: min(255, v + 12)), 0.45)
    if name == "Sharpen":
        return img.filter(ImageFilter.UnsharpMask(2.2, 120, 3))
    return img


def auto_enhance(img):
    out = ImageOps.autocontrast(img.convert("RGB"), cutoff=1)
    out = ImageEnhance.Color(out).enhance(1.15)
    return ImageEnhance.Sharpness(out).enhance(1.35)


def smart_upscale(image, factor=2, on_progress=None):
    """Progressive 2x Lanczos steps with unsharp masking between steps."""
    factor = max(0.05, float(factor))
    tw = max(1, round(image.width * factor))
    th = max(1, round(image.height * factor))
    if factor < 1:
        out = image.resize((tw, th), LANCZOS)
    else:
        out = image
        while out.width * 2 <= tw:
            out = out.resize((out.width * 2, out.height * 2), LANCZOS)
            out = out.filter(ImageFilter.UnsharpMask(1.5, 55, 3))
            if on_progress:
                on_progress(45)
        out = out.resize((tw, th), LANCZOS)
        out = out.filter(ImageFilter.UnsharpMask(2, 75, 3))
    if on_progress:
        on_progress(100)
    return out


def rotate_ccw(img): return img.transpose(Image.Transpose.ROTATE_90)
def rotate_cw(img): return img.transpose(Image.Transpose.ROTATE_270)
def flip_h(img): return img.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
def flip_v(img): return img.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
def resize_to(img, w, h): return img.resize((max(1, int(w)), max(1, int(h))), LANCZOS)
