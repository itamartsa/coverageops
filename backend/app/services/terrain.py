"""
Terrain Elevation Grid Service
───────────────────────────────
Downloads Terrarium RGB elevation tiles and decodes them into 2-D numpy
elevation arrays (metres ASL). This module is the single shared tile layer
for the backend: the coverage engine, cross-section profiles, the topo
overlay and point-elevation lookups (``elevation.py``) all go through it.

Tile encoding (Terrarium format):
  elevation_m = (R * 256 + G + B / 256) - 32768

Public API
  fetch_tile(x, y, z)            -> (256, 256) float32 elevation | None
  fetch_tiles([(z, x, y), ...])  -> {(z, x, y): array | None}   (parallel)
  fetch_elevation_grid(...)      -> (H, W) float32 | None        (legacy shape)
  fetch_elevation_grid_ex(...)   -> ElevationGrid | None         (with metadata)
  clear_tile_cache(), tile_cache_info()
  DemAreaTooLarge                (ValueError) bbox exceeds tile/pixel budget

Grid geometry
  Output cell (r, c) is a plate-carrée cell of an equal-step lat/lon grid:
    Δlat = (ne_lat - sw_lat) / H,  Δlon = (ne_lon - sw_lon) / W
  and ``elev[r, c]`` is the elevation at the cell CENTRE
    lat_r = ne_lat - (r + 0.5) · Δlat,   lon_c = sw_lon + (c + 0.5) · Δlon.
  Source tiles are Web-Mercator, so each output row is sampled at the exact
  Mercator y of its centre latitude (rows are NOT uniform in Mercator space).

Honesty rules
  * Elevation is decoded to metres per tile BEFORE any resampling
    (resampling RGB and then decoding mixes the R/G/B channels and yields
    garbage at channel carry boundaries).
  * A grid is returned only if at least one tile was fetched; otherwise
    ``None`` — never a fake all-zeros DEM.
  * Pixels covered by tiles that failed to download are ``NaN`` in the
    output, never -32768 m.

Config (read with getattr from app.core.config.settings, so config.py does
not need to declare them):
  TILE_CACHE_SIZE       in-memory LRU size in tiles      (default 2048, ~256 KB/tile)
  MAX_DEM_TILES         tile budget per elevation grid   (default 64)
  TILE_FETCH_WORKERS    parallel download threads        (default 8)
  TILE_FETCH_TIMEOUT_S  per-tile HTTP timeout, seconds   (default 4)
  TILE_FETCH_DEADLINE_S total deadline per fetch_tiles   (default 20)
  MAX_DEM_PIXELS        hard cap on mosaic / output pixels (default 100M)
"""
from __future__ import annotations

import logging
import math
import urllib.request
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import dataclass
from functools import lru_cache
from io import BytesIO

import numpy as np
from PIL import Image

from app.core.config import settings

log = logging.getLogger(__name__)

TILE_SIZE     = 256
TERRARIUM_URL = "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"
USER_AGENT    = "CoverageOps/1.0"

MIN_ZOOM = 8     # ~550 m/px at lat 32°
MAX_ZOOM = 12    # ~38 m/px at lat 32°

# Web-Mercator ground resolution at the equator for zoom 0 (m/px, 256-px tiles)
_EQUATOR_M_PER_PX_Z0 = 156543.03392

TILE_CACHE_SIZE:       int   = int(getattr(settings, "TILE_CACHE_SIZE", 2048))
MAX_DEM_TILES:         int   = int(getattr(settings, "MAX_DEM_TILES", 64))
TILE_FETCH_WORKERS:    int   = int(getattr(settings, "TILE_FETCH_WORKERS", 8))
TILE_FETCH_TIMEOUT_S:  float = float(getattr(settings, "TILE_FETCH_TIMEOUT_S", 4.0))
TILE_FETCH_DEADLINE_S: float = float(getattr(settings, "TILE_FETCH_DEADLINE_S", 20.0))
MAX_DEM_PIXELS:        int   = int(getattr(settings, "MAX_DEM_PIXELS", 100_000_000))


# ── Data types ────────────────────────────────────────────────────────────────

@dataclass
class ElevationGrid:
    """
    Elevation grid plus provenance metadata.

    Attributes:
        elev:      (out_height, out_width) float32, metres ASL, row 0 = north.
                   NaN where the source tile could not be fetched.
        zoom:      Terrarium zoom level the grid was built from.
        m_per_px:  approximate native ground resolution of that zoom at the
                   bbox centre latitude (metres per source pixel).
        valid_pct: percentage (0–100) of output pixels that are not NaN.
        source:    short provenance tag, e.g. "terrarium/z12".
    """
    elev:      np.ndarray
    zoom:      int
    m_per_px:  float
    valid_pct: float
    source:    str


