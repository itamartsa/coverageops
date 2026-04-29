"""
CoverageOps – Unit & Integration Tests
Run with: pytest tests/ -v
"""
import pytest
import math
from app.services.coverage_engine import (
    SiteParams, BoundingBox, calculate_coverage,
    fspl_db, haversine_km, classify_rssi, antenna_height_gain_db
)


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
        assert classify_rssi(-70)  == "excellent"   # boundary: >-70
        assert classify_rssi(-70.1) == "good"


# ── Unit: Height gain ─────────────────────────────────────────────────────────
class TestHeightGain:
    def test_baseline_is_zero(self):
        """1.5m antenna = 0 dB gain"""
        assert antenna_height_gain_db(1.5) == pytest.approx(0.0, abs=0.01)

    def test_higher_antenna_more_gain(self):
        assert antenna_height_gain_db(10) > antenna_height_gain_db(5)

    def test_gain_formula(self):
        """6m antenna should give ~12 dB over 1.5m"""
        gain = antenna_height_gain_db(6)
        assert 11 < gain < 13


# ── Integration: Coverage calculation ────────────────────────────────────────
class TestCoverageCalculation:
    @pytest.fixture
    def sample_site(self):
        return SiteParams(
            lat=32.0, lon=35.0,
            ant_height=10.0,
            frequency=900,
            tx_power=43.0,
            rx_threshold=-90.0,
            max_radius=5.0,
        )

    @pytest.fixture
    def sample_bbox(self):
        return BoundingBox(
            sw_lat=31.95, sw_lon=34.95,
            ne_lat=32.05, ne_lon=35.05,
        )

    def test_returns_result(self, sample_site, sample_bbox):
        result = calculate_coverage(sample_site, sample_bbox, "DTM", resolution=20)
        assert result is not None
        assert isinstance(result.covered_pct, float)
        assert isinstance(result.rssi_avg, float)

    def test_coverage_percentage_in_range(self, sample_site, sample_bbox):
        result = calculate_coverage(sample_site, sample_bbox, "DTM", resolution=20)
        assert 0 <= result.covered_pct <= 100

    def test_dsm_lower_coverage_than_dtm(self, sample_site, sample_bbox):
        """DSM adds clutter loss → less coverage than DTM for same site"""
        dtm = calculate_coverage(sample_site, sample_bbox, "DTM", resolution=30)
        dsm = calculate_coverage(sample_site, sample_bbox, "DSM", resolution=30)
        assert dsm.covered_pct <= dtm.covered_pct

    def test_geojson_structure(self, sample_site, sample_bbox):
        result = calculate_coverage(sample_site, sample_bbox, "DTM", resolution=15)
        gj = result.geojson
        assert gj["type"] == "FeatureCollection"
        assert isinstance(gj["features"], list)
        if gj["features"]:
            feat = gj["features"][0]
            assert "rssi" in feat["properties"]
            assert "level" in feat["properties"]
            assert "color" in feat["properties"]

    def test_higher_power_more_coverage(self, sample_bbox):
        low_power = SiteParams(lat=32, lon=35, ant_height=10,
                               frequency=900, tx_power=30, rx_threshold=-90, max_radius=5)
        hi_power  = SiteParams(lat=32, lon=35, ant_height=10,
                               frequency=900, tx_power=50, rx_threshold=-90, max_radius=5)
        r_low = calculate_coverage(low_power,  sample_bbox, "DTM", resolution=20)
        r_hi  = calculate_coverage(hi_power,   sample_bbox, "DTM", resolution=20)
        assert r_hi.covered_pct >= r_low.covered_pct

    def test_duration_recorded(self, sample_site, sample_bbox):
        result = calculate_coverage(sample_site, sample_bbox, "DTM", resolution=20)
        assert result.duration_sec > 0

    def test_rssi_stats_consistent(self, sample_site, sample_bbox):
        result = calculate_coverage(sample_site, sample_bbox, "DTM", resolution=20)
        if result.cells:
            assert result.rssi_max >= result.rssi_avg >= result.rssi_min
