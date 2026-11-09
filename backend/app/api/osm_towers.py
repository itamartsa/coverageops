"""
/api/cell-sites/osm — cellular towers from OpenStreetMap (Overpass API), proxied + cached.

The public Overpass servers are slow (tens of seconds for a modest bbox) and
rate-limit aggressive clients, so the browser no longer queries them directly:
  * the bbox is limited (≤ MAX_SPAN_DEG per side),
  * it is snapped outward to a GRID_DEG grid so nearby viewports share one query,
  * results are cached in-process (TTL CACHE_TTL_S, at most CACHE_MAX entries),
  * mirrors are tried in order; 429/504/5xx/non-JSON → next mirror.
"""
import json
import math
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import OrderedDict
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.security import get_current_user
from app.models.user import User

router = APIRouter()

OVERPASS_MIRRORS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]
OVERPASS_QUERY_TIMEOUT_S = 40          # [timeout:..] inside the query
HTTP_TIMEOUT_S = 45                    # socket timeout per mirror
USER_AGENT = "CoverageOps/1.0"

MAX_SPAN_DEG = 1.5
GRID_DEG = 0.1
CACHE_TTL_S = 30 * 60
CACHE_MAX = 256

TAG_SUBSET = ("operator", "operator:he", "height", "name", "ref",
              "tower:type", "man_made", "communication:mobile_phone",
              "communication", "tower:construction")

_cache: "OrderedDict[tuple, tuple[float, dict]]" = OrderedDict()
_cache_lock = threading.Lock()

ERR_TOO_LARGE = (f"התקרב למפה – האזור המבוקש גדול מדי לשכבת OSM "
                 f"(עד {MAX_SPAN_DEG}° לכל צד)")
ERR_UNAVAILABLE = "שרתי OpenStreetMap (Overpass) עמוסים כרגע – נסה שוב בעוד דקה"


class _MirrorError(Exception):
    pass


def _snap(south: float, west: float, north: float, east: float) -> tuple:
    """Snap the bbox outward to the GRID_DEG grid (epsilon guards float noise)."""
    k = 1.0 / GRID_DEG
    eps = 1e-9
    return (
        round(math.floor(south * k + eps) / k, 4),
        round(math.floor(west * k + eps) / k, 4),
        round(math.ceil(north * k - eps) / k, 4),
        round(math.ceil(east * k - eps) / k, 4),
    )


def build_query(s: float, w: float, n: float, e: float) -> str:
    bbox = f"{s},{w},{n},{e}"
    return (f"[out:json][timeout:{OVERPASS_QUERY_TIMEOUT_S}];("
            f'node["tower:type"="communication"]({bbox});'
            f'node["man_made"="mast"]["communication:mobile_phone"="yes"]({bbox});'
            f'node["man_made"="tower"]["communication"="mobile_phone"]({bbox});'
            ");out body;")


def _query_mirror(url: str, query: str) -> dict:
    body = urllib.parse.urlencode({"data": query}).encode()
    req = urllib.request.Request(url, data=body, headers={
        "User-Agent": USER_AGENT,
        "Content-Type": "application/x-www-form-urlencoded",
    })
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as resp:
            status = getattr(resp, "status", 200)
            if status == 429 or status >= 500:
                raise _MirrorError(f"HTTP {status}")
            raw = resp.read()
    except urllib.error.HTTPError as ex:        # 429 / 504 / 5xx / other 4xx
        raise _MirrorError(f"HTTP {ex.code}") from ex
    except (urllib.error.URLError, OSError) as ex:   # timeouts, DNS, resets
        raise _MirrorError(str(ex)) from ex
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as ex:   # Overpass returns HTML/XML on overload
        raise _MirrorError("non-JSON response") from ex
    if not isinstance(data, dict) or not isinstance(data.get("elements"), list):
        raise _MirrorError("unexpected payload")
    if data.get("remark") and not data["elements"]:  # runtime error / timeout inside Overpass
        raise _MirrorError(str(data["remark"])[:200])
    return data


def _fetch_overpass(snapped: tuple) -> tuple[list, str]:
    query = build_query(*snapped)
    errors = []
    for url in OVERPASS_MIRRORS:
        try:
            data = _query_mirror(url, query)
        except _MirrorError as ex:
            errors.append(f"{urlparse(url).hostname}: {ex}")
            continue
        elements = []
        for el in data["elements"]:
            lat, lon = el.get("lat"), el.get("lon")
            if lat is None or lon is None:
                continue
            tags = el.get("tags") or {}
            elements.append({
                "id": el.get("id"),
                "lat": lat,
                "lon": lon,
                "operator": tags.get("operator") or tags.get("operator:he"),
                "height": tags.get("height"),
                "tags": {k: tags[k] for k in TAG_SUBSET if k in tags},
            })
        return elements, urlparse(url).hostname
    print(f"⚠️  Overpass: all mirrors failed: {'; '.join(errors)}")
    raise HTTPException(503, detail=ERR_UNAVAILABLE)


def _cache_get(key: tuple):
    with _cache_lock:
        hit = _cache.get(key)
        if not hit:
            return None
        ts, payload = hit
        if time.time() - ts > CACHE_TTL_S:
            del _cache[key]
            return None
        _cache.move_to_end(key)
        return payload


def _cache_put(key: tuple, payload: dict) -> None:
    with _cache_lock:
        _cache[key] = (time.time(), payload)
        _cache.move_to_end(key)
        while len(_cache) > CACHE_MAX:
            _cache.popitem(last=False)


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()


@router.get("/")
@router.get("", include_in_schema=False)
def osm_towers(
    south: float = Query(..., ge=-90, le=90),
    west: float = Query(..., ge=-180, le=180),
    north: float = Query(..., ge=-90, le=90),
    east: float = Query(..., ge=-180, le=180),
    _: User = Depends(get_current_user),
):
    """Cellular towers from OSM inside the bbox (cached per 0.1° grid-snapped bbox)."""
    if south >= north or west >= east:
        raise HTTPException(400, detail="תיבת גבולות לא תקינה (south<north, west<east)")
    if (north - south) > MAX_SPAN_DEG or (east - west) > MAX_SPAN_DEG:
        raise HTTPException(400, detail=ERR_TOO_LARGE)

    snapped = _snap(south, west, north, east)
    cached = _cache_get(snapped)
    if cached is None:
        elements, source = _fetch_overpass(snapped)
        cached = {"elements": elements, "source": source}
        _cache_put(snapped, cached)
        was_cached = False
    else:
        was_cached = True

    inside = [el for el in cached["elements"]
              if south <= el["lat"] <= north and west <= el["lon"] <= east]
    return {
        "elements": inside,
        "count": len(inside),
        "source": cached["source"],
        "cached": was_cached,
        "bbox": {"south": snapped[0], "west": snapped[1],
                 "north": snapped[2], "east": snapped[3]},
    }
