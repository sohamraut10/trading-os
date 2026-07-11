# Build the dashboard first so a single-container deploy (Render, Fly.io,
# plain Docker) serves a real built SPA at / instead of the raw unbuilt
# dashboard/index.html (unprocessed module imports, no Vite transform).
FROM node:22-slim AS dashboard-build
WORKDIR /dashboard
COPY dashboard/package*.json ./
RUN npm ci
COPY dashboard/ ./
RUN npm run build

FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl gcc g++ && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
COPY --from=dashboard-build /dashboard/dist ./dashboard/dist

ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

EXPOSE 8000

# Single worker: AppState (portfolio, websocket clients, event log) is
# process-local, so >1 worker would each hold a divergent portfolio/event
# log and only a fraction of clients would see any given broadcast.
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
