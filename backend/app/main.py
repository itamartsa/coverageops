"""
CoverageOps – Backend API
FastAPI + PostgreSQL + PostGIS
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware

from app.core.config import settings
from app.core.database import engine, Base
from app.core.migrations import run_startup_migrations
from app.api import auth, sites, coverage, users

def init_db() -> None:
    """Create tables, then add columns introduced after first deployment."""
    Base.metadata.create_all(bind=engine)
    run_startup_migrations(engine)


# DB initialisation runs at application startup (not at import time), so the
# module can be imported – e.g. by tests – without a reachable database.
@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="CoverageOps API",
    description="מערכת ניתוח כיסוי סלולרי מבצעי",
    version="1.0.0",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    lifespan=lifespan,
)

# ── Middleware ────────────────────────────────────────────────────────────────
app.add_middleware(GZipMiddleware, minimum_size=1000)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition"],   # lets the SPA read download filenames
)

# ── Routers ───────────────────────────────────────────────────────────────────
app.include_router(auth.router,     prefix="/api/auth",     tags=["auth"])
app.include_router(users.router,    prefix="/api/users",    tags=["users"])
app.include_router(sites.router,    prefix="/api/sites",    tags=["sites"])
app.include_router(coverage.router, prefix="/api/coverage", tags=["coverage"])


@app.get("/api/health")
def health():
    return {"status": "ok", "version": "1.0.0"}
