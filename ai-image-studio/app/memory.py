"""Memory policy for local diffusion pipelines + idle auto-unload."""
from __future__ import annotations

from PySide6.QtCore import QObject, QTimer, Signal

from .diagnostics import LOG as _ROOT_LOG, gpu_info

LOG = _ROOT_LOG.memory

STRATEGIES = ("quality", "balanced", "low", "ultra")

STRATEGY_LABELS = [
    ("High quality (no offload)", "quality"),
    ("Balanced (attention + VAE slicing)", "balanced"),
    ("Low VRAM (model CPU offload)", "low"),
    ("Ultra-low VRAM (sequential offload, slow)", "ultra"),
]


def estimate_vram_gb(model_id: str) -> float:
    m = (model_id or "").lower()
    if "xl" in m:
        return 10.0
    if "diffusion-2" in m:
        return 6.0
    return 4.5


class MemoryPolicy:
    """Reads memory settings and applies them to diffusers pipelines."""

    def __init__(self, settings):
        self.settings = settings

    def _get(self, key, default):
        if self.settings is None:
            return default
        return self.settings.value(key, default)

    def _get_bool(self, key, default):
        v = self._get(key, default)
        if isinstance(v, str):
            return v.strip().lower() in ("true", "1", "yes", "on")
        return bool(v)

    @property
    def strategy(self) -> str:
        v = self._get("memory/strategy", "balanced") or "balanced"
        return v if v in STRATEGIES else "balanced"

    @property
    def auto_unload(self) -> bool:
        return self._get_bool("memory/auto_unload", True)

    @property
    def auto_unload_minutes(self) -> int:
        try:
            return max(1, int(self._get("memory/auto_unload_min", 10)))
        except (TypeError, ValueError):
            return 10

    @property
    def single_pipeline(self) -> bool:
        return self._get_bool("memory/single_pipeline", True)

    def is_offload(self) -> bool:
        return self.strategy in ("low", "ultra")

    def vram_check(self, model_id: str):
        info = gpu_info()
        if not info["available"]:
            return True, "no CUDA — will run on CPU"
        need = estimate_vram_gb(model_id)
        free_gb = info["free"] / 1e9
        if free_gb < need * 0.9:
            return False, (f"needs ~{need:.0f} GB VRAM but only {free_gb:.1f} GB "
                           f"free — switch the memory strategy to 'low'/'ultra' "
                           f"(Settings) or pick a smaller model (Models tab)")
        return True, f"~{need:.0f} GB needed, {free_gb:.1f} GB free"

    def apply(self, pipe):
        strat = self.strategy
        applied = []
        try:
            if self.is_offload():
                if strat == "low" and hasattr(pipe, "enable_model_cpu_offload"):
                    pipe.enable_model_cpu_offload()
                    applied.append("model-cpu-offload")
                elif strat == "ultra" and \
                        hasattr(pipe, "enable_sequential_cpu_offload"):
                    pipe.enable_sequential_cpu_offload()
                    applied.append("sequential-cpu-offload")
            if hasattr(pipe, "enable_attention_slicing"):
                pipe.enable_attention_slicing()
                applied.append("attention-slicing")
            if hasattr(pipe, "enable_vae_slicing"):
                pipe.enable_vae_slicing()
                applied.append("vae-slicing")
            if self.is_offload() and hasattr(pipe, "enable_vae_tiling"):
                pipe.enable_vae_tiling()
                applied.append("vae-tiling")
        except Exception as exc:
            LOG.warn(f"some memory options could not be applied ({exc})")
        if applied:
            LOG.info(f"memory policy '{strat}': {', '.join(applied)}")


class IdleUnloader(QObject):
    """Unloads the local model after N minutes without any job activity."""
    unloaded = Signal(str)

    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._on_timeout)

    def notify_activity(self):
        pol = self.ctx.memory_policy
        if not pol.auto_unload:
            self._timer.stop()
            return
        self._timer.start(pol.auto_unload_minutes * 60 * 1000)

    def _on_timeout(self):
        from .backends import LocalSDBackend
        be = self.ctx.backends.get("local")
        if isinstance(be, LocalSDBackend) and be.loaded:
            model = be.status().get("model")
            be.unload()
            msg = (f"Auto-unloaded {model or 'model'} after "
                   f"{self.ctx.memory_policy.auto_unload_minutes} min idle")
            LOG.info(msg)
            self.unloaded.emit(msg)
