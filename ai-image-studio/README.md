# AI Image Studio
Desktop AI image generator & photo editor (PySide6).

- Demo mode (no AI deps):  `pip install -r requirements.txt` → `python main.py`
- Full install:            `pip install -e .[local,bg-removal,keyring]` → `ai-image-studio`
- Data & logs:             `~/.ai-image-studio` (override with `AIIS_DATA_DIR`)
- Diagnostics:             `Ctrl+D` · flags: `--version`, `--reset-ui`
- Env config:              see `.env.example` (environment → .env → Settings)
- Tests:                   `pip install -e .[dev] && pytest -q`

## Environment variables
All optional; precedence: environment → `.env` (CWD, then data dir) → Settings.
Highlights: `AIIS_DATA_DIR`, `AIIS_LOG_DIR`, `AIIS_LOG_LEVEL`, `AIIS_CONSOLE_LOG`,
`AIIS_THEME`, `AIIS_BACKEND=demo|local|api`, `AIIS_DEVICE=cpu|cuda`,
`AIIS_SD_MODEL`, `AIIS_SD_INPAINT_MODEL`, `AIIS_OUTPUT_DIR`,
`AIIS_NO_UPDATES`, `AIIS_ALLOW_MULTI`, `AIIS_MAX_IMAGE_PIXELS`,
`HF_TOKEN`, `OPENAI_API_KEY`, `OPENAI_BASE_URL`.
Active overrides appear in Diagnostics → System (secrets masked).
