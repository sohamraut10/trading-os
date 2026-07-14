# Multi-stage Dockerfile — targets: development (hot-reload), production (uvicorn)
# Note: single uvicorn worker is intentional — AppState (portfolio, websocket
# clients, event log) is process-local; multiple workers diverge immediately.

# ── Stage 1: build the Vite dashboard ─────────────────────────────────────────
FROM node:20-alpine AS dashboard-build
WORKDIR /dashboard
COPY dashboard/package*.json ./
RUN npm ci --prefer-offline
COPY dashboard/ ./
RUN npm run build

# ── Stage 2: Python dependencies (cached layer) ───────────────────────────────
FROM python:3.11-slim AS python-deps
WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

# ── Stage 3: development image (bind-mount source, uvicorn --reload) ──────────
FROM python:3.11-slim AS development
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=python-deps /root/.local /root/.local
ENV PATH=/root/.local/bin:$PATH
ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

EXPOSE 8000
# Source is bind-mounted by docker-compose.dev.yml; CMD is overridden there.
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]

# ── Stage 4: production image ─────────────────────────────────────────────────
FROM python:3.11-slim AS production
WORKDIR /app

# Runtime system deps only
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 1001 appgroup \
    && useradd --uid 1001 --gid appgroup --shell /bin/sh --no-create-home appuser

# Copy pre-built Python packages
COPY --from=python-deps /root/.local /home/appuser/.local
ENV PATH=/home/appuser/.local/bin:$PATH

# Copy application source
COPY --chown=appuser:appgroup api/ ./api/
COPY --chown=appuser:appgroup config/ ./config/
COPY --chown=appuser:appgroup core/ ./core/
COPY --chown=appuser:appgroup infrastructure/init.sql ./infrastructure/init.sql
COPY --chown=appuser:appgroup start.sh ./start.sh

# Inject built dashboard
COPY --from=dashboard-build --chown=appuser:appgroup /dashboard/dist ./dashboard/dist

RUN chmod +x /app/start.sh

ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

USER appuser
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
  CMD curl -f http://localhost:8000/health || exit 1

CMD ["/app/start.sh"]
