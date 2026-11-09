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
from app.api import auth, sites, coverage, users, cell_sites, osm_towers
from app.models import cell_site  # noqa: F401  (register cell_sites tables)
from app.core.database import SessionLocal
from app.services.cell_sites_import import ensure_loaded

def init_db() -> None:
    """Create tables, then add columns introduced after first deployment."""
    Base.metadata.create_all(bind=engine)
    run_startup_migrations(engine)
    load_cell_sites()


def load_cell_sites() -> None:
    """Civil cellular-sites layer: load the bundled snapshot if the table is empty."""
    db = SessionLocal()
    try:
        ensure_loaded(db)
    except Exception as e:  # never block startup on the layer
        print(f"⚠️  cell_sites load skipped: {e}")
    finally:
        db.close()


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
# osm_towers before cell_sites: otherwise /api/cell-sites/osm is captured by /api/cell-sites/{site_id}
app.include_router(osm_towers.router, prefix="/api/cell-sites/osm", tags=["cell-sites"])
app.include_router(cell_sites.router, prefix="/api/cell-sites", tags=["cell-sites"])


@app.get("/api/health")
def health():
    return {"status": "ok", "version": "1.0.0"}
