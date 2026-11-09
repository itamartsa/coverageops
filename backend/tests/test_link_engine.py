"""
Point-to-point link engine – unit tests (synthetic DEMs, no network).
Run with: pytest tests/test_link_engine.py -q
"""
import math

import numpy as np
import pytest

from app.services.coverage_engine import (
    BoundingBox, SiteParams, DSM_CLUTTER_DB, haversine_km, classify_rssi,
)
from app.services.link_engine import (
    Receiver, LinkResult, calculate_link, best_servers, link_bbox, bearing_deg,
    RX_HEIGHT_MAX_M,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

BBOX = BoundingBox(sw_lat=31.95, sw_lon=34.95, ne_lat=32.05, ne_lon=35.05)
N    = 200   # 0.1° / 200 ≈ 55 m (lat) × 47 m (lon) per cell

TX_LAT, TX_LON = 32.0, 34.97
RX_LAT, RX_LON = 32.0, 35.03          # ≈ 5.7 km due east


def make_site(**kw) -> SiteParams:
    base = dict(lat=TX_LAT, lon=TX_LON, ant_height=10.0, elevation_m=0.0,
                frequency=900, tx_power=43.0, rx_threshold=-90.0, max_radius=20.0)
    base.update(kw)
    return SiteParams(**base)


def flat_grid(elev: float = 0.0) -> np.ndarray:
    return np.full((N, N), elev, dtype=np.float32)


def ridge_grid(lon0: float, height: float, width_deg: float = 0.0015) -> np.ndarray:
    """North–south Gaussian ridge centred on longitude lon0 (cell-centre lons)."""
    lons = BBOX.sw_lon + (np.arange(N) + 0.5) * (BBOX.ne_lon - BBOX.sw_lon) / N
    row  = height * np.exp(-((lons - lon0) / width_deg) ** 2)
    return np.tile(row, (N, 1)).astype(np.float32)


# ── Flat terrain ──────────────────────────────────────────────────────────────

class TestFlat:
    def test_flat_short_link_is_los(self):
        # ≈1.9 km, 30 m mast: first Fresnel zone essentially clear over flat ground
        # (a 10 m mast at 5.7 km already loses a few dB to ground/bulge intrusion)
        r = calculate_link(make_site(ant_height=30.0), Receiver(RX_LAT, 34.99), "DTM",
                           flat_grid(), BBOX)
        assert isinstance(r, LinkResult)
        assert r.is_los
        assert r.diffraction_db == pytest.approx(0.0, abs=0.5)
        assert r.fresnel_clearance_pct > 0
        assert r.margin_db > 0
        assert r.level == classify_rssi(r.rssi_dbm)
        assert r.required_rx_height_m == 0.0
        # only Fresnel-zone intrusion, nothing cuts the ray
        assert r.worst_obstacle is None or r.worst_obstacle["nu"] <= 0
        assert r.dem_source == "terrarium"
        assert r.dem_valid_pct == 100.0
        assert any("תקין" in s for s in r.recommendations)

    def test_budget_adds_up(self):
        rx = Receiver(RX_LAT, RX_LON, antenna_gain_dbi=3.0, sensitivity_dbm=-100.0)
        r  = calculate_link(make_site(), rx, "DTM", flat_grid(), BBOX)
        expected = 43.0 - r.fspl_db - r.diffraction_db - r.clutter_db + r.height_gain_db + 3.0
        assert r.rssi_dbm == pytest.approx(expected, abs=0.02)
        assert r.margin_db == pytest.approx(r.rssi_dbm + 100.0, abs=0.02)

    def test_distance_matches_haversine(self):
        r = calculate_link(make_site(), Receiver(RX_LAT, RX_LON), "DTM", flat_grid(), BBOX)
        assert r.distance_km == pytest.approx(haversine_km(TX_LAT, TX_LON, RX_LAT, RX_LON), abs=1e-3)

    def test_min_distance_clamp(self):
        r = calculate_link(make_site(), Receiver(TX_LAT, TX_LON), "DTM", flat_grid(), BBOX)
        clamped = calculate_link(make_site(), Receiver(TX_LAT, TX_LON + 0.0002), "DTM",
                                 flat_grid(), BBOX)   # ≈ 19 m
        assert math.isfinite(r.rssi_dbm)
        assert r.fspl_db == pytest.approx(clamped.fspl_db, abs=0.01)

    def test_profile_endpoints_and_size(self):
        site = make_site(elevation_m=0.0)
        r = calculate_link(site, Receiver(RX_LAT, RX_LON, height_agl=2.0), "DTM",
                           flat_grid(), BBOX, profile_points=80)
        assert len(r.profile) >= 81
        assert len(r.profile) <= 1001
        dists = [p["dist_km"] for p in r.profile]
        assert dists == sorted(dists) and len(set(dists)) == len(dists)
        first, last = r.profile[0], r.profile[-1]
        assert (first["lat"], first["lon"]) == (TX_LAT, TX_LON)
        assert (last["lat"], last["lon"]) == (RX_LAT, RX_LON)
        assert first["dist_km"] == 0.0
        assert last["dist_km"] == pytest.approx(r.distance_km, abs=1e-3)
        assert first["los_m"] == pytest.approx(r.tx_h_asl, abs=0.05)
        assert last["los_m"] == pytest.approx(r.rx_h_asl, abs=0.05)
        assert first["fresnel_m"] == 0.0 and last["fresnel_m"] == 0.0

    def test_dsm_lowers_rssi_by_clutter(self):
        a = calculate_link(make_site(), Receiver(RX_LAT, RX_LON), "DTM", flat_grid(), BBOX)
        b = calculate_link(make_site(), Receiver(RX_LAT, RX_LON), "DSM", flat_grid(), BBOX)
        assert a.rssi_dbm - b.rssi_dbm == pytest.approx(DSM_CLUTTER_DB, abs=0.011)
        assert b.clutter_db == DSM_CLUTTER_DB
        assert any("DSM" in s for s in b.recommendations)

    def test_tx_ground_override(self):
        r = calculate_link(make_site(elevation_m=5.0), Receiver(RX_LAT, RX_LON), "DTM",
                           flat_grid(), BBOX, tx_ground_asl=40.0)
        assert r.tx_ground_asl == 40.0
        assert r.tx_h_asl == 50.0


# ── Ridge (NLOS) ──────────────────────────────────────────────────────────────

class TestRidge:
    def test_ridge_blocks_link(self):
        flat  = calculate_link(make_site(), Receiver(RX_LAT, RX_LON), "DTM", flat_grid(), BBOX)
        ridge = calculate_link(make_site(), Receiver(RX_LAT, RX_LON), "DTM",
                               ridge_grid(35.0, 120.0), BBOX)
        assert not ridge.is_los
        assert ridge.diffraction_db > 10
        assert ridge.rssi_dbm < flat.rssi_dbm
        assert ridge.fresnel_clearance_pct < 0
        ob = ridge.worst_obstacle
        assert ob is not None
        assert ob["lon"] == pytest.approx(35.0, abs=0.002)
        assert ob["terrain_m"] > 100
        assert ob["nu"] > 0
        assert any("אין קו ראייה" in s for s in ridge.recommendations)

    def test_required_rx_height_found_and_works(self):
        # Low-power link, ridge close to the receiver → raising rx clears it
        site = make_site(tx_power=0.0)
        grid = ridge_grid(35.021, 30.0)
        r = calculate_link(site, Receiver(RX_LAT, RX_LON), "DTM", grid, BBOX)
        assert r.margin_db < 0
        h = r.required_rx_height_m
        assert h is not None and 1.5 < h <= RX_HEIGHT_MAX_M
        assert any(f"{h:g} מ'" in s for s in r.recommendations)

        ok = calculate_link(site, Receiver(RX_LAT, RX_LON, height_agl=h), "DTM", grid, BBOX)
        assert ok.margin_db >= 0
        assert ok.required_rx_height_m == 0.0
        # minimal: half a metre lower is not enough
        lower = calculate_link(site, Receiver(RX_LAT, RX_LON, height_agl=h - 0.5), "DTM",
                               grid, BBOX)
        assert lower.margin_db < 0

    def test_unfixable_link_needs_relay(self):
        site = make_site(tx_power=-30.0)
        r = calculate_link(site, Receiver(RX_LAT, RX_LON), "DTM", ridge_grid(35.0, 400.0), BBOX)
        assert r.margin_db < 0
        assert r.required_rx_height_m is None
        assert any("ממסר" in s for s in r.recommendations)

    def test_los_consistent_with_clearance(self):
        r = calculate_link(make_site(), Receiver(RX_LAT, RX_LON), "DTM",
                           ridge_grid(35.0, 120.0), BBOX)
        # los_m − terrain_m at the obstacle is the (negative) clearance
        ob = r.worst_obstacle
        assert ob["los_m"] < ob["terrain_m"]


# ── Bearing ───────────────────────────────────────────────────────────────────

class TestBearing:
    @pytest.mark.parametrize("dlat,dlon,expected", [
        (0.02, 0.0, 0.0), (0.0, 0.02, 90.0), (-0.02, 0.0, 180.0), (0.0, -0.02, 270.0),
    ])
    def test_cardinal(self, dlat, dlon, expected):
        site = make_site(lat=32.0, lon=35.0)
        r = calculate_link(site, Receiver(32.0 + dlat, 35.0 + dlon), "DTM", flat_grid(), BBOX)
        assert (r.bearing_deg - expected + 180) % 360 - 180 == pytest.approx(0.0, abs=0.1)

    def test_range(self):
        assert 0.0 <= bearing_deg(32.0, 35.0, 31.99, 34.99) < 360.0


# ── DEM fallbacks ─────────────────────────────────────────────────────────────

class TestDemFallback:
    def test_nan_dem_falls_back(self):
        grid = flat_grid(50.0)
        grid[:, N // 2:] = np.nan                  # east half (incl. rx) unknown
        r = calculate_link(make_site(elevation_m=50.0), Receiver(RX_LAT, RX_LON), "DTM",
                           grid, BBOX, tx_ground_asl=50.0)
        assert r.rx_ground_fallback
        assert r.rx_ground_asl == 50.0
        assert r.profile[-1]["terrain_m"] is None          # unknown, not tx ground
        assert r.profile[0]["terrain_m"] == 50.0
        assert r.dem_valid_pct == pytest.approx(50.0, abs=0.1)
        assert any(p["terrain_m"] is None for p in r.profile)
        assert any("אינו ידוע" in s for s in r.recommendations)
        assert any("חלקיים" in s for s in r.recommendations)

    def test_no_dem(self):
        r = calculate_link(make_site(elevation_m=30.0), Receiver(RX_LAT, RX_LON), "DTM",
                           None, None)
        assert r.dem_source == "none"
        assert r.diffraction_db == 0.0
        assert r.rx_ground_asl == 30.0
        assert r.rx_ground_fallback and r.profile[-1]["terrain_m"] is None
        assert all(p["terrain_m"] is None for p in r.profile[1:-1])
        assert any("ללא נתוני טופוגרפיה" in s for s in r.recommendations)

    def test_rx_outside_grid(self):
        r = calculate_link(make_site(elevation_m=7.0), Receiver(32.2, 35.03), "DTM",
                           flat_grid(), BBOX)
        assert r.rx_ground_fallback and r.rx_ground_asl == 7.0
        assert r.profile[-1]["terrain_m"] is None

    def test_known_rx_ground_in_profile(self):
        r = calculate_link(make_site(), Receiver(RX_LAT, RX_LON), "DTM", flat_grid(12.0), BBOX)
        assert not r.rx_ground_fallback
        assert r.profile[-1]["terrain_m"] == 12.0


# ── Best servers / bbox ───────────────────────────────────────────────────────

class TestBestServersAndBbox:
    def test_best_servers_order_and_top_n(self):
        rx = Receiver(32.0, 35.0)
        cands = [
            ("far",  make_site(lat=32.0, lon=34.96)),
            ("near", make_site(lat=32.0, lon=34.995)),
            ("mid",  make_site(lat=32.0, lon=34.98)),
            (7,      make_site(lat=32.0, lon=34.99, tx_power=60.0)),
        ]
        out = best_servers(cands, rx, "DTM", flat_grid(), BBOX, top_n=3)
        assert len(out) == 3
        rssis = [r.rssi_dbm for _, r in out]
        assert rssis == sorted(rssis, reverse=True)
        assert [cid for cid, _ in out] == [7, "near", "mid"]
        assert len(out[0][1].profile) >= 41

    def test_best_servers_empty(self):
        assert best_servers([], Receiver(32.0, 35.0), "DTM", None, None) == []

    def test_link_bbox_contains_endpoints(self):
        b = link_bbox(32.0, 35.1, 31.9, 34.9, margin_deg=0.01)
        for lat, lon in ((32.0, 35.1), (31.9, 34.9)):
            assert b.sw_lat < lat < b.ne_lat
            assert b.sw_lon < lon < b.ne_lon
        assert b.ne_lat == pytest.approx(32.01) and b.sw_lon == pytest.approx(34.89)


# ── Profile sampling vs DEM resolution ────────────────────────────────────────

LONG_BBOX = BoundingBox(sw_lat=31.98, sw_lon=34.88, ne_lat=32.02, ne_lon=35.12)
LH, LW    = 40, 480      # ≈ 47 m per column along an east–west path


def long_ridge(width_cells: int, centre_col: int = 243, height: float = 400.0) -> np.ndarray:
    g  = np.zeros((LH, LW), dtype=np.float32)
    c0 = centre_col - width_cells // 2
    g[:, c0:c0 + width_cells] = height
    return g


class TestProfileResolution:
    SITE = dict(lat=32.0, lon=34.9, ant_height=30.0, elevation_m=0.0)
    RX   = Receiver(32.0, 35.1)                         # ≈ 18.9 km due east

    def test_samples_scale_with_length_and_cell(self):
        r = calculate_link(make_site(**self.SITE), self.RX, "DTM", long_ridge(1), LONG_BBOX)
        assert r.distance_km > 18
        assert len(r.profile) >= 18_000 / 50            # ≥ one sample per DEM cell
        assert len(r.profile) <= 1001
        s = best_servers([(1, make_site(**self.SITE))], self.RX, "DTM", long_ridge(1), LONG_BBOX)
        assert 41 <= len(s[0][1].profile) <= 401

    def test_one_cell_ridge_not_skipped(self):
        site = make_site(**self.SITE)
        one   = calculate_link(site, self.RX, "DTM", long_ridge(1), LONG_BBOX)
        three = calculate_link(site, self.RX, "DTM", long_ridge(3), LONG_BBOX)
        # The 1-cell ridge is seen at full height, as the same principal edge
        assert one.worst_obstacle["terrain_m"] == pytest.approx(400.0, abs=0.5)
        assert one.worst_obstacle["nu"] == pytest.approx(three.worst_obstacle["nu"], rel=0.02)
        # Principal-edge loss equal within 2 dB. (The 3-cell plateau adds Deygout
        # secondary edges on its flat top, so its total loss is legitimately higher.)
        from app.services.coverage_engine import _knife_edge_loss_db
        j_principal = _knife_edge_loss_db(three.worst_obstacle["nu"])
        assert one.diffraction_db == pytest.approx(j_principal, abs=2.0)
        assert three.diffraction_db >= one.diffraction_db
        # Old fixed 80-point sampling skipped this ridge
        coarse = calculate_link(site, self.RX, "DTM", long_ridge(1), LONG_BBOX,
                                max_profile_points=80)
        assert coarse.diffraction_db < one.diffraction_db - 20

    @pytest.mark.parametrize("col", [237, 240, 251])
    def test_ridge_position_independent(self, col):
        site = make_site(**self.SITE)
        ref  = calculate_link(site, self.RX, "DTM", long_ridge(1, 243), LONG_BBOX)
        r    = calculate_link(site, self.RX, "DTM", long_ridge(1, col), LONG_BBOX)
        assert r.diffraction_db == pytest.approx(ref.diffraction_db, abs=2.0)

    def test_dem_m_per_px_override(self):
        site = make_site(**self.SITE)
        r = calculate_link(site, self.RX, "DTM", long_ridge(1), LONG_BBOX, dem_m_per_px=500.0)
        assert len(r.profile) == 81                       # ⌈18.9 km / 500 m⌉ = 38 < 80 → 80
