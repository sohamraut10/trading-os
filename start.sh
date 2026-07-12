#!/bin/sh
set -e

# Initialize the Postgres schema (all statements are CREATE TABLE IF NOT EXISTS
# so this is safe to run on every cold start).
python - <<'PYEOF'
import asyncio, asyncpg, os, sys

async def init_schema():
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        print("DATABASE_URL not set — skipping schema init")
        return
    url = url.replace("postgresql+asyncpg://", "postgresql://")
    try:
        pool = await asyncpg.create_pool(url, min_size=1, max_size=1, timeout=10)
        with open("/app/infrastructure/init.sql") as f:
            sql = f.read()
        await pool.execute(sql)
        await pool.close()
        print("DB schema ready")
    except Exception as e:
        print(f"DB schema init skipped: {e}", file=sys.stderr)

asyncio.run(init_schema())
PYEOF

exec uvicorn api.main:app --host 0.0.0.0 --port 8000 --workers 1
