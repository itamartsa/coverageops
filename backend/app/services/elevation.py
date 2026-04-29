"""
Point elevation lookup using Terrarium RGB tiles.
Encoding: elevation_m = (R * 256 + G + B/256) - 32768
"""
import math
import urllib.request
from io import BytesIO
from PIL import Image

ZOOM = 13          # ~10 m/pixel — precise enough for a point lookup
TILE_SIZE = 256


def _deg2tile(lat: float, lon: float, z: int) -> tuple[int, int]:
    lat_r = math.radians(lat)
    n = 2 ** z
    x = int((lon + 180.0) / 360.0 * n)
    y = int((1.0 - math.asinh(math.tan(lat_r)) / math.pi) / 2.0 * n)
    return x, y


def _deg2px_in_tile(lat: float, lon: float, z: int, tx: int, ty: int) -> tuple[int, int]:
    """Pixel coordinate inside the tile (0–255)."""
    n = 2 ** z
    px = ((lon + 180.0) / 360.0 * n - tx) * TILE_SIZE
    py = ((1.0 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2.0 * n - ty) * TILE_SIZE
    return int(px) % TILE_SIZE, int(py) % TILE_SIZE


def fetch_elevation(lat: float, lon: float) -> float:
    """
    Return the terrain elevation in metres at the given WGS-84 point.
    Returns 0.0 on any network or decode error.
    """
    try:
        tx, ty = _deg2tile(lat, lon, ZOOM)
        url = f"https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{ZOOM}/{tx}/{ty}.png"
        req = urllib.request.Request(url, headers={"User-Agent": "CoverageOps/1.0"})
        with urllib.request.urlopen(req, timeout=4) as resp:
            img = Image.open(BytesIO(resp.read())).convert("RGB")

        px, py = _deg2px_in_tile(lat, lon, ZOOM, tx, ty)
        r, g, b = img.getpixel((px, py))
        elevation = (r * 256 + g + b / 256) - 32768
        return round(elevation, 1)
    except Exception:
        return 0.0
