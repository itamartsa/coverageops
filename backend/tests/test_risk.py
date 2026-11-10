"""
CoverageOps – risk module tests (ג3)
"""
import pytest

from app.services import coverage_engine as ce
from app.services.coverage_engine import (
    SiteParams, BoundingBox, DeadZone, calculate_coverage, calculate_cross_section,
)
from app.services.risk import (
    assess_risk, operational_summary,
    build_route_recommendations, build_area_recommendations,
)


def make_site(**kw) -> SiteParams:
    base = dict(lat=32.0, lon=35.0, ant_height=10.0, elevation_m=0.0,
                frequency=900, tx_power=43.0, rx_threshold=-90.0, max_radius=5.0)
    base.update(kw)
    return SiteParams(**base)


def dz(length: float) -> DeadZone:
    return DeadZone(start_km=0, end_km=length, length_km=length,
                    start_lat=32, start_lon=35, end_lat=32.01, end_lon=35.01)


class TestAssessRisk:
    @pytest.mark.parametrize("kwargs, level, score", [
        (dict(covered_pct=95),                              "LOW",      1),
        (dict(covered_pct=80),                              "MEDIUM",   2),
        (dict(covered_pct=60),                              "HIGH",     3),
        (dict(covered_pct=40),                              "CRITICAL", 4),
        (dict(covered_pct=95, max_dead_length_km=1.5),      "MEDIUM",   2),
        (dict(covered_pct=95, max_dead_length_km=2.5),      "HIGH",     3),
        (dict(covered_pct=95, max_dead_length_km=3.5),      "CRITICAL", 4),
        (dict(covered_pct=95, weak_pct=15),                 "MEDIUM",   2),
        (dict(covered_pct=95, weak_pct=25),                 "HIGH",     3),
        (dict(covered_pct=95, weak_pct=35),                 "CRITICAL", 4),
        # RSSI criterion is relative to the threshold (deficit = thr − rssi_min)
        (dict(covered_pct=95, rssi_min=-107, threshold_dbm=-100), "MEDIUM",   2),
        (dict(covered_pct=95, rssi_min=-112, threshold_dbm=-100), "HIGH",     3),
        (dict(covered_pct=95, rssi_min=-120, threshold_dbm=-100), "CRITICAL", 4),
        (dict(covered_pct=95, rssi_min=-104, threshold_dbm=-100), "LOW",      1),
        (dict(covered_pct=95, rssi_min=None, threshold_dbm=-100), "LOW",      1),
        # no threshold (area analyses) → RSSI criterion not applied
        (dict(covered_pct=95, rssi_min=-140),               "LOW",      1),
    ])
    def test_rules(self, kwargs, level, score):
        assert assess_risk(**kwargs) == (level, score)

    def test_stricter_of_both_rule_sets(self):
        """Old engine rule (dead zone) and old API rule (weak %) both still raise the level."""
        assert assess_risk(90, max_dead_length_km=2.1, weak_pct=5)[0] == "HIGH"
        assert assess_risk(90, max_dead_length_km=0.0, weak_pct=21)[0] == "HIGH"

    def test_engine_wrapper_consistent(self):
        """Same inputs → same level from the engine wrapper and from risk.assess_risk."""
        for cov in (30, 55, 75, 90, 99):
            for dead in ([], [dz(0.5)], [dz(1.2)], [dz(2.5)], [dz(4.0)]):
                max_dead = max((d.length_km for d in dead), default=0.0)
                assert ce._assess_risk(cov, dead) == assess_risk(cov, max_dead)[0]

    def test_cross_section_uses_risk_module(self):
        wps = [{"lat": 32.0, "lon": 35.01}, {"lat": 32.0, "lon": 35.2}]
        xs  = calculate_cross_section(make_site(), "s", wps, "DTM", resolution=3)
        weak = (xs.signal_distribution["weak"]["count"]
                + xs.signal_distribution["marginal"]["count"]) / len(xs.points) * 100
        max_dead = max((d.length_km for d in xs.dead_zones), default=0.0)
        # risk uses the minimum over ALL points (incl. "none"), not the display rssi_min
        assert xs.rssi_min_all is not None
        assert xs.rssi_min_all <= xs.rssi_min or xs.signal_distribution["none"]["count"] == 0
        assert (xs.risk_level, xs.risk_score) == assess_risk(
            xs.covered_pct, max_dead, weak, xs.rssi_min_all)

    def test_area_result_feeds_same_rules(self):
        """API layer computing risk from CoverageStats gets a deterministic answer."""
        r = calculate_coverage(make_site(), BoundingBox(31.95, 34.95, 32.05, 35.05), "DTM", 20)
        a = assess_risk(r.covered_pct, weak_pct=r.weak_pct, rssi_min=r.rssi_min)
        b = assess_risk(r.metadata["covered_pct"], weak_pct=r.metadata["weak_pct"],
                        rssi_min=r.rssi_min)
        assert a == b

    def test_threshold_relative_rssi_criterion(self):
        """The RSSI criterion depends on the receiver threshold, not fixed dBm."""
        # same −116 dBm: 11 dB below a −105 threshold → HIGH; 1 dB below −115 → LOW
        assert assess_risk(95, rssi_min=-116, threshold_dbm=-105) == ("HIGH", 3)
        assert assess_risk(95, rssi_min=-116, threshold_dbm=-115) == ("LOW", 1)
        assert assess_risk(95, rssi_min=float("nan"), threshold_dbm=-90) == ("LOW", 1)

    def test_area_risk_ignores_deep_shadow_cells(self):
        """Regression: 89 % covered area with a few deeply shadowed cells was CRITICAL."""
        r = calculate_coverage(make_site(tx_power=-30.0),
                               BoundingBox(31.95, 34.95, 32.05, 35.05), "DTM", 20)
        assert r.rssi_min_all <= -120
        # area callers pass covered/weak only
        assert assess_risk(89.4, weak_pct=5.0) == ("LOW", 1)


