import threading
import time

import pytest
from PIL import Image

from app.diagnostics import redact
from app.errors import JobCancelled, UserError
from app.job_manager import JobManager
from app.persistence import atomic_save_image, open_image


def test_redact_secrets():
    s = redact("using key sk-abcdef1234567890 and hf_0123456789abcdef now")
    assert "sk-***" in s and "hf_***" in s
    assert "sk-abcdef" not in s and "hf_0123456" not in s


def test_atomic_save_png_jpg(tmp_path):
    img = Image.new("RGB", (10, 7), (10, 200, 30))
    p1, p2 = tmp_path / "a.png", tmp_path / "b.jpg"
    atomic_save_image(img, str(p1))
    atomic_save_image(img, str(p2))
    assert Image.open(p1).size == (10, 7)
    assert Image.open(p2).mode == "RGB"
    assert not list(tmp_path.glob(".aiis-*"))


def test_atomic_save_rejects_bad_ext(tmp_path):
    with pytest.raises(UserError):
        atomic_save_image(Image.new("RGB", (4, 4)), str(tmp_path / "x.gif"))


def test_open_image_garbage(tmp_path):
    bad = tmp_path / "bad.png"
    bad.write_bytes(b"not an image")
    with pytest.raises(UserError):
        open_image(str(bad))


def test_job_manager_serializes(qapp):
    jm = JobManager()
    release = threading.Event()
    jm.submit("first", release.wait, kwargs={"timeout": 3})
    with pytest.raises(UserError):
        jm.submit("second", lambda: None)
    release.set()
    jm._worker.wait(4000)


def test_job_manager_cancel(qapp):
    jm = JobManager()
    result = {}

    def job(cancel_event):
        for _ in range(300):
            if cancel_event.is_set():
                raise JobCancelled()
            time.sleep(0.01)
        return "done"

    jm.submit("cancellable", job, kwargs={"cancel_event": threading.Event()},
              on_done=lambda r: result.setdefault("done", r),
              on_finish=lambda c: result.setdefault("cancelled", c))
    time.sleep(0.05)
    jm.cancel_current()
    jm._worker.wait(5000)
    deadline = time.time() + 3
    while "cancelled" not in result and time.time() < deadline:
        qapp.processEvents()
    assert result.get("cancelled") is True
    assert "done" not in result