class DemAreaTooLarge(ValueError):
    """
    The requested bbox cannot be served within the DEM budget (tile count at
    the coarsest allowed zoom > max_tiles, or mosaic/output > MAX_DEM_PIXELS).
    Raised BEFORE any tile is downloaded.

    Attributes:
        tiles, max_tiles, zoom, pixels, max_pixels: the numbers involved.
        message_he: user-facing Hebrew message (safe to show in the UI).
    """
    def __init__(self, message: str, *, tiles: int, max_tiles: int, zoom: int,
                 pixels: int, max_pixels: int):
        super().__init__(message)
        self.tiles      = tiles
        self.max_tiles  = max_tiles
        self.zoom       = zoom
        self.pixels     = pixels
        self.max_pixels = max_pixels
        self.message_he = ("האזור המבוקש גדול מדי לחישוב מודל פני השטח. "
                           "יש להקטין את האזור או את רדיוס החישוב.")


class _TileFetchError(Exception):
    """Raised inside the cached loader so failures are never memoised."""


# ── Tile maths ────────────────────────────────────────────────────────────────

def _deg2num(lat: float, lon: float, z: int) -> tuple[int, int]:
    lat_r = math.radians(lat)
    n     = 2.0 ** z
    x     = int((lon + 180.0) / 360.0 * n)
    y     = int((1.0 - math.asinh(math.tan(lat_r)) / math.pi) / 2.0 * n)
    return x, y


def _deg2px(lat: float, lon: float, z: int) -> tuple[float, float]:
    """Global (fractional) pixel coordinate of a WGS-84 point at zoom z."""
    lat_r = math.radians(lat)
    n     = 2.0 ** z
    px    = ((lon + 180.0) / 360.0 * n) * TILE_SIZE
    py    = ((1.0 - math.asinh(math.tan(lat_r)) / math.pi) / 2.0 * n) * TILE_SIZE
    return px, py


def _tile_range(sw_lat: float, sw_lon: float,
                ne_lat: float, ne_lon: float,
                z: int) -> tuple[int, int, int, int]:
    """Return (x_min, x_max, y_min, y_max) tile indices covering the bbox."""
    x_min, y_max = _deg2num(sw_lat, sw_lon, z)
    x_max, y_min = _deg2num(ne_lat, ne_lon, z)
    n = 2 ** z
    x_min, x_max = max(0, x_min), min(n - 1, x_max)
    y_min, y_max = max(0, y_min), min(n - 1, y_max)
    return x_min, x_max, y_min, y_max


def _tile_count(sw_lat: float, sw_lon: float,
                ne_lat: float, ne_lon: float, z: int) -> int:
    x_min, x_max, y_min, y_max = _tile_range(sw_lat, sw_lon, ne_lat, ne_lon, z)
    return (x_max - x_min + 1) * (y_max - y_min + 1)


def _choose_zoom(sw_lat: float, sw_lon: float,
                 ne_lat: float, ne_lon: float,
                 max_tiles: int | None = None) -> int:
    """
    Pick the highest zoom in [MIN_ZOOM, MAX_ZOOM] whose tile mosaic for the
    bbox needs at most ``max_tiles`` tiles (default settings.MAX_DEM_TILES).
    If even MIN_ZOOM exceeds the budget, MIN_ZOOM is returned — callers must
    re-check the count (fetch_elevation_grid_ex raises DemAreaTooLarge).
    """
    budget = MAX_DEM_TILES if max_tiles is None else max_tiles
    for z in range(MAX_ZOOM, MIN_ZOOM - 1, -1):
        if _tile_count(sw_lat, sw_lon, ne_lat, ne_lon, z) <= budget:
            return z
    return MIN_ZOOM


def _m_per_px(lat: float, z: int) -> float:
    """Approximate Web-Mercator ground resolution (m/px) at latitude ``lat``."""
    return _EQUATOR_M_PER_PX_Z0 * math.cos(math.radians(lat)) / (2 ** z)


# ── Tile fetch + decode ───────────────────────────────────────────────────────

