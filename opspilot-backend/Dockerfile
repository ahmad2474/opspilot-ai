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

# --reload is fine for a local/dev image; a prod image would drop this flag,
# but this project's zero-spend scope doesn't need a separate prod build.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]