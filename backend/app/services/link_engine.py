"""
Point-to-Point Link Budget Engine
─────────────────────────────────
Pure (no DB, no network) single-link analysis between one transmitter site
and one receiver point, plus a "best server" ranking over candidate sites.

  1. Link budget    – calculate_link()
  2. Best servers   – best_servers()     (caller passes distance-filtered candidates)
  3. DEM bbox       – link_bbox()        (bbox the API fetches the DEM for)

Propagation model (same physics as coverage_engine)
───────────────────────────────────────────────────
  RSSI = Tx_power − FSPL(d, f) − DiffractionLoss − ClutterLoss
         + HeightGain(tx AGL) + RxAntennaGain

  FSPL             : Friis, d clamped to ≥ MIN_LINK_KM (20 m)
  DiffractionLoss  : ITU-R P.526-15 Deygout (coverage_engine._profile_diffraction_loss)
                     on an n-segment terrain profile, terrain raised
                     by the earth bulge d1·d2 / (2·k·R_e), k = K_FACTOR.
                     NaN DEM samples are replaced by the linear ground between
                     the endpoints (same rule as calculate_cross_section).
                     No DEM → 0 dB (no terrain knowledge; flagged).
                     n = clamp(⌈d / cell_m⌉, profile_points, max_profile_points)
                     with cell_m the DEM cell size along the path, so no DEM
                     cell (e.g. a one-cell-wide ridge) is skipped on long links.
  ClutterLoss      : DSM_CLUTTER_DB when mode == "DSM", else 0
  HeightGain       : effective_height_gain_db(site.ant_height, has_dem)
  RxAntennaGain    : rx.antenna_gain_dbi

  margin = RSSI − rx.sensitivity_dbm

Geometry and the earth bulge
────────────────────────────
  tx_h_asl = tx_ground_asl + site.ant_height
  rx_h_asl = rx_ground_asl + rx.height_agl, rx_ground_asl = DEM(rx) (bilinear,
             cell-centre); DEM NaN / outside / no DEM → tx ground (flagged).

  Profile points report `terrain_m` (raw DEM, None where unknown) and
  `los_m` = chord height − bulge, i.e. the ray height drawn over a FLAT
  earth.  Hence  los_m − terrain_m = chord − (terrain + bulge)  – exactly the
  clearance the diffraction model sees.  `fresnel_m` is the first Fresnel
  radius r1 = √(λ·d1·d2/d) at that point (draw los_m ± fresnel_m).

  For each intermediate point  ν = −√2 · clearance / r1  (P.526 eq. 26).
    is_los                ⇔ worst (max) ν ≤ 0  (no terrain cuts the ray;
                            the Fresnel zone may still be partly obstructed,
                            which then shows as 0–6 dB diffraction loss)
    fresnel_clearance_pct = min(clearance / r1) · 100, clamped to −100..100
                            (100 = first Fresnel zone fully clear; < 60 is
                            the classic "insufficient clearance" rule)
    worst_obstacle        = max-ν point, reported only when ν ≥ NU_MIN
                            (it actually contributes diffraction loss)

  required_rx_height_m: 0.0 when margin ≥ 0 already; otherwise the lowest
  rx AGL in RX_HEIGHT_STEP_M steps up to RX_HEIGHT_MAX_M giving margin ≥ 0;
  None when no such height exists (or no DEM: height has no effect then).
"""
import math
import time
from dataclasses import dataclass, field
from typing import Any, Hashable, List, Optional, Sequence, Tuple

import numpy as np

from app.services.coverage_engine import (
    DEM_SOURCE_NONE,
    DEM_SOURCE_TERRARIUM,
    DSM_CLUTTER_DB,
    NU_MIN,
    R_EFF_M,
    RX_HEIGHT_AGL,
    BoundingBox,
    SiteParams,
    _bilinear_scalar,
    _grid_coords,
    _profile_diffraction_loss,
    classify_rssi,
    effective_height_gain_db,
    fresnel_radius_m,
    fspl_db,
    haversine_km,
)


