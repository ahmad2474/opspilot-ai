"""Shared pytest fixtures.

`auth_headers` mints a valid session bearer token the same way the
frontend does (see opspilot-frontend/lib/auth.ts) so route tests can
exercise the real, protected path rather than mocking the dependency
away. `test_settings` overrides get_settings() app-wide so tests never
depend on whatever happens to be in a developer's local .env.
"""
from __future__ import annotations

import time
from collections.abc import Iterator

import jwt
import pytest

from app.core.config import Settings, get_settings
from app.main import app

TEST_AUTH_SHARED_SECRET = "test-shared-secret-not-for-production"
TEST_ADMIN_EMAIL = "admin@example.com"


@pytest.fixture(autouse=True)
def _override_settings() -> Iterator[None]:
    """Force a known, deterministic auth secret for every test, regardless
    of what a developer has set locally in opspilot-backend/.env."""

    def _test_settings() -> Settings:
        return Settings(
            auth_shared_secret=TEST_AUTH_SHARED_SECRET,
            admin_email=TEST_ADMIN_EMAIL,
        )

    app.dependency_overrides[get_settings] = _test_settings
    yield
    app.dependency_overrides.pop(get_settings, None)


@pytest.fixture
def auth_headers() -> dict[str, str]:
    """A valid Authorization header for TEST_ADMIN_EMAIL.

    Includes `exp` since require_session now requires it (options={"require":
    ["exp", "sub"]}) — a token missing it is rejected, see test_security.py.
    """
    token = jwt.encode(
        {"sub": TEST_ADMIN_EMAIL, "exp": int(time.time()) + 3600},
        TEST_AUTH_SHARED_SECRET,
        algorithm="HS256",
    )
    return {"Authorization": f"Bearer {token}"}
