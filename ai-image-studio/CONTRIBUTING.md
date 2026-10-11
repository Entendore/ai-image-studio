# Contributing
1. `python -m venv .venv && source .venv/bin/activate`
2. `pip install -e .[dev]` and `pre-commit install`
3. `ruff check app main.py && pytest -q` must pass before every push.
4. Keep `app/main.py` the only entry point; logic lives in `app/`.
5. New user-facing errors must go through `errors.UserError` / `humanize()`.
6. New configuration must go through `envconfig.py` (add to SPEC + .env.example).
7. Update `CHANGELOG.md` for user-visible changes.
