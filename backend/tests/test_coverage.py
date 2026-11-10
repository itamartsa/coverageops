"""
CoverageOps – Unit & Integration Tests
Run with: pytest tests/ -v
"""
import math

import numpy as np
import pytest
from io import BytesIO
from PIL import Image

from app.services import coverage_engine as ce
from app.services.coverage_engine import (
    SiteParams, BoundingBox, calculate_coverage, calculate_cross_section,
    fspl_db, haversine_km, classify_rssi, height_gain_db,
    effective_height_gain_db, HEIGHT_GAIN_CAP_DB, K_FACTOR, R_EFF_M,
    encode_rssi_grid, decode_rssi_grid, rssi_grid_to_png, rssi_grid_to_geojson,
    level_distribution_from_grid, LEVELS, LEVEL_RGBA,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def make_site(**kw) -> SiteParams:
    base = dict(lat=32.0, lon=35.0, ant_height=10.0, elevation_m=0.0,
                frequency=900, tx_power=43.0, rx_threshold=-90.0, max_radius=5.0)
    base.update(kw)
    return SiteParams(**base)


def flat_profile(total_km: float, n: int = 40, elev: float = 0.0):
    """Flat terrain profile [(dist_km, elev_m)] incl. both endpoints."""
    return [(total_km * k / n, elev) for k in range(n + 1)]


def ridge_profile(total_km: float, n: int = 40, at: float = 0.5, height: float = 80.0):
    prof = flat_profile(total_km, n)
    k = int(round(at * n))
    prof[k] = (prof[k][0], height)
    return prof


SMALL_BBOX = BoundingBox(sw_lat=31.95, sw_lon=34.95, ne_lat=32.05, ne_lon=35.05)


# ── Unit: Path loss ───────────────────────────────────────────────────────────
class TestFSPL:
    def test_known_value(self):
        """1 km at 900 MHz ≈ 91.5 dB"""
        result = fspl_db(1.0, 900)
        assert 89 < result < 93

    def test_doubles_with_distance(self):
        """Doubling distance adds ~6 dB"""
        a = fspl_db(1.0, 900)
        b = fspl_db(2.0, 900)
        assert abs((b - a) - 6.02) < 0.5

    def test_higher_freq_more_loss(self):
        assert fspl_db(5, 2600) > fspl_db(5, 900)

    def test_zero_distance(self):
        assert fspl_db(0, 900) == 0


# ── Unit: Distance ────────────────────────────────────────────────────────────
class TestHaversine:
    def test_same_point(self):
        assert haversine_km(32, 35, 32, 35) == 0.0

    def test_known_distance(self):
        """Tel Aviv to Jerusalem ≈ 52 km"""
        d = haversine_km(32.0853, 34.7818, 31.7683, 35.2137)
        assert 50 < d < 55

    def test_symmetry(self):
        a = haversine_km(31, 35, 32, 36)
        b = haversine_km(32, 36, 31, 35)
        assert abs(a - b) < 0.001


# ── Unit: RSSI classification ─────────────────────────────────────────────────
class TestClassifyRSSI:
    def test_levels(self):
        assert classify_rssi(-60)  == "excellent"
        assert classify_rssi(-75)  == "good"
        assert classify_rssi(-85)  == "medium"
        assert classify_rssi(-95)  == "weak"
        assert classify_rssi(-105) == "marginal"
        assert classify_rssi(-115) == "none"

    def test_boundaries(self):
        assert classify_rssi(-70)  == "good"        # boundary: strictly > -70 is excellent
        assert classify_rssi(-69.9) == "excellent"
        assert classify_rssi(-70.1) == "good"

    def test_vectorised_matches_scalar(self):
        vals = np.array([-50, -70, -70.1, -80, -85, -90, -99.9, -100, -110, -110.1, -130],
                        dtype=np.float32)
        idx = ce._level_index_grid(vals)
        assert [LEVELS[i] for i in idx] == [classify_rssi(float(v)) for v in vals]


# ── Unit: Height gain (א5) ────────────────────────────────────────────────────
class TestHeightGain:
    def test_baseline_is_zero(self):
        """1.5m antenna = 0 dB gain"""
        assert height_gain_db(1.5) == pytest.approx(0.0, abs=0.01)

    def test_higher_antenna_more_gain(self):
        assert height_gain_db(10) > height_gain_db(5)

    def test_gain_formula(self):
        """6m antenna should give ~12 dB over 1.5m"""
        gain = height_gain_db(6)
        assert 11 < gain < 13

    def test_no_dem_keeps_full_gain(self):
        assert effective_height_gain_db(30, has_dem=False) == pytest.approx(height_gain_db(30))

    def test_dem_gain_is_capped(self):
        assert height_gain_db(30) > HEIGHT_GAIN_CAP_DB
        assert effective_height_gain_db(30, has_dem=True) == pytest.approx(HEIGHT_GAIN_CAP_DB)
        # below the cap the formula is unchanged
        assert effective_height_gain_db(2.5, has_dem=True) == pytest.approx(height_gain_db(2.5))

    def test_coverage_uses_capped_gain_with_dem(self):
        site = make_site(ant_height=30.0, max_radius=5.0)
        flat = np.zeros((20, 20), dtype=np.float32)
        r = calculate_coverage(site, SMALL_BBOX, "DTM", resolution=20, elev_grid=flat)
        assert r.metadata["height_gain_db"] == pytest.approx(HEIGHT_GAIN_CAP_DB)
        r0 = calculate_coverage(site, SMALL_BBOX, "DTM", resolution=20)
        assert r0.metadata["height_gain_db"] == pytest.approx(height_gain_db(30.0), abs=0.01)


# ── Integration: Coverage calculation ────────────────────────────────────────
class TestCoverageCalculation:
    @pytest.fixture
    def sample_site(self):
        return make_site()

    @pytest.fixture
    def sample_bbox(self):
        return SMALL_BBOX

    def test_returns_result(self, sample_site, sample_bbox):
        result = calculate_coverage(sample_site, sample_bbox, "DTM", resolution=20)
        assert result is not None
        assert isinstance(result.covered_pct, float)
        assert isinstance(result.rssi_avg, float)

    def test_coverage_percentage_in_range(self, sample_site, sample_bbox):
        result = calculate_coverage(sample_site, sample_bbox, "DTM", resolution=20)
        assert 0 <= result.covered_pct <= 100
        assert 0 <= result.covered_pct_any_signal <= 100

    def test_dsm_lower_coverage_than_dtm(self, sample_site, sample_bbox):
        """DSM adds clutter loss → less coverage than DTM for same site"""
        dtm = calculate_coverage(sample_site, sample_bbox, "DTM", resolution=30)
        dsm = calculate_coverage(sample_site, sample_bbox, "DSM", resolution=30)
        assert dsm.covered_pct <= dtm.covered_pct

    def test_dsm_lower_coverage_than_dtm_with_dem(self, sample_site, sample_bbox):
        flat = np.zeros((20, 20), dtype=np.float32)
        dtm = calculate_coverage(sample_site, sample_bbox, "DTM", 20, elev_grid=flat)
        dsm = calculate_coverage(sample_site, sample_bbox, "DSM", 20, elev_grid=flat)
        assert dsm.covered_pct <= dtm.covered_pct

    def test_geojson_structure(self, sample_site, sample_bbox):
        result = calculate_coverage(sample_site, sample_bbox, "DTM", resolution=15,
                                    build_geojson=True)
        gj = result.geojson
        assert gj["type"] == "FeatureCollection"
        assert isinstance(gj["features"], list)
        assert gj["features"]
        feat = gj["features"][0]
        assert "rssi" in feat["properties"]
        assert "level" in feat["properties"]
        assert "color" in feat["properties"]
        assert len(result.cells) == len(gj["features"])

    def test_higher_power_more_coverage(self, sample_bbox):
        low_power = make_site(tx_power=30)
        hi_power  = make_site(tx_power=50)
        r_low = calculate_coverage(low_power,  sample_bbox, "DTM", resolution=20)
        r_hi  = calculate_coverage(hi_power,   sample_bbox, "DTM", resolution=20)
        assert r_hi.covered_pct >= r_low.covered_pct

    def test_duration_recorded(self, sample_site, sample_bbox):
        result = calculate_coverage(sample_site, sample_bbox, "DTM", resolution=20)
        assert result.duration_sec > 0

    def test_rssi_stats_consistent(self, sample_site, sample_bbox):
        result = calculate_coverage(sample_site, sample_bbox, "DTM", resolution=20)
        assert result.rssi_max >= result.rssi_avg >= result.rssi_min

    def test_bad_elev_shape_rejected(self, sample_site, sample_bbox):
        with pytest.raises(ValueError):
            calculate_coverage(sample_site, sample_bbox, "DTM", 20,
                               elev_grid=np.zeros((10, 10), np.float32))

    def test_bad_method_rejected(self, sample_site, sample_bbox):
        with pytest.raises(ValueError):
            calculate_coverage(sample_site, sample_bbox, "DTM", 20, diffraction_method="foo")


# ── DEM validity / NaN handling (א1) ──────────────────────────────────────────
class TestDemValidity:
    def test_no_dem_labelled_none(self):
        r = calculate_coverage(make_site(), SMALL_BBOX, "DTM", 20)
        assert r.dem_source == "none"
        assert r.metadata["dem_source"] == "none"
        assert r.metadata["dem_valid_pct"] == 0.0
        assert "P.526" not in r.metadata["model"].replace("not ITU-R P.526", "")
        assert r.geojson["metadata"]["dem_source"] == "none"

    def test_dem_labelled_terrarium(self):
        flat = np.zeros((20, 20), dtype=np.float32)
        r = calculate_coverage(make_site(), SMALL_BBOX, "DTM", 20, elev_grid=flat)
        assert r.dem_source == "terrarium"
        assert r.dem_valid_pct == 100.0
        assert "P.526" in r.metadata["model"]

    def test_nan_cells_produce_finite_rssi(self):
        R = 20
        elev = np.full((R, R), 50.0, dtype=np.float32)
        elev[5:12, 3:15] = np.nan            # a block of failed tiles
        r = calculate_coverage(make_site(), SMALL_BBOX, "DTM", R, elev_grid=elev)
        assert r.dem_valid_pct == pytest.approx(100 * (1 - 7 * 12 / R**2), abs=0.1)
        # every in-radius cell must still have a finite RSSI
        assert np.all(np.isfinite(r.rssi_grid[r.mask]))
        assert np.all(np.isnan(r.rssi_grid[~r.mask]))

    def test_all_nan_equals_flat_ground_at_site_elevation(self):
        """Unknown terrain everywhere ⇒ linear fallback ⇒ same as flat DEM at site elevation."""
        R = 16
        site = make_site(elevation_m=120.0)
        nan_grid  = np.full((R, R), np.nan, dtype=np.float32)
        flat_grid = np.full((R, R), 120.0, dtype=np.float32)
        a = calculate_coverage(site, SMALL_BBOX, "DTM", R, elev_grid=nan_grid)
        b = calculate_coverage(site, SMALL_BBOX, "DTM", R, elev_grid=flat_grid)
        assert a.dem_valid_pct == 0.0
        np.testing.assert_allclose(a.rssi_grid, b.rssi_grid, atol=1e-3, equal_nan=True)

    def test_profile_nan_falls_back_to_linear(self):
        grid = np.full((10, 10), np.nan, dtype=np.float32)
        bbox = BoundingBox(31.9, 34.9, 32.1, 35.1)
        prof = ce._sample_profile_elev(32.0, 35.0, 100.0, 32.05, 35.05, 200.0, grid, bbox, n=10)
        elevs = [e for _, e in prof]
        assert all(math.isfinite(e) for e in elevs)
        assert elevs[0] == pytest.approx(100.0)
        assert elevs[-1] == pytest.approx(200.0)
        assert elevs[5] == pytest.approx(150.0)

    def test_cross_section_with_nan_dem(self):
        grid = np.full((30, 30), 80.0, dtype=np.float32)
        grid[10:20, :] = np.nan
        bbox = BoundingBox(31.95, 34.95, 32.05, 35.05)
        wps  = [{"lat": 31.97, "lon": 34.97}, {"lat": 32.03, "lon": 35.03}]
        xs   = calculate_cross_section(make_site(elevation_m=80.0), "s", wps, "DTM",
                                       elev_grid=grid, bbox=bbox)
        assert xs.dem_source == "terrarium"
        assert all(math.isfinite(p.rssi) for p in xs.points)


# ── Coverage relative to rx_threshold (א2) ────────────────────────────────────
class TestThresholdCoverage:
    def test_stricter_threshold_less_coverage(self):
        lo = calculate_coverage(make_site(rx_threshold=-110), SMALL_BBOX, "DTM", 25)
        hi = calculate_coverage(make_site(rx_threshold=-90),  SMALL_BBOX, "DTM", 25)
        assert hi.covered_pct <= lo.covered_pct
        assert hi.threshold_dbm == -90
        assert hi.metadata["threshold_dbm"] == -90

    def test_covered_pct_definition(self):
        site = make_site(rx_threshold=-80, tx_power=20)
        r    = calculate_coverage(site, SMALL_BBOX, "DTM", 25)
        vals = r.rssi_grid[r.mask]
        assert r.covered_pct == pytest.approx(round((vals >= -80).mean() * 100, 1))
        assert r.covered_pct_any_signal == pytest.approx(round((vals > -110).mean() * 100, 1))
        assert r.covered_pct <= r.covered_pct_any_signal

    def test_level_distribution_complete(self):
        r = calculate_coverage(make_site(tx_power=20), SMALL_BBOX, "DTM", 25)
        assert set(r.level_distribution) == set(LEVELS)
        assert sum(v["count"] for v in r.level_distribution.values()) == int(r.mask.sum())
        assert sum(v["pct"] for v in r.level_distribution.values()) == pytest.approx(100, abs=0.5)

    def test_level_distribution_from_grid(self):
        g = np.array([[-60, -75, np.nan], [-95, -105, -120]], dtype=np.float32)
        d = level_distribution_from_grid(g, -100)
        assert d["total_cells"] == 5
        assert d["covered_pct"] == pytest.approx(60.0)          # -60, -75, -95
        assert d["covered_pct_any_signal"] == pytest.approx(80.0)
        assert d["weak_pct"] == pytest.approx(40.0)
        assert d["level_distribution"]["none"]["count"] == 1


# ── Earth curvature (א4) ──────────────────────────────────────────────────────
class TestEarthCurvature:
    def test_constants(self):
        assert K_FACTOR == pytest.approx(4 / 3)
        assert R_EFF_M == pytest.approx(8_495_000, rel=1e-3)
        assert ce.earth_bulge_m(20_000, 20_000) == pytest.approx(23.5, abs=0.1)

    def test_flat_long_path_loses_los(self):
        """Flat terrain at 0 m, 20 m masts: clear at 2 km, obstructed at 40 km."""
        short = ce._profile_diffraction_loss(flat_profile(2.0), 20.0, 20.0, 900)
        long_ = ce._profile_diffraction_loss(flat_profile(40.0), 20.0, 20.0, 900)
        assert short == pytest.approx(0.0, abs=0.01)
        assert long_ > 3.0

    def test_grid_flat_dem_bulge(self):
        """Vectorised grid: flat DEM, far cells get diffraction loss, near cells ~none."""
        R    = 40
        bbox = BoundingBox(31.6, 34.55, 32.4, 35.45)       # ~±45 km around site
        site = make_site(ant_height=60.0)
        dlat = (bbox.ne_lat - bbox.sw_lat) / R
        dlon = (bbox.ne_lon - bbox.sw_lon) / R
        lat_c = bbox.sw_lat + (np.arange(R) + 0.5) * dlat
        lon_c = bbox.sw_lon + (np.arange(R) + 0.5) * dlon
        lon_g, lat_g = np.meshgrid(lon_c, lat_c[::-1])
        dist = ce._haversine_km_vec(site.lat, site.lon, lat_g, lon_g)
        flat = np.zeros((R, R), dtype=np.float32)
        loss = ce._diffraction_grid(site.lat, site.lon, 60.0, lat_g, lon_g, flat,
                                    dist, 900, flat, bbox, site_ground_asl=0.0)
        near = loss[(dist > 1.0) & (dist < 3.0)]
        far  = loss[(dist > 38.0) & (dist < 42.0)]
        assert near.size and far.size
        assert near.max() < 1.0
        assert far.min() > 3.0


# ── Deygout multi-edge diffraction (ג1) ───────────────────────────────────────
class TestDeygout:
    def test_flat_short_path_no_loss(self):
        for m in ("single", "deygout"):
            assert ce._profile_diffraction_loss(flat_profile(5.0), 30.0, 30.0, 900, m) \
                == pytest.approx(0.0, abs=0.01)

    def test_ridge_produces_loss(self):
        loss = ce._profile_diffraction_loss(ridge_profile(5.0, height=80.0), 20.0, 20.0, 900)
        assert loss > 6.0

    def test_deygout_ge_single(self):
        profiles = [
            ridge_profile(10.0, height=60.0),
            ridge_profile(10.0, at=0.3, height=90.0),
            flat_profile(40.0),
        ]
        # two ridges: Deygout must add the secondary edge
        two = ridge_profile(10.0, at=0.3, height=70.0)
        two[28] = (two[28][0], 65.0)
        profiles.append(two)
        for prof in profiles:
            s = ce._profile_diffraction_loss(prof, 15.0, 10.0, 900, "single")
            d = ce._profile_diffraction_loss(prof, 15.0, 10.0, 900, "deygout")
            assert d >= s - 1e-9
        s2 = ce._profile_diffraction_loss(two, 15.0, 10.0, 900, "single")
        d2 = ce._profile_diffraction_loss(two, 15.0, 10.0, 900, "deygout")
        assert d2 > s2 + 3.0

    def test_grid_matches_scalar(self):
        """Vectorised and scalar Deygout agree for the same path geometry."""
        R    = 30
        bbox = BoundingBox(31.85, 34.85, 32.15, 35.15)
        site = make_site(ant_height=20.0, max_radius=30)
        lon_c = bbox.sw_lon + (np.arange(R) + 0.5) * (0.3 / R)
        lat_c = bbox.sw_lat + (np.arange(R) + 0.5) * (0.3 / R)
        lon_g, lat_g = np.meshgrid(lon_c, lat_c[::-1])
        elev = np.zeros((R, R), np.float32)
        elev[:, np.abs(lon_c - 35.05) < 0.006] = 150.0
        dist = ce._haversine_km_vec(site.lat, site.lon, lat_g, lon_g)
        for m in ("single", "deygout"):
            g = ce._diffraction_grid(site.lat, site.lon, 20.0, lat_g, lon_g, elev, dist,
                                     900, elev, bbox, site_ground_asl=0.0, method=m)
            r, c = R // 2, R - 3
            prof = ce._sample_profile_elev(site.lat, site.lon, 0.0, lat_g[r, c], lon_g[r, c],
                                           float(elev[r, c]), elev, bbox,
                                           n=ce.PROFILE_STEPS + 1)
            sc = ce._profile_diffraction_loss(prof, 20.0, float(elev[r, c]) + 1.5, 900, m)
            assert g[r, c] > 0
            assert g[r, c] == pytest.approx(sc, abs=0.05)

    def test_ridge_grid_reduces_coverage(self):
        R    = 30
        bbox = BoundingBox(31.85, 34.85, 32.15, 35.15)
        site = make_site(ant_height=20.0, max_radius=30, rx_threshold=-95)
        lon_c = bbox.sw_lon + (np.arange(R) + 0.5) * (0.3 / R)
        flat  = np.zeros((R, R), np.float32)
        ridge = flat.copy()
        ridge[:, np.abs(lon_c - 35.05) < 0.006] = 150.0
        f  = calculate_coverage(site, bbox, "DTM", R, elev_grid=flat)
        rs = calculate_coverage(site, bbox, "DTM", R, elev_grid=ridge, diffraction_method="single")
        rd = calculate_coverage(site, bbox, "DTM", R, elev_grid=ridge)
        assert rd.covered_pct <= rs.covered_pct <= f.covered_pct
        assert rd.covered_pct < f.covered_pct
        assert np.all(rd.rssi_grid[rd.mask] <= rs.rssi_grid[rs.mask] + 1e-4)
        assert rd.metadata["diffraction_method"] == "deygout"


# ── Raster output (ב1) ────────────────────────────────────────────────────────
class TestRasterOutput:
    def test_rssi_grid_shape_and_mask(self):
        r = calculate_coverage(make_site(), SMALL_BBOX, "DTM", 20)
        assert r.rssi_grid.shape == (20, 20)
        assert r.rssi_grid.dtype == np.float32
        assert r.mask.shape == (20, 20) and r.mask.dtype == bool
        assert np.isnan(r.rssi_grid[~r.mask]).all()

    def test_row0_is_north(self):
        """Site at the south edge ⇒ the strongest row is the last (southern) row."""
        bbox = BoundingBox(31.99, 34.95, 32.09, 35.05)
        r = calculate_coverage(make_site(max_radius=50), bbox, "DTM", 20)
        assert np.nanmean(r.rssi_grid[-1]) > np.nanmean(r.rssi_grid[0])

    def test_geojson_not_built_by_default(self):
        r = calculate_coverage(make_site(), SMALL_BBOX, "DTM", 20)
        assert r.geojson["type"] == "FeatureCollection"
        assert r.geojson["features"] == []
        assert r.cells == []
        md = r.geojson["metadata"]
        for key in ("covered_pct", "covered_pct_any_signal", "threshold_dbm",
                    "dem_source", "dem_valid_pct", "level_distribution", "model"):
            assert key in md

    def test_geojson_from_grid_matches_cells(self):
        r  = calculate_coverage(make_site(), SMALL_BBOX, "DTM", 15)
        gj = rssi_grid_to_geojson(r.rssi_grid, SMALL_BBOX, r.metadata)
        any_sig = int((r.rssi_grid[r.mask] > -110).sum())
        assert len(gj["features"]) == any_sig
        assert gj["metadata"]["covered_pct"] == r.covered_pct

    def test_encode_decode_roundtrip(self):
        rng  = np.random.default_rng(0)
        grid = rng.uniform(-140, -30, size=(37, 41)).astype(np.float32)
        grid[::5, ::3] = np.nan
        blob = encode_rssi_grid(grid)
        assert isinstance(blob, bytes)
        assert len(blob) < grid.nbytes
        out  = decode_rssi_grid(blob)
        assert out.shape == grid.shape
        np.testing.assert_array_equal(np.isnan(out), np.isnan(grid))
        valid = ~np.isnan(grid)
        assert np.max(np.abs(out[valid] - grid[valid])) <= 0.1

    def test_encode_decode_engine_output(self):
        r   = calculate_coverage(make_site(), SMALL_BBOX, "DTM", 20)
        out = decode_rssi_grid(encode_rssi_grid(r.rssi_grid))
        np.testing.assert_allclose(out, r.rssi_grid, atol=0.1, equal_nan=True)

    def test_decode_rejects_garbage(self):
        with pytest.raises(ValueError):
            decode_rssi_grid(b"not a grid")

    def test_png_dims_and_colours(self):
        grid = np.array([[-60, -75, -85], [-95, -105, -120], [np.nan, -60, -60]],
                        dtype=np.float32)
        png  = rssi_grid_to_png(grid, 90, 60)
        img  = Image.open(BytesIO(png))
        assert img.size == (90, 60)
        assert img.mode == "RGBA"
        px = np.array(img)
        # nearest-neighbour: each source cell → 30×20 block; sample block centres
        assert tuple(px[10, 15]) == LEVEL_RGBA["excellent"]
        assert tuple(px[10, 45]) == LEVEL_RGBA["good"]
        assert tuple(px[30, 75])[3] == 0          # none → transparent
        assert tuple(px[50, 15])[3] == 0          # NaN → transparent
        assert tuple(px[30, 45]) == LEVEL_RGBA["marginal"]

    def test_png_from_engine(self):
        r   = calculate_coverage(make_site(), SMALL_BBOX, "DTM", 20)
        img = Image.open(BytesIO(rssi_grid_to_png(r.rssi_grid, 200, 200)))
        assert img.size == (200, 200)


# ── Route coverage relative to rx_threshold ───────────────────────────────────
class TestCrossSectionThreshold:
    WPS = [{"lat": 32.0, "lon": 35.005}, {"lat": 32.0, "lon": 35.3}]

    def _run(self, thr: float):
        site = make_site(tx_power=10.0, rx_threshold=thr, max_radius=50)
        return calculate_cross_section(site, "s", self.WPS, "DTM", resolution=3)

    def test_fields(self):
        xs = self._run(-90)
        assert xs.threshold_dbm == -90
        assert 0 <= xs.covered_pct <= xs.covered_pct_any_signal <= 100
        assert set(xs.signal_distribution) == set(LEVELS)
        assert sum(v["count"] for v in xs.signal_distribution.values()) == len(xs.points)

    def test_covered_pct_definition(self):
        xs  = self._run(-90)
        n   = len(xs.points)
        cov = sum(1 for p in xs.points if p.rssi >= -90) / n * 100
        assert xs.covered_pct == pytest.approx(cov, abs=100 / n + 0.1)
        any_sig = sum(1 for p in xs.points if p.level != "none") / n * 100
        assert xs.covered_pct_any_signal == pytest.approx(round(any_sig, 1))

    def test_dead_zones_are_below_threshold_runs(self):
        xs = self._run(-90)
        assert xs.dead_zones, "route should cross below -90 dBm"
        for dzn in xs.dead_zones:
            inside = [p for p in xs.points if dzn.start_km <= p.dist_along < dzn.end_km]
            assert inside
            assert all(p.rssi < -90 + 0.05 for p in inside)
        # a point below threshold but with signal (weak/marginal) can start a dead zone
        starts = {dzn.start_km for dzn in xs.dead_zones}
        assert any(p.dist_along in starts and p.level != "none" for p in xs.points)

    def test_stricter_threshold_less_coverage_more_dead(self):
        loose  = self._run(-110)
        strict = self._run(-90)
        assert strict.covered_pct <= loose.covered_pct
        assert strict.covered_pct < loose.covered_pct            # the route does cross -90
        dead_len = lambda xs: sum(d.length_km for d in xs.dead_zones)
        max_dead = lambda xs: max((d.length_km for d in xs.dead_zones), default=0.0)
        assert dead_len(strict) >= dead_len(loose)
        assert max_dead(strict) >= max_dead(loose)
        # the any-signal number and the distribution do not depend on the threshold
        assert strict.covered_pct_any_signal == loose.covered_pct_any_signal
        assert strict.signal_distribution == loose.signal_distribution

    def test_threshold_minus_110_matches_old_definition_closely(self):
        """At -110 the new definition differs from 'level != none' only at exactly -110 dBm."""
        xs = self._run(-110)
        assert xs.covered_pct == pytest.approx(xs.covered_pct_any_signal, abs=100 / len(xs.points) + 0.1)


# ── Fresnel-Kirchhoff ν (ITU-R P.526 §4.1) ───────────────────────────────────
class TestFresnelNu:
    def test_known_geometry(self):
        """λ=1 m, d1=d2=1000 m, h=10 m → ν = 10·√(2·2000/(1·1e6)) = 0.6325."""
        expected = 10 * math.sqrt(2 * 2000 / (1.0 * 1000 * 1000))
        assert expected == pytest.approx(0.6325, abs=1e-4)
        assert ce.fresnel_nu(10.0, 1000.0, 1000.0, 1.0) == pytest.approx(expected, rel=1e-9)
        # ν = √2·h / r1
        r1 = ce.fresnel_radius_m(1000.0, 1000.0, 1.0)
        assert ce.fresnel_nu(10.0, 1000.0, 1000.0, 1.0) == pytest.approx(math.sqrt(2) * 10 / r1)

    def test_scalar_profile_uses_formula(self):
        """_max_nu_scalar: obstacle 10 m above a level 0 m LOS at mid-path."""
        nu, i = ce._max_nu_scalar([1000.0], [10.0], 0, 1, 0.0, 0.0, 2000.0, 0.0, 1.0)
        assert i == 0
        assert nu == pytest.approx(0.6325, abs=1e-4)

    def test_vectorised_uses_formula(self):
        h = np.full((2, 2), 10.0, dtype=np.float32)
        D = np.full((2, 2), 2000.0, dtype=np.float32)
        nu = ce._nu_segment_vec(h, 0.5, 0.0, 0.0, 1.0, np.zeros((2, 2)), D, 1.0)
        np.testing.assert_allclose(nu, 0.6325, atol=1e-4)

    def test_knife_edge_at_zero_is_6db(self):
        assert ce._knife_edge_loss_db(0.0) == pytest.approx(6.0, abs=0.05)


# ── Deygout on flat ground with a low receiver ────────────────────────────────
class TestDeygoutFlatGround:
    """Flat 0 m DEM, 10 m mast, 1.5 m rx, 400 MHz: no spurious secondary edges."""
    DISTANCES = (2.5, 5.0, 9.5)

    @pytest.mark.parametrize("km", DISTANCES)
    def test_scalar_profile(self, km):
        prof = flat_profile(km, n=40)
        s = ce._profile_diffraction_loss(prof, 10.0, 1.5, 400, "single")
        d = ce._profile_diffraction_loss(prof, 10.0, 1.5, 400, "deygout")
        assert 0.0 <= s <= 6.0            # LOS grazing: first Fresnel zone partly blocked
        assert s - 1e-9 <= d <= s + 1.0

    def test_grid(self):
        R    = 60
        bbox = BoundingBox(31.9, 34.88, 32.1, 35.12)        # ~±11 km around site
        site = make_site(ant_height=10.0, frequency=400)
        dlat = (bbox.ne_lat - bbox.sw_lat) / R
        dlon = (bbox.ne_lon - bbox.sw_lon) / R
        lat_c = bbox.sw_lat + (np.arange(R) + 0.5) * dlat
        lon_c = bbox.sw_lon + (np.arange(R) + 0.5) * dlon
        lon_g, lat_g = np.meshgrid(lon_c, lat_c[::-1])
        dist = ce._haversine_km_vec(site.lat, site.lon, lat_g, lon_g)
        flat = np.zeros((R, R), dtype=np.float32)
        kw = dict(site_ground_asl=0.0)
        s = ce._diffraction_grid(site.lat, site.lon, 10.0, lat_g, lon_g, flat, dist, 400,
                                 flat, bbox, method="single", **kw)
        d = ce._diffraction_grid(site.lat, site.lon, 10.0, lat_g, lon_g, flat, dist, 400,
                                 flat, bbox, method="deygout", **kw)
        for km in self.DISTANCES:
            ring = (dist > km - 0.3) & (dist < km + 0.3)
            assert ring.any()
            assert s[ring].max() <= 6.0
            assert np.all(d[ring] <= s[ring] + 1.0)
            assert np.all(d[ring] >= s[ring] - 1e-4)

    def test_ridge_still_adds_secondary(self):
        """A real obstruction plus a second ridge: Deygout ≥ single (and > here)."""
        two = ridge_profile(10.0, at=0.3, height=70.0)
        two[28] = (two[28][0], 65.0)
        s = ce._profile_diffraction_loss(two, 15.0, 10.0, 900, "single")
        d = ce._profile_diffraction_loss(two, 15.0, 10.0, 900, "deygout")
        assert d > s + 3.0


# ── DEM cell-centre convention ────────────────────────────────────────────────
class TestDemCellCentre:
    R    = 8
    BBOX = BoundingBox(sw_lat=32.0, sw_lon=35.0, ne_lat=32.08, ne_lon=35.08)

    def _grid(self):
        g = np.full((self.R, self.R), 400.0, dtype=np.float32)
        g[3, 3] = 300.0
        return g

    def _centre(self, r, c):
        dlat = (self.BBOX.ne_lat - self.BBOX.sw_lat) / self.R
        dlon = (self.BBOX.ne_lon - self.BBOX.sw_lon) / self.R
        return self.BBOX.ne_lat - (r + 0.5) * dlat, self.BBOX.sw_lon + (c + 0.5) * dlon

    def test_grid_coords_of_centre(self):
        lat, lon = self._centre(3, 3)
        rf, cf = ce._grid_coords(lat, lon, self.BBOX, self.R)
        assert rf == pytest.approx(3.0) and cf == pytest.approx(3.0)

    def test_scalar_lookup_at_cell_centre(self):
        g = self._grid()
        lat, lon = self._centre(3, 3)
        rf, cf = ce._grid_coords(lat, lon, self.BBOX, self.R)
        assert ce._bilinear_scalar(g, rf, cf) == pytest.approx(300.0, abs=1e-3)

    def test_vector_lookup_at_cell_centre(self):
        g = self._grid()
        lat, lon = self._centre(3, 3)
        rf, cf = ce._grid_coords(np.array([lat]), np.array([lon]), self.BBOX, self.R)
        assert ce._bilinear(g, rf, cf)[0] == pytest.approx(300.0, abs=1e-3)

    def test_profile_sample_at_cell_centre(self):
        g = self._grid()
        lat, lon = self._centre(3, 3)
        prof = ce._sample_profile_elev(lat, lon, 0.0, lat + 0.01, lon, 0.0, g, self.BBOX, n=4)
        assert prof[0][1] == pytest.approx(300.0, abs=1e-3)

    def test_edges_and_outside(self):
        g = self._grid()
        R = self.R
        # outer half cell → clamped to edge value; beyond → NaN
        assert ce._bilinear_scalar(g, -0.5, -0.5) == pytest.approx(400.0)
        assert ce._bilinear_scalar(g, R - 0.5, R - 0.5) == pytest.approx(400.0)
        assert math.isnan(ce._bilinear_scalar(g, -0.51, 2.0))
        assert math.isnan(ce._bilinear_scalar(g, 2.0, R - 0.49))
        v = ce._bilinear(g, np.array([-0.5, R - 0.5]), np.array([-0.5, R - 0.5]))
        np.testing.assert_allclose(v, 400.0)


# ── rssi_min_all / valid_mask (risk inputs) ───────────────────────────────────
class TestRssiMinAll:
    def test_area_includes_none_cells(self):
        from app.services.risk import assess_risk
        r = calculate_coverage(make_site(tx_power=-30.0), SMALL_BBOX, "DTM", 20)
        assert np.nanmin(r.rssi_grid) <= -120          # grid has a −120 dBm cell
        assert r.rssi_min_all <= -120
        assert r.rssi_min_all == pytest.approx(round(float(np.nanmin(r.rssi_grid)), 1))
        assert r.rssi_min > -110                       # display stat unchanged
        # route-style use: deficit vs the site threshold drives the level
        assert assess_risk(95, rssi_min=r.rssi_min_all,
                           threshold_dbm=r.threshold_dbm)[0] == "CRITICAL"

    def test_cross_section_includes_none_points(self):
        wps = [{"lat": 32.0, "lon": 35.005}, {"lat": 32.0, "lon": 35.3}]
        xs  = calculate_cross_section(make_site(tx_power=-20.0, max_radius=50), "s",
                                      wps, "DTM", resolution=3)
        assert xs.signal_distribution["none"]["count"] > 0
        assert xs.rssi_min_all <= -120
        assert xs.rssi_min_all == pytest.approx(min(p.rssi for p in xs.points), abs=0.05)
        assert xs.risk_level == "CRITICAL"

    def test_valid_mask(self):
        R = 20
        elev = np.full((R, R), 50.0, dtype=np.float32)
        elev[5:12, 3:15] = np.nan
        r = calculate_coverage(make_site(), SMALL_BBOX, "DTM", R, elev_grid=elev)
        assert r.valid_mask.shape == (R, R) and r.valid_mask.dtype == bool
        np.testing.assert_array_equal(r.valid_mask, ~np.isnan(elev))
        assert r.dem_valid_pct == pytest.approx(r.valid_mask.mean() * 100, abs=0.1)
        assert calculate_coverage(make_site(), SMALL_BBOX, "DTM", R).valid_mask is None


# ── Route point `covered` flag ────────────────────────────────────────────────
class TestCrossSectionCoveredFlag:
    WPS = [{"lat": 32.0, "lon": 35.005}, {"lat": 32.0, "lon": 35.3}]

    def test_covered_matches_dead_zones(self):
        xs = calculate_cross_section(make_site(tx_power=10.0, rx_threshold=-90, max_radius=50),
                                     "s", self.WPS, "DTM", resolution=3)
        assert any(p.covered for p in xs.points) and any(not p.covered for p in xs.points)
        pts = xs.points
        # rebuild the dead-zone membership of every point from the zones
        in_dz = [False] * len(pts)
        for dzn in xs.dead_zones:
            i = next(k for k, p in enumerate(pts) if p.dist_along == dzn.start_km)
            while i < len(pts) and not pts[i].covered:
                in_dz[i] = True
                i += 1
            # zone ends at the first covered point after it (or the last point)
            end = pts[i] if i < len(pts) else pts[-1]
            assert end.dist_along == dzn.end_km
        assert in_dz == [not p.covered for p in pts]
        n = len(pts)
        assert xs.covered_pct == pytest.approx(round(sum(p.covered for p in pts) / n * 100, 1))

    def test_covered_uses_unrounded_rssi(self):
        xs = calculate_cross_section(make_site(tx_power=10.0, rx_threshold=-90, max_radius=50),
                                     "s", self.WPS, "DTM", resolution=3)
        for p in xs.points:
            if abs(p.rssi - (-90)) > 0.05:
                assert p.covered == (p.rssi >= -90)
