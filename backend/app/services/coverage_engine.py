"""
Coverage Calculation Engine
────────────────────────────
Implements RF propagation modelling for two use-cases:

  1. Area coverage  – calculate_coverage()
     Sweeps a lat/lon bounding box with a resolution×resolution grid.
     When a real elevation grid is supplied the engine uses vectorised
     ITU-R P.526 knife-edge diffraction; otherwise falls back to a
     seeded pseudo-random terrain simulation.

  2. Cross-section  – calculate_cross_section()
     Samples RSSI along a multi-segment route, detects dead zones and
     produces operational risk scoring + recommendations.
     Uses real terrain profile + knife-edge diffraction when an elevation
     grid is available.

Propagation model
─────────────────
  RSSI = Tx_power – FSPL(d, f) – DiffractionLoss – ClutterLoss + HeightGain

  FSPL             : Free-Space Path Loss (Friis, dB)
  DiffractionLoss  : ITU-R P.526-15 single knife-edge, worst obstacle
  ClutterLoss      : statistical building/vegetation loss (DSM mode only)
  HeightGain       : antenna AGL height advantage over 1.5 m baseline

References
──────────
  ITU-R P.526-15  – Propagation by diffraction
  ITU-R P.525-4   – Calculation of free-space attenuation
"""
import math
import time
from dataclasses import dataclass
from typing import List, Optional

import numpy as np

from app.core.config import settings


# ── Constants ──────────────────────────────────────────────────────────────────

RX_HEIGHT_AGL  = 1.5    # assumed receiver height above ground (m) — handheld
PROFILE_STEPS  = 24     # profile sample points between tx and rx (area coverage)
XS_PROFILE_PTS = 30     # profile points for cross-section analysis
DSM_CLUTTER_DB = 9.0    # median building/vegetation clutter loss for DSM mode


# ── Data classes ───────────────────────────────────────────────────────────────

@dataclass
class SiteParams:
    lat:          float   # WGS-84 latitude
    lon:          float   # WGS-84 longitude
    ant_height:   float   # metres above ground (AGL)
    elevation_m:  float   # site elevation above sea level (ASL), metres
    frequency:    int     # MHz
    tx_power:     float   # dBm EIRP
    rx_threshold: float   # minimum receivable signal, dBm
    max_radius:   float   # analysis radius cap, km


@dataclass
class BoundingBox:
    sw_lat: float
    sw_lon: float
    ne_lat: float
    ne_lon: float


@dataclass
class CoverageCell:
    lat:   float
    lon:   float
    rssi:  float   # dBm
    level: str     # excellent / good / medium / weak / marginal / none


@dataclass
class CoverageStats:
    cells:        List[CoverageCell]
    covered_pct:  float
    rssi_avg:     float
    rssi_max:     float
    rssi_min:     float
    duration_sec: float
    geojson:      dict


@dataclass
class CrossSectionPoint:
    dist_along:     float   # km from route start
    dist_from_site: float   # km from antenna
    lat:            float
    lon:            float
    rssi:           float   # dBm
    level:          str


@dataclass
class DeadZone:
    start_km:  float
    end_km:    float
    length_km: float
    start_lat: float
    start_lon: float
    end_lat:   float
    end_lon:   float


@dataclass
class CrossSectionStats:
    site_name:           str
    mode:                str
    waypoints:           list
    total_length_km:     float
    covered_pct:         float
    rssi_min:            float
    rssi_max:            float
    rssi_avg:            float
    risk_level:          str     # LOW / MEDIUM / HIGH / CRITICAL
    points:              List[CrossSectionPoint]
    dead_zones:          List[DeadZone]
    signal_distribution: dict
    recommendations:     List[str]
    duration_sec:        float


# ── RSSI classification ────────────────────────────────────────────────────────

def classify_rssi(rssi: float) -> str:
    """Map received signal strength (dBm) to a quality level string."""
    if rssi > -70:   return "excellent"
    if rssi > -80:   return "good"
    if rssi > -90:   return "medium"
    if rssi > -100:  return "weak"
    if rssi > -110:  return "marginal"
    return "none"


LEVEL_COLORS = {
    "excellent": "#00ff88",
    "good":      "#7dff6b",
    "medium":    "#ffe600",
    "weak":      "#ff8c00",
    "marginal":  "#ff3b5c",
    "none":      None,
}


# ── Basic propagation helpers (scalar) ────────────────────────────────────────