# ── Constants ──────────────────────────────────────────────────────────────────

MIN_LINK_KM          = 0.02    # 20 m distance clamp (same as coverage mask)
LINK_PROFILE_POINTS  = 80      # profile segments → points = n + 1 (incl. endpoints)
BEST_PROFILE_POINTS  = 40
MAX_PROFILE_POINTS   = 1000    # cap for DEM-resolution profile sampling (calculate_link)
BEST_MAX_PROFILE_PTS = 400     # same cap for best_servers (many links per request)
M_PER_DEG_LAT        = 111_320.0
RX_HEIGHT_MAX_M      = 60.0    # search ceiling for required_rx_height_m
RX_HEIGHT_STEP_M     = 0.5
MARGIN_OK_DB         = 10.0    # margin ≥ this → link "תקין"
FRESNEL_OK_PCT       = 60.0    # classic 60 % first-Fresnel-zone clearance rule
DEM_PARTIAL_PCT      = 95.0    # below this the DEM is reported as partial


# ── Data classes ───────────────────────────────────────────────────────────────

@dataclass
class Receiver:
    lat:             float
    lon:             float
    height_agl:      float = RX_HEIGHT_AGL   # metres above ground
    sensitivity_dbm: float = -105.0
    antenna_gain_dbi: float = 0.0
    name:            str   = ""


@dataclass
class LinkResult:
    distance_km:           float
    bearing_deg:           float            # tx → rx, 0–360 (0 = north, clockwise)
    tx_h_asl:              float            # antenna height ASL (ground + AGL)
    rx_h_asl:              float
    tx_ground_asl:         float
    rx_ground_asl:         float
    fspl_db:               float
    diffraction_db:        float
    clutter_db:            float
    height_gain_db:        float
    rx_gain_db:            float
    rssi_dbm:              float
    margin_db:             float            # rssi − sensitivity
    level:                 str              # classify_rssi
    is_los:                bool             # worst ν ≤ 0
    fresnel_clearance_pct: float            # min clearance / r1 · 100, −100..100
    worst_obstacle:        Optional[dict]   # {dist_km, lat, lon, terrain_m, los_m, nu}
    profile:               List[dict]       # {dist_km, lat, lon, terrain_m, los_m, fresnel_m}
    required_rx_height_m:  Optional[float]  # 0.0 = already OK, None = not achievable
    dem_source:            str
    dem_valid_pct:         float
    mode:                  str
    recommendations:       List[str] = field(default_factory=list)
    duration_sec:          float     = 0.0
    rx_ground_fallback:    bool      = False   # rx ground unknown (NaN / outside / no DEM)
                                               # → tx ground assumed, profile[-1].terrain_m = None
    profile_valid_pct:     float     = 0.0     # % profile samples with known DEM


# ── Geometry helpers ───────────────────────────────────────────────────────────

