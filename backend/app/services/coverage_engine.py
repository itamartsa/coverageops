"""
Coverage Calculation Engine
────────────────────────────
Implements RF propagation modelling for two use-cases:

  1. Area coverage  – calculate_coverage()
     Sweeps a lat/lon bounding box with a resolution×resolution grid.
     When a real elevation grid is supplied the engine uses vectorised
     ITU-R P.526 multi-edge (Deygout) diffraction over a k=4/3 curved earth;
     otherwise falls back to a seeded pseudo-random terrain simulation
     (clearly labelled dem_source="none" in the result metadata).
     The primary output is a raw RSSI raster (CoverageStats.rssi_grid);
     GeoJSON is only built on request (build_geojson=True).

  2. Cross-section  – calculate_cross_section()
     Samples RSSI along a multi-segment route, detects dead zones and
     produces operational risk scoring + recommendations (see risk.py).
     Uses real terrain profile + Deygout diffraction when an elevation
     grid is available.

Propagation model
─────────────────
  RSSI = Tx_power – FSPL(d, f) – DiffractionLoss – ClutterLoss + HeightGain

  FSPL             : Free-Space Path Loss (Friis, dB)
  DiffractionLoss  : ITU-R P.526-15 §4.5 Deygout multi-edge (up to 3 edges),
                     terrain raised by the earth bulge d1·d2 / (2·k·R_e).
                     Fresnel-Kirchhoff parameter (P.526 §4.1, eq. 26):
                         ν = h · √(2·d / (λ·d1·d2)) = √2 · h / r1
                     with r1 = √(λ·d1·d2/d) the first Fresnel-zone radius.
                     Secondary (Deygout) edges are only evaluated when the
                     principal edge obstructs the LOS (ν_p > 0) and only
                     contribute when their own ν > 0.
  ClutterLoss      : statistical building/vegetation loss (DSM mode only)
  HeightGain       : antenna AGL height advantage over 1.5 m baseline
                     (capped when a DEM is used – see effective_height_gain_db)

DEM validity
────────────
  elev_grid may be None (no terrain → pseudo-random fallback) or a float32
  array in which NaN marks cells whose tiles could not be fetched. NaN terrain
  samples are treated as "unknown" and replaced by linear interpolation of the
  ground between the path endpoints.

  The elevation raster follows terrain.py: elev[r, c] is the value at the
  CENTRE of cell (r, c), so a WGS-84 point maps to fractional indices
      row_f = (ne_lat − lat) / lat_span · R − 0.5
      col_f = (lon − sw_lon) / lon_span · R − 0.5
  (see _grid_coords); points with −0.5 ≤ f ≤ R − 0.5 are inside the raster
  and lookups are clamped to the outermost cell centres.

References
──────────
  ITU-R P.526-15  – Propagation by diffraction
  ITU-R P.525-4   – Calculation of free-space attenuation
  ITU-R P.453     – effective earth-radius factor k (median 4/3)
"""
import io
import math
import time
import zlib
from dataclasses import dataclass, field
from typing import Dict, Iterator, List, Optional, Tuple

import numpy as np
from PIL import Image

from app.core.config import settings
from app.services.risk import (
    assess_risk,
    build_route_recommendations,
)


# ── Constants ──────────────────────────────────────────────────────────────────

RX_HEIGHT_AGL  = 1.5    # assumed receiver height above ground (m) — handheld
PROFILE_STEPS  = 24     # profile sample points between tx and rx (area coverage)
XS_PROFILE_PTS = 30     # profile points for cross-section analysis
DSM_CLUTTER_DB = 9.0    # median building/vegetation clutter loss for DSM mode

EARTH_RADIUS_KM = 6371.0
K_FACTOR        = 4.0 / 3.0                              # effective earth-radius factor
R_EFF_M         = EARTH_RADIUS_KM * 1000.0 * K_FACTOR    # ≈ 8 494 667 m

HEIGHT_GAIN_CAP_DB = 6.0   # max empirical height gain when a real DEM is used

DIFFRACTION_METHODS = ("deygout", "single")
DIFFRACTION_METHOD  = "deygout"   # module default; override per call
NU_MIN              = -0.78       # ν below which knife-edge loss is negligible
NU_SECONDARY_MIN    = 0.0         # Deygout: principal / secondary edges must obstruct LOS

ANY_SIGNAL_DBM = -110.0   # "any signal" boundary (level != "none")

LEVELS = ("excellent", "good", "medium", "weak", "marginal", "none")

DEM_SOURCE_TERRARIUM = "terrarium"
DEM_SOURCE_NONE      = "none"


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
    cells:        List[CoverageCell]   # only populated when build_geojson=True
    covered_pct:  float                # % in-radius cells with rssi >= rx_threshold
    rssi_avg:     float                # over cells with any signal (> -110 dBm)
    rssi_max:     float
    rssi_min:     float                # over cells with any signal (display only)
    duration_sec: float
    geojson:      dict                 # FeatureCollection; features empty unless requested
    covered_pct_any_signal: float               = 0.0     # % in-radius cells > -110 dBm
    level_distribution:     Dict[str, dict]     = field(default_factory=dict)
    weak_pct:               float               = 0.0     # % weak + marginal
    threshold_dbm:          float               = 0.0
    dem_source:             str                 = DEM_SOURCE_NONE
    dem_valid_pct:          float               = 0.0
    metadata:               dict                = field(default_factory=dict)
    rssi_grid:              Optional[np.ndarray] = None   # (R,R) float32, NaN outside mask
    mask:                   Optional[np.ndarray] = None   # (R,R) bool, in-radius cells
    # min RSSI over ALL in-radius cells incl. "none" (< -110 dBm); feeds risk.
    # None when there is no in-radius cell.
    rssi_min_all:           Optional[float]      = None
    # (R,R) bool DEM validity (non-NaN elevation); None when no DEM was given
    valid_mask:             Optional[np.ndarray] = None


