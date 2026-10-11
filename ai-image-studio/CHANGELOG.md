# Changelog
## 1.3.0 — Environment configuration
- Central env layer (envconfig.py) with typed getters and documented spec
- Dependency-free .env loader (env wins over file; unknown keys ignored)
- AIIS_BACKEND forces demo/local/api (CI-safe with demo fallback)
- AIIS_DEVICE, AIIS_SD_MODEL, AIIS_SD_INPAINT_MODEL, AIIS_OUTPUT_DIR,
  AIIS_LOG_DIR, AIIS_LOG_LEVEL, AIIS_THEME, AIIS_NO_UPDATES,
  AIIS_ALLOW_MULTI, AIIS_MAX_IMAGE_PIXELS
- OPENAI_API_KEY / OPENAI_BASE_URL / HF_TOKEN support (secrets masked)

## 1.2.0 — Production hardening + performance
- Job watchdog, secret redaction, crash welcome dialog, opt-in update check,
  canvas display cache, undo byte budget, cancellable batches, fast adjustment
  previews, single-copy pixmap conversion, PIL thumbnails, dirty-region
  repaints, disk-usage TTL cache, low-RAM model loads, deferred startup,
  CI matrix, ruff, pre-commit, PyInstaller spec, expanded tests.

## 1.1.0 — Production hardening I
- Single job slot with cooperative cancellation; friendly ErrorDialog; atomic
  saves; single-instance lock; keyring API key; HF token; disk preflight;
  EXIF/bomb-safe open; window/splitter persistence; --version/--reset-ui.

## 1.0.0 — Initial feature set
Generator (txt2img/img2img, prompt forms), editor (masks, crop, filters,
adjustments, Fill/Expand), model manager, memory strategies, diagnostics,
rotating logs with categories, dark/light themes.