def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    Initial great-circle bearing from point 1 to point 2, degrees 0–360
    (0 = north, 90 = east):
        θ = atan2(sin Δλ · cos φ2, cos φ1 · sin φ2 − sin φ1 · cos φ2 · cos Δλ)
    """
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl     = math.radians(lon2 - lon1)
    x = math.sin(dl) * math.cos(p2)
    y = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    if x == 0.0 and y == 0.0:
        return 0.0
    return (math.degrees(math.atan2(x, y)) + 360.0) % 360.0


def link_bbox(site_lat: float, site_lon: float,
              rx_lat: float, rx_lon: float,
              margin_deg: float = 0.01) -> BoundingBox:
    """Bounding box covering both link endpoints plus margin_deg on every side."""
    m = abs(margin_deg)
    return BoundingBox(
        sw_lat=min(site_lat, rx_lat) - m, sw_lon=min(site_lon, rx_lon) - m,
        ne_lat=max(site_lat, rx_lat) + m, ne_lon=max(site_lon, rx_lon) + m,
    )


def _unwrap_grid(elev_grid: Any) -> Tuple[Optional[np.ndarray], Optional[str]]:
    """Accept a raw ndarray or a terrain.ElevationGrid (uses .elev / .source)."""
    if elev_grid is None:
        return None, None
    if hasattr(elev_grid, "elev"):
        return np.asarray(elev_grid.elev, dtype=np.float32), getattr(elev_grid, "source", None)
    return np.asarray(elev_grid, dtype=np.float32), None


def _dem_at(grid: np.ndarray, bbox: BoundingBox, lat: float, lon: float) -> float:
    """Bilinear DEM lookup (cell-centre convention, H × W grid); NaN = unknown."""
    H, W = grid.shape
    row_f, _ = _grid_coords(lat, lon, bbox, H)
    _, col_f = _grid_coords(lat, lon, bbox, W)
    return _bilinear_scalar(grid, row_f, col_f)


# ── Core path evaluation ───────────────────────────────────────────────────────

@dataclass
class _Path:
    """Sampled terrain profile shared by all rx-height evaluations of one link."""
    ts:       List[float]             # fraction along the path, 0 … 1
    lats:     List[float]
    lons:     List[float]
    dists_km: List[float]
    raw:      List[Optional[float]]   # DEM terrain (None = unknown)
    filled:   List[float]             # NaN → linear ground between endpoints
    total_km: float


def _cell_m_along_path(grid: np.ndarray, bbox: BoundingBox,
                       lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    Ground length (m) of one DEM cell measured along the tx→rx direction θ:
        cell_m = 1 / max(|cos θ| / Δy, |sin θ| / Δx)
    (Δy, Δx = cell height / width in metres) – i.e. the path distance after
    which the profile has advanced one cell in its dominant axis.
    """
    H, W  = grid.shape
    mid   = math.radians((bbox.ne_lat + bbox.sw_lat) / 2.0)
    dy    = (bbox.ne_lat - bbox.sw_lat) / H * M_PER_DEG_LAT
    dx    = (bbox.ne_lon - bbox.sw_lon) / W * M_PER_DEG_LAT * max(math.cos(mid), 1e-6)
    th    = math.radians(bearing_deg(lat1, lon1, lat2, lon2))
    rate  = max(abs(math.cos(th)) / max(dy, 1e-9), abs(math.sin(th)) / max(dx, 1e-9))
    return 1.0 / rate if rate > 0 else min(dy, dx)


def _profile_segments(dist_km: float, cell_m: Optional[float],
                      min_n: int, max_n: int) -> int:
    """n = clamp(⌈dist_m / cell_m⌉, min_n, max_n) (min_n when cell size unknown)."""
    lo = max(int(min_n), 2)
    if not cell_m or cell_m <= 0:
        return lo
    n = int(math.ceil(dist_km * 1000.0 / cell_m))
    return max(lo, min(n, max(int(max_n), lo)))


def _cell_centre_ts(grid: np.ndarray, bbox: BoundingBox,
                    lat1: float, lon1: float, lat2: float, lon2: float) -> List[float]:
    """
    Path fractions t ∈ (0, 1) where the tx→rx line passes the cell-centre
    lines of its dominant raster axis (integer row_f or col_f). Sampling there
    makes the bilinear lookup exact along that axis, so a one-cell-wide ridge
    is seen at its full height instead of being smeared between two samples.
    """
    H, W   = grid.shape
    r0, _  = _grid_coords(lat1, lon1, bbox, H)
    _, c0  = _grid_coords(lat1, lon1, bbox, W)
    r1, _  = _grid_coords(lat2, lon2, bbox, H)
    _, c1  = _grid_coords(lat2, lon2, bbox, W)
    f0, f1 = (c0, c1) if abs(c1 - c0) >= abs(r1 - r0) else (r0, r1)
    df     = f1 - f0
    if abs(df) < 1e-12:
        return []
    lo, hi = math.floor(min(f0, f1)) + 1, math.ceil(max(f0, f1)) - 1
    return [(m - f0) / df for m in range(int(lo), int(hi) + 1)]