@dataclass
class CrossSectionPoint:
    dist_along:     float   # km from route start
    dist_from_site: float   # km from antenna
    lat:            float
    lon:            float
    rssi:           float   # dBm (rounded to 0.1)
    level:          str
    covered:        bool = False   # unrounded rssi >= rx_threshold (dead-zone flag)


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
    risk_score:          int   = 1
    dem_source:          str   = DEM_SOURCE_NONE
    dem_valid_pct:       float = 0.0
    covered_pct_any_signal: float = 0.0     # % points > -110 dBm (old definition)
    threshold_dbm:          float = 0.0     # rx_threshold used for covered_pct / dead zones
    rssi_min_all:           Optional[float] = None   # min over ALL points (incl. "none"); feeds risk


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

# RGBA used by the PNG renderer (same as api/coverage._COVERAGE_COLOR_MAP, alpha ≈ 63%)
LEVEL_RGBA = {
    "excellent": (0,   255, 136, 160),
    "good":      (125, 255, 107, 160),
    "medium":    (255, 230, 0,   160),
    "weak":      (255, 140, 0,   160),
    "marginal":  (255, 59,  92,  160),
    "none":      (0,   0,   0,   0),
}


def _level_index_grid(rssi_grid: np.ndarray) -> np.ndarray:
    """
    Vectorised classify_rssi: returns int8 index into LEVELS for each cell,
    or -1 for NaN cells. Uses the same strict '>' boundaries as classify_rssi.
    """
    r   = np.asarray(rssi_grid)
    idx = np.full(r.shape, 5, dtype=np.int8)
    for i, thr in ((4, -110), (3, -100), (2, -90), (1, -80), (0, -70)):
        idx = np.where(r > thr, np.int8(i), idx)
    idx[np.isnan(r)] = -1
    return idx


def level_distribution_from_grid(rssi_grid: np.ndarray, threshold: float) -> dict:
    """
    Signal statistics over the valid (non-NaN, i.e. in-radius) cells of an
    RSSI raster.

    Returns:
        {
          "level_distribution":     {level: {"count", "pct"}} for all LEVELS
                                    (incl. "none"; pct of in-radius cells),
          "total_cells":            number of in-radius cells,
          "covered_pct":            % cells with rssi >= threshold,
          "covered_pct_any_signal": % cells with rssi > -110 dBm,
          "weak_pct":               % cells in weak + marginal,
          "threshold_dbm":          threshold,
        }
    """
    r     = np.asarray(rssi_grid, dtype=np.float32)
    valid = ~np.isnan(r)
    n     = int(valid.sum())
    idx   = _level_index_grid(r)
    counts = {lvl: int((idx == i).sum()) for i, lvl in enumerate(LEVELS)}

    def _pct(c: int) -> float:
        return round(c / n * 100, 1) if n else 0.0

    with np.errstate(invalid="ignore"):
        n_cov = int((valid & (r >= threshold)).sum())
        n_any = int((valid & (r > ANY_SIGNAL_DBM)).sum())

    return {
        "level_distribution":     {lvl: {"count": c, "pct": _pct(c)} for lvl, c in counts.items()},
        "total_cells":            n,
        "covered_pct":            _pct(n_cov),
        "covered_pct_any_signal": _pct(n_any),
        "weak_pct":               _pct(counts["weak"] + counts["marginal"]),
        "threshold_dbm":          float(threshold),
    }


# ── Basic propagation helpers (scalar) ────────────────────────────────────────

def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two WGS-84 points (km)."""
    R  = EARTH_RADIUS_KM
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
    Antenna height gain above 1.5 m ground reference (uncapped).
      Gain = 20·log₁₀(h / 1.5)
    Kept for backward compatibility; the engine uses effective_height_gain_db().
    """
    return 20 * math.log10(max(ant_height_agl, 1.5) / 1.5)


def effective_height_gain_db(ant_height_agl: float, has_dem: bool) -> float:
    """
    Height gain actually applied by the engine.

    Without a DEM there is no geometric model of the antenna height, so the
    empirical 20·log₁₀(h/1.5) gain is the only place where antenna height
    enters the budget and is kept unchanged.

    With a real DEM the tx height (elevation_m + ant_height) is already part
    of the LOS / Fresnel-zone geometry used by the diffraction model: raising
    the antenna clears obstacles and lowers the diffraction loss. Adding the
    full 20·log₁₀(h/1.5) on top would count the same physical benefit twice
    (a 30 m mast would get +26 dB "for free"). We therefore keep only a small
    residual gain – capturing near-field ground-reflection / clutter-clearance
    effects that the knife-edge model does not represent – capped at
    HEIGHT_GAIN_CAP_DB:

        gain = min(20·log₁₀(h/1.5), HEIGHT_GAIN_CAP_DB)
    """
    g = height_gain_db(ant_height_agl)
    if has_dem:
        return min(g, HEIGHT_GAIN_CAP_DB)
    return g


def earth_bulge_m(d1_m: float, d2_m: float, k_factor: float = K_FACTOR) -> float:
    """
    Earth bulge (m) at a point d1 / d2 metres from the path ends, for an
    effective earth radius k·R_e:  b = d1·d2 / (2·k·R_e).
    """
    return d1_m * d2_m / (2.0 * EARTH_RADIUS_KM * 1000.0 * k_factor)


def _resolve_method(method: Optional[str]) -> str:
    """Validate a diffraction method name (None → module default)."""
    m = (method or DIFFRACTION_METHOD).lower()
    if m not in DIFFRACTION_METHODS:
        raise ValueError(f"diffraction_method must be one of {DIFFRACTION_METHODS}, got {method!r}")
    return m


# ── ITU-R P.526 knife-edge diffraction (scalar) ───────────────────────────────

def fresnel_radius_m(d1_m: float, d2_m: float, wavelength_m: float) -> float:
    """First Fresnel-zone radius r1 = √(λ·d1·d2 / (d1 + d2)) in metres."""
    d = d1_m + d2_m
    if d1_m <= 0 or d2_m <= 0 or d <= 0:
        return 0.0
    return math.sqrt(wavelength_m * d1_m * d2_m / d)


