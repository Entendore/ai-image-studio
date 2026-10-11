import time

import pytest
from PIL import Image

from app.errors import UserError, humanize
from app.fast_adjust import prepare_adjust_cache, render_adjust
from app.image_ops import (FILTERS, apply_adjustments, apply_filter,
                           smart_upscale)
from app.model_manager import ModelManager
from app.utils import pil_thumbnail_pixmap
from app.widgets import PromptForm


def _base():
    return Image.effect_mandelbrot((64, 64), (-2, -1.5, 1, 1.5), 100) \
        .convert("RGB")


def test_upscale_sizes():
    img = Image.new("RGB", (10, 6), "red")
    assert smart_upscale(img, 2).size == (20, 12)
    assert smart_upscale(img, 1.5).size == (15, 9)
    assert smart_upscale(img, 0.5).size == (5, 3)


def test_adjustments_identity_at_defaults():
    img = Image.new("RGB", (8, 8), (120, 60, 30))
    assert list(apply_adjustments(img).getdata()) == list(img.getdata())


def test_all_filters_run():
    img = Image.new("RGB", (16, 16), "blue")
    for name in FILTERS:
        assert apply_filter(img, name).size == img.size


def test_prompt_form_combined(qapp):
    form = PromptForm((("a", "A", ""), ("b", "B", "")), show_preview=False)
    form._edits["a"].setText("sky")
    form._edits["b"].setText("night")
    assert form.combined() == "sky, night"


def test_humanize_mapping():
    assert humanize(UserError("boom"))[0] == "Something went wrong"
    assert humanize(ConnectionError("nope"))[0] == "Network problem"
    assert humanize(FileNotFoundError("x"))[0] == "File not found"


SINGLE = [
    dict(brightness=140), dict(brightness=60), dict(contrast=130),
    dict(saturation=60), dict(saturation=180), dict(hue=90), dict(hue=-120),
    dict(temperature=40), dict(temperature=-60), dict(sharpness=160),
    dict(sharpness=40),
]


@pytest.mark.parametrize("kw", SINGLE)
def test_fast_matches_commit_single(kw):
    base = _base()
    cache = prepare_adjust_cache(base)
    fast = render_adjust(cache, **kw).tobytes()
    ref = apply_adjustments(base, **kw).convert("RGB").tobytes()
    diff = [abs(a - b) for a, b in zip(fast, ref)]
    assert sum(diff) / len(diff) < 1.0
    assert max(diff) <= 6


def test_fast_matches_commit_combined():
    kw = dict(brightness=120, contrast=115, saturation=140, hue=45,
              temperature=25, sharpness=130)
    base = _base()
    cache = prepare_adjust_cache(base)
    fast = render_adjust(cache, **kw).tobytes()
    ref = apply_adjustments(base, **kw).convert("RGB").tobytes()
    diff = [abs(a - b) for a, b in zip(fast, ref)]
    assert sum(diff) / len(diff) < 6.0
    assert max(diff) <= 64


def test_defaults_are_identity():
    base = _base()
    cache = prepare_adjust_cache(base)
    out = render_adjust(cache)
    assert list(out.getdata()) == list(base.getdata())


def test_fast_render_is_fast():
    base = _base().resize((512, 512))
    cache = prepare_adjust_cache(base)
    kw = dict(brightness=125, contrast=118, saturation=135, hue=60,
              temperature=20, sharpness=125)
    t0 = time.perf_counter()
    render_adjust(cache, **kw)
    assert time.perf_counter() - t0 < 1.5


def test_thumbnail_pixmap(qapp):
    img = Image.new("RGB", (300, 200), (10, 120, 240))
    pm = pil_thumbnail_pixmap(img, 104)
    assert pm.width() == 104 and pm.height() == 69


def test_disk_usage_cached(tmp_path):
    mgr = ModelManager(None)
    mgr.hub_cache_path = lambda: tmp_path
    d = mgr.repo_dir("org/model")
    d.mkdir(parents=True)
    (d / "a.bin").write_bytes(1000)
    first = mgr.disk_usage("org/model")
    (d / "b.bin").write_bytes(5000)
    assert mgr.disk_usage("org/model") == first      # within TTL → cached
    mgr.invalidate_usage("org/model")
    assert mgr.disk_usage("org/model") == first + 5000
