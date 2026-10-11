import os

import pytest

from app.backends import create_backends
from app.envconfig import (apply_runtime_side_effects, get_bool, get_int,
                           load_dotenv, report_rows)
from app.model_manager import ModelManager

_PREFIXES = ("AIIS_", "OPENAI_", "HF_", "HUGGING_")


@pytest.fixture
def clean_env(monkeypatch):
    before = set(os.environ)
    for k in [k for k in list(os.environ) if k.startswith(_PREFIXES)]:
        monkeypatch.delenv(k, raising=False)
    yield
    for k in [k for k in list(os.environ)
              if k.startswith(_PREFIXES) and k not in before]:
        os.environ.pop(k, None)


def test_dotenv_parses_and_env_wins(tmp_path, monkeypatch, clean_env):
    f = tmp_path / ".env"
    f.write_text("# comment\n"
                 'AIIS_THEME="light"\n'
                 "AIIS_LOG_LEVEL=debug\n"
                 "OPENAI_API_KEY=sk-test123456\n"
                 "BOGUS_VAR=1\n", encoding="utf-8")
    monkeypatch.setenv("AIIS_LOG_LEVEL", "info")      # real env wins
    n = load_dotenv([f])
    assert n == 2                                     # theme + key (level skipped)
    assert os.environ["AIIS_THEME"] == "light"
    assert os.environ["AIIS_LOG_LEVEL"] == "info"     # not overwritten
    assert "BOGUS_VAR" not in os.environ              # unknown keys ignored


def test_typed_getters(clean_env, monkeypatch):
    monkeypatch.setenv("AIIS_CONSOLE_LOG", "TRUE")
    monkeypatch.setenv("AIIS_MAX_IMAGE_PIXELS", "not-a-number")
    assert get_bool("AIIS_CONSOLE_LOG") is True
    assert get_bool("AIIS_NO_UPDATES", False) is False
    assert get_int("AIIS_MAX_IMAGE_PIXELS", 7) == 7   # invalid → default


def test_report_masks_secrets(clean_env, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-supersecret")
    monkeypatch.setenv("AIIS_THEME", "light")
    rows = dict(report_rows())
    assert rows["OPENAI_API_KEY"] == "***"
    assert rows["AIIS_THEME"] == "light"


def test_side_effects_pillow_limit(clean_env, monkeypatch):
    from PIL import Image
    orig = Image.MAX_IMAGE_PIXELS
    try:
        monkeypatch.setenv("AIIS_MAX_IMAGE_PIXELS", "12345678")
        apply_runtime_side_effects()
        assert Image.MAX_IMAGE_PIXELS == 12345678
    finally:
        Image.MAX_IMAGE_PIXELS = orig


def test_model_overrides(clean_env, monkeypatch):
    monkeypatch.setenv("AIIS_SD_MODEL", "Lykon/dreamshaper-8")
    monkeypatch.setenv("HF_TOKEN", "hf_tokenvalue123")
    mgr = ModelManager(None)
    assert mgr.active_gen_model() == "Lykon/dreamshaper-8"
    assert mgr._hf_token() == "hf_tokenvalue123"


def test_backend_forcing(clean_env, monkeypatch):
    monkeypatch.setattr("app.backends.resolve_key", lambda s: "")
    monkeypatch.setenv("AIIS_BACKEND", "demo")
    assert list(create_backends(None)) == ["demo"]

    monkeypatch.setenv("AIIS_BACKEND", "api")         # no key → safe fallback
    assert list(create_backends(None)) == ["demo"]

    monkeypatch.setenv("AIIS_BACKEND", "api")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://proxy.example/v1")
    monkeypatch.setattr("app.backends.resolve_key", lambda s: "k")
    be = create_backends(None)
    assert list(be) == ["api"]
    assert be["api"].base_url == "https://proxy.example/v1"