def fresnel_nu(h_m: float, d1_m: float, d2_m: float, wavelength_m: float) -> float:
    """
    Fresnel-Kirchhoff diffraction parameter (ITU-R P.526-15 §4.1, eq. 26):

        ν = h · √(2·d / (λ·d1·d2)) = √2 · h / r1,   d = d1 + d2

    h is the height of the obstacle tip above the direct tx→rx line (negative
    when below it). Returns −inf for degenerate geometry (d1 or d2 ≤ 0).
    """
    if d1_m <= 0 or d2_m <= 0 or wavelength_m <= 0:
        return -math.inf
    return h_m * math.sqrt(2.0 * (d1_m + d2_m) / (wavelength_m * d1_m * d2_m))


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


def _max_nu_scalar(
    xs: List[float], hs: List[float],
    i_lo: int, i_hi: int,
    xa: float, ha: float, xb: float, hb: float,
    wavelength: float,
) -> Tuple[float, int]:
    """
    Highest ν among profile points i_lo ≤ i < i_hi relative to the straight
    line from (xa, ha) to (xb, hb). Returns (ν, index) or (−inf, −1).
    """
    best_nu, best_i = -math.inf, -1
    span = xb - xa
    if span <= 0:
        return best_nu, best_i
    for i in range(i_lo, i_hi):
        d1 = xs[i] - xa
        d2 = xb - xs[i]
        if d1 <= 0 or d2 <= 0:
            continue
        los = ha + (hb - ha) * (d1 / span)
        nu  = fresnel_nu(hs[i] - los, d1, d2, wavelength)
        if nu > best_nu:
            best_nu, best_i = nu, i
    return best_nu, best_i


def _deygout_loss_scalar(
    xs:         List[float],   # distance of each intermediate point from tx (m)
    hs:         List[float],   # effective obstacle height ASL (terrain + bulge)
    total_m:    float,
    tx_h:       float,
    rx_h:       float,
    wavelength: float,
    method:     str = DIFFRACTION_METHOD,
) -> float:
    """
    Multi-edge diffraction loss along a profile (ITU-R P.526-15 §4.5, Deygout).

      1. Principal edge p = argmax ν over the full tx→rx path;
         L = J(ν_p) (J = 0 for ν < −0.78, clamped to ≥ 0).
      2. Only if the principal edge actually obstructs the LOS (ν_p > 0),
         find the dominant edge on tx→p and on p→rx (one recursion level
         each side → at most 3 edges).
      3. A secondary edge contributes J(ν) only when its own ν > 0.

    Restricting the decomposition to obstructing edges keeps a sub-path
    whose ground merely intrudes into the Fresnel zone (−0.78 < ν ≤ 0,
    e.g. flat ground next to a 1.5 m receiver) from being counted as an
    extra 3–6 dB "edge" — that loss is already in J(ν_p).

    method="single" returns J(ν_p) only (classic worst-obstacle knife-edge).
    Deygout loss is therefore always ≥ the single-edge loss.
    """
    n = len(xs)
    if n == 0 or total_m <= 0:
        return 0.0

    nu_p, p = _max_nu_scalar(xs, hs, 0, n, 0.0, tx_h, total_m, rx_h, wavelength)
    if p < 0:
        return 0.0
    loss = max(_knife_edge_loss_db(nu_p), 0.0)
    if method == "single" or nu_p <= NU_SECONDARY_MIN:
        return loss

    nu_l, _ = _max_nu_scalar(xs, hs, 0, p, 0.0, tx_h, xs[p], hs[p], wavelength)
    nu_r, _ = _max_nu_scalar(xs, hs, p + 1, n, xs[p], hs[p], total_m, rx_h, wavelength)
    for nu in (nu_l, nu_r):
        if nu > NU_SECONDARY_MIN:
            loss += max(_knife_edge_loss_db(nu), 0.0)
    return loss


# ── Vectorised helpers (numpy) ─────────────────────────────────────────────────

def _haversine_km_vec(lat1: float, lon1: float,
                      lat2: np.ndarray, lon2: np.ndarray) -> np.ndarray:
    """Vectorised haversine: scalar site vs. (R, R) cell arrays → (R, R) km."""
    R  = EARTH_RADIUS_KM
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


def _grid_coords(lat, lon, bbox: BoundingBox, R: int):
    """
    Fractional (row_f, col_f) raster coordinates of a point (scalar or array)
    for an R×R grid whose elev[r, c] is the value at the CENTRE of cell (r, c)
    (row 0 = north edge of bbox):

        row_f = (ne_lat − lat) / lat_span · R − 0.5
        col_f = (lon − sw_lon) / lon_span · R − 0.5

    The point lies inside the raster iff −0.5 ≤ row_f, col_f ≤ R − 0.5.
    """
    lat_span = bbox.ne_lat - bbox.sw_lat
    lon_span = bbox.ne_lon - bbox.sw_lon
    row_f = (bbox.ne_lat - lat) / lat_span * R - 0.5
    col_f = (lon - bbox.sw_lon) / lon_span * R - 0.5
    return row_f, col_f


def _inside_grid(row_f, col_f, H: int, W: int):
    """Cell-centre raster bounds test: −0.5 ≤ f ≤ N − 0.5 (scalar or array)."""
    return ((row_f >= -0.5) & (row_f <= H - 0.5)
            & (col_f >= -0.5) & (col_f <= W - 0.5))


def _bilinear(grid: np.ndarray, row_f: np.ndarray, col_f: np.ndarray) -> np.ndarray:
    """
    Vectorised bilinear interpolation into `grid` (H × W), cell-centre
    convention (integer coordinates = cell centres).
    row_f, col_f are floating-point grid coordinates (any shape); they are
    clamped to [0, N−1], so the half cell outside the outermost centres takes
    the edge value. Caller masks points outside the raster (_inside_grid).
    A NaN in any of the four neighbours yields NaN (unknown terrain).
    """
    H, W  = grid.shape
    rf    = np.clip(np.nan_to_num(row_f, nan=0.0), 0.0, H - 1)
    cf    = np.clip(np.nan_to_num(col_f, nan=0.0), 0.0, W - 1)
    r0    = np.floor(rf).astype(np.int32)
    c0    = np.floor(cf).astype(np.int32)
    r1    = np.minimum(r0 + 1, H - 1)
    c1    = np.minimum(c0 + 1, W - 1)
    fr    = rf - r0
    fc    = cf - c0
    return (grid[r0, c0] * (1 - fc) * (1 - fr)
            + grid[r0, c1] * fc       * (1 - fr)
            + grid[r1, c0] * (1 - fc) * fr
            + grid[r1, c1] * fc       * fr)


