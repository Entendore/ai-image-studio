"""AI backends: demo (offline), local diffusion (diffusers), OpenAI-compatible.

All heavy dependencies are imported lazily. Backends support cooperative
cancellation via `cancel_event` (set by the JobManager per job). AIIS_BACKEND
forces a single backend; AIIS_DEVICE forces the compute device.
"""
from __future__ import annotations

import base64
import gc
import hashlib
import io
import os
import random
import time
from abc import ABC, abstractmethod

from PIL import Image, ImageDraw, ImageFilter

from .diagnostics import LOG as _ROOT_LOG, gpu_info
from .envconfig import device_pref, force_backend, openai_base_env
from .errors import JobCancelled, UserError
from .image_ops import smart_upscale
from .memory import MemoryPolicy
from .secrets import resolve_key

LOG = _ROOT_LOG.backend

LANCZOS = Image.Resampling.LANCZOS


class BackendBase(ABC):
    name = "base"

    def __init__(self) -> None:
        self.progress_cb = None
        self.cancel_event = None

    def _progress(self, pct: float) -> None:
        if self.progress_cb:
            try:
                self.progress_cb(max(0, min(100, int(pct))))
            except Exception:
                pass

    def _check_cancel(self):
        if self.cancel_event is not None and self.cancel_event.is_set():
            raise JobCancelled()

    @staticmethod
    def _resolve_seed(seed: int) -> int:
        return random.randint(0, 2**31 - 1) if seed < 0 else int(seed)

    @abstractmethod
    def generate(self, prompt, negative="", width=512, height=512,
                 steps=25, guidance=7.0, seed=-1) -> Image.Image:
        ...

    def img2img(self, image, prompt, negative="", strength=0.65, steps=25,
                guidance=7.0, seed=-1) -> Image.Image:
        raise NotImplementedError(f'"{self.name}" does not support image-to-image.')

    def inpaint(self, image, mask, prompt, negative="", steps=25,
                guidance=7.0, seed=-1) -> Image.Image:
        raise NotImplementedError(f'"{self.name}" does not support inpainting.')

    def upscale(self, image: Image.Image, factor: int = 2) -> Image.Image:
        return smart_upscale(image, factor, on_progress=self._progress)

    def remove_background(self, image: Image.Image) -> Image.Image:
        try:
            from rembg import remove
        except ImportError:
            raise NotImplementedError(
                "Background removal needs the `rembg` package:  pip install rembg")
        self._progress(10)
        out = remove(image.convert("RGB"))
        self._progress(100)
        return out

    def unload(self) -> None:
        pass