def decode_terrarium(rgb: np.ndarray) -> np.ndarray:
    """
    Decode a (..., 3) Terrarium RGB array to float32 metres:
        elevation_m = R * 256 + G + B / 256 - 32768
    """
    a = np.asarray(rgb, dtype=np.float32)
    return (a[..., 0] * 256.0 + a[..., 1] + a[..., 2] / 256.0 - 32768.0).astype(np.float32)


def _download_tile_png(x: int, y: int, z: int, timeout: float) -> bytes:
    """Raw HTTP GET of one Terrarium PNG (isolated so tests can mock it)."""
    url = TERRARIUM_URL.format(z=z, x=x, y=y)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


@lru_cache(maxsize=TILE_CACHE_SIZE)
def _load_tile_cached(z: int, x: int, y: int) -> np.ndarray:
    """
    Download + decode one tile. Raises _TileFetchError on failure so that
    lru_cache stores successes only (exceptions are never memoised).
    lru_cache's bookkeeping is thread-safe; two threads racing on the same
    uncached key may both download it, which is harmless.
    """
    try:
        data = _download_tile_png(x, y, z, TILE_FETCH_TIMEOUT_S)
        img  = Image.open(BytesIO(data)).convert("RGB")
        if img.size != (TILE_SIZE, TILE_SIZE):
            raise ValueError(f"unexpected tile size {img.size}")
        elev = decode_terrarium(np.asarray(img))
    except Exception as exc:
        raise _TileFetchError(f"terrarium z{z}/{x}/{y}: {exc}") from exc
    elev.flags.writeable = False     # shared via cache — callers must copy to mutate
    return elev


def fetch_tile(x: int, y: int, z: int) -> np.ndarray | None:
    """
    Return the decoded (256, 256) float32 elevation tile (metres ASL, row 0 =
    north) or None if it could not be downloaded/decoded.

    Successful tiles are kept in a module-level LRU cache (TILE_CACHE_SIZE);
    failures are not cached, so a later call retries the download.
    The returned array is read-only (it is shared with the cache).
    """
    try:
        return _load_tile_cached(z, x, y)
    except _TileFetchError as exc:
        log.warning("Tile fetch failed: %s", exc)
        return None


def fetch_tiles(
    coords: list[tuple[int, int, int]],
    max_workers: int | None = None,
    deadline_s: float | None = None,
) -> dict[tuple[int, int, int], np.ndarray | None]:
    """
    Fetch many tiles in parallel.

    Args:
        coords:      list of (z, x, y) tile coordinates.
        max_workers: thread pool size (default settings.TILE_FETCH_WORKERS).
        deadline_s:  total wall-clock budget (default TILE_FETCH_DEADLINE_S);
                     each individual download is also limited by
                     TILE_FETCH_TIMEOUT_S.

    Returns:
        {(z, x, y): array | None} for every requested coordinate. Tiles that
        failed or did not finish before the deadline map to None.
    """
    unique = list(dict.fromkeys(coords))
    result: dict[tuple[int, int, int], np.ndarray | None] = {c: None for c in unique}
    if not unique:
        return result

    workers  = max(1, min(max_workers or TILE_FETCH_WORKERS, len(unique)))
    deadline = TILE_FETCH_DEADLINE_S if deadline_s is None else deadline_s

    pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="terrarium")
    try:
        futures = {pool.submit(fetch_tile, x, y, z): (z, x, y) for (z, x, y) in unique}
        done, not_done = wait(futures, timeout=deadline)
        for fut in done:
            try:
                result[futures[fut]] = fut.result()
            except Exception as exc:                       # pragma: no cover
                log.warning("Tile worker error %s: %s", futures[fut], exc)
        if not_done:
            log.warning("Tile fetch deadline (%.1fs) hit: %d/%d tiles unfinished",
                        deadline, len(not_done), len(unique))
    finally:
        # Don't block on stragglers past the deadline; they finish in the
        # background (and still populate the cache if they succeed).
        pool.shutdown(wait=False, cancel_futures=True)
    return result


def clear_tile_cache() -> None:
    """Drop all cached tiles."""
    _load_tile_cached.cache_clear()


def tile_cache_info():
    """functools cache statistics (hits, misses, maxsize, currsize)."""
    return _load_tile_cached.cache_info()