def _bilinear_scalar(grid: np.ndarray, row_f: float, col_f: float) -> float:
    """
    Scalar bilinear lookup, cell-centre convention (see _grid_coords).
    Returns NaN when the point is outside the raster (f < −0.5 or f > N − 0.5)
    or any neighbour is NaN (unknown terrain). Coordinates in the outer half
    cell are clamped to the edge cell centre.
    """
    H, W = grid.shape
    if not (-0.5 <= row_f <= H - 0.5 and -0.5 <= col_f <= W - 0.5):
        return math.nan
    rf     = min(max(row_f, 0.0), H - 1.0)
    cf     = min(max(col_f, 0.0), W - 1.0)
    r0, c0 = int(math.floor(rf)), int(math.floor(cf))
    r1     = min(r0 + 1, H - 1)
    c1     = min(c0 + 1, W - 1)
    fr, fc = rf - r0, cf - c0
    return float(
        grid[r0, c0] * (1 - fc) * (1 - fr)
        + grid[r0, c1] * fc       * (1 - fr)
        + grid[r1, c0] * (1 - fc) * fr
        + grid[r1, c1] * fc       * fr
    )


def _knife_edge_loss_vec(nu: np.ndarray) -> np.ndarray:
    """Vectorised ITU-R P.526 knife-edge loss (same formula as scalar version)."""
    loss   = np.zeros_like(nu)
    m1     = (nu >= -0.78) & (nu <= 2.4)
    m2     = nu > 2.4
    inner  = np.sqrt(np.maximum((nu - 0.1)**2 + 1, 1e-18)) + nu - 0.1
    loss   = np.where(m1, 6.9 + 20 * np.log10(np.maximum(inner, 1e-9)), loss)
    loss   = np.where(m2, 13.0 + 20 * np.log10(np.maximum(nu,   1e-9)), loss)
    return loss


def _nu_segment_vec(
    h:  np.ndarray,          # (R, R) obstacle height (terrain + bulge)
    tk: float,               # fractional position of the obstacle on the full path
    ta, ha, tb, hb,          # segment endpoints: fraction (scalar/array), height
    D:  np.ndarray,          # (R, R) full path length (m)
    wavelength: float,
) -> np.ndarray:
    """
    ν of an obstacle at fraction tk relative to the line (ta, ha) → (tb, hb),
    ITU-R P.526 §4.1:  ν = h · √(2·d / (λ·d1·d2)) = √2 · h / r1,
    with d = d1 + d2 the segment length (same as fresnel_nu()).
    Invalid geometry (tk outside (ta, tb)) yields garbage; caller masks it.
    """
    seg = tb - ta
    d1  = (tk - ta) * D
    d2  = (tb - tk) * D
    los = ha + (hb - ha) * ((tk - ta) / seg)
    r1  = np.sqrt(np.maximum(wavelength * d1 * d2 / np.maximum(seg * D, 1e-6), 1e-9))
    return math.sqrt(2.0) * (h - los) / r1


# ── Vectorised diffraction over full coverage grid ────────────────────────────

