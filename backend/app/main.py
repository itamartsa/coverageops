"""
CoverageOps – Backend API
FastAPI + PostgreSQL + PostGIS
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware

from sqlalchemy import text

from app.core.config import settings
from app.core.database import engine, Base
from app.api import auth, sites, coverage, users

# ── Create tables on startup ─────────────────────────────────────────────────
Base.metadata.create_all(bind=engine)

# ── Additive migrations (idempotent) ─────────────────────────────────────────
with engine.connect() as _conn:
    try:
        _conn.execute(text(
            "ALTER TABLE sites ADD COLUMN IF NOT EXISTS elevation_m FLOAT DEFAULT 0"
        ))
        _conn.commit()
    except Exception:
        pass

app = FastAPI(
    title="CoverageOps API",
    description="מערכת ניתוח כיסוי סלולרי מבצעי",
    version="1.0.0",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
)

# ── Middleware ────────────────────────────────────────────────────────────────
app.add_middleware(GZipMiddleware, minimum_size=1000)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routers ───────────────────────────────────────────────────────────────────
app.include_router(auth.router,     prefix="/api/auth",     tags=["auth"])
app.include_router(users.router,    prefix="/api/users",    tags=["users"])
app.include_router(sites.router,    prefix="/api/sites",    tags=["sites"])
app.include_router(coverage.router, prefix="/api/coverage", tags=["coverage"])


@app.get("/api/health")
def health():
    return {"status": "ok", "version": "1.0.0"}
