# syntax=docker/dockerfile:1

FROM python:3.11-slim

# Prevent Python from writing .pyc files and enable unbuffered stdout/stderr
# so logs surface immediately in `fly logs`.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DATA_DIR=/data

WORKDIR /app

# Install dependencies first to maximize layer caching.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application source.
COPY --chmod=644 d.py f.py a.py ht.py ehl.py ./

# Informational only — Fly.io routes to internal_port from fly.toml (8000).
EXPOSE 8000

# Create the volume mount point and a non-root user, then hand ownership of
# the mount point to that user so the app can persist history.csv / meta.json.
RUN mkdir -p /data && \
    useradd --create-home --shell /bin/bash appuser && \
    chown -R appuser:appuser /data

USER appuser

CMD ["python", "ehl.py"]