def _diffraction_grid(
    site_lat:   float,
    site_lon:   float,
    site_h_asl: float,           # tx ASL + AGL (metres)
    lat_grid:   np.ndarray,      # (R, R) cell latitudes
    lon_grid:   np.ndarray,      # (R, R) cell longitudes
    cell_elev:  np.ndarray,      # (R, R) cell terrain ASL (metres), NaN-free
    dist_grid:  np.ndarray,      # (R, R) site→cell distances (km)
    freq_mhz:   int,
    elev_grid:  np.ndarray,      # (R, R) full elevation grid (may contain NaN)
    bbox:       BoundingBox,
    site_ground_asl: Optional[float] = None,   # tx ground ASL (for NaN fallback)
    method:     Optional[str] = None,
) -> np.ndarray:
    """
    Compute ITU-R P.526 diffraction loss for every cell in the grid.

    For each of PROFILE_STEPS intermediate points along the path from site to
    cell we:
      1. Interpolate the terrain elevation from elev_grid (NaN / outside the
         grid → linear interpolation of the ground between tx and rx)
      2. Add the earth bulge d1·d2/(2·k·R_e) (k = K_FACTOR)
      3. Compute ν = √2·h/r1 (P.526 §4.1) relative to the tx→rx line and
         track the principal edge; Deygout sub-edges as in _deygout_loss_scalar
    The effective heights of all steps are kept in an (S, R, R) float32 stack
    (24 × 500 × 500 ≈ 24 MB) so that the Deygout sub-paths tx→principal and
    principal→rx can be evaluated with numpy masks.

    Returns: (R, R) float32 array of diffraction loss in dB.
    """
    method     = _resolve_method(method)
    R          = lat_grid.shape[0]
    wavelength = 300.0 / freq_mhz          # metres  (c = 3×10⁸ m/s)
    if site_ground_asl is None:
        site_ground_asl = site_h_asl

    rx_h_asl   = (cell_elev + RX_HEIGHT_AGL).astype(np.float32)   # (R, R)
    dist_m     = (dist_grid * 1000).astype(np.float32)            # (R, R) in metres
    D          = np.maximum(dist_m, 1e-3)

    t_vals = np.linspace(0, 1, PROFILE_STEPS + 2)[1:-1]  # exclude tx/rx endpoints
    S      = len(t_vals)

    h_eff  = np.empty((S, R, R), dtype=np.float32)
    max_nu = np.full((R, R), -10.0, dtype=np.float32)
    p_idx  = np.zeros((R, R), dtype=np.int32)

    with np.errstate(invalid="ignore", divide="ignore"):
        for k, t in enumerate(t_vals):
            # Intermediate lat/lon: (R, R)
            lat_t = site_lat + t * (lat_grid - site_lat)
            lon_t = site_lon + t * (lon_grid - site_lon)

            # Map to fractional grid indices (cell-centre convention, row 0 = north)
            row_f, col_f = _grid_coords(lat_t, lon_t, bbox, R)
            inside = _inside_grid(row_f, col_f, R, R)

            terrain_t = _bilinear(elev_grid, row_f, col_f)
            # Outside the grid or unknown (NaN) terrain: linear ground profile
            ground_linear = site_ground_asl + t * (cell_elev - site_ground_asl)
            terrain_t     = np.where(inside & ~np.isnan(terrain_t), terrain_t, ground_linear)

            # Earth bulge: terrain appears higher relative to the straight chord
            d1    = t * D
            d2    = (1 - t) * D
            bulge = d1 * d2 / (2.0 * R_EFF_M)
            h_eff[k] = terrain_t + bulge

            nu = _nu_segment_vec(h_eff[k], t, 0.0, site_h_asl, 1.0, rx_h_asl, D, wavelength)
            better = nu > max_nu
            max_nu = np.where(better, nu, max_nu)
            p_idx  = np.where(better, k, p_idx)

        loss = np.maximum(_knife_edge_loss_vec(max_nu), 0.0)
        if method == "single":
            return loss.astype(np.float32)

        # ── Deygout: dominant edge on each side of the principal edge ────────
        t_p = t_vals.astype(np.float32)[p_idx]                              # (R, R)
        h_p = np.take_along_axis(h_eff, p_idx[None, :, :], axis=0)[0]       # (R, R)
        nu_l = np.full((R, R), -10.0, dtype=np.float32)
        nu_r = np.full((R, R), -10.0, dtype=np.float32)

        for k, t in enumerate(t_vals):
            left  = k < p_idx
            right = k > p_idx
            if left.any():
                nl   = _nu_segment_vec(h_eff[k], t, 0.0, site_h_asl, t_p, h_p, D, wavelength)
                nu_l = np.where(left, np.maximum(nu_l, nl), nu_l)
            if right.any():
                nr   = _nu_segment_vec(h_eff[k], t, t_p, h_p, 1.0, rx_h_asl, D, wavelength)
                nu_r = np.where(right, np.maximum(nu_r, nr), nu_r)

        # Same rule as _deygout_loss_scalar: decompose only behind an
        # obstructing principal edge (ν_p > 0); secondary edges need ν > 0.
        sub = (np.where(nu_l > NU_SECONDARY_MIN, np.maximum(_knife_edge_loss_vec(nu_l), 0.0), 0.0)
               + np.where(nu_r > NU_SECONDARY_MIN, np.maximum(_knife_edge_loss_vec(nu_r), 0.0), 0.0))
        loss = loss + np.where(max_nu > NU_SECONDARY_MIN, sub, 0.0)

    return loss.astype(np.float32)


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


# ── Raster output helpers ──────────────────────────────────────────────────────

_RSSI_BLOB_MAGIC   = b"CVRSSI1"
_RSSI_NODATA_I16   = np.int16(-32768)
_RSSI_SCALE        = 10.0          # 0.1 dB quantum → max error 0.05 dB
_RSSI_CLIP         = 3276.7


def encode_rssi_grid(rssi_grid: np.ndarray) -> bytes:
    """
    Compact binary encoding of an RSSI raster.

    Values are quantised to 0.1 dB as int16 (NaN → −32768 sentinel), saved
    with np.save and zlib-compressed, prefixed by a magic header.
    Round-trip error ≤ 0.05 dB; NaN is preserved; shape is preserved.
    """
    a = np.asarray(rssi_grid, dtype=np.float64)
    nan = np.isnan(a)
    q = np.round(np.clip(np.where(nan, 0.0, a), -_RSSI_CLIP, _RSSI_CLIP) * _RSSI_SCALE)
    q = np.where(nan, _RSSI_NODATA_I16, q).astype("<i2")
    buf = io.BytesIO()
    np.save(buf, q, allow_pickle=False)
    return _RSSI_BLOB_MAGIC + zlib.compress(buf.getvalue(), 6)


def decode_rssi_grid(data: bytes) -> np.ndarray:
    """Inverse of encode_rssi_grid → float32 array with NaN for no-data."""
    if not data.startswith(_RSSI_BLOB_MAGIC):
        raise ValueError("not an encoded RSSI grid")
    raw = zlib.decompress(data[len(_RSSI_BLOB_MAGIC):])
    q   = np.load(io.BytesIO(raw), allow_pickle=False)
    out = q.astype(np.float32) / np.float32(_RSSI_SCALE)
    out[q == _RSSI_NODATA_I16] = np.nan
    return out


def rssi_grid_to_png(rssi_grid: np.ndarray, width: int, height: int) -> bytes:
    """
    Render an RSSI raster (row 0 = north) to an RGBA PNG.

    Colours match LEVEL_RGBA / api.coverage._COVERAGE_COLOR_MAP; "none" and
    NaN cells are fully transparent. Resized to width × height with
    nearest-neighbour so cell edges stay crisp.
    """
    idx = _level_index_grid(rssi_grid)
    lut = np.zeros((len(LEVELS) + 1, 4), dtype=np.uint8)   # last row = NaN
    for i, lvl in enumerate(LEVELS):
        lut[i] = LEVEL_RGBA[lvl]
    rgba = lut[np.where(idx < 0, len(LEVELS), idx)]
    img  = Image.fromarray(np.ascontiguousarray(rgba))
    if img.size != (width, height):
        img = img.resize((int(width), int(height)), Image.Resampling.NEAREST)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _iter_grid_cells(
    rssi_grid: np.ndarray, bbox: BoundingBox,
) -> Iterator[Tuple[float, float, float, str]]:
    """Yield (lat, lon, rssi, level) for every cell with any signal (level != none)."""
    R, C = rssi_grid.shape
    dlat = (bbox.ne_lat - bbox.sw_lat) / R
    dlng = (bbox.ne_lon - bbox.sw_lon) / C
    for row in range(R):
        # row 0 = northernmost cell
        cell_lat = bbox.ne_lat - (row + 0.5) * dlat
        for col in range(C):
            rssi_val = float(rssi_grid[row, col])
            if np.isnan(rssi_val):
                continue
            level = classify_rssi(rssi_val)
            if LEVEL_COLORS[level] is None:
                continue
            yield cell_lat, bbox.sw_lon + (col + 0.5) * dlng, rssi_val, level


