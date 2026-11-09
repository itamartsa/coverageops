"""
Point elevation lookup using Terrarium RGB tiles.
Encoding: elevation_m = (R * 256 + G + B/256) - 32768

Tiles come from the shared, cached tile layer in ``app.services.terrain``.

Pixel math (Web-Mercator, 256-px tiles, zoom z, n = 2**z):
  gx = (lon + 180) / 360 * n                         # global tile-x, fractional
  gy = (1 - asinh(tan(lat)) / pi) / 2 * n            # global tile-y, fractional
  tile  = (floor(gx), floor(gy))
  pixel = (floor((gx - tx) * 256), floor((gy - ty) * 256)), clamped to 0..255
Pixel (px, py) covers the half-open area [px, px+1) × [py, py+1) of the tile,
so floor() selects the pixel containing the point; the array is indexed
[row = py, col = px].
"""
from __future__ import annotations

import math

from app.services.terrain import TILE_SIZE, fetch_tile

ZOOM = 13          # ~10 m/pixel at lat 32° — precise enough for a point lookup


def _deg2tile(lat: float, lon: float, z: int) -> tuple[int, int]:
    lat_r = math.radians(lat)
    n = 2 ** z
    x = int(math.floor((lon + 180.0) / 360.0 * n))
    y = int(math.floor((1.0 - math.asinh(math.tan(lat_r)) / math.pi) / 2.0 * n))
    return min(max(x, 0), n - 1), min(max(y, 0), n - 1)


def _deg2px_in_tile(lat: float, lon: float, z: int, tx: int, ty: int) -> tuple[int, int]:
    """Pixel coordinate (col, row) inside the tile, each in 0–255."""
    n = 2 ** z
    px = ((lon + 180.0) / 360.0 * n - tx) * TILE_SIZE
    py = ((1.0 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2.0 * n - ty) * TILE_SIZE
    clamp = lambda v: min(max(int(math.floor(v)), 0), TILE_SIZE - 1)
    return clamp(px), clamp(py)


def fetch_elevation(lat: float, lon: float) -> float | None:
    """
    Return the terrain elevation in metres ASL (rounded to 0.1 m) at the given
    WGS-84 point, or None if the tile could not be fetched/decoded.
    """
    try:
        tx, ty = _deg2tile(lat, lon, ZOOM)
        tile = fetch_tile(tx, ty, ZOOM)
        if tile is None:
            return None
        px, py = _deg2px_in_tile(lat, lon, ZOOM, tx, ty)
        elevation = float(tile[py, px])
        if not math.isfinite(elevation):
            return None
        return round(elevation, 1)
    except Exception:
        return None
