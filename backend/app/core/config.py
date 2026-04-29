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
    COVERAGE_GRID_RESOLUTION: int = 300        # grid points per axis

    # ── Storage ───────────────────────────────────────────────────────────────
    RESULTS_DIR: str = "/data/results"

    class Config:
        env_file = ".env"
        case_sensitive = True


settings = Settings()