def _check_budget(n_tiles: int, budget: int, z: int,
                  out_width: int, out_height: int) -> None:
    """Raise DemAreaTooLarge if the tile count or pixel counts are over budget."""
    mosaic_px = n_tiles * TILE_SIZE * TILE_SIZE
    out_px    = int(out_width) * int(out_height)
    if n_tiles > budget or mosaic_px > MAX_DEM_PIXELS or out_px > MAX_DEM_PIXELS:
        raise DemAreaTooLarge(
            f"DEM area too large: {n_tiles} tiles at z{z} (budget {budget}), "
            f"mosaic {mosaic_px} px / output {out_px} px (cap {MAX_DEM_PIXELS})",
            tiles=n_tiles, max_tiles=budget, zoom=z,
            pixels=max(mosaic_px, out_px), max_pixels=MAX_DEM_PIXELS)


def _lerp_index(f: np.ndarray, size: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    For fractional pixel-centre coordinates ``f`` (pixel k's centre at k),
    return (i0, i1, t) with i0/i1 clamped to [0, size-1] and t in [0, 1] so
    that value ≈ (1-t)·a[i0] + t·a[i1]. Edges clamp (no extrapolation).
    """
    i0 = np.floor(f).astype(np.int64)
    t  = (f - i0).astype(np.float32)
    lo_out = i0 < 0
    hi_out = i0 >= size - 1
    t[lo_out | hi_out] = 0.0
    i0 = np.clip(i0, 0, size - 1)
    i1 = np.clip(i0 + 1, 0, size - 1)
    return i0, i1, t


def _resample_to_latlon(
    mosaic: np.ndarray, valid: np.ndarray, z: int, x_min: int, y_min: int,
    sw_lat: float, sw_lon: float, ne_lat: float, ne_lon: float,
    out_width: int, out_height: int,
) -> tuple[np.ndarray, np.ndarray]:
    """
    Sample a Web-Mercator mosaic (top-left tile = (x_min, y_min) at zoom z)
    onto an equal-step lat/lon grid of (out_height, out_width) cells.

    Output cell (r, c) is evaluated at its centre
        lat_r = ne_lat - (r + 0.5)·Δlat,  lon_c = sw_lon + (c + 0.5)·Δlon
    projected to its exact mosaic pixel position (rows individually through
    the Mercator y formula; columns are linear in lon).

    Returns (elev float32 with NaN where invalid, ok bool mask).
    """
    H, W = mosaic.shape
    n    = 2.0 ** z
    dlat = (ne_lat - sw_lat) / out_height
    dlon = (ne_lon - sw_lon) / out_width

    lat_c = ne_lat - (np.arange(out_height, dtype=np.float64) + 0.5) * dlat
    lon_c = sw_lon + (np.arange(out_width,  dtype=np.float64) + 0.5) * dlon
    lat_c = np.clip(lat_c, -85.05112878, 85.05112878)

    # Global pixel coords (pixel k covers [k, k+1)), then mosaic-local.
    gx = (lon_c + 180.0) / 360.0 * n * TILE_SIZE - x_min * TILE_SIZE
    gy = ((1.0 - np.arcsinh(np.tan(np.radians(lat_c))) / np.pi) / 2.0 * n * TILE_SIZE
          - y_min * TILE_SIZE)

    # Validity: nearest = the source pixel containing the centre point.
    rn = np.clip(np.floor(gy).astype(np.int64), 0, H - 1)
    cn = np.clip(np.floor(gx).astype(np.int64), 0, W - 1)
    mask = valid[np.ix_(rn, cn)] > 0.5

    # Elevation: bilinear between pixel centres (centre of pixel k is k+0.5).
    r0, r1, ty = _lerp_index(gy - 0.5, H)
    c0, c1, tx = _lerp_index(gx - 0.5, W)
    ty = ty[:, None]
    tx = tx[None, :]
    w00 = (1 - ty) * (1 - tx)
    w01 = (1 - ty) * tx
    w10 = ty * (1 - tx)
    w11 = ty * tx

    mv = mosaic * valid
    num = (w00 * mv[np.ix_(r0, c0)] + w01 * mv[np.ix_(r0, c1)]
           + w10 * mv[np.ix_(r1, c0)] + w11 * mv[np.ix_(r1, c1)])
    den = (w00 * valid[np.ix_(r0, c0)] + w01 * valid[np.ix_(r0, c1)]
           + w10 * valid[np.ix_(r1, c0)] + w11 * valid[np.ix_(r1, c1)])

    ok = mask & (den > 1e-6)
    with np.errstate(invalid="ignore", divide="ignore"):
        elev = np.where(ok, num / den, np.nan).astype(np.float32)
    return elev, ok


# ── Elevation grid ────────────────────────────────────────────────────────────

def fetch_elevation_grid_ex(
    sw_lat: float, sw_lon: float,
    ne_lat: float, ne_lon: float,
    out_width:  int,
    out_height: int,
    max_tiles:  int | None = None,
) -> ElevationGrid | None:
    """
    Build an elevation grid for the bbox, resampled to (out_height, out_width).

    Pipeline:
      1. choose zoom (highest in [8, 12] within the ``max_tiles`` budget)
      2. fetch covering tiles in parallel (cached)
      3. mosaic decoded elevations (float32) + a validity mask
      4. resample to the equal-step lat/lon output grid, sampling each cell
         CENTRE at its exact Web-Mercator pixel position:
           elevation — bilinear, weighted by the validity mask so missing
                       tiles never bleed into valid neighbours
           validity  — nearest (the source pixel containing the centre);
                       invalid output pixels → NaN

    Returns None if no tile could be fetched at all.
    Raises DemAreaTooLarge (before downloading anything) if the bbox needs
    more than ``max_tiles`` tiles even at MIN_ZOOM, or the mosaic/output
    would exceed MAX_DEM_PIXELS.
    """
    budget = MAX_DEM_TILES if max_tiles is None else max_tiles
    z = _choose_zoom(sw_lat, sw_lon, ne_lat, ne_lon, budget)
    x_min, x_max, y_min, y_max = _tile_range(sw_lat, sw_lon, ne_lat, ne_lon, z)
    w_tiles = x_max - x_min + 1
    h_tiles = y_max - y_min + 1
    _check_budget(w_tiles * h_tiles, budget, z, out_width, out_height)

    coords = [(z, tx, ty) for ty in range(y_min, y_max + 1)
                          for tx in range(x_min, x_max + 1)]
    tiles  = fetch_tiles(coords)

    mosaic = np.zeros((h_tiles * TILE_SIZE, w_tiles * TILE_SIZE), dtype=np.float32)
    valid  = np.zeros(mosaic.shape, dtype=np.float32)
    n_ok = 0
    for (tz, tx, ty), tile in tiles.items():
        if tile is None:
            continue
        r0 = (ty - y_min) * TILE_SIZE
        c0 = (tx - x_min) * TILE_SIZE
        mosaic[r0:r0 + TILE_SIZE, c0:c0 + TILE_SIZE] = tile
        valid[r0:r0 + TILE_SIZE, c0:c0 + TILE_SIZE] = 1.0
        n_ok += 1

    if n_ok == 0:
        log.warning("No terrain tiles available for bbox (%.4f,%.4f)-(%.4f,%.4f) z%d",
                    sw_lat, sw_lon, ne_lat, ne_lon, z)
        return None

    elev, ok = _resample_to_latlon(mosaic, valid, z, x_min, y_min,
                                   sw_lat, sw_lon, ne_lat, ne_lon,
                                   out_width, out_height)

    valid_pct = 100.0 * float(ok.mean())
    if n_ok < len(tiles):
        log.warning("Terrain grid z%d: %d/%d tiles missing, %.1f%% pixels valid",
                    z, len(tiles) - n_ok, len(tiles), valid_pct)

    return ElevationGrid(
        elev=elev,
        zoom=z,
        m_per_px=_m_per_px((sw_lat + ne_lat) / 2.0, z),
        valid_pct=valid_pct,
        source=f"terrarium/z{z}",
    )


def fetch_elevation_grid(
    sw_lat: float, sw_lon: float,
    ne_lat: float, ne_lon: float,
    out_width:  int,
    out_height: int,
    max_tiles:  int | None = None,
) -> np.ndarray | None:
    """
    Return a (out_height, out_width) float32 array of terrain elevations in
    metres ASL for the given bounding box (row 0 = north).

    Returns None if no tile could be downloaded (callers fall back to their
    no-terrain model). Pixels covered by individual failed tiles are NaN.

    Args:
        sw_lat, sw_lon: south-west corner (WGS-84)
        ne_lat, ne_lon: north-east corner (WGS-84)
        out_width:      desired columns (should match coverage grid)
        out_height:     desired rows    (should match coverage grid)
        max_tiles:      tile budget (default settings.MAX_DEM_TILES)

    Raises:
        DemAreaTooLarge: bbox exceeds the tile/pixel budget (nothing fetched).
    """
    grid = fetch_elevation_grid_ex(sw_lat, sw_lon, ne_lat, ne_lon,
                                   out_width, out_height, max_tiles)
    return None if grid is None else grid.elev
