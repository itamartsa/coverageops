"""
Terrain Elevation Grid Service
───────────────────────────────
Downloads Terrarium RGB elevation tiles for a bounding box and decodes
them into a 2-D numpy elevation array (metres ASL).

Used by the coverage engine to compute real terrain-based path loss instead
of the pseudo-random simulation.

Tile encoding (Terrarium format):
  elevation_m = (R * 256 + G + B / 256) - 32768
"""
import math
import urllib.request
from io import BytesIO

import numpy as np
from PIL import Image

TILE_SIZE = 256
TERRARIUM_URL = "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"
USER_AGENT    = "CoverageOps/1.0"


def _deg2num(lat: float, lon: float, z: int) -> tuple[int, int]:
    lat_r = math.radians(lat)
    n     = 2.0 ** z
    x     = int((lon + 180.0) / 360.0 * n)
    y     = int((1.0 - math.asinh(math.tan(lat_r)) / math.pi) / 2.0 * n)
    return x, y


def _deg2px(lat: float, lon: float, z: int) -> tuple[float, float]:
    lat_r = math.radians(lat)
    n     = 2.0 ** z
    px    = ((lon + 180.0) / 360.0 * n) * TILE_SIZE
    py    = ((1.0 - math.asinh(math.tan(lat_r)) / math.pi) / 2.0 * n) * TILE_SIZE
    return px, py


def _fetch_tile(x: int, y: int, z: int) -> Image.Image | None:
    url = TERRARIUM_URL.format(z=z, x=x, y=y)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=4) as resp:
            return Image.open(BytesIO(resp.read())).convert("RGB")
    except Exception:
        return None


def _choose_zoom(sw_lat: float, sw_lon: float,
                 ne_lat: float, ne_lon: float) -> int:
    """
    Pick a zoom level so the tile mosaic stays under ~16 tiles.
    Higher zoom = more detail but more tiles / slower.
    """
    span = max(abs(ne_lat - sw_lat), abs(ne_lon - sw_lon))
    if span < 0.05: return 12   # ~5 km box
    if span < 0.2:  return 11
    if span < 0.5:  return 10
    if span < 2.0:  return 9
    return 8


def fetch_elevation_grid(
    sw_lat: float, sw_lon: float,
    ne_lat: float, ne_lon: float,
    out_width:  int,
    out_height: int,
) -> np.ndarray:
    """
    Return a (out_height, out_width) float32 array of terrain elevations
    in metres ASL for the given bounding box.

    Falls back to an all-zero array if tiles cannot be downloaded.

    Args:
        sw_lat, sw_lon: south-west corner (WGS-84)
        ne_lat, ne_lon: north-east corner (WGS-84)
        out_width:      desired columns (should match coverage grid)
        out_height:     desired rows    (should match coverage grid)
    """
    z = _choose_zoom(sw_lat, sw_lon, ne_lat, ne_lon)

    x_min, y_max = _deg2num(sw_lat, sw_lon, z)
    x_max, y_min = _deg2num(ne_lat, ne_lon, z)

    w_tiles = x_max - x_min + 1
    h_tiles = y_max - y_min + 1

    # Assemble tile mosaic
    mosaic = Image.new("RGB", (w_tiles * TILE_SIZE, h_tiles * TILE_SIZE))
    any_tile = False
    for tx in range(x_min, x_max + 1):
        for ty in range(y_min, y_max + 1):
            tile = _fetch_tile(tx, ty, z)
            if tile:
                mosaic.paste(tile, ((tx - x_min) * TILE_SIZE,
                                    (ty - y_min) * TILE_SIZE))
                any_tile = True

    if not any_tile:
        return np.zeros((out_height, out_width), dtype=np.float32)

    # Crop to exact bbox pixel bounds
    px_sw_x, px_sw_y = _deg2px(sw_lat, sw_lon, z)
    px_ne_x, px_ne_y = _deg2px(ne_lat, ne_lon, z)

    left   = px_sw_x - x_min * TILE_SIZE
    right  = px_ne_x - x_min * TILE_SIZE
    top    = px_ne_y - y_min * TILE_SIZE
    bottom = px_sw_y - y_min * TILE_SIZE

    cropped = mosaic.crop((int(left), int(top), int(right), int(bottom)))

    # Resize to match the coverage grid resolution
    # Note: NEAREST avoids blurring elevation boundaries
    resized = cropped.resize((out_width, out_height), Image.Resampling.BILINEAR)

    # Decode Terrarium RGB → elevation
    arr  = np.array(resized, dtype=np.float32)
    elev = arr[:, :, 0] * 256.0 + arr[:, :, 1] + arr[:, :, 2] / 256.0 - 32768.0

    return elev
