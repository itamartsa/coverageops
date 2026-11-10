"""Time helpers for the field module: naive-UTC conventions, ISO output and
Asia/Jerusalem calendar days (aggregates and schedules are in Israel time).

zoneinfo needs a tz database; Linux/Docker has one, Windows only with the
`tzdata` package — which is not a dependency of this project. Without it the
Israeli DST rule (in force since 2013) is applied directly:
DST from the Friday before the last Sunday of March, 02:00 local, to the last
Sunday of October, 02:00 local (UTC+3), otherwise UTC+2.
"""
from datetime import date, datetime, timedelta, timezone
from typing import Optional

try:  # pragma: no cover - depends on the platform
    from zoneinfo import ZoneInfo
    _TZ = ZoneInfo("Asia/Jerusalem")
except Exception:  # pragma: no cover
    _TZ = None


def utcnow() -> datetime:
    return datetime.utcnow()


def iso(dt: Optional[datetime]) -> Optional[str]:
    """Naive UTC → ISO 8601 with Z (the convention of api/cell_sites._iso)."""
    if dt is None:
        return None
    return dt.isoformat() + "Z"


def parse_iso(value) -> Optional[datetime]:
    """ISO 8601 (Z / offset / naive=UTC) → naive UTC. None if unparsable."""
    if value is None or not isinstance(value, str) or not value:
        return None
    s = value.strip()
    if s.endswith("Z") or s.endswith("z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _last_sunday(year: int, month: int) -> date:
    d = date(year, month + 1, 1) - timedelta(days=1) if month < 12 else date(year, 12, 31)
    return d - timedelta(days=(d.weekday() + 1) % 7)


def _il_offset_rule(utc: datetime) -> timedelta:
    y = utc.year
    start_local = datetime.combine(_last_sunday(y, 3) - timedelta(days=2), datetime.min.time()) + timedelta(hours=2)
    end_local = datetime.combine(_last_sunday(y, 10), datetime.min.time()) + timedelta(hours=2)
    start_utc = start_local - timedelta(hours=2)   # 02:00 IST
    end_utc = end_local - timedelta(hours=3)       # 02:00 IDT
    return timedelta(hours=3) if start_utc <= utc < end_utc else timedelta(hours=2)


def il_offset(utc: datetime) -> timedelta:
    if _TZ is not None:
        return utc.replace(tzinfo=timezone.utc).astimezone(_TZ).utcoffset()
    return _il_offset_rule(utc)


def il_day(utc: datetime) -> date:
    """Asia/Jerusalem calendar day of a naive-UTC instant."""
    return (utc + il_offset(utc)).date()


def il_day_bounds(d: date) -> tuple:
    """[start, end) of an Israeli calendar day, as naive UTC."""
    local_midnight = datetime.combine(d, datetime.min.time())
    start = local_midnight - timedelta(hours=2)
    start = local_midnight - il_offset(start)
    nxt = local_midnight + timedelta(days=1)
    end = nxt - il_offset(nxt - timedelta(hours=2))
    return start, end


def floor_hour(dt: datetime) -> datetime:
    return dt.replace(minute=0, second=0, microsecond=0)
