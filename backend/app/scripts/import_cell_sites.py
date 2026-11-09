"""
Load / refresh the civil cellular-sites layer.

  python -m app.scripts.import_cell_sites            # live from data.gov.il, fallback to snapshot
  python -m app.scripts.import_cell_sites --snapshot # bundled snapshot only (offline)
"""
import sys

from app.core.database import SessionLocal, engine, Base
from app.models import cell_site  # noqa: F401  (register tables)
from app.services.cell_sites_import import import_data, load_snapshot, sync_live_or_snapshot


def main():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        if "--snapshot" in sys.argv:
            s = import_data(db, load_snapshot())
        else:
            s = sync_live_or_snapshot(db)
        print(f"source={s.source} active={s.active_count} construction={s.construction_count} "
              f"skipped={s.skipped_count} data_fetched_at={s.data_fetched_at} {s.note or ''}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