def _path_ts(n: int, centres: Sequence[float]) -> List[float]:
    """
    Exactly n + 1 path fractions (0 … 1) that include every DEM cell centre:

      * len(centres) ≥ n − 1 → endpoints + centres (one sample per cell)
      * otherwise            → uniform n-segment grid in which the sample
                               nearest to each centre is moved onto it
                               (centres are sparser than samples, so each
                               centre claims a distinct sample).
    """
    cs = sorted(t for t in centres if 0.0 < t < 1.0)
    if len(cs) >= n - 1:
        return [0.0] + cs + [1.0]
    ts   = [k / n for k in range(n + 1)]
    used = set()
    for c in cs:
        k = int(round(c * n))
        if 0 < k < n and k not in used:
            ts[k] = c
            used.add(k)
    return ts


def _sample_path(site_lat: float, site_lon: float, tx_ground: float,
                 rx_lat: float, rx_lon: float, rx_ground: float,
                 grid: Optional[np.ndarray], bbox: Optional[BoundingBox],
                 ts: Sequence[float], rx_ground_known: bool = True) -> _Path:
    total_km = haversine_km(site_lat, site_lon, rx_lat, rx_lon)
    n = len(ts) - 1
    p = _Path([], [], [], [], [], [], total_km)
    for k, t in enumerate(ts):
        lat = site_lat + t * (rx_lat - site_lat)
        lon = site_lon + t * (rx_lon - site_lon)
        lin = tx_ground + t * (rx_ground - tx_ground)
        if k == 0:
            raw: Optional[float] = tx_ground
        elif k == n:
            raw = rx_ground if rx_ground_known else None   # fallback → unknown
        elif grid is not None and bbox is not None:
            v   = _dem_at(grid, bbox, lat, lon)
            raw = None if math.isnan(v) else v
        else:
            raw = None
        p.ts.append(t)
        p.lats.append(lat)
        p.lons.append(lon)
        p.dists_km.append(t * total_km)
        p.raw.append(raw)
        p.filled.append(lin if raw is None else raw)
    return p


def _diffraction(path: _Path, tx_h: float, rx_h: float, freq: int, has_dem: bool) -> float:
    if not has_dem:
        return 0.0
    prof = list(zip(path.dists_km, path.filled))
    return max(_profile_diffraction_loss(prof, tx_h, rx_h, freq), 0.0)


def _geometry(path: _Path, tx_h: float, rx_h: float, freq: int):
    """
    Profile rows + worst-ν point + min Fresnel clearance.
    Returns (rows, worst_nu, worst_index, clearance_pct).
    """
    wavelength = 300.0 / freq
    total_m    = path.total_km * 1000.0
    n          = len(path.dists_km) - 1
    rows: List[dict] = []
    worst_nu, worst_i = -math.inf, -1
    min_ratio = math.inf
    for k in range(n + 1):
        t     = path.ts[k]
        d1    = path.dists_km[k] * 1000.0
        d2    = total_m - d1
        bulge = d1 * d2 / (2.0 * R_EFF_M) if (d1 > 0 and d2 > 0) else 0.0
        chord = tx_h + t * (rx_h - tx_h)
        los   = chord - bulge                      # ray height over a flat earth
        r1    = fresnel_radius_m(d1, d2, wavelength)
        raw   = path.raw[k]
        rows.append({
            "dist_km":   round(path.dists_km[k], 4),
            "lat":       round(path.lats[k], 6),
            "lon":       round(path.lons[k], 6),
            "terrain_m": None if raw is None else round(raw, 1),
            "los_m":     round(los, 1),
            "fresnel_m": round(r1, 2),
        })
        if 0 < k < n and r1 > 0:
            clearance = los - path.filled[k]
            ratio     = clearance / r1
            nu        = -math.sqrt(2.0) * ratio
            if nu > worst_nu:
                worst_nu, worst_i = nu, k
            min_ratio = min(min_ratio, ratio)
    if math.isinf(min_ratio):
        clearance_pct = 100.0
    else:
        clearance_pct = max(-100.0, min(100.0, min_ratio * 100.0))
    return rows, worst_nu, worst_i, clearance_pct


# ── Recommendations (Hebrew) ───────────────────────────────────────────────────

