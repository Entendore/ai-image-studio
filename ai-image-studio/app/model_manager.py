"""Model catalog, download (resumable, cancellable, disk-preflighted),
cache management with TTL-cached disk usage, env-overridable selection."""
from __future__ import annotations

import os
import shutil
import threading
import time
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from .diagnostics import LOG as _ROOT_LOG
from .envconfig import (hf_token as env_hf_token, sd_inpaint_override,
                        sd_model_override)
from .errors import UserError

LOG = _ROOT_LOG.model


class DownloadCancelled(Exception):
    pass


def human_size(n) -> str:
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{int(n)} B" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def _dir_size(path: Path) -> int:
    total = 0
    for p in Path(path).rglob("*"):
        try:
            if p.is_file():
                total += p.stat().st_size
        except OSError:
            pass
    return total


class ModelManager(QObject):
    changed = Signal()
    activeChanged = Signal()
    downloadProgress = Signal(str, int, str)    # repo_id, percent(-1 busy), label
    downloadFinished = Signal(str, bool, str)   # repo_id, ok, message

    GEN_DEFAULT = "stabilityai/stable-diffusion-2-1-base"
    INPAINT_DEFAULT = "stabilityai/stable-diffusion-2-inpainting"
    _USAGE_TTL = 60.0

    CATALOG = [
        dict(repo_id="stable-diffusion-v1-5/stable-diffusion-v1-5",
             purpose="generate", size="~4.3 GB", vram="≥4 GB",
             desc="Classic SD 1.5 — fast, huge ecosystem of fine-tunes."),
        dict(repo_id="Lykon/dreamshaper-8",
             purpose="generate", size="~4.3 GB", vram="≥4 GB",
             desc="Popular SD 1.5 fine-tune; great stylized / illustrative results."),
        dict(repo_id="stabilityai/stable-diffusion-2-1-base",
             purpose="generate", size="~5 GB", vram="≥6 GB",
             desc="SD 2.1 base, 512 px default; solid all-rounder (app default)."),
        dict(repo_id="stabilityai/stable-diffusion-xl-base-1.0",
             purpose="generate", size="~6.9 GB", vram="≥10 GB",
             desc="SDXL — much higher quality at 1024 px. Slower, more VRAM."),
        dict(repo_id="stabilityai/stable-diffusion-2-inpainting",
             purpose="inpaint", size="~5 GB", vram="≥6 GB",
             desc="Official inpainting model for Fill & Expand (512 px). App default."),
        dict(repo_id="runwayml/stable-diffusion-inpainting",
             purpose="inpaint", size="~4.3 GB", vram="≥4 GB",
             desc="SD 1.5 inpainting. (Original repo removed from HF; "
                  "availability depends on mirrors.)"),
        dict(repo_id="diffusers/stable-diffusion-xl-1.0-inpainting-0.1",
             purpose="inpaint", size="~6.9 GB", vram="≥10 GB",
             desc="SDXL inpainting — best quality for Fill & Expand (1024 px)."),
    ]

    def __init__(self, settings):
        super().__init__()
        self.settings = settings
        self._cancel = threading.Event()
        self._usage_cache: dict = {}

    # ------------------------------------------------------------ availability
    @property
    def hub_available(self) -> bool:
        try:
            import huggingface_hub  # noqa: F401
            return True
        except Exception:
            return False

    @property
    def diffusers_available(self) -> bool:
        try:
            import diffusers, torch  # noqa: F401
            return True
        except Exception:
            return False

    # ------------------------------------------------------------ active models
    def active_gen_model(self) -> str:
        env = sd_model_override()
        if env:
            return env
        return self.settings.value("models/sd", self.GEN_DEFAULT) or self.GEN_DEFAULT

    def set_active_gen_model(self, model_id: str):
        if model_id and model_id != self.active_gen_model():
            self.settings.setValue("models/sd", model_id)
            self.settings.sync()
            LOG.info(f"active generate model → {model_id}")
            self.activeChanged.emit()

    def active_inpaint_model(self) -> str:
        env = sd_inpaint_override()
        if env:
            return env
        return (self.settings.value("models/sd_inpaint", self.INPAINT_DEFAULT)
                or self.INPAINT_DEFAULT)

    def set_active_inpaint_model(self, model_id: str):
        if not model_id:
            return
        if self.is_local_file(model_id):
            raise ValueError("Local checkpoint files can't be used for "
                             "inpainting — pick a dedicated inpaint model.")
        if model_id != self.active_inpaint_model():
            self.settings.setValue("models/sd_inpaint", model_id)
            self.settings.sync()
            LOG.info(f"active inpaint model → {model_id}")
            self.activeChanged.emit()

    # ------------------------------------------------------------ local files
    def local_models(self) -> list:
        val = self.settings.value("models/local", []) or []
        if isinstance(val, str):
            val = [val]
        return [v for v in val if v]

    @staticmethod
    def is_local_file(model_id: str) -> bool:
        return bool(model_id) and str(model_id).lower().endswith(
            (".safetensors", ".ckpt", ".pt", ".pth"))

    def add_local(self, path: str) -> str:
        path = os.path.abspath(path)
        lst = self.local_models()
        if path not in lst:
            lst.insert(0, path)
            self.settings.setValue("models/local", lst)
            self.settings.sync()
            LOG.info(f"local checkpoint registered: {path}")
            self.changed.emit()
        return path

    def remove_local(self, path: str):
        lst = [p for p in self.local_models() if p != path]
        self.settings.setValue("models/local", lst)
        self.settings.sync()
        LOG.info(f"local checkpoint removed: {path}")
        self.changed.emit()

    # ------------------------------------------------------------ disk cache
    def hub_cache_path(self) -> Path:
        try:
            from huggingface_hub.constants import HF_HUB_CACHE
            return Path(HF_HUB_CACHE)
        except Exception:
            return Path.home() / ".cache" / "huggingface" / "hub"

    def repo_dir(self, repo_id: str) -> Path:
        return self.hub_cache_path() / ("models--" + repo_id.replace("/", "--"))

    _repo_dir = repo_dir   # backwards-compatible alias

    def is_cached(self, repo_id: str) -> bool:
        d = self.repo_dir(repo_id)
        return d.is_dir() and any(d.iterdir())

    def disk_usage(self, repo_id: str) -> int:
        d = self.repo_dir(repo_id)
        if not d.is_dir():
            return 0
        now = time.monotonic()
        hit = self._usage_cache.get(repo_id)
        if hit is not None and now - hit[0] < self._USAGE_TTL:
            return hit[1]
        total = _dir_size(d)
        self._usage_cache[repo_id] = (now, total)
        return total

    def invalidate_usage(self, repo_id: str | None = None):
        if repo_id is None:
            self._usage_cache.clear()
        else:
            self._usage_cache.pop(repo_id, None)

    def total_cache_size(self) -> int:
        return sum(self.disk_usage(m["repo_id"]) for m in self.CATALOG)

    def delete(self, model_id: str):
        if self.is_local_file(model_id):
            self.remove_local(model_id)
            return
        d = self.repo_dir(model_id)
        if d.is_dir():
            shutil.rmtree(d, ignore_errors=True)
            LOG.info(f"deleted model from disk: {model_id}")
        self.invalidate_usage(model_id)
        self.changed.emit()

    # ------------------------------------------------------------ downloading
    def cancel_download(self):
        self._cancel.set()

    def _hf_token(self):
        return env_hf_token() or \
            (self.settings.value("api/hf_token", "") or "").strip() or None

    def _remote_size(self, repo_id: str, token=None):
        try:
            from huggingface_hub import HfApi
            info = HfApi(token=token).model_info(repo_id, files_metadata=True)
            total = 0
            for s in info.siblings or []:
                lfs = getattr(s, "lfs", None)
                size = None
                if isinstance(lfs, dict):
                    size = lfs.get("size")
                elif lfs is not None:
                    size = getattr(lfs, "size", None)
                if not size:
                    size = getattr(s, "size", None)
                if size:
                    total += size
            return total or None
        except Exception:
            return None

    def _make_tqdm(self, report, state, total, cancel):
        try:
            from tqdm import tqdm
        except Exception:
            return None

        class _T(tqdm):
            def update(self, n=1):
                super().update(n)
                if cancel.is_set():
                    raise DownloadCancelled("Download cancelled.")
                try:
                    desc = str(self.desc or "file").strip(": ")
                    state["done"][desc] = int(self.n)
                    done = sum(state["done"].values())
                    if total:
                        report(f"Downloading {desc}", min(done / total, 1.0))
                    else:
                        report(f"Downloading {desc} "
                               f"({human_size(int(self.n))})", None)
                except DownloadCancelled:
                    raise
                except Exception:
                    pass
        return _T

    def download(self, repo_id: str, cancel_event: threading.Event = None):
        """Blocking download into the HF cache (resumes partial downloads).
        Intended for a Worker thread. Never raises, except pre-flight errors."""
        from huggingface_hub import snapshot_download
        self._cancel = cancel_event or threading.Event()
        token = self._hf_token()
        total = self._remote_size(repo_id, token)

        # disk-space pre-flight (fail fast, before GBs of traffic)
        try:
            free = shutil.disk_usage(self.hub_cache_path()).free
            need = int((total or 6e9) * 1.1) + 500_000_000
            if free < need:
                raise UserError(
                    f"Not enough disk space to download {repo_id}.",
                    hint=f"About {human_size(need)} is needed, but only "
                         f"{human_size(free)} is free at {self.hub_cache_path()}.")
        except UserError:
            raise
        except Exception:
            pass

        state = {"done": {}}

        def report(label, frac):
            self.downloadProgress.emit(
                repo_id, int(frac * 100) if frac is not None else -1, label)

        LOG.info(f"downloading {repo_id} "
                 f"(≈{total // (1024 * 1024) if total else '?'} MB, "
                 f"token={'yes' if token else 'no'})")
        report("Starting…", 0.0 if total else None)
        kw = {"token": token} if token else {}
        try:
            tcls = self._make_tqdm(report, state, total, self._cancel)
            if tcls is not None:
                try:
                    snapshot_download(repo_id=repo_id, tqdm_class=tcls, **kw)
                except TypeError:
                    self._cancel.clear()
                    snapshot_download(repo_id=repo_id, **kw)
            else:
                snapshot_download(repo_id=repo_id, **kw)
            LOG.info(f"download finished: {repo_id}")
            self.invalidate_usage(repo_id)
            self.downloadFinished.emit(repo_id, True, "downloaded")
            self.changed.emit()
        except DownloadCancelled:
            LOG.warn(f"download cancelled: {repo_id}")
            self.downloadFinished.emit(repo_id, False, "download cancelled")
        except Exception as exc:
            LOG.error(f"download failed: {repo_id} — {type(exc).__name__}: {exc}")
            self.downloadFinished.emit(repo_id, False,
                                       f"{type(exc).__name__}: {exc}")

    def catalog(self) -> list:
        return list(self.CATALOG)
