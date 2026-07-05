from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import chat, dashboard, health, resources
from app.core.config import get_settings
from app.core.logging import RequestIdMiddleware, configure_logging

configure_logging()

settings = get_settings()

app = FastAPI(title="OpsPilot AI", version="0.1.0")

# Order matters: added last = runs first on the way in. Request-ID needs to
# wrap everything (including CORS) so every log line has an ID, but CORS
# headers still need to land on every response including errors.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(RequestIdMiddleware)

app.include_router(health.router, tags=["health"])
app.include_router(chat.router, tags=["chat"])
app.include_router(resources.router, tags=["resources"])
app.include_router(dashboard.router, tags=["dashboard"])
