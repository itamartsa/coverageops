from pydantic_settings import BaseSettings
from typing import List


class Settings(BaseSettings):
    # ── App ──────────────────────────────────────────────────────────────────
    APP_NAME: str = "CoverageOps"
    SECRET_KEY: str = "CHANGE-ME-IN-PRODUCTION-USE-LONG-RANDOM-STRING"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 480  # 8 hours for field use

    # ── Database ─────────────────────────────────────────────────────────────
    DATABASE_URL: str = "postgresql://coverageops:coverageops@db:5432/coverageops"

    # ── CORS ─────────────────────────────────────────────────────────────────
    CORS_ORIGINS: List[str] = [
        "http://localhost:3000",
        "http://localhost:5173",
        "https://coverageops.yourdomain.com",
    ]

    # ── Coverage engine ───────────────────────────────────────────────────────
    SRTM_DATA_DIR: str = "/data/srtm"          # SRTM elevation tiles
    SIGNAL_SERVER_BIN: str = "/usr/local/bin/signal-server"
    MAX_ANALYSIS_RADIUS_KM: float = 350.0
    COVERAGE_GRID_RESOLUTION: int = 300        # grid points per axis (legacy default)

    # Adaptive grid resolution (analyze without an explicit `resolution`):
    #   R = clamp(round(max_span_m / max(TARGET_CELL_M, dem_m_per_px)), MIN, MAX)
    TARGET_CELL_M: float = 40.0                # target cell size, metres
    MAX_GRID_RESOLUTION: int = 500             # cap per axis (also caps DEM extension)
    MIN_GRID_RESOLUTION: int = 60              # floor per axis
    MAX_BBOX_SPAN_KM: float = 400.0            # max bbox side (analyze, topo-overlay) → 400

    # ── Terrain (Terrarium DEM tiles) ─────────────────────────────────────────
    TILE_CACHE_SIZE: int = 512                 # in-memory LRU, tiles (~256 KB each)
    MAX_DEM_TILES: int = 64                    # tile budget per elevation grid
    TILE_FETCH_WORKERS: int = 8                # parallel download threads
    TILE_FETCH_TIMEOUT_S: float = 4.0          # per-tile HTTP timeout
    TILE_FETCH_DEADLINE_S: float = 20.0        # total deadline per grid fetch
    MAX_DEM_PIXELS: int = 100_000_000          # hard cap on DEM mosaic/output pixels → 413

    # ── Storage ───────────────────────────────────────────────────────────────
    RESULTS_DIR: str = "/data/results"         # legacy GeoJSON files (read-only now)

    class Config:
        env_file = ".env"
        case_sensitive = True


settings = Settings()
