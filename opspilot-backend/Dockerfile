# --- Base -----------------------------------------------------------------
FROM python:3.12-slim AS base

# Keep Python from writing .pyc files / buffering stdout — cleaner container logs
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# --- Dependencies (cached separately from app code) ------------------------
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# --- App code ---------------------------------------------------------------
COPY app ./app

# Run as a non-root user — no reason this process needs root inside the container
RUN useradd --create-home --uid 1000 appuser
USER appuser

EXPOSE 8000

# Bind to $PORT when the platform sets one (Render, Koyeb, Fly all inject it),
# falling back to 8000 for docker-compose / plain `docker run`. Shell form so
# the variable actually expands. No --reload here -- docker-compose.yml adds it
# via `command:` for local live-reload; a hosted image never wants a file watcher.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]