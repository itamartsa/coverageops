"""
CoverageOps – Backend API
FastAPI + PostgreSQL + PostGIS
"""
import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware

from app.core.config import settings
from app.core.database import engine, Base
from app.core.migrations import run_startup_migrations
from app.api import auth, sites, coverage, users, cell_sites, device_profiles, link
from app.api import field_ingest, field
from app.models import cell_site  # noqa: F401  (register cell_sites tables)
from app.models import link_result  # noqa: F401  (register link_results table)
from app.models import device_profile  # noqa: F401  (register device_profiles table)
from app.models import field as field_models  # noqa: F401  (register field_* tables)
from app.core.device_auth import FieldHTTPError, field_http_error_handler, pepper_is_default
from app.services.field_config import seed_field_defaults
from app.services.field_maintenance import loop_enabled, maintenance_loop
from app.models.device_profile import seed_device_profiles
from app.core.database import SessionLocal
from app.services.cell_sites_import import ensure_loaded

def init_db() -> None:
    """Create tables, then add columns introduced after first deployment."""
    Base.metadata.create_all(bind=engine)
    run_startup_migrations(engine)
    load_device_profiles()
    load_cell_sites()
    load_field_defaults()


def load_field_defaults() -> None:
    """Field reception: default group "כללי", operator map, default alert rules."""
    if pepper_is_default():
        print("⚠️  FIELD_TOKEN_PEPPER is the default value — set it in production")
    db = SessionLocal()
    try:
        seed_field_defaults(db)
    except Exception as e:  # never block startup
        print(f"⚠️  field defaults seed skipped: {e}")
    finally:
        db.close()


def load_device_profiles() -> None:
    """Seed the built-in receiver profiles (idempotent, keeps admin edits)."""
    db = SessionLocal()
    try:
        seed_device_profiles(db)
    except Exception as e:
        print(f"⚠️  device_profiles seed skipped: {e}")
    finally:
        db.close()


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
    task = None
    if loop_enabled():   # field maintenance (aggregation/alerts/retention/purge); off under pytest
        try:
            task = asyncio.create_task(maintenance_loop())
        except Exception as e:  # never block startup
            logging.getLogger(__name__).warning("field maintenance loop not started: %s", e)
    yield
    if task is not None:
        task.cancel()
        with contextlib.suppress(BaseException):
            await task


app = FastAPI(
    title="COVIRADYX API",
    description="מערכת ניתוח כיסוי סלולרי מבצעי",
    version="1.0.0",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    lifespan=lifespan,
)

app.add_exception_handler(FieldHTTPError, field_http_error_handler)

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
app.include_router(cell_sites.router, prefix="/api/cell-sites", tags=["cell-sites"])
app.include_router(device_profiles.router, prefix="/api/device-profiles", tags=["device-profiles"])
app.include_router(link.router,     prefix="/api/link",     tags=["link"])
# field reception: device endpoints first, so /devices/enroll|heartbeat|me/* win over /devices/{id}
app.include_router(field_ingest.router, prefix="/api/field", tags=["field-ingest"])
app.include_router(field.router,        prefix="/api/field", tags=["field"])


@app.get("/api/health")
def health():
    return {"status": "ok", "version": "1.0.0"}