class TestOperationalSummary:
    def test_levels(self):
        assert operational_summary("CRITICAL")["status"] == "בעייתי"
        assert operational_summary("HIGH")["status"] == "בעייתי"
        assert operational_summary("MEDIUM")["status"] == "גבולי"
        assert operational_summary("LOW")["status"] == "תקין"
        assert set(operational_summary("LOW")) == {"status", "conclusion"}


class TestRecommendations:
    def test_route_wrapper_matches(self):
        site = make_site()
        a = ce._build_recommendations(site, 60, [dz(2.0)], "HIGH", -105, -95, 10.0, "DTM")
        b = build_route_recommendations(site, 60, [dz(2.0)], "HIGH",
                                        rssi_min=-105, rssi_avg=-95,
                                        total_length_km=10.0, mode="DTM")
        assert a == b and a

    def test_route_dem_warning(self):
        site = make_site()
        recs = build_route_recommendations(site, 90, [], "LOW", dem_source="none")
        assert "ללא נתוני טופוגרפיה" in recs[0]

    def test_area_dem_warning_first(self):
        recs = build_area_recommendations(make_site(), 95, 2, -85, 0, dem_source="none")
        assert "ללא נתוני טופוגרפיה" in recs[0]

    def test_area_no_warning_with_dem(self):
        recs = build_area_recommendations(make_site(), 95, 2, -75, 0, dem_source="terrarium")
        assert not any("ללא נתוני טופוגרפיה" in r for r in recs)
        assert recs == ["הכיסוי תקין – אין המלצות דחופות"]

    def test_area_partial_dem_warning(self):
        recs = build_area_recommendations(make_site(), 95, 2, -85, 0,
                                          dem_source="terrarium", dem_valid_pct=70.0)
        assert "חלקיים" in recs[0]

    def test_area_bad_coverage_rules(self):
        recs = build_area_recommendations(make_site(ant_height=6), 40, 25, -100, 3,
                                          dem_source="terrarium")
        text = " ".join(recs)
        assert "ממסר" in text
        assert "גובה האנטנה" in text
        assert "25%" in text
        assert "3 אשכולות" in text
        assert "נמוכה מסף הקבלה" in text

    def test_cross_section_without_dem_warns(self):
        wps = [{"lat": 32.0, "lon": 35.01}, {"lat": 32.0, "lon": 35.05}]
        xs  = calculate_cross_section(make_site(), "s", wps, "DTM")
        assert xs.dem_source == "none"
        assert "ללא נתוני טופוגרפיה" in xs.recommendations[0]