def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two WGS-84 points (km)."""
    R  = 6371.0
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a  = math.sin(dp / 2)**2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def fspl_db(dist_km: float, freq_mhz: int) -> float:
    """Free Space Path Loss: FSPL = 20·log₁₀(d_m) + 20·log₁₀(f_hz) − 147.55"""
    if dist_km <= 0:
        return 0.0
    return (20 * math.log10(dist_km * 1000)
            + 20 * math.log10(freq_mhz * 1e6)
            - 147.55)


def height_gain_db(ant_height_agl: float) -> float:
    """
    Antenna height gain above 1.5 m ground reference.
      Gain = 20·log₁₀(h / 1.5)
    """
    return 20 * math.log10(max(ant_height_agl, 1.5) / 1.5)


# ── ITU-R P.526 knife-edge diffraction (scalar, single obstacle) ──────────────

def _knife_edge_loss_db(nu: float) -> float:
    """
    Diffraction loss for Fresnel-Kirchhoff parameter ν (ITU-R P.526-15 §4.1).

      ν < −0.78  → 0 dB   (clear first Fresnel zone, negligible loss)
      −0.78 ≤ ν ≤ 2.4  → 6.9 + 20·log₁₀(√((ν−0.1)²+1) + ν − 0.1)
      ν > 2.4    → 13.0 + 20·log₁₀(ν)
    """
    if nu < -0.78:
        return 0.0
    if nu <= 2.4:
        inner = math.sqrt((nu - 0.1)**2 + 1) + nu - 0.1
        return 6.9 + 20 * math.log10(max(inner, 1e-9))
    return 13.0 + 20 * math.log10(max(nu, 1e-9))


# ── Vectorised helpers (numpy) ─────────────────────────────────────────────────

def _haversine_km_vec(lat1: float, lon1: float,
                      lat2: np.ndarray, lon2: np.ndarray) -> np.ndarray:
    """Vectorised haversine: scalar site vs. (R, R) cell arrays → (R, R) km."""
    R  = 6371.0
    p1 = math.radians(lat1)
    p2 = np.radians(lat2)
    dp = np.radians(lat2 - lat1)
    dl = np.radians(lon2 - lon1)
    a  = np.sin(dp / 2)**2 + math.cos(p1) * np.cos(p2) * np.sin(dl / 2)**2
    return R * 2 * np.arctan2(np.sqrt(a), np.sqrt(1 - a))


def _fspl_db_vec(dist_km: np.ndarray, freq_mhz: int) -> np.ndarray:
    """Vectorised FSPL over a distance array."""
    d = np.maximum(dist_km * 1000, 1.0)
    return 20 * np.log10(d) + 20 * math.log10(freq_mhz * 1e6) - 147.55


def _bilinear(grid: np.ndarray, row_f: np.ndarray, col_f: np.ndarray) -> np.ndarray:
    """
    Vectorised bilinear interpolation into `grid` (H × W).
    row_f, col_f are floating-point grid coordinates (any shape).
    Out-of-bounds indices are clamped; caller should set invalid values after.
    """
    H, W  = grid.shape
    r0    = np.clip(row_f.astype(np.int32), 0, H - 1)
    c0    = np.clip(col_f.astype(np.int32), 0, W - 1)
    r1    = np.minimum(r0 + 1, H - 1)
    c1    = np.minimum(c0 + 1, W - 1)
    fr    = row_f - r0.astype(float)
    fc    = col_f - c0.astype(float)
    return (grid[r0, c0] * (1 - fc) * (1 - fr)
            + grid[r0, c1] * fc       * (1 - fr)
            + grid[r1, c0] * (1 - fc) * fr
            + grid[r1, c1] * fc       * fr)


def _knife_edge_loss_vec(nu: np.ndarray) -> np.ndarray:
    """Vectorised ITU-R P.526 knife-edge loss (same formula as scalar version)."""
    loss   = np.zeros_like(nu)
    m1     = (nu >= -0.78) & (nu <= 2.4)
    m2     = nu > 2.4
    inner  = np.sqrt(np.maximum((nu - 0.1)**2 + 1, 1e-18)) + nu - 0.1
    loss   = np.where(m1, 6.9 + 20 * np.log10(np.maximum(inner, 1e-9)), loss)
    loss   = np.where(m2, 13.0 + 20 * np.log10(np.maximum(nu,   1e-9)), loss)
    return loss


# ── Vectorised diffraction over full coverage grid ────────────────────────────

def _diffraction_grid(
    site_lat:   float,
    site_lon:   float,
    site_h_asl: float,           # tx ASL + AGL (metres)
    lat_grid:   np.ndarray,      # (R, R) cell latitudes
    lon_grid:   np.ndarray,      # (R, R) cell longitudes
    cell_elev:  np.ndarray,      # (R, R) cell terrain ASL (metres)
    dist_grid:  np.ndarray,      # (R, R) site→cell distances (km)
    freq_mhz:   int,
    elev_grid:  np.ndarray,      # (R, R) full elevation grid for interpolation
    bbox:       BoundingBox,
) -> np.ndarray:
    """
    Compute ITU-R P.526 knife-edge diffraction loss for every cell in the grid.

    For each of PROFILE_STEPS intermediate points along the path from site to
    cell we:
      1. Interpolate the terrain elevation from elev_grid
      2. Compute the LOS height at that point
      3. Compute the first Fresnel-zone radius
      4. Derive the Fresnel-Kirchhoff ν parameter
    Then take the WORST (maximum ν) obstacle per cell and convert to dB loss.

    Returns: (R, R) float32 array of diffraction loss in dB.
    """
    R          = lat_grid.shape[0]
    wavelength = 300.0 / freq_mhz          # metres  (c = 3×10⁸ m/s)
    lat_span   = bbox.ne_lat - bbox.sw_lat
    lon_span   = bbox.ne_lon - bbox.sw_lon

    rx_h_asl   = cell_elev + RX_HEIGHT_AGL  # (R, R)
    dist_m     = dist_grid * 1000           # (R, R) in metres

    # Accumulate worst ν across all profile steps
    max_nu = np.full((R, R), -10.0, dtype=np.float32)

    t_vals = np.linspace(0, 1, PROFILE_STEPS + 2)[1:-1]  # exclude tx/rx endpoints

    for t in t_vals:
        # Intermediate lat/lon: (R, R)
        lat_t = site_lat + t * (lat_grid - site_lat)
        lon_t = site_lon + t * (lon_grid - site_lon)

        # Map to fractional grid indices (row 0 = north edge of bbox)
        row_f = (bbox.ne_lat - lat_t) / lat_span * R
        col_f = (lon_t - bbox.sw_lon) / lon_span * R
        inside = ((row_f >= 0) & (row_f < R) & (col_f >= 0) & (col_f < R))

        terrain_t = _bilinear(elev_grid, row_f, col_f)
        # For points outside the grid, linearly interpolate site↔cell elevation
        terrain_linear = site_h_asl + t * (rx_h_asl - site_h_asl)
        terrain_t      = np.where(inside, terrain_t, terrain_linear)

        # LOS height at this fractional distance
        los_h = site_h_asl + t * (rx_h_asl - site_h_asl)

        # Obstacle clearance above LOS (positive = blocks)
        clearance = terrain_t - los_h

        # First Fresnel zone radius at this point
        d1   = t * dist_m
        d2   = (1 - t) * dist_m
        denom = np.maximum(d1 + d2, 1e-6)
        r1   = np.sqrt(np.maximum(wavelength * d1 * d2 / denom, 1e-9))

        nu = clearance / r1
        max_nu = np.maximum(max_nu, nu)

    return _knife_edge_loss_vec(max_nu)


# ── Pseudo-random fallback (no DEM available) ──────────────────────────────────

def _pseudorandom_terrain_loss(
    lat_grid: np.ndarray,
    lon_grid: np.ndarray,
    seed:     int,
    mode:     str,
    relief:   float,
) -> np.ndarray:
    """
    Deterministic pseudo-random terrain loss used when no elevation data is
    available. Loss is repeatable: same site → same grid pattern.

    DTM: 0–15 dB base, attenuated by site elevation relief (0–1).
    DSM: additional 0–20 dB clutter.
    """
    i = (np.abs(lat_grid) * 1000).astype(int) % 200
    j = (np.abs(lon_grid) * 1000).astype(int) % 200

    x  = np.sin(i * 127.1 + j * 311.7 + seed * 0.01) * 43758.5453
    r  = x - np.floor(x)
    loss = r * 15 * (1.0 - 0.5 * relief)

    if mode == "DSM":
        bx = np.sin(i * 200.1 + j * 411.7 + seed * 0.02) * 43758.5453
        br = bx - np.floor(bx)
        loss = loss + br * 20

    return loss.astype(np.float32)


# ── Area coverage ──────────────────────────────────────────────────────────────

def calculate_coverage(
    site:       SiteParams,
    bbox:       BoundingBox,
    mode:       str,
    resolution: int = None,
    elev_grid:  Optional[np.ndarray] = None,
) -> CoverageStats:
    """
    Compute RF coverage over a bounding box.

    When elev_grid is provided (real Terrarium DEM) the engine runs a
    fully-vectorised ITU-R P.526 knife-edge diffraction model.
    Otherwise it falls back to a seeded pseudo-random terrain simulation.

    Args:
        site:       transmitter parameters (lat, lon, ant_height, elevation_m, …)
        bbox:       SW/NE corners of the analysis area
        mode:       "DTM" (terrain only) or "DSM" (terrain + building clutter)
        resolution: grid points per axis; defaults to settings value
        elev_grid:  (resolution × resolution) float32 elevation array (metres ASL)

    Returns:
        CoverageStats with aggregate RSSI metrics and a GeoJSON FeatureCollection
    """
    t0 = time.time()
    if resolution is None:
        resolution = settings.COVERAGE_GRID_RESOLUTION

    R    = resolution
    dlat = (bbox.ne_lat - bbox.sw_lat) / R
    dlng = (bbox.ne_lon - bbox.sw_lon) / R

    # ── Build cell coordinate grids ───────────────────────────────────────────
    # lat_cells increases south→north; lon_cells increases west→east
    lat_cells = bbox.sw_lat + (np.arange(R) + 0.5) * dlat   # (R,)
    lon_cells = bbox.sw_lon + (np.arange(R) + 0.5) * dlng   # (R,)

    # lon_grid[row, col], lat_grid[row, col]
    # row 0 corresponds to northernmost latitude (matches raster convention)
    lon_grid, lat_grid = np.meshgrid(lon_cells, lat_cells[::-1])  # (R, R)

    # ── Distance from site to every cell ──────────────────────────────────────
    dist_km = _haversine_km_vec(site.lat, site.lon, lat_grid, lon_grid)  # (R, R)

    # ── FSPL ──────────────────────────────────────────────────────────────────
    fspl = _fspl_db_vec(dist_km, site.frequency)   # (R, R)

    # ── Terrain loss ──────────────────────────────────────────────────────────
    h_gain     = height_gain_db(site.ant_height)
    site_h_asl = site.elevation_m + site.ant_height

    if elev_grid is not None:
        # Real DEM path: knife-edge diffraction
        cell_elev  = elev_grid.astype(np.float32)           # (R, R), row 0 = north
        diff_loss  = _diffraction_grid(
            site.lat, site.lon, site_h_asl,
            lat_grid, lon_grid, cell_elev, dist_km,
            site.frequency, elev_grid, bbox,
        )
        # DSM: add statistical clutter loss on top of diffraction
        clutter = DSM_CLUTTER_DB if mode == "DSM" else 0.0
        t_loss  = diff_loss + clutter
    else:
        # Pseudo-random fallback
        seed   = int(abs(site.lat) * 1000 + abs(site.lon) * 1000)
        relief = min(1.0, max(0.0, site.elevation_m / 500.0))
        t_loss = _pseudorandom_terrain_loss(lat_grid, lon_grid, seed, mode, relief)

    # ── RSSI grid ─────────────────────────────────────────────────────────────
    rssi = site.tx_power - fspl - t_loss + h_gain   # (R, R)

    # ── Mask cells outside analysis radius or too close ───────────────────────
    mask = (dist_km <= site.max_radius) & (dist_km >= 0.02)
    rssi = np.where(mask, rssi, np.nan)

    # ── Build GeoJSON features ────────────────────────────────────────────────
    geojson_features = []
    covered_cells:   List[CoverageCell] = []
    rssi_values:     List[float]        = []

    for row in range(R):
        # row 0 = northernmost cell → actual lat increases as row increases
        cell_lat = lat_cells[R - 1 - row]
        for col in range(R):
            cell_lon  = lon_cells[col]
            rssi_val  = float(rssi[row, col])
            if np.isnan(rssi_val):
                continue

            level = classify_rssi(rssi_val)
            color = LEVEL_COLORS[level]
            if color is None:
                continue

            rssi_r = round(rssi_val, 1)
            rssi_values.append(rssi_val)
            covered_cells.append(
                CoverageCell(lat=cell_lat, lon=cell_lon,
                             rssi=rssi_r, level=level)
            )
            geojson_features.append({
                "type": "Feature",
                "properties": {"rssi": rssi_r, "level": level, "color": color},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[
                        [cell_lon - dlng / 2, cell_lat - dlat / 2],
                        [cell_lon + dlng / 2, cell_lat - dlat / 2],
                        [cell_lon + dlng / 2, cell_lat + dlat / 2],
                        [cell_lon - dlng / 2, cell_lat + dlat / 2],
                        [cell_lon - dlng / 2, cell_lat - dlat / 2],
                    ]],
                },
            })

    total_cells = int(mask.sum())
    covered_pct = round(len(rssi_values) / total_cells * 100, 1) if total_cells else 0
    rssi_avg    = round(float(np.mean(rssi_values)), 1)  if rssi_values else 0
    rssi_max    = round(float(np.max(rssi_values)),  1)  if rssi_values else 0
    rssi_min    = round(float(np.min(rssi_values)),  1)  if rssi_values else 0

    return CoverageStats(
        cells=covered_cells,
        covered_pct=covered_pct,
        rssi_avg=rssi_avg,
        rssi_max=rssi_max,
        rssi_min=rssi_min,
        duration_sec=round(time.time() - t0, 2),
        geojson={
            "type": "FeatureCollection",
            "features": geojson_features,
            "metadata": {
                "site_lat":    site.lat,
                "site_lon":    site.lon,
                "mode":        mode,
                "frequency":   site.frequency,
                "covered_pct": covered_pct,
                "model":       "ITU-R P.526 knife-edge" if elev_grid is not None
                               else "pseudo-random fallback",
            },
        },
    )


# ── Cross-section terrain profile ──────────────────────────────────────────────

def _sample_profile_elev(
    site_lat:   float, site_lon:   float, site_elev_asl: float,
    cell_lat:   float, cell_lon:   float, cell_elev_asl: float,
    elev_grid:  np.ndarray,
    bbox:       BoundingBox,
    n:          int = XS_PROFILE_PTS,
) -> List[tuple]:
    """
    Sample terrain elevations at n equally-spaced points between site and cell.
    Returns list of (dist_km, elev_m) including the two endpoints.
    Falls back to linear interpolation for points outside the grid bbox.
    """
    R        = elev_grid.shape[0]
    lat_span = bbox.ne_lat - bbox.sw_lat
    lon_span = bbox.ne_lon - bbox.sw_lon
    total_km = haversine_km(site_lat, site_lon, cell_lat, cell_lon)

    profile = []
    for k in range(n + 1):
        t   = k / n
        lat = site_lat + t * (cell_lat - site_lat)
        lon = site_lon + t * (cell_lon - site_lon)

        # Map to fractional grid coordinates
        row_f = (bbox.ne_lat - lat) / lat_span * R
        col_f = (lon - bbox.sw_lon) / lon_span * R

        if 0 <= row_f < R and 0 <= col_f < R:
            r0, c0 = int(row_f), int(col_f)
            r1     = min(r0 + 1, R - 1)
            c1     = min(c0 + 1, R - 1)
            fr, fc = row_f - r0, col_f - c0
            elev   = float(
                elev_grid[r0, c0] * (1 - fc) * (1 - fr)
                + elev_grid[r0, c1] * fc       * (1 - fr)
                + elev_grid[r1, c0] * (1 - fc) * fr
                + elev_grid[r1, c1] * fc       * fr
            )
        else:
            # Outside grid: linear interpolation between known endpoints
            elev = site_elev_asl + t * (cell_elev_asl - site_elev_asl)

        profile.append((t * total_km, elev))

    return profile


def _profile_diffraction_loss(
    profile:      List[tuple],
    tx_h_asl:     float,
    rx_h_asl:     float,
    freq_mhz:     int,
) -> float:
    """
    Single knife-edge diffraction loss along a terrain profile.

    Iterates over intermediate profile points (skipping tx/rx endpoints),
    finds the worst obstacle (highest ν) and returns the ITU-R P.526 loss.

    Args:
        profile:   list of (dist_km, terrain_elev_m) from _sample_profile_elev
        tx_h_asl:  transmitter height ASL (elevation_m + ant_height)
        rx_h_asl:  receiver height ASL (cell elevation + RX_HEIGHT_AGL)
        freq_mhz:  carrier frequency

    Returns:
        Diffraction loss in dB (0 if clear LOS).
    """
    if len(profile) < 3:
        return 0.0

    wavelength = 300.0 / freq_mhz
    total_d_m  = profile[-1][0] * 1000

    if total_d_m <= 0:
        return 0.0

    max_nu = -10.0

    for dist_km, terrain_elev in profile[1:-1]:
        d1 = dist_km * 1000
        d2 = total_d_m - d1
        if d1 <= 0 or d2 <= 0:
            continue

        los_h  = tx_h_asl + (rx_h_asl - tx_h_asl) * (d1 / total_d_m)
        h      = terrain_elev - los_h                    # positive = obstacle
        r1     = math.sqrt(wavelength * d1 * d2 / total_d_m)
        if r1 <= 0:
            continue

        nu = h / r1
        if nu > max_nu:
            max_nu = nu

    return _knife_edge_loss_db(max_nu)


# ── Cross-section ──────────────────────────────────────────────────────────────

def _interpolate_route(waypoints: list, resolution_per_km: int = 5) -> list:
    """
    Generate evenly-spaced sample points along a multi-segment route.
    Returns list of {"lat", "lon", "dist_along"} dicts.
    """
    pts         = []
    dist_so_far = 0.0

    for i in range(len(waypoints) - 1):
        p1, p2  = waypoints[i], waypoints[i + 1]
        seg_len = haversine_km(p1["lat"], p1["lon"], p2["lat"], p2["lon"])
        n       = max(2, int(seg_len * resolution_per_km))

        for j in range(n):
            t = j / n
            pts.append({
                "lat":        p1["lat"] + t * (p2["lat"] - p1["lat"]),
                "lon":        p1["lon"] + t * (p2["lon"] - p1["lon"]),
                "dist_along": dist_so_far + t * seg_len,
            })
        dist_so_far += seg_len

    last = waypoints[-1]
    pts.append({"lat": last["lat"], "lon": last["lon"], "dist_along": dist_so_far})
    return pts


def _assess_risk(covered_pct: float, dead_zones: List[DeadZone]) -> str:
    """Four-level operational risk rating."""
    max_dead = max((dz.length_km for dz in dead_zones), default=0.0)
    if covered_pct < 50 or max_dead > 3: return "CRITICAL"
    if covered_pct < 70 or max_dead > 2: return "HIGH"
    if covered_pct < 85 or max_dead > 1: return "MEDIUM"
    return "LOW"


def _build_recommendations(
    site:            SiteParams,
    covered_pct:     float,
    dead_zones:      List[DeadZone],
    risk_level:      str,
    rssi_min:        float = -999.0,
    rssi_avg:        float = -999.0,
    total_length_km: float = 0.0,
    mode:            str   = "DTM",
) -> List[str]:
    """
    Generate Hebrew operational recommendations from cross-section results.
    Rules are ordered: critical blockers first, then improvements, then positives.
    """
    recs: List[str] = []

    # ── 1. Overall risk verdict ───────────────────────────────────────────────
    if risk_level == "CRITICAL":
        recs.append(
            f"הכיסוי קריטי ({covered_pct:.1f}% מהציר) – אסור להסתמך על קשר ישיר. "
            "יש לפרוס ממסר/ים לפני הפעלה מבצעית בציר זה."
        )
    elif risk_level == "HIGH":
        recs.append(
            f"הכיסוי גבולי-גבוה ({covered_pct:.1f}%) – קיים סיכון לניתוקי קשר. "
            "מומלץ להצטייד בממסר נייד ולתכנן נקודות תצפית חלופיות לאורך הציר."
        )
    elif risk_level == "MEDIUM":
        recs.append(
            f"הכיסוי גבולי ({covered_pct:.1f}%) – ייתכנו פערי קשר מקומיים. "
            "מומלץ לאמת בשטח לפני פעילות קריטית."
        )
    else:
        recs.append(
            f"הכיסוי תקין ({covered_pct:.1f}%) – ניתן להסתמך על קשר ישיר לאורך רוב הציר."
        )

    # ── 2. Dead-zone analysis ─────────────────────────────────────────────────
    if dead_zones:
        longest = max(dead_zones, key=lambda dz: dz.length_km)
        mid_lat  = (longest.start_lat + longest.end_lat) / 2
        mid_lon  = (longest.start_lon + longest.end_lon) / 2

        if len(dead_zones) == 1:
            recs.append(
                f"זוהה אזור מת אחד בין ק\"מ {longest.start_km:.1f} ל-{longest.end_km:.1f} "
                f"(אורך {longest.length_km:.2f} ק\"מ). "
                f"מיקום ממסר מומלץ: {mid_lat:.4f}°N, {mid_lon:.4f}°E."
            )
        else:
            total_dead = sum(dz.length_km for dz in dead_zones)
            dead_pct   = round(total_dead / total_length_km * 100, 1) if total_length_km else 0
            recs.append(
                f"זוהו {len(dead_zones)} אזורי מתים בסה\"כ {total_dead:.2f} ק\"מ "
                f"({dead_pct}% מהציר). "
                f"האזור הקריטי ביותר: ק\"מ {longest.start_km:.1f}–{longest.end_km:.1f} "
                f"({longest.length_km:.2f} ק\"מ) — ממסר מומלץ ב-{mid_lat:.4f}°N, {mid_lon:.4f}°E."
            )

        # Flag extremely long dead zones separately
        if longest.length_km > 3.0:
            recs.append(
                f"אזור מת קריטי של {longest.length_km:.1f} ק\"מ – "
                "ממסר קבוע נדרש, לא מספיק ממסר נייד."
            )
        elif longest.length_km > 1.5:
            recs.append(
                "אורך אזור המת הגדול ביותר חורג מ-1.5 ק\"מ – "
                "שקול ממסר קבוע לתמיכה מבצעית רציפה."
            )

    # ── 3. RSSI margin relative to rx_threshold ───────────────────────────────
    margin_min = rssi_min - site.rx_threshold
    margin_avg = rssi_avg - site.rx_threshold

    if rssi_min > -900:   # valid value
        if margin_min < 3:
            recs.append(
                f"עוצמת האות המינימלית ({rssi_min} dBm) קרובה מאוד לסף הקבלה "
                f"({site.rx_threshold} dBm, מרווח: {margin_min:.1f} dB) – "
                "שולי מבצעית; שינויי מזג אוויר או תנועת כלי רכב עלולים לגרום ניתוק."
            )
        elif margin_min < 8:
            recs.append(
                f"מרווח האות המינימלי ({margin_min:.1f} dB מהסף) נמוך – "
                "מומלץ להגביה את האנטנה או להוריד את סף הקבלה אם הרדיו מאפשר."
            )

    if rssi_avg > -900 and margin_avg < 10:
        recs.append(
            f"ממוצע עוצמת האות ({rssi_avg} dBm) קרוב לסף – "
            "בשטח הרים ומבנים הפועלים כמחסומים, הביצועים עשויים להיות גרועים יותר מהחישוב."
        )

    # ── 4. Antenna height ─────────────────────────────────────────────────────
    if site.ant_height < 8:
        recs.append(
            f"גובה האנטנה נמוך ({site.ant_height} מ'). "
            "הגבהה ל-15–20 מ' יכולה לשפר את הכיסוי משמעותית ולהפחית חסימת טרן."
        )
    elif site.ant_height < 15 and covered_pct < 80:
        recs.append(
            f"שקול הגבהת האנטנה מ-{site.ant_height} מ' ל-15 מ' ומעלה "
            "לשיפור קו הראייה בטרן מחוספס."
        )

    # ── 5. Frequency / propagation trade-off ─────────────────────────────────
    if site.frequency >= 1800 and covered_pct < 80:
        recs.append(
            f"תדר {site.frequency} MHz רגיש לחסימות טרן. "
            "שקול שימוש ב-700/900 MHz לכיסוי רחב יותר בשטח פתוח/הרים, "
            "או פריסת ממסרים בנקודות בינוניות לאורך הציר."
        )
    elif site.frequency <= 900 and covered_pct < 70:
        recs.append(
            f"גם בתדר {site.frequency} MHz הכיסוי נמוך – הגורם העיקרי הוא טרן חוסם "
            "ולא איבוד נתיב. יש לבחון מיקום אנטנה בנקודת תצפית גבוהה יותר."
        )

    # ── 6. DSM mode implications ──────────────────────────────────────────────
    if mode == "DSM" and covered_pct < 75:
        recs.append(
            "החישוב בוצע במצב DSM (כולל בניינים וצמחייה). "
            "הכיסוי הנמוך מצביע על חסימות עירוניות/יעריות משמעותיות – "
            "שקול ממסרים על גגות מבנים גבוהים לאורך הציר."
        )

    # ── 7. Positive conclusions ───────────────────────────────────────────────
    if covered_pct >= 95 and not dead_zones:
        recs.append(
            "כיסוי מצוין ללא אזורי מתים – הציר מתאים לתקשורת מבצעית רציפה."
        )
    elif covered_pct >= 80 and len(dead_zones) <= 1:
        recs.append(
            "כיסוי סביר לרוב שימושי שטח; יש להגדיר נקודות ממסר "
            "באזורי המתים המזוהים לפני תרגיל/פעילות קריטית."
        )

    return recs


def calculate_cross_section(
    site:       SiteParams,
    site_name:  str,
    waypoints:  list,
    mode:       str,
    resolution: int = 5,
    elev_grid:  Optional[np.ndarray] = None,
    bbox:       Optional[BoundingBox] = None,
) -> CrossSectionStats:
    """
    Compute RSSI along a multi-point route and detect dead zones.

    When elev_grid + bbox are supplied, uses real terrain profile +
    ITU-R P.526 knife-edge diffraction for each route point.
    Falls back to pseudo-random terrain when DEM is unavailable.

    Args:
        site:       transmitter parameters
        site_name:  display name
        waypoints:  list of {"lat", "lon"} route points (minimum 2)
        mode:       "DTM" or "DSM"
        resolution: sample points per km along the route
        elev_grid:  optional elevation grid for real terrain
        bbox:       bbox that the elev_grid covers

    Returns:
        CrossSectionStats with per-point RSSI, dead zones, risk, recommendations.
    """
    t0        = time.time()
    pts       = _interpolate_route(waypoints, resolution)
    seed      = int(abs(site.lat) * 1000 + abs(site.lon) * 1000)
    h_gain    = height_gain_db(site.ant_height)
    site_h    = site.elevation_m + site.ant_height
    use_real  = (elev_grid is not None and bbox is not None)
    relief    = min(1.0, max(0.0, site.elevation_m / 500.0))

    result_pts:  List[CrossSectionPoint] = []
    rssi_values: List[float]             = []
    level_counts = {k: 0 for k in
                    ["excellent", "good", "medium", "weak", "marginal", "none"]}

    for p in pts:
        d_site = max(haversine_km(site.lat, site.lon, p["lat"], p["lon"]), 0.02)

        if use_real:
            # Fetch cell elevation from grid (or fall back to linear interpolation)
            R        = elev_grid.shape[0]
            lat_span = bbox.ne_lat - bbox.sw_lat
            lon_span = bbox.ne_lon - bbox.sw_lon
            row_f    = (bbox.ne_lat - p["lat"]) / lat_span * R
            col_f    = (p["lon"] - bbox.sw_lon) / lon_span * R
            if 0 <= row_f < R and 0 <= col_f < R:
                r0, c0 = int(row_f), int(col_f)
                r1     = min(r0 + 1, R - 1)
                c1     = min(c0 + 1, R - 1)
                fr, fc = row_f - r0, col_f - c0
                cell_elev = float(
                    elev_grid[r0, c0] * (1-fc)*(1-fr)
                    + elev_grid[r0, c1] * fc*(1-fr)
                    + elev_grid[r1, c0] * (1-fc)*fr
                    + elev_grid[r1, c1] * fc*fr
                )
            else:
                cell_elev = site.elevation_m  # outside grid: use site elevation

            rx_h     = cell_elev + RX_HEIGHT_AGL
            profile  = _sample_profile_elev(
                site.lat, site.lon, site_h,
                p["lat"], p["lon"], cell_elev,
                elev_grid, bbox,
            )
            diff_loss = _profile_diffraction_loss(profile, site_h, rx_h, site.frequency)
            clutter   = DSM_CLUTTER_DB if mode == "DSM" else 0.0
            t_loss    = diff_loss + clutter
        else:
            # Pseudo-random fallback
            i   = int(abs(p["lat"]) * 1000) % 200
            j   = int(abs(p["lon"]) * 1000) % 200
            x   = math.sin(i * 127.1 + j * 311.7 + seed * 0.01) * 43758.5453
            r   = x - math.floor(x)
            t_loss = r * 15 * (1 - 0.5 * relief)
            if mode == "DSM":
                bx = math.sin(i * 200.1 + j * 411.7 + seed * 0.02) * 43758.5453
                t_loss += (bx - math.floor(bx)) * 20

        rssi  = site.tx_power - fspl_db(d_site, site.frequency) - t_loss + h_gain
        level = classify_rssi(rssi)
        level_counts[level] += 1
        if level != "none":
            rssi_values.append(rssi)

        result_pts.append(CrossSectionPoint(
            dist_along=round(p["dist_along"], 3),
            dist_from_site=round(d_site, 3),
            lat=round(p["lat"], 6),
            lon=round(p["lon"], 6),
            rssi=round(rssi, 1),
            level=level,
        ))

    total       = len(result_pts)
    covered_pct = round((total - level_counts["none"]) / total * 100, 1) if total else 0
    rssi_avg    = round(sum(rssi_values) / len(rssi_values), 1) if rssi_values else 0
    rssi_min    = round(min(rssi_values), 1)                     if rssi_values else 0
    rssi_max    = round(max(rssi_values), 1)                     if rssi_values else 0

    # ── Dead zone detection ────────────────────────────────────────────────────
    dead_zones: List[DeadZone] = []
    in_dead, dz_start = False, None

    for p in result_pts:
        if p.level == "none":
            if not in_dead:
                in_dead, dz_start = True, p
        else:
            if in_dead:
                dead_zones.append(DeadZone(
                    start_km=dz_start.dist_along, end_km=p.dist_along,
                    length_km=round(p.dist_along - dz_start.dist_along, 3),
                    start_lat=dz_start.lat, start_lon=dz_start.lon,
                    end_lat=p.lat,          end_lon=p.lon,
                ))
                in_dead = False

    if in_dead and result_pts:
        last = result_pts[-1]
        dead_zones.append(DeadZone(
            start_km=dz_start.dist_along, end_km=last.dist_along,
            length_km=round(last.dist_along - dz_start.dist_along, 3),
            start_lat=dz_start.lat, start_lon=dz_start.lon,
            end_lat=last.lat,       end_lon=last.lon,
        ))

    total_length = result_pts[-1].dist_along if result_pts else 0.0
    signal_dist  = {
        k: {"count": v, "pct": round(v / total * 100, 1) if total else 0}
        for k, v in level_counts.items()
    }
    risk = _assess_risk(covered_pct, dead_zones)
    recs = _build_recommendations(
        site, covered_pct, dead_zones, risk,
        rssi_min=rssi_min, rssi_avg=rssi_avg,
        total_length_km=total_length,
        mode=mode,
    )

    return CrossSectionStats(
        site_name=site_name,
        mode=mode,
        waypoints=waypoints,
        total_length_km=round(total_length, 2),
        covered_pct=covered_pct,
        rssi_min=rssi_min,
        rssi_max=rssi_max,
        rssi_avg=rssi_avg,
        risk_level=risk,
        points=result_pts,
        dead_zones=dead_zones,
        signal_distribution=signal_dist,
        recommendations=recs,
        duration_sec=round(time.time() - t0, 2),
    )