# ---------------------------------------------------------------------------
class DemoBackend(BackendBase):
    """Offline placeholder backend — procedural art, full UI coverage."""
    name = "Demo (offline, no AI)"

    def _demo_art(self, prompt, width, height, seed):
        digest = int.from_bytes(hashlib.sha256(prompt.encode()).digest()[:6], "big")
        rng = random.Random(seed ^ digest)
        base = (rng.randint(30, 220), rng.randint(30, 220), rng.randint(30, 220))
        img = Image.new("RGB", (width, height), base)
        d = ImageDraw.Draw(img)
        for y in range(height):
            t = y / max(height - 1, 1)
            d.line([(0, y), (width, y)],
                   fill=tuple(int(c * (0.55 + 0.45 * t)) for c in base))
        for _ in range(rng.randint(5, 11)):
            cx, cy = rng.randint(-width // 4, width), rng.randint(-height // 4, height)
            r = rng.randint(width // 10, width // 2)
            col = tuple(min(255, max(0, c + rng.randint(-70, 90))) for c in base)
            d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=col)
        img = img.filter(ImageFilter.GaussianBlur(width / 45))
        d = ImageDraw.Draw(img)
        d.text((12, 10), (prompt.strip() or "untitled")[:38], fill=(255, 255, 255))
        d.text((12, height - 26), f"demo - seed {seed}", fill=(255, 255, 255))
        return img

    def generate(self, prompt, negative="", width=512, height=512,
                 steps=25, guidance=7.0, seed=-1):
        seed = self._resolve_seed(seed)
        total = max(4, int(steps))
        for i in range(total):
            self._check_cancel()
            self._progress(i / total * 90)
            time.sleep(0.03)
        img = self._demo_art(prompt, width, height, seed)
        self._progress(100)
        return img

    def img2img(self, image, prompt, negative="", strength=0.65, steps=25,
                guidance=7.0, seed=-1):
        seed = self._resolve_seed(seed)
        total = max(4, int(steps))
        for i in range(total):
            self._check_cancel()
            self._progress(i / total * 90)
            time.sleep(0.02)
        base = image.convert("RGB")
        art = self._demo_art(prompt, *base.size, seed)
        out = Image.blend(base, art, max(0.05, min(0.95, float(strength))))
        self._progress(100)
        return out

    def inpaint(self, image, mask, prompt, negative="", steps=25,
                guidance=7.0, seed=-1):
        for i in range(10):
            self._check_cancel()
            self._progress(i * 10)
            time.sleep(0.05)
        blurred = image.convert("RGB").filter(ImageFilter.GaussianBlur(10))
        m = mask.convert("L").point(lambda v: 255 if v > 10 else 0)
        out = Image.composite(blurred, image.convert("RGB"), m)
        self._progress(100)
        return out


# ---------------------------------------------------------------------------
class LocalSDBackend(BackendBase):
    """Local diffusion via diffusers. Pipelines cached per kind, single-pipeline
    mode, memory policies, VRAM pre-checks, OOM recovery, cancellation."""
    name = "Local Stable Diffusion"

    SAMPLERS = {
        "Euler a": ("EulerAncestralDiscreteScheduler", {}),
        "Euler": ("EulerDiscreteScheduler", {}),
        "DPM++ 2M": ("DPMSolverMultistepScheduler", {}),
        "DPM++ 2M Karras": ("DPMSolverMultistepScheduler", {"use_karras_sigmas": True}),
        "DPM++ SDE": ("DPMSolverSDEScheduler", {}),
        "Heun": ("HeunDiscreteScheduler", {}),
        "DDIM": ("DDIMScheduler", {}),
        "LMS": ("LMSDiscreteScheduler", {}),
    }

    def __init__(self, model_manager=None, memory_policy=None):
        super().__init__()
        self.mgr = model_manager
        self.policy = memory_policy or MemoryPolicy(None)
        self.sampler_name = "Euler a"
        self._pipes = {}          # kind -> (pipe, model_id)
        self._sampler_applied = None
        self._device = "cpu"
        self._dtype = None
        self._offload = False

    # ------------------------------------------------------- model selection
    def gen_model_id(self) -> str:
        return self.mgr.active_gen_model() if self.mgr else \
            "stabilityai/stable-diffusion-2-1-base"

    def inpaint_model_id(self) -> str:
        return self.mgr.active_inpaint_model() if self.mgr else \
            "stabilityai/stable-diffusion-2-inpainting"

    @staticmethod
    def is_single_file(model_id: str) -> bool:
        return str(model_id).lower().endswith((".safetensors", ".ckpt", ".pt", ".pth"))

    def status(self) -> dict:
        return {"loaded": bool(self._pipes), "device": self._device,
                "dtype": str(self._dtype).replace("torch.", "") if self._dtype else None,
                "model": self._pipes.get("txt", (None, None))[1],
                "pipelines": sorted(self._pipes.keys()),
                "offload": self._offload}

    @property
    def loaded(self) -> bool:
        return bool(self._pipes)

    def memory_info(self) -> dict:
        info = gpu_info()
        info["pipelines"] = sorted(self._pipes.keys())
        return info

    # --------------------------------------------------------------- loading
    def _setup(self):
        import torch
        pref = device_pref()
        if pref == "cpu":
            self._device = "cpu"
        elif pref == "cuda" and torch.cuda.is_available():
            self._device = "cuda"
        else:
            self._device = "cuda" if torch.cuda.is_available() else "cpu"
        self._dtype = torch.float16 if self._device == "cuda" else torch.float32
        if pref != "auto":
            LOG.info(f"device forced by AIIS_DEVICE={pref}: {self._device}")

    @staticmethod
    def _release():
        gc.collect()
        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass

    @staticmethod
    def _pipeline_classes():
        try:
            from diffusers import (AutoPipelineForImage2Image,
                                   AutoPipelineForInpainting,
                                   AutoPipelineForText2Image)
            return AutoPipelineForText2Image, AutoPipelineForImage2Image, \
                AutoPipelineForInpainting
        except Exception:
            from diffusers import (StableDiffusionImg2ImgPipeline,
                                   StableDiffusionInpaintPipeline,
                                   StableDiffusionPipeline)
            return StableDiffusionPipeline, StableDiffusionImg2ImgPipeline, \
                StableDiffusionInpaintPipeline

    def _from_pretrained(self, cls, model_id):
        base = dict(torch_dtype=self._dtype)
        # low_cpu_mem_usage shards the weight load through accelerate and
        # roughly halves peak RAM; degrade gracefully without it.
        for kw in (dict(base, low_cpu_mem_usage=True, safety_checker=None,
                        requires_safety_checker=False),
                   dict(base, low_cpu_mem_usage=True),
                   base):
            try:
                return cls.from_pretrained(model_id, **kw)
            except (TypeError, ValueError):
                continue
        return cls.from_pretrained(model_id, **base)

    def _from_single_file(self, cls, path):
        try:
            return cls.from_single_file(path, torch_dtype=self._dtype)
        except Exception:
            from diffusers import StableDiffusionPipeline
            return StableDiffusionPipeline.from_single_file(path,
                                                            torch_dtype=self._dtype)

    def _load_pipe(self, cls, model_id):
        ok, msg = self.policy.vram_check(model_id)
        LOG.info(f"Loading {model_id} ({cls.__name__}): {msg}")
        if not ok:
            raise RuntimeError(f"Not enough VRAM to load {model_id}: {msg}.")
        self._setup()
        self._progress(3)
        t0 = time.perf_counter()
        if self.is_single_file(model_id) and os.path.exists(model_id):
            pipe = self._from_single_file(cls, model_id)
        else:
            pipe = self._from_pretrained(cls, model_id)
        self._offload = self.policy.is_offload()
        if self._offload:
            self.policy.apply(pipe)
        else:
            pipe.to(self._device)
            self.policy.apply(pipe)
        pipe.set_progress_bar_config(disable=True)
        LOG.info(f"Loaded {model_id} in {time.perf_counter() - t0:.1f}s "
                 f"on {self._device} ({self._dtype})")
        return pipe

    def _get_pipe(self, kind, cls, model_id):
        entry = self._pipes.get(kind)
        if entry is not None and entry[1] == model_id:
            return entry[0]
        if self.policy.single_pipeline:
            self._release_pipes(exclude=kind)
        self._pipes.pop(kind, None)
        self._release()
        pipe = self._load_pipe(cls, model_id)
        self._pipes[kind] = (pipe, model_id)
        return pipe

    def _release_pipes(self, exclude=None):
        for k in [k for k in self._pipes if k != exclude]:
            LOG.info(f"Releasing pipeline '{k}' to free memory")
            self._pipes.pop(k, None)
        if exclude is None:
            self._sampler_applied = None

    def ensure_loaded(self, *_args, **_kwargs):
        self._get_pipe("txt", self._pipeline_classes()[0], self.gen_model_id())
        self._progress(100)
        return True

    # --------------------------------------------------------------- sampler
    def set_sampler(self, name: str):
        self.sampler_name = name
        entry = self._pipes.get("txt")
        if entry is not None:
            self._apply_sampler(entry[0])

    def _apply_sampler(self, pipe):
        if pipe is None or self._sampler_applied == self.sampler_name:
            return
        spec = self.SAMPLERS.get(self.sampler_name)
        if not spec:
            return
        cls_name, kwargs = spec
        try:
            import diffusers
            cls = getattr(diffusers, cls_name, None)
            if cls is None:
                return
            pipe.scheduler = cls.from_config(pipe.scheduler.config, **kwargs)
            self._sampler_applied = self.sampler_name
        except Exception:
            pass

    def _pipe_call(self, pipe, kwargs):
        import inspect
        steps = kwargs["num_inference_steps"]

        def cb(*args, **_):
            self._check_cancel()
            step = args[0] if args else 0
            self._progress(min(97, (step + 1) / steps * 97))
            return {}

        params = inspect.signature(pipe.__call__).parameters
        if "callback_on_step_end" in params:
            return pipe(**kwargs, callback_on_step_end=cb)
        return pipe(**kwargs, callback=cb, callback_steps=1)

    def _run(self, pipe, kwargs):
        try:
            return self._pipe_call(pipe, kwargs)
        except JobCancelled:
            raise
        except RuntimeError as exc:
            if "out of memory" in str(exc).lower():
                self.unload()
                msg = ("CUDA out of memory — the model was unloaded to free VRAM. "
                       "Try the 'low' or 'ultra' memory strategy (Settings), a "
                       "smaller resolution, or fewer steps.")
                LOG.error(msg)
                raise RuntimeError(msg) from exc
            raise

    def _generator(self, seed):
        import torch
        device = "cpu" if self._offload else self._device
        return torch.Generator(device).manual_seed(self._resolve_seed(seed))

    # ------------------------------------------------------------- pipelines
    def generate(self, prompt, negative="", width=512, height=512,
                 steps=25, guidance=7.0, seed=-1):
        pipe = self._get_pipe("txt", self._pipeline_classes()[0],
                              self.gen_model_id())
        self._apply_sampler(pipe)
        steps = max(4, int(steps))
        width, height = width // 8 * 8, height // 8 * 8
        kwargs = dict(prompt=prompt, negative_prompt=negative or None,
                      width=width, height=height, num_inference_steps=steps,
                      guidance_scale=float(guidance),
                      generator=self._generator(seed))
        out = self._run(pipe, kwargs)
        self._progress(100)
        return out.images[0]

    def img2img(self, image, prompt, negative="", strength=0.65, steps=25,
                guidance=7.0, seed=-1):
        model_id = self.gen_model_id()
        pipe = self._get_pipe("img2", self._pipeline_classes()[1], model_id)
        w, h = image.size
        cap = 1024 if "xl" in model_id.lower() else 768
        scale = min(1.0, cap / max(w, h))
        iw, ih = max(64, int(w * scale) // 8 * 8), max(64, int(h * scale) // 8 * 8)
        init = image.convert("RGB").resize((iw, ih), LANCZOS)
        steps = max(4, int(steps))
        kwargs = dict(prompt=prompt, negative_prompt=negative or None,
                      image=init,
                      strength=max(0.05, min(0.95, float(strength))),
                      num_inference_steps=steps, guidance_scale=float(guidance),
                      generator=self._generator(seed))
        out = self._run(pipe, kwargs)
        self._progress(100)
        return out.images[0].resize(image.size, LANCZOS)

    def inpaint(self, image, mask, prompt, negative="", steps=25,
                guidance=7.0, seed=-1):
        model_id = self.inpaint_model_id()
        pipe = self._get_pipe("inpaint", self._pipeline_classes()[2], model_id)
        w, h = image.size
        cap = 1024 if "xl" in model_id.lower() else 512
        scale = min(1.0, cap / max(w, h))
        iw, ih = max(64, int(w * scale) // 8 * 8), max(64, int(h * scale) // 8 * 8)
        img = image.convert("RGB").resize((iw, ih), LANCZOS)
        msk = (mask.convert("L").resize((iw, ih), Image.Resampling.NEAREST)
               .point(lambda v: 255 if v > 10 else 0))
        steps = max(4, int(steps))
        kwargs = dict(prompt=prompt, negative_prompt=negative or None,
                      image=img, mask_image=msk, num_inference_steps=steps,
                      guidance_scale=float(guidance),
                      generator=self._generator(seed))
        out = self._run(pipe, kwargs)
        self._progress(100)
        return out.images[0].resize(image.size, LANCZOS)

    def unload(self):
        if self._pipes:
            LOG.info(f"Unloading pipelines: {', '.join(sorted(self._pipes))}")
        self._pipes.clear()
        self._sampler_applied = None
        self._offload = False
        self._release()


# ---------------------------------------------------------------------------
class OpenAIImageBackend(BackendBase):
    """OpenAI-compatible /images endpoints with retries and friendly errors."""
    name = "OpenAI-compatible API"
    _DALLE3 = ["1024x1024", "1024x1792", "1792x1024"]
    _GPTIMAGE = ["1024x1024", "1536x1024", "1024x1536"]

    def __init__(self, base_url, api_key, model="dall-e-3"):
        super().__init__()
        self.base_url = (base_url or "https://api.openai.com/v1").rstrip("/")
        self.api_key = api_key
        self.model = model or "dall-e-3"

    def _snap_size(self, w, h):
        if self.model == "dall-e-2":
            s = min((256, 512, 1024), key=lambda s: abs(s - max(w, h)))
            return f"{s}x{s}"
        allowed = {"dall-e-3": self._DALLE3,
                   "gpt-image-1": self._GPTIMAGE}.get(self.model)
        if not allowed:
            return f"{int(w)}x{int(h)}"
        target = w / h
        return min(allowed, key=lambda s: abs(int(s.split("x")[0]) /
                                              int(s.split("x")[1]) - target))

    def _extract_image(self, item):
        raw = base64.b64decode(item["b64_json"]) if item.get("b64_json") \
            else self._download(item["url"])
        return Image.open(io.BytesIO(raw)).convert("RGB")

    @staticmethod
    def _download(url):
        import requests
        return requests.get(url, timeout=300).content

    @staticmethod
    def _api_error(r):
        try:
            msg = r.json().get("error", {}).get("message", r.text)
        except Exception:
            msg = r.text
        if r.status_code in (401, 403):
            return UserError(
                f"The API rejected the credentials (HTTP {r.status_code}).",
                hint="Check the API key in Settings.")
        if r.status_code == 404:
            return UserError(f"Endpoint or model not found (HTTP 404): {msg}",
                             hint="Check the Base URL and model name in Settings.")
        if r.status_code == 429:
            return UserError("Rate limited by the API (HTTP 429).",
                             hint="Wait a moment and try again.")
        if r.status_code >= 500:
            return UserError(f"API server error (HTTP {r.status_code}).",
                             hint="The service may be temporarily unavailable.")
        return UserError(f"API error {r.status_code}: {msg}")

    def _post(self, path, **kw):
        import requests
        last_exc = None
        for attempt in range(3):                      # retry transient failures
            self._check_cancel()
            try:
                t0 = time.perf_counter()
                r = requests.post(f"{self.base_url}{path}",
                                  headers={"Authorization":
                                           f"Bearer {self.api_key}"},
                                  timeout=300, **kw)
                LOG.info(f"API {path} [{self.model}] → {r.status_code} "
                         f"in {time.perf_counter() - t0:.1f}s"
                         + (f" (retry {attempt})" if attempt else ""))
                if r.status_code >= 400:
                    raise self._api_error(r)
                return r
            except (requests.ConnectionError, requests.Timeout) as exc:
                last_exc = exc
                time.sleep(1.5 * (attempt + 1))
        raise UserError("Could not reach the API after several attempts.",
                        hint="Check your internet connection or the Base URL "
                             "in Settings.") from last_exc

    def generate(self, prompt, negative="", width=512, height=512,
                 steps=25, guidance=7.0, seed=-1):
        self._progress(10)
        full_prompt = prompt if not negative else f"{prompt}\n\nAvoid: {negative}"
        body = {"model": self.model, "prompt": full_prompt, "n": 1,
                "size": self._snap_size(width, height)}
        if self.model != "gpt-image-1":
            body["response_format"] = "b64_json"
        r = self._post("/images/generations", json=body)
        self._progress(80)
        img = self._extract_image(r.json()["data"][0])
        self._progress(100)
        return img

    def img2img(self, image, prompt, negative="", strength=0.65, steps=25,
                guidance=7.0, seed=-1):
        if self.model not in ("gpt-image-1", "dall-e-2"):
            raise NotImplementedError(
                "Image-to-image needs model 'gpt-image-1' or 'dall-e-2' "
                "(set it in Settings).")
        self._progress(10)
        full_prompt = prompt if not negative else f"{prompt}\n\nAvoid: {negative}"
        buf = io.BytesIO()
        image.convert("RGB").save(buf, "PNG")
        data = {"model": self.model, "prompt": full_prompt, "n": "1",
                "size": self._snap_size(*image.size)}
        if self.model == "dall-e-2":
            data["response_format"] = "b64_json"
        files = {"image": ("init.png", buf.getvalue(), "image/png")}
        r = self._post("/images/edits", data=data, files=files)
        self._progress(80)
        img = self._extract_image(r.json()["data"][0])
        self._progress(100)
        return img


# ---------------------------------------------------------------------------
def create_backends(settings, model_manager=None, memory_policy=None) -> dict:
    """Build the dict of usable backends.

    AIIS_BACKEND=demo|local|api forces a single backend (tests/CI); when the
    forced backend is unavailable, demo is the safe fallback so the UI
    always has something to run."""
    backends: dict = {}
    forced = force_backend()
    if forced in ("", "local"):
        try:
            import diffusers, torch  # noqa: F401
            backends["local"] = LocalSDBackend(model_manager, memory_policy)
        except Exception:
            if forced == "local":
                LOG.warn("AIIS_BACKEND=local requested but torch/diffusers "
                         "are unavailable — falling back to demo")
    if forced in ("", "api"):
        key = resolve_key(settings)     # keyring → OPENAI_API_KEY → settings
        if key:
            base = openai_base_env() or \
                settings.value("api/openai_base", "https://api.openai.com/v1")
            backends["api"] = OpenAIImageBackend(
                base, key, settings.value("api/openai_model", "dall-e-3"))
        elif forced == "api":
            LOG.warn("AIIS_BACKEND=api requested but no API key is configured "
                     "— falling back to demo")
    if forced in ("", "demo") or not backends:
        backends["demo"] = DemoBackend()
    return backends