def rssi_grid_to_geojson(
    rssi_grid: np.ndarray,
    bbox:      BoundingBox,
    metadata:  Optional[dict] = None,
) -> dict:
    """
    Convert an RSSI raster into a GeoJSON FeatureCollection of square cell
    polygons (cells with level "none" or NaN are omitted).
    Expensive for large grids – prefer the raster / PNG for display.
    """
    R, C = rssi_grid.shape
    dlat = (bbox.ne_lat - bbox.sw_lat) / R
    dlng = (bbox.ne_lon - bbox.sw_lon) / C
    features = []
    for cell_lat, cell_lon, rssi_val, level in _iter_grid_cells(rssi_grid, bbox):
        features.append({
            "type": "Feature",
            "properties": {"rssi": round(rssi_val, 1), "level": level,
                           "color": LEVEL_COLORS[level]},
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
    return {
        "type":     "FeatureCollection",
        "features": features,
        "metadata": dict(metadata or {}),
    }


# ── Area coverage ──────────────────────────────────────────────────────────────

def calculate_coverage(
    site:               SiteParams,
    bbox:               BoundingBox,
    mode:               str,
    resolution:         int = None,
    elev_grid:          Optional[np.ndarray] = None,
    build_geojson:      bool = False,
    diffraction_method: Optional[str] = None,
) -> CoverageStats:
    """
    Compute RF coverage over a bounding box.

    When elev_grid is provided (real Terrarium DEM, NaN = missing tile) the
    engine runs a fully-vectorised ITU-R P.526 Deygout diffraction model over
    a k=4/3 earth. Otherwise it falls back to a seeded pseudo-random terrain
    simulation and labels the result dem_source="none".

    Args:
        site:               transmitter parameters (lat, lon, ant_height, elevation_m, …)
        bbox:               SW/NE corners of the analysis area
        mode:               "DTM" (terrain only) or "DSM" (terrain + building clutter)
        resolution:         grid points per axis; defaults to settings value
        elev_grid:          (resolution × resolution) float32 elevation array
                            (metres ASL, row 0 = north, NaN = unknown) or None
        build_geojson:      also build the (slow, large) GeoJSON polygons + cells
        diffraction_method: "deygout" (default) or "single"

    Returns:
        CoverageStats with the RSSI raster, aggregate metrics and metadata.
    """
    t0 = time.time()
    if resolution is None:
        resolution = settings.COVERAGE_GRID_RESOLUTION
    method = _resolve_method(diffraction_method)

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
    has_dem    = elev_grid is not None
    h_gain     = effective_height_gain_db(site.ant_height, has_dem)
    site_h_asl = site.elevation_m + site.ant_height

    if has_dem:
        elev = np.asarray(elev_grid, dtype=np.float32)
        if elev.shape != (R, R):
            raise ValueError(f"elev_grid shape {elev.shape} != ({R}, {R})")
        valid_dem     = ~np.isnan(elev)
        valid_mask    = valid_dem
        dem_valid_pct = round(float(valid_dem.mean()) * 100, 1)
        dem_source    = DEM_SOURCE_TERRARIUM

        # Unknown rx-cell elevation → assume site ground level (flat-earth fallback)
        cell_elev = np.where(valid_dem, elev, np.float32(site.elevation_m))   # (R, R)
        diff_loss = _diffraction_grid(
            site.lat, site.lon, site_h_asl,
            lat_grid, lon_grid, cell_elev, dist_km,
            site.frequency, elev, bbox,
            site_ground_asl=site.elevation_m,
            method=method,
        )
        # DSM: add statistical clutter loss on top of diffraction
        clutter = DSM_CLUTTER_DB if mode == "DSM" else 0.0
        t_loss  = diff_loss + clutter
    else:
        dem_valid_pct = 0.0
        dem_source    = DEM_SOURCE_NONE
        valid_mask    = None
        # Pseudo-random fallback
        seed   = int(abs(site.lat) * 1000 + abs(site.lon) * 1000)
        relief = min(1.0, max(0.0, site.elevation_m / 500.0))
        t_loss = _pseudorandom_terrain_loss(lat_grid, lon_grid, seed, mode, relief)

    # ── RSSI grid ─────────────────────────────────────────────────────────────
    rssi = site.tx_power - fspl - t_loss + h_gain   # (R, R)

    # ── Mask cells outside analysis radius or too close ───────────────────────
    mask = (dist_km <= site.max_radius) & (dist_km >= 0.02)
    rssi = np.where(mask, rssi, np.nan).astype(np.float32)

    # ── Statistics ────────────────────────────────────────────────────────────
    dist = level_distribution_from_grid(rssi, site.rx_threshold)
    with np.errstate(invalid="ignore"):
        any_sig = rssi[mask & (rssi > ANY_SIGNAL_DBM)]
    rssi_avg = round(float(np.mean(any_sig)), 1) if any_sig.size else 0.0
    rssi_max = round(float(np.max(any_sig)),  1) if any_sig.size else 0.0
    rssi_min = round(float(np.min(any_sig)),  1) if any_sig.size else 0.0
    # Risk input: minimum over ALL in-radius cells, including "none" (< -110)
    in_radius    = rssi[mask]
    in_radius    = in_radius[~np.isnan(in_radius)]
    rssi_min_all = round(float(np.min(in_radius)), 1) if in_radius.size else None

    if has_dem:
        model = (f"ITU-R P.526 "
                 f"{'Deygout multi-edge' if method == 'deygout' else 'single knife-edge'}"
                 f" diffraction, k={K_FACTOR:.3f} earth curvature")
        if mode == "DSM":
            model += f" + DSM clutter {DSM_CLUTTER_DB:g} dB"
    else:
        model = "pseudo-random terrain fallback (no DEM – not ITU-R P.526)"

    metadata = {
        "site_lat":               site.lat,
        "site_lon":               site.lon,
        "mode":                   mode,
        "frequency":              site.frequency,
        "covered_pct":            dist["covered_pct"],
        "covered_pct_any_signal": dist["covered_pct_any_signal"],
        "threshold_dbm":          site.rx_threshold,
        "weak_pct":               dist["weak_pct"],
        "level_distribution":     dist["level_distribution"],
        "model":                  model,
        "dem_source":             dem_source,
        "dem_valid_pct":          dem_valid_pct,
        "diffraction_method":     method if has_dem else None,
        "k_factor":               round(K_FACTOR, 4),
        "height_gain_db":         round(h_gain, 2),
        "resolution":             R,
        "bbox": {
            "sw_lat": bbox.sw_lat, "sw_lon": bbox.sw_lon,
            "ne_lat": bbox.ne_lat, "ne_lon": bbox.ne_lon,
        },
    }

    # ── Optional GeoJSON / cell list ──────────────────────────────────────────
    cells: List[CoverageCell] = []
    if build_geojson:
        geojson = rssi_grid_to_geojson(rssi, bbox, metadata)
        cells   = [CoverageCell(lat=la, lon=lo, rssi=round(rv, 1), level=lv)
                   for la, lo, rv, lv in _iter_grid_cells(rssi, bbox)]
    else:
        geojson = {"type": "FeatureCollection", "features": [], "metadata": dict(metadata)}

    return CoverageStats(
        cells=cells,
        covered_pct=dist["covered_pct"],
        rssi_avg=rssi_avg,
        rssi_max=rssi_max,
        rssi_min=rssi_min,
        duration_sec=max(round(time.time() - t0, 2), 0.01),
        geojson=geojson,
        covered_pct_any_signal=dist["covered_pct_any_signal"],
        level_distribution=dist["level_distribution"],
        weak_pct=dist["weak_pct"],
        threshold_dbm=site.rx_threshold,
        dem_source=dem_source,
        dem_valid_pct=dem_valid_pct,
        metadata=metadata,
        rssi_grid=rssi,
        mask=mask,
        rssi_min_all=rssi_min_all,
        valid_mask=valid_mask,
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

    site_elev_asl / cell_elev_asl are GROUND elevations of the endpoints. They
    are used (linear interpolation) for points outside the grid bbox and for
    points whose DEM value is unknown (NaN).
    """
    R        = elev_grid.shape[0]
    total_km = haversine_km(site_lat, site_lon, cell_lat, cell_lon)

    profile = []
    for k in range(n + 1):
        t   = k / n
        lat = site_lat + t * (cell_lat - site_lat)
        lon = site_lon + t * (cell_lon - site_lon)

        # Map to fractional grid coordinates (cell-centre convention)
        row_f, col_f = _grid_coords(lat, lon, bbox, R)

        elev = _bilinear_scalar(elev_grid, row_f, col_f)
        if math.isnan(elev):
            # Outside grid / unknown: linear interpolation between known endpoints
            elev = site_elev_asl + t * (cell_elev_asl - site_elev_asl)

        profile.append((t * total_km, elev))

    return profile


def _profile_diffraction_loss(
    profile:      List[tuple],
    tx_h_asl:     float,
    rx_h_asl:     float,
    freq_mhz:     int,
    method:       Optional[str] = None,
) -> float:
    """
    Diffraction loss along a terrain profile (Deygout multi-edge by default).

    Intermediate profile points (tx/rx endpoints skipped) are raised by the
    earth bulge d1·d2/(2·k·R_e); NaN samples are ignored (no obstacle).

    Args:
        profile:   list of (dist_km, terrain_elev_m) from _sample_profile_elev
        tx_h_asl:  transmitter height ASL (elevation_m + ant_height)
        rx_h_asl:  receiver height ASL (cell elevation + RX_HEIGHT_AGL)
        freq_mhz:  carrier frequency
        method:    "deygout" | "single" (None → DIFFRACTION_METHOD)

    Returns:
        Diffraction loss in dB (0 if clear LOS).
    """
    method = _resolve_method(method)
    if len(profile) < 3:
        return 0.0

    wavelength = 300.0 / freq_mhz
    total_d_m  = profile[-1][0] * 1000

    if total_d_m <= 0:
        return 0.0

    xs: List[float] = []
    hs: List[float] = []
    for dist_km, terrain_elev in profile[1:-1]:
        if terrain_elev is None or math.isnan(terrain_elev):
            continue
        d1 = dist_km * 1000
        d2 = total_d_m - d1
        if d1 <= 0 or d2 <= 0:
            continue
        xs.append(d1)
        hs.append(terrain_elev + d1 * d2 / (2.0 * R_EFF_M))

    return _deygout_loss_scalar(xs, hs, total_d_m, tx_h_asl, rx_h_asl, wavelength, method)


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
    """Four-level operational risk rating (compat wrapper → risk.assess_risk)."""
    max_dead = max((dz.length_km for dz in dead_zones), default=0.0)
    return assess_risk(covered_pct, max_dead_length_km=max_dead)[0]


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
    """Compat wrapper → risk.build_route_recommendations."""
    return build_route_recommendations(
        site, covered_pct, dead_zones, risk_level,
        rssi_min=rssi_min, rssi_avg=rssi_avg,
        total_length_km=total_length_km, mode=mode,
    )


def calculate_cross_section(
    site:       SiteParams,
    site_name:  str,
    waypoints:  list,
    mode:       str,
    resolution: int = 5,
    elev_grid:  Optional[np.ndarray] = None,
    bbox:       Optional[BoundingBox] = None,
    diffraction_method: Optional[str] = None,
) -> CrossSectionStats:
    """
    Compute RSSI along a multi-point route and detect dead zones.

    When elev_grid + bbox are supplied, uses real terrain profile +
    ITU-R P.526 Deygout diffraction (k=4/3 earth) for each route point.
    NaN cells in elev_grid are treated as unknown terrain.
    Falls back to pseudo-random terrain when DEM is unavailable.

    Coverage uses the same definition as area coverage: a point is covered
    iff rssi >= site.rx_threshold; dead zones are contiguous runs of points
    below the threshold. covered_pct_any_signal keeps the old (> -110 dBm)
    figure, and signal_distribution still covers all six levels. Each
    CrossSectionPoint carries that `covered` flag (computed from the
    unrounded RSSI) and dead zones are built from exactly the same flag.

    Risk uses rssi_min_all (minimum over every route point, including
    "none" points below -110 dBm); rssi_min / rssi_avg / rssi_max remain
    any-signal statistics for display.

    Args:
        site:       transmitter parameters
        site_name:  display name
        waypoints:  list of {"lat", "lon"} route points (minimum 2)
        mode:       "DTM" or "DSM"
        resolution: sample points per km along the route
        elev_grid:  optional elevation grid for real terrain
        bbox:       bbox that the elev_grid covers
        diffraction_method: "deygout" (default) or "single"

    Returns:
        CrossSectionStats with per-point RSSI, dead zones, risk, recommendations.
    """
    t0        = time.time()
    method    = _resolve_method(diffraction_method)
    pts       = _interpolate_route(waypoints, resolution)
    seed      = int(abs(site.lat) * 1000 + abs(site.lon) * 1000)
    use_real  = (elev_grid is not None and bbox is not None)
    h_gain    = effective_height_gain_db(site.ant_height, use_real)
    site_h    = site.elevation_m + site.ant_height
    relief    = min(1.0, max(0.0, site.elevation_m / 500.0))

    if use_real:
        elev_grid     = np.asarray(elev_grid, dtype=np.float32)
        dem_source    = DEM_SOURCE_TERRARIUM
        dem_valid_pct = round(float((~np.isnan(elev_grid)).mean()) * 100, 1)
        R             = elev_grid.shape[0]
    else:
        dem_source    = DEM_SOURCE_NONE
        dem_valid_pct = 0.0

    result_pts:    List[CrossSectionPoint] = []
    rssi_values:   List[float]             = []   # points with any signal
    rssi_all:      List[float]             = []   # every point (incl. "none")
    level_counts = {k: 0 for k in LEVELS}

    for p in pts:
        d_site = max(haversine_km(site.lat, site.lon, p["lat"], p["lon"]), 0.02)

        if use_real:
            # Fetch cell elevation from grid (outside grid / NaN → site ground)
            row_f, col_f = _grid_coords(p["lat"], p["lon"], bbox, R)
            cell_elev = _bilinear_scalar(elev_grid, row_f, col_f)
            if math.isnan(cell_elev):
                cell_elev = site.elevation_m

            rx_h     = cell_elev + RX_HEIGHT_AGL
            profile  = _sample_profile_elev(
                site.lat, site.lon, site.elevation_m,
                p["lat"], p["lon"], cell_elev,
                elev_grid, bbox,
            )
            diff_loss = _profile_diffraction_loss(profile, site_h, rx_h,
                                                  site.frequency, method)
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
        rssi_all.append(rssi)

        result_pts.append(CrossSectionPoint(
            dist_along=round(p["dist_along"], 3),
            dist_from_site=round(d_site, 3),
            lat=round(p["lat"], 6),
            lon=round(p["lon"], 6),
            rssi=round(rssi, 1),
            level=level,
            covered=bool(rssi >= site.rx_threshold),   # unrounded RSSI
        ))

    # ── Coverage relative to rx_threshold (same definition as area coverage) ──
    # p.covered uses the unrounded RSSI so the decision matches the model.
    threshold   = site.rx_threshold
    total       = len(result_pts)
    n_covered   = sum(1 for p in result_pts if p.covered)
    covered_pct = round(n_covered / total * 100, 1) if total else 0.0
    covered_pct_any_signal = (round((total - level_counts["none"]) / total * 100, 1)
                              if total else 0.0)
    # RSSI stats over points with any signal (> -110 dBm), as for area coverage
    rssi_avg    = round(sum(rssi_values) / len(rssi_values), 1) if rssi_values else 0
    rssi_min    = round(min(rssi_values), 1)                     if rssi_values else 0
    rssi_max    = round(max(rssi_values), 1)                     if rssi_values else 0
    # Risk input: minimum over ALL points, including "none" (< -110 dBm)
    finite_all   = [v for v in rssi_all if not math.isnan(v)]
    rssi_min_all = round(min(finite_all), 1) if finite_all else None

    # ── Dead zone detection: contiguous runs of points below rx_threshold ─────
    dead_zones: List[DeadZone] = []
    in_dead, dz_start = False, None

    for p in result_pts:
        if not p.covered:
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
    weak_pct = ((level_counts["weak"] + level_counts["marginal"]) / total * 100
                if total else 0.0)
    max_dead = max((dz.length_km for dz in dead_zones), default=0.0)
    risk, risk_score = assess_risk(
        covered_pct,
        max_dead_length_km=max_dead,
        weak_pct=weak_pct,
        rssi_min=rssi_min_all,
    )
    recs = build_route_recommendations(
        site, covered_pct, dead_zones, risk,
        rssi_min=rssi_min, rssi_avg=rssi_avg,
        total_length_km=total_length,
        mode=mode,
        dem_source=dem_source,
        dem_valid_pct=dem_valid_pct if use_real else None,
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
        risk_score=risk_score,
        dem_source=dem_source,
        dem_valid_pct=dem_valid_pct,
        covered_pct_any_signal=covered_pct_any_signal,
        threshold_dbm=threshold,
        rssi_min_all=rssi_min_all,
    )