def _link_recommendations(r: LinkResult, has_dem: bool, max_radius_km: Optional[float]) -> List[str]:
    recs: List[str] = []

    # ── 0. Data quality ───────────────────────────────────────────────────────
    if not has_dem:
        recs.append(
            "אזהרה: החישוב בוצע ללא נתוני טופוגרפיה – לא חושבו חסימות טרן "
            "(הפסד עקיפה 0 dB) והתוצאה אופטימית. יש להריץ מחדש כאשר נתוני הגבהים זמינים."
        )
    elif r.profile_valid_pct < DEM_PARTIAL_PCT:
        recs.append(
            f"אזהרה: נתוני הגבהים לאורך התוואי חלקיים ({r.profile_valid_pct:.0f}% זמינים) – "
            "בקטעים ללא נתונים הונח שטח מישורי, ולכן התוצאה עשויה להיות אופטימית."
        )
    if has_dem and r.rx_ground_fallback:
        recs.append(
            f"גובה הקרקע בנקודת הקליטה אינו ידוע – הונח גובה קרקע האתר ({r.tx_ground_asl:.0f} מ')."
        )

    # ── 1. Verdict ────────────────────────────────────────────────────────────
    if r.margin_db >= MARGIN_OK_DB:
        recs.append(f"הקשר תקין – מרווח של {r.margin_db:.1f} dB מעל רגישות המקלט.")
    elif r.margin_db >= 0:
        recs.append(
            f"הקשר גבולי – מרווח של {r.margin_db:.1f} dB בלבד. "
            "ייתכנו ניתוקים בתנועה או בתנאי שטח משתנים; מומלץ לאמת בשטח."
        )
    else:
        recs.append(f"אין קשר – חסרים {-r.margin_db:.1f} dB להגעה לרגישות המקלט.")
        if r.required_rx_height_m is not None and r.required_rx_height_m > 0:
            recs.append(
                f"הגבהת המכשיר ל-{r.required_rx_height_m:g} מ' מעל הקרקע תאפשר קשר."
            )
        else:
            recs.append(
                f"הגבהת המכשיר (עד {RX_HEIGHT_MAX_M:g} מ') אינה מספיקה – נדרש ממסר/אתר אחר."
            )

    # ── 2. Line of sight / obstacle ───────────────────────────────────────────
    ob = r.worst_obstacle
    if has_dem and not r.is_los and ob is not None:
        recs.append(
            f"אין קו ראייה – המכשול העיקרי במרחק {ob['dist_km']:.2f} ק\"מ מהאתר "
            f"({ob['lat']:.4f}°N, {ob['lon']:.4f}°E, גובה קרקע {ob['terrain_m']:.0f} מ'). "
            "ממסר על המכשול או בקרבתו יעקוף את החסימה."
        )
    elif has_dem and r.fresnel_clearance_pct < FRESNEL_OK_PCT:
        recs.append(
            f"קיים קו ראייה אך אזור פרנל הראשון חסום חלקית "
            f"(מרווח {r.fresnel_clearance_pct:.0f}% מתוך 60% הנדרשים)."
        )

    # ── 3. Assumptions ────────────────────────────────────────────────────────
    if r.mode == "DSM":
        recs.append(
            f"מצב DSM: הונח הפסד בנייה/צמחייה סטטיסטי של {DSM_CLUTTER_DB:g} dB."
        )
    if max_radius_km is not None and r.distance_km > max_radius_km:
        recs.append(
            f"נקודת הקליטה מחוץ לרדיוס הפעולה המוגדר לאתר ({max_radius_km:g} ק\"מ)."
        )
    return recs


# ── Link budget ────────────────────────────────────────────────────────────────

