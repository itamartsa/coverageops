"""
Field reception maintenance — one pass of: command expiry, pending purges
(withdraw/delete), aggregation of dirty buckets, alert evaluation, retention.

    python -m app.scripts.field_maintenance                 # all tasks
    python -m app.scripts.field_maintenance --task aggregate --task alerts

Idempotent; safe to run from cron while the API is up (PostgreSQL advisory lock).
The API process also runs it in the background every FIELD_MAINTENANCE_INTERVAL_S
seconds (0 disables that loop — then schedule this script instead).
"""
import argparse
import json
import sys

from app.core.database import Base, SessionLocal, engine
from app.models import field  # noqa: F401  (register field_* tables)
from app.services.field_maintenance import ALL_TASKS, run_maintenance


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="CoverageOps field maintenance (one pass)")
    p.add_argument("--task", action="append", choices=ALL_TASKS,
                   help="task to run (repeatable); default: all")
    args = p.parse_args(argv)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        res = run_maintenance(db, args.task or list(ALL_TASKS))
    finally:
        db.close()
    print(json.dumps(res, ensure_ascii=False, default=str, indent=1))
    return 1 if any(isinstance(v, dict) and "error" in v for v in res.values()) else 0


if __name__ == "__main__":
    sys.exit(main())
