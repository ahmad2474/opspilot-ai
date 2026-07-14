"""Auto-docs (/docs, /redoc, /openapi.json) are registered directly on the
FastAPI app object, so they never pass through require_session no matter
how routers are wired — they must instead be disabled entirely outside
local dev (see app/main.py).

docs_url/redoc_url/openapi_url are baked in at FastAPI construction time
from settings.opspilot_app_env, so exercising both branches means
reloading app.main under each env value rather than using
app.dependency_overrides (which only affects request-time DI).
"""
from __future__ import annotations

import importlib

import app.main as main_module
from app.core.config import get_settings


def _reload_main_with_env(monkeypatch, env_value: str):
    monkeypatch.setenv("OPSPILOT_APP_ENV", env_value)
    get_settings.cache_clear()
    reloaded = importlib.reload(main_module)
    return reloaded


def test_docs_enabled_in_local_env(monkeypatch) -> None:
    reloaded = _reload_main_with_env(monkeypatch, "local")
    try:
        assert reloaded.app.docs_url == "/docs"
        assert reloaded.app.redoc_url == "/redoc"
        assert reloaded.app.openapi_url == "/openapi.json"
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()
        importlib.reload(main_module)


def test_docs_disabled_outside_local_env(monkeypatch) -> None:
    reloaded = _reload_main_with_env(monkeypatch, "prod")
    try:
        assert reloaded.app.docs_url is None
        assert reloaded.app.redoc_url is None
        assert reloaded.app.openapi_url is None

        from fastapi.testclient import TestClient

        client = TestClient(reloaded.app)
        assert client.get("/docs").status_code == 404
        assert client.get("/redoc").status_code == 404
        assert client.get("/openapi.json").status_code == 404
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()
        importlib.reload(main_module)