def calculate_link(
    site:           SiteParams,
    rx:             Receiver,
    mode:           str,
    elev_grid:      Optional[np.ndarray],
    bbox:           Optional[BoundingBox],
    profile_points: int = LINK_PROFILE_POINTS,
    tx_ground_asl:  Optional[float] = None,
    dem_m_per_px:   Optional[float] = None,
    max_profile_points: int = MAX_PROFILE_POINTS,
) -> LinkResult:
    """
    Point-to-point link budget site → receiver.

        RSSI = Tx_power − FSPL(d, f) − Diffraction(Deygout, bulge)
               − Clutter(DSM) + HeightGain(tx AGL, has_dem) + G_rx
        margin = RSSI − rx.sensitivity_dbm

    Args:
        site:           transmitter parameters
        rx:             receiver point / height / sensitivity / antenna gain
        mode:           "DTM" or "DSM" (DSM adds DSM_CLUTTER_DB)
        elev_grid:      (H, W) float32 metres ASL (row 0 = north, NaN = unknown),
                        or a terrain.ElevationGrid, or None (no terrain)
        bbox:           bbox the grid covers (see link_bbox)
        profile_points: MINIMUM profile segments; with a DEM the path is
                        sampled once per DEM cell:
                        n = clamp(⌈d / cell_m⌉, profile_points, max_profile_points)
                        (points = n + 1, endpoints included)
        tx_ground_asl:  sampled tx ground ASL; overrides site.elevation_m
        dem_m_per_px:   DEM cell size (m); default = derived from grid / bbox
                        along the path direction
        max_profile_points: upper bound on n

    Returns:
        LinkResult (see module docstring for is_los / Fresnel semantics).
    """
    t0     = time.time()
    mode_u = (mode or "DTM").upper()
    grid, grid_src = _unwrap_grid(elev_grid)
    has_dem = grid is not None and bbox is not None
    if not has_dem:
        grid = None

    tx_ground = float(site.elevation_m if tx_ground_asl is None else tx_ground_asl)
    tx_h      = tx_ground + site.ant_height

    rx_ground_fallback = True
    rx_ground = tx_ground
    if has_dem:
        v = _dem_at(grid, bbox, rx.lat, rx.lon)
        if not math.isnan(v):
            rx_ground, rx_ground_fallback = float(v), False

    cell_m = None
    if has_dem:
        cell_m = (float(dem_m_per_px) if dem_m_per_px and dem_m_per_px > 0
                  else _cell_m_along_path(grid, bbox, site.lat, site.lon, rx.lat, rx.lon))
    n_seg  = _profile_segments(haversine_km(site.lat, site.lon, rx.lat, rx.lon),
                               cell_m, profile_points, max_profile_points)
    # One sample per DEM cell, placed on the cell centres, as long as that fits
    # the n_seg budget; otherwise (capped / coarser dem_m_per_px) uniform n_seg.
    centres = (_cell_centre_ts(grid, bbox, site.lat, site.lon, rx.lat, rx.lon)
               if has_dem else [])
    if len(centres) + 1 > min(max(int(max_profile_points), 2), n_seg + 2):
        centres = []          # capped / coarser dem_m_per_px → uniform n_seg
    else:
        n_seg = max(n_seg, len(centres) + 1)           # ⌈d/cell⌉ rounding vs crossings
    ts     = _path_ts(n_seg, centres)
    path   = _sample_path(site.lat, site.lon, tx_ground, rx.lat, rx.lon, rx_ground,
                          grid, bbox, ts, rx_ground_known=not rx_ground_fallback)
    d_km   = max(path.total_km, MIN_LINK_KM)
    fspl   = fspl_db(d_km, site.frequency)
    h_gain = effective_height_gain_db(site.ant_height, has_dem)
    clut   = DSM_CLUTTER_DB if mode_u == "DSM" else 0.0
    base   = site.tx_power - fspl - clut + h_gain + rx.antenna_gain_dbi

    def _rssi_at(h_agl: float) -> Tuple[float, float]:
        diff = _diffraction(path, tx_h, rx_ground + h_agl, site.frequency, has_dem)
        return base - diff, diff

    rx_h        = rx_ground + rx.height_agl
    rssi, diff  = _rssi_at(rx.height_agl)
    margin      = rssi - rx.sensitivity_dbm

    # ── Required receiver height ──────────────────────────────────────────────
    required: Optional[float]
    if margin >= 0:
        required = 0.0
    else:
        required = None
        if has_dem:
            steps = int(round(RX_HEIGHT_MAX_M / RX_HEIGHT_STEP_M))
            for i in range(1, steps + 1):
                h = i * RX_HEIGHT_STEP_M
                if h <= rx.height_agl:
                    continue
                if _rssi_at(h)[0] - rx.sensitivity_dbm >= 0:
                    required = h
                    break

    # ── Geometry: profile rows, LOS, Fresnel clearance ────────────────────────
    rows, worst_nu, worst_i, clear_pct = _geometry(path, tx_h, rx_h, site.frequency)
    is_los = (not has_dem) or worst_nu <= 0.0
    worst: Optional[dict] = None
    if has_dem and worst_i > 0 and worst_nu >= NU_MIN:
        row = rows[worst_i]
        worst = {
            "dist_km":   row["dist_km"],
            "lat":       row["lat"],
            "lon":       row["lon"],
            "terrain_m": round(path.filled[worst_i], 1),
            "los_m":     row["los_m"],
            "nu":        round(worst_nu, 3),
        }

    inner     = path.raw[1:-1]
    prof_pct  = (100.0 * sum(v is not None for v in inner) / len(inner)) if (has_dem and inner) else 0.0
    if has_dem:
        dem_valid_pct = round(float((~np.isnan(grid)).mean()) * 100.0, 1)
        dem_source    = grid_src or DEM_SOURCE_TERRARIUM
    else:
        dem_valid_pct = 0.0
        dem_source    = DEM_SOURCE_NONE

    result = LinkResult(
        distance_km=round(path.total_km, 4),
        bearing_deg=round(bearing_deg(site.lat, site.lon, rx.lat, rx.lon), 1),
        tx_h_asl=round(tx_h, 1),
        rx_h_asl=round(rx_h, 1),
        tx_ground_asl=round(tx_ground, 1),
        rx_ground_asl=round(rx_ground, 1),
        fspl_db=round(fspl, 2),
        diffraction_db=round(diff, 2),
        clutter_db=round(clut, 2),
        height_gain_db=round(h_gain, 2),
        rx_gain_db=round(float(rx.antenna_gain_dbi), 2),
        rssi_dbm=round(rssi, 2),
        margin_db=round(margin, 2),
        level=classify_rssi(rssi),
        is_los=bool(is_los),
        fresnel_clearance_pct=round(clear_pct, 1),
        worst_obstacle=worst,
        profile=rows,
        required_rx_height_m=required,
        dem_source=dem_source,
        dem_valid_pct=dem_valid_pct,
        mode=mode_u,
        rx_ground_fallback=rx_ground_fallback,   # also True without a DEM
        profile_valid_pct=round(prof_pct, 1),
    )
    result.recommendations = _link_recommendations(
        result, has_dem, getattr(site, "max_radius", None))
    result.duration_sec = round(time.time() - t0, 4)
    return result


# ── Best server ranking ────────────────────────────────────────────────────────

def best_servers(
    candidates:     Sequence[Tuple[Hashable, SiteParams]],
    rx:             Receiver,
    mode:           str,
    elev_grid:      Optional[np.ndarray],
    bbox:           Optional[BoundingBox],
    top_n:          int = 5,
    profile_points: int = BEST_PROFILE_POINTS,
    dem_m_per_px:   Optional[float] = None,
    max_profile_points: int = BEST_MAX_PROFILE_PTS,
) -> List[Tuple[Hashable, LinkResult]]:
    """
    Evaluate calculate_link() for every candidate (the caller is responsible
    for distance-filtering them) and return the top_n by RSSI, strongest first
    (ties → shorter distance first).
    """
    results = [(cid, calculate_link(s, rx, mode, elev_grid, bbox, profile_points,
                                         dem_m_per_px=dem_m_per_px,
                                         max_profile_points=max_profile_points))
               for cid, s in candidates]
    results.sort(key=lambda x: (-x[1].rssi_dbm, x[1].distance_km))
    return results[:max(int(top_n), 0)]
