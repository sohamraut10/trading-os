"""
Vercel serverless entrypoint.

Vercel invokes whatever ASGI `app` this file exports, passing through the
full request path *including* the /api prefix (Vercel routes /api/* here).

This does NOT reuse api.main's `app` via app.mount("/api", ...): Starlette's
Mount does not forward ASGI lifespan events to a mounted sub-application, so
state.db.connect() (and the portfolio-snapshot resume) would silently never
run — the persistence layer this whole deployment depends on would be dead
on arrival. Instead, api.main exposes its routes on a plain `router` and its
`lifespan` context manager as importable names; this file builds its own
top-level FastAPI app, attaches that same lifespan directly (so it actually
fires), and includes the router under /api.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config.settings import settings
from api.main import router, lifespan

app = FastAPI(
    title="Trading OS",
    version="1.0.0",
    description="Multi-Agent Consensus Trading System",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_allowed_origins.split(",") if o.strip()],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(router, prefix="/api")
