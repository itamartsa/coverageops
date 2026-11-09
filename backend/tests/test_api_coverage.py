"""
API tests for /api/coverage and /api/sites.

Run with: pytest tests/test_api_coverage.py -v

The real PostgreSQL database and the Terrarium tile server are replaced by:
  * an in-memory SQLite database (dependency override of get_db),
  * a synthetic ElevationGrid generator (monkeypatched fetch_elevation_grid_ex)
    that evaluates a geographic terrain function at each cell centre, so the
    same ridge appears consistently in any requested bbox,
  * a fake ADMIN user (override of get_current_user; require_role() depends
    on it, so role checks pass).
"""
import json
import math
from io import BytesIO

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import coverage as cov_api
from app.api import sites as sites_api
from app.api.coverage import (
    LEVEL_LOWER_BOUND_DBM, adaptive_resolution, dead_zone_clusters,
    _crop_grid_to_bbox, _extend_bbox_to_site,
)
from app.core.database import Base, get_db
from app.core.migrations import run_startup_migrations
from app.core.security import get_current_user
from app.main import app
from app.models.user import CoverageResult, Site, User, UserRole, AnalysisMode
from app.services.coverage_engine import (
    BoundingBox, classify_rssi, decode_rssi_grid, rssi_grid_to_geojson,
)
from app.services.risk import RISK_LEVELS
from app.services.terrain import ElevationGrid

M_PER_DEG = 111_320.0


# ── Synthetic terrain ─────────────────────────────────────────────────────────

BASE_ELEV = 100.0


def flat_terrain(lat, lon):
    return np.full(np.shape(lat), BASE_ELEV, dtype=np.float32)


def ridge_at(lat0: float, height: float = 400.0, sigma_deg: float = 0.003):
    """East-west ridge (Gaussian cross-section) centred on latitude lat0."""
    def f(lat, lon):
        return (BASE_ELEV + height * np.exp(-((np.asarray(lat) - lat0) / sigma_deg) ** 2)
                ).astype(np.float32)
    return f


def make_grid_fetcher(terrain_fn, calls=None, nan_rows: int = 0, nan_where=None):
    """Return a stand-in for terrain.fetch_elevation_grid_ex."""
    def _fetch(sw_lat, sw_lon, ne_lat, ne_lon, out_width, out_height, max_tiles=None):
        if calls is not None:
            calls.append((sw_lat, sw_lon, ne_lat, ne_lon, out_width, out_height))
        dlat = (ne_lat - sw_lat) / out_height
        dlon = (ne_lon - sw_lon) / out_width
        lats = ne_lat - (np.arange(out_height) + 0.5) * dlat          # row 0 = north
        lons = sw_lon + (np.arange(out_width) + 0.5) * dlon
        lon_g, lat_g = np.meshgrid(lons, lats)
        elev = terrain_fn(lat_g, lon_g).astype(np.float32)
        if nan_rows:
            elev[:nan_rows, :] = np.nan
        if nan_where is not None:
            elev[nan_where(lat_g, lon_g)] = np.nan
        valid = float(np.isfinite(elev).mean()) * 100
        return ElevationGrid(elev=elev, zoom=12, m_per_px=32.4, valid_pct=valid,
                             source="terrarium/z12")
    return _fetch


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def db_session_factory():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    yield factory
    engine.dispose()


@pytest.fixture
def client(db_session_factory, monkeypatch):
    db = db_session_factory()
    admin = User(id=1, username="admin", full_name="Admin", hashed_pw="x",
                 role=UserRole.ADMIN, is_active=True)
    db.add(admin)
    db.commit()
    db.close()

    def _get_db():
        s = db_session_factory()
        try:
            yield s
        finally:
            s.close()

    def _user():
        return User(id=1, username="admin", full_name="Admin", hashed_pw="x",
                    role=UserRole.ADMIN, is_active=True)

    app.dependency_overrides[get_db] = _get_db
    app.dependency_overrides[get_current_user] = _user
    monkeypatch.setattr(cov_api, "fetch_elevation_grid_ex", make_grid_fetcher(flat_terrain))
    monkeypatch.setattr(cov_api, "fetch_elevation", lambda lat, lon: BASE_ELEV)
    monkeypatch.setattr(sites_api, "fetch_elevation", lambda lat, lon: BASE_ELEV)
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def create_site(client, **kw):
    body = dict(name="אתר בדיקה", lat=32.0, lon=35.0, ant_height=10.0,
                frequency=900, tx_power=43.0, rx_threshold=-90.0, max_radius=30.0)
    body.update(kw)
    r = client.post("/api/sites/", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def bbox_around(lat, lon, half_km):
    dlat = half_km * 1000 / M_PER_DEG
    dlon = half_km * 1000 / (M_PER_DEG * math.cos(math.radians(lat)))
    return dict(sw_lat=lat - dlat, sw_lon=lon - dlon, ne_lat=lat + dlat, ne_lon=lon + dlon)


def analyze(client, site_id, bbox, mode="DTM", **kw):
    r = client.post("/api/coverage/analyze", json=dict(site_id=site_id, mode=mode, **bbox, **kw))
    assert r.status_code == 200, r.text
    return r.json()


NEW_FIELDS = (
    "covered_pct_any_signal", "threshold_dbm", "weak_pct", "level_distribution",
    "dem_source", "dem_valid_pct", "dem_m_per_px", "resolution", "model",
    "diffraction_method", "warnings", "has_raster",
)


# ── Pure helpers ──────────────────────────────────────────────────────────────

class TestAdaptiveResolution:
    def _bbox_km(self, km, lat=32.0, lon=35.0):
        b = bbox_around(lat, lon, km / 2)
        return BoundingBox(b["sw_lat"], b["sw_lon"], b["ne_lat"], b["ne_lon"])

    def test_5km_bbox(self):
        # 5 km / 40 m = 125 cells
        assert adaptive_resolution(self._bbox_km(5)) == 125
        assert adaptive_resolution(self._bbox_km(5), dem_m_per_px=32.4) == 125

    def test_60km_bbox_is_capped(self):
        assert adaptive_resolution(self._bbox_km(60)) == 500

    def test_tiny_bbox_floor(self):
        assert adaptive_resolution(self._bbox_km(1)) == 60

    def test_coarse_dem_limits_cells(self):
        # never finer than the DEM: 10 km at 100 m/px → 100 cells
        assert adaptive_resolution(self._bbox_km(10), dem_m_per_px=100.0) == 100


class TestExtendAndCrop:
    BBOX = BoundingBox(32.05, 34.98, 32.10, 35.02)

    def test_site_inside_no_extension(self):
        ext, R_ext, extended, capped = _extend_bbox_to_site(self.BBOX, 32.07, 35.0, 100, 500)
        assert ext == self.BBOX and R_ext == 100 and not extended and not capped

    def test_site_outside_extends_with_aligned_cells(self):
        ext, R_ext, extended, capped = _extend_bbox_to_site(self.BBOX, 32.0, 35.0, 100, 500)
        assert extended and not capped
        assert ext.sw_lat < 32.0 - 0.004            # site + ~0.5 km margin
        dlat = (self.BBOX.ne_lat - self.BBOX.sw_lat) / 100
        dlon = (self.BBOX.ne_lon - self.BBOX.sw_lon) / 100
        assert (ext.ne_lat - ext.sw_lat) / R_ext == pytest.approx(dlat)
        assert (ext.ne_lon - ext.sw_lon) / R_ext == pytest.approx(dlon)

    def test_crop_recovers_user_window(self):
        ext, R_ext, _, _ = _extend_bbox_to_site(self.BBOX, 32.0, 35.0, 100, 500)
        # encode row/col of each extended cell → crop must be the bbox window
        rows = np.arange(R_ext)[:, None] * 1000 + np.arange(R_ext)[None, :]
        sub  = _crop_grid_to_bbox(rows.astype(np.float32), ext, self.BBOX, 100)
        assert sub.shape == (100, 100)
        r0 = int(round((ext.ne_lat - self.BBOX.ne_lat) / (ext.ne_lat - ext.sw_lat) * R_ext))
        c0 = int(round((self.BBOX.sw_lon - ext.sw_lon) / (ext.ne_lon - ext.sw_lon) * R_ext))
        assert sub[0, 0] == r0 * 1000 + c0
        assert sub[-1, -1] == (r0 + 99) * 1000 + (c0 + 99)

    def test_far_site_is_capped(self):
        ext, R_ext, extended, capped = _extend_bbox_to_site(self.BBOX, 30.0, 35.0, 100, 500)
        assert capped and extended and R_ext <= 500


def test_level_bounds_match_engine():
    for lvl, lo in LEVEL_LOWER_BOUND_DBM.items():
        assert classify_rssi(lo + 0.01) == lvl
    assert classify_rssi(-110.0) == "none"


def test_dead_zone_clusters_connected_components():
    g = np.full((20, 20), -60.0, dtype=np.float32)
    g[2:5, 2:5]   = -95.0          # 9 cells, below -90
    g[10:12, 10:12] = -120.0       # 4 cells, no signal
    g[12, 12]     = -120.0         # diagonal neighbour → same cluster (8-conn)
    g[0, :]       = np.nan         # outside radius – ignored
    bbox = BoundingBox(32.0, 35.0, 32.02, 35.02)
    cl = dead_zone_clusters(g, bbox, -90.0)
    assert [c["cell_count"] for c in cl] == [9, 5]
    assert cl[0]["severity"] == "marginal" and cl[1]["severity"] == "none"
    assert cl[0]["rssi_avg"] == -95.0
    assert cl[0]["area_km2"] > 0
    # centroid of the 3×3 block: rows 2..4 → centre row 3.5 from north edge
    assert cl[0]["centroid_lat"] == pytest.approx(32.02 - 3.5 * 0.001, abs=1e-5)


def test_startup_migrations_add_columns_idempotently():
    engine = create_engine("sqlite://", poolclass=StaticPool,
                           connect_args={"check_same_thread": False})
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE coverage_results (id INTEGER PRIMARY KEY, site_id INTEGER, "
            "mode VARCHAR(3), poly_sw_lat FLOAT, poly_sw_lon FLOAT, poly_ne_lat FLOAT, "
            "poly_ne_lon FLOAT, covered_pct FLOAT, rssi_avg FLOAT, rssi_max FLOAT, "
            "rssi_min FLOAT, geojson_path VARCHAR(512), created_at DATETIME, duration_sec FLOAT)"))
        conn.execute(text("CREATE TABLE sites (id INTEGER PRIMARY KEY, name VARCHAR(128))"))
    run_startup_migrations(engine)
    run_startup_migrations(engine)            # second run is a no-op
    cols = {c["name"] for c in inspect(engine).get_columns("coverage_results")}
    for c in ("rssi_raster", "resolution", "threshold_dbm", "covered_pct_any_signal",
              "weak_pct", "dem_source", "dem_valid_pct", "model", "dem_m_per_px"):
        assert c in cols
    assert "elevation_m" in {c["name"] for c in inspect(engine).get_columns("sites")}


# ── Endpoints ─────────────────────────────────────────────────────────────────

class TestAnalyze:
    def test_stores_raster_and_returns_new_fields(self, client, db_session_factory):
        site = create_site(client)
        bbox = bbox_around(32.0, 35.0, 1.5)              # 3 km → 75 cells adaptive
        res  = analyze(client, site["id"], bbox)

        for f in NEW_FIELDS:
            assert f in res
        assert res["geojson"] is None
        assert res["has_raster"] is True
        assert res["resolution"] == 75
        assert res["dem_source"] == "terrarium"
        assert res["dem_valid_pct"] == 100.0
        assert res["dem_m_per_px"] == pytest.approx(32.4)
        assert res["threshold_dbm"] == -90.0
        assert res["diffraction_method"] == "deygout"
        assert "Deygout" in res["model"]
        assert res["warnings"] == []
        assert res["dem_extended"] is False
        assert set(res["level_distribution"]) == {"excellent", "good", "medium",
                                                  "weak", "marginal", "none"}
        assert 0 <= res["covered_pct"] <= res["covered_pct_any_signal"] <= 100
        assert res["sw_lat"] == pytest.approx(bbox["sw_lat"])

        db = db_session_factory()
        row = db.get(CoverageResult, res["id"])
        grid = decode_rssi_grid(row.rssi_raster)
        assert grid.shape == (75, 75)
        assert row.geojson_path is None
        assert row.covered_pct == res["covered_pct"]
        db.close()

        # GET /result returns the same shape
        got = client.get(f"/api/coverage/result/{res['id']}").json()
        assert got["geojson"] is None
        for f in NEW_FIELDS:
            assert got[f] == res[f] or f == "warnings"
        assert got["dem_extended"] is None

    def test_explicit_resolution_is_honoured(self, client):
        site = create_site(client)
        res  = analyze(client, site["id"], bbox_around(32.0, 35.0, 1.5), resolution=40)
        assert res["resolution"] == 40

    def test_no_dem_labels_and_warns(self, client, monkeypatch):
        monkeypatch.setattr(cov_api, "fetch_elevation_grid_ex", lambda *a, **k: None)
        site = create_site(client)
        res  = analyze(client, site["id"], bbox_around(32.0, 35.0, 1.5))
        assert res["dem_source"] == "none"
        assert res["dem_m_per_px"] is None
        assert res["diffraction_method"] is None
        assert res["warnings"] and "טופוגרפיה" in res["warnings"][0]

    def test_partial_dem_warns(self, client, monkeypatch):
        monkeypatch.setattr(cov_api, "fetch_elevation_grid_ex",
                            make_grid_fetcher(flat_terrain, nan_rows=20))
        site = create_site(client)
        res  = analyze(client, site["id"], bbox_around(32.0, 35.0, 1.5))
        assert res["dem_valid_pct"] < 95
        assert any("חלקיים" in w for w in res["warnings"])

    def test_site_outside_bbox_terrain_counts(self, client, monkeypatch):
        """A ridge between an outside site and the bbox must lower the RSSI."""
        site = create_site(client, lat=32.0, lon=35.0, rx_threshold=-80.0)
        bbox = dict(sw_lat=32.05, sw_lon=34.98, ne_lat=32.10, ne_lon=35.02)

        calls = []
        monkeypatch.setattr(cov_api, "fetch_elevation_grid_ex",
                            make_grid_fetcher(flat_terrain, calls))
        flat = analyze(client, site["id"], bbox, resolution=60)
        # DEM requested for a bbox that contains the site
        sw_lat, sw_lon, ne_lat, ne_lon, w, h = calls[-1]
        assert sw_lat < 32.0 < ne_lat and sw_lon < 35.0 < ne_lon and w == h
        assert flat["dem_extended"] is True
        assert flat["resolution"] == 60

        monkeypatch.setattr(cov_api, "fetch_elevation_grid_ex",
                            make_grid_fetcher(ridge_at(32.03)))
        ridge = analyze(client, site["id"], bbox, resolution=60)

        assert ridge["rssi_avg"] < flat["rssi_avg"] - 5
        assert ridge["covered_pct"] < flat["covered_pct"]
        # stored raster covers the USER bbox at the requested resolution
        assert ridge["sw_lat"] == pytest.approx(32.05)
        png = client.get(f"/api/coverage/result/{ridge['id']}/png?width=60&height=60")
        assert Image.open(BytesIO(png.content)).size == (60, 60)

    def test_invalid_bbox(self, client):
        site = create_site(client)
        r = client.post("/api/coverage/analyze", json=dict(
            site_id=site["id"], mode="DTM", sw_lat=32.1, sw_lon=35.0, ne_lat=32.0, ne_lon=35.1))
        assert r.status_code == 400


class TestDerivedOutputs:
    @pytest.fixture
    def result(self, client, monkeypatch):
        # ridge ~0.9 km north of the site → shadowed cells form dead-zone clusters
        monkeypatch.setattr(cov_api, "fetch_elevation_grid_ex",
                            make_grid_fetcher(ridge_at(32.008)))
        site = create_site(client, rx_threshold=-75.0)
        return analyze(client, site["id"], bbox_around(32.0, 35.0, 2.0),
                       mode="DSM", resolution=60)

    def test_png(self, client, result):
        r = client.get(f"/api/coverage/result/{result['id']}/png?width=300&height=200"
                       "&sw_lat=0&sw_lon=0&ne_lat=1&ne_lon=1")   # legacy params ignored
        assert r.status_code == 200
        assert r.headers["content-type"] == "image/png"
        assert "max-age" in r.headers["cache-control"]
        img = Image.open(BytesIO(r.content))
        assert img.size == (300, 200) and img.mode == "RGBA"
        assert np.asarray(img)[..., 3].max() > 0

    def test_geojson_download(self, client, result):
        r = client.get(f"/api/coverage/result/{result['id']}/geojson")
        assert r.status_code == 200
        assert "attachment" in r.headers["content-disposition"]
        gj = json.loads(r.content)
        assert gj["type"] == "FeatureCollection"
        assert len(gj["features"]) > 0
        assert gj["metadata"]["result_id"] == result["id"]

    def test_report(self, client, result):
        rep = client.get(f"/api/coverage/result/{result['id']}/report").json()
        assert rep["risk"]["level"] in RISK_LEVELS
        assert 1 <= rep["risk"]["score"] <= 4
        dq = rep["data_quality"]
        assert dq == {"dem_source": "terrarium", "dem_valid_pct": 100.0,
                      "dem_m_per_px": pytest.approx(32.4), "resolution": 60,
                      "diffraction_method": "deygout"}
        assert "Deygout" in rep["propagation"]["model"] and "DSM" in rep["propagation"]["model"]
        # DEM in use → height gain capped (6 dB) instead of 20·log10(10/1.5)=16.5 dB
        assert rep["propagation"]["height_gain_db"] == 6.0
        assert rep["macro"]["covered_pct"] == result["covered_pct"]
        assert "none" in rep["signal_distribution"]
        cl = rep["dead_zones_clusters"]
        assert 0 < len(cl) <= 10
        assert {"centroid_lat", "centroid_lon", "cell_count", "area_km2",
                "rssi_avg", "severity"} <= set(cl[0])
        assert cl == sorted(cl, key=lambda c: -c["cell_count"])
        assert rep["operational_summary"]["status"]
        assert rep["recommendations"]

    def test_history_and_list(self, client, result):
        h = client.get(f"/api/coverage/history/{result['site_id']}").json()
        lst = client.get("/api/coverage/").json()
        for rows in (h, lst):
            assert rows[0]["id"] == result["id"]
            for f in ("covered_pct_any_signal", "threshold_dbm", "weak_pct",
                      "dem_source", "dem_valid_pct", "resolution", "model", "has_raster"):
                assert f in rows[0]
        assert lst[0]["site_name"] == "אתר בדיקה"

    def test_legacy_geojson_row(self, client, db_session_factory, tmp_path, result):
        """Rows without a raster keep working via the stored GeoJSON file."""
        db = db_session_factory()
        new = db.get(CoverageResult, result["id"])
        bbox = BoundingBox(new.poly_sw_lat, new.poly_sw_lon, new.poly_ne_lat, new.poly_ne_lon)
        path = tmp_path / "legacy.geojson"
        path.write_text(json.dumps(rssi_grid_to_geojson(decode_rssi_grid(new.rssi_raster), bbox)))
        legacy = CoverageResult(
            site_id=new.site_id, mode=AnalysisMode.DTM,
            poly_sw_lat=bbox.sw_lat, poly_sw_lon=bbox.sw_lon,
            poly_ne_lat=bbox.ne_lat, poly_ne_lon=bbox.ne_lon,
            covered_pct=90.0, rssi_avg=-80.0, rssi_max=-50.0, rssi_min=-105.0,
            geojson_path=str(path),
        )
        db.add(legacy); db.commit(); lid = legacy.id; db.close()

        got = client.get(f"/api/coverage/result/{lid}").json()
        assert got["has_raster"] is False and got["warnings"]
        png = client.get(f"/api/coverage/result/{lid}/png?width=100&height=100")
        assert png.status_code == 200 and Image.open(BytesIO(png.content)).size == (100, 100)
        gj = client.get(f"/api/coverage/result/{lid}/geojson")
        assert json.loads(gj.content)["type"] == "FeatureCollection"
        rep = client.get(f"/api/coverage/result/{lid}/report").json()
        assert rep["meta"]["legacy"] is True
        assert rep["macro"]["covered_pct"] == 90.0
        assert rep["recommendations"][0] == got["warnings"][0]


class TestCrossSectionAndTerrainEndpoints:
    WPS = [{"lat": 32.01, "lon": 35.0}, {"lat": 32.03, "lon": 35.01}]

    def test_cross_section_json(self, client):
        site = create_site(client)
        r = client.post("/api/coverage/cross-section",
                        json=dict(site_id=site["id"], mode="DTM", waypoints=self.WPS))
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["dem_source"] == "terrarium"
        assert body["dem_valid_pct"] == 100.0
        assert body["threshold_dbm"] == -90.0
        assert 0 <= body["covered_pct"] <= body["covered_pct_any_signal"] <= 100
        assert 1 <= body["risk_score"] <= 4
        assert body["warnings"] == []
        assert body["points"] and "level" in body["points"][0]

    def test_cross_section_no_dem(self, client, monkeypatch):
        monkeypatch.setattr(cov_api, "fetch_elevation_grid_ex", lambda *a, **k: None)
        site = create_site(client)
        body = client.post("/api/coverage/cross-section",
                           json=dict(site_id=site["id"], mode="DTM", waypoints=self.WPS)).json()
        assert body["dem_source"] == "none" and body["warnings"]

    def test_cross_section_docx(self, client):
        site = create_site(client)
        r = client.post("/api/coverage/cross-section/docx",
                        json=dict(site_id=site["id"], mode="DSM", waypoints=self.WPS))
        assert r.status_code == 200
        assert r.content[:2] == b"PK"

    def test_topo_overlay_nan_transparent(self, client, monkeypatch):
        monkeypatch.setattr(cov_api, "fetch_elevation_grid_ex",
                            make_grid_fetcher(ridge_at(32.0), nan_rows=10))
        r = client.get("/api/coverage/topo-overlay?sw_lat=31.99&sw_lon=34.99"
                       "&ne_lat=32.01&ne_lon=35.01&width=64&height=64")
        a = np.asarray(Image.open(BytesIO(r.content)))
        assert a.shape == (64, 64, 4)
        assert (a[:10, :, 3] == 0).all() and (a[10:, :, 3] == 160).all()

    def test_topo_overlay_none_is_transparent(self, client, monkeypatch):
        monkeypatch.setattr(cov_api, "fetch_elevation_grid_ex", lambda *a, **k: None)
        r = client.get("/api/coverage/topo-overlay?sw_lat=31.99&sw_lon=34.99"
                       "&ne_lat=32.01&ne_lon=35.01&width=32&height=16")
        a = np.asarray(Image.open(BytesIO(r.content)))
        assert a.shape == (16, 32, 4) and (a[..., 3] == 0).all()

    def test_point_elevation_null(self, client, monkeypatch):
        monkeypatch.setattr(cov_api, "fetch_elevation", lambda lat, lon: None)
        r = client.get("/api/coverage/point-elevation?lat=32&lon=35").json()
        assert r["elevation_m"] is None

    def test_site_elevation_fallback(self, client, monkeypatch):
        monkeypatch.setattr(sites_api, "fetch_elevation", lambda lat, lon: None)
        site = create_site(client)
        assert site["elevation_m"] is None


# ── Review round: risk input, DEM validity, limits, auth, site elevation ─────

from sqlalchemy import inspect as sa_inspect
from app.services.terrain import DemAreaTooLarge


def _too_large(*a, **k):
    raise DemAreaTooLarge("too large", tiles=999, max_tiles=64, zoom=8,
                          pixels=10**9, max_pixels=10**8)


class TestReviewRound:
    def test_rssi_min_all_persisted_and_used_for_risk(self, client, db_session_factory):
        site = create_site(client, max_radius=30.0)
        # 40 km bbox: far cells have no usable signal → rssi_min_all < rssi_min
        res = analyze(client, site["id"], bbox_around(32.0, 35.0, 20.0), resolution=60)
        assert res["rssi_min_all"] is not None
        assert res["rssi_min_all"] <= res["rssi_min"]
        assert res["risk_level"] in RISK_LEVELS
        db = db_session_factory()
        row = db.get(CoverageResult, res["id"])
        assert row.rssi_min_all == res["rssi_min_all"]
        db.close()
        rep = client.get(f"/api/coverage/result/{res['id']}/report").json()
        assert rep["macro"]["rssi_min_all"] == res["rssi_min_all"]
        from app.services.risk import assess_risk
        assert rep["risk"]["level"] == assess_risk(
            rep["macro"]["covered_pct"], weak_pct=rep["macro"]["weak_pct"],
            rssi_min=res["rssi_min_all"])[0]

    def test_dem_valid_pct_is_for_user_bbox(self, client, monkeypatch):
        """NaN only in the extension (between site and bbox) → user bbox is 100 % valid."""
        site = create_site(client, lat=32.0, lon=35.0)
        bbox = dict(sw_lat=32.05, sw_lon=34.98, ne_lat=32.10, ne_lon=35.02)
        monkeypatch.setattr(cov_api, "fetch_elevation_grid_ex", make_grid_fetcher(
            flat_terrain, nan_where=lambda lat, lon: (lat > 32.01) & (lat < 32.045)))
        res = analyze(client, site["id"], bbox, resolution=60)
        assert res["dem_extended"] is True
        assert res["dem_valid_pct"] == 100.0
        assert not any("חלקיים" in w for w in res["warnings"])

    def test_dem_area_too_large_is_413(self, client, monkeypatch):
        monkeypatch.setattr(cov_api, "fetch_elevation_grid_ex", _too_large)
        site = create_site(client)
        r = client.post("/api/coverage/analyze", json=dict(
            site_id=site["id"], mode="DTM", **bbox_around(32.0, 35.0, 1.5)))
        assert r.status_code == 413 and "גדול מדי" in r.json()["detail"]
        r = client.post("/api/coverage/cross-section", json=dict(
            site_id=site["id"], mode="DTM",
            waypoints=[{"lat": 32.01, "lon": 35.0}, {"lat": 32.02, "lon": 35.0}]))
        assert r.status_code == 413
        r = client.get("/api/coverage/topo-overlay?sw_lat=31.9&sw_lon=34.9&ne_lat=32.1&ne_lon=35.1")
        assert r.status_code == 413

    def test_bbox_span_limit(self, client):
        site = create_site(client)
        big = bbox_around(32.0, 35.0, 250.0)          # 500 km sides > 400 km
        r = client.post("/api/coverage/analyze", json=dict(site_id=site["id"], mode="DTM", **big))
        assert r.status_code == 400 and "ק\"מ" in r.json()["detail"]
        q = "&".join(f"{k}={v}" for k, v in big.items())
        assert client.get(f"/api/coverage/topo-overlay?{q}").status_code == 400

    def test_topo_and_point_elevation_require_auth(self, client):
        app.dependency_overrides.pop(get_current_user)
        assert client.get("/api/coverage/point-elevation?lat=32&lon=35").status_code == 401
        assert client.get("/api/coverage/topo-overlay?sw_lat=31.99&sw_lon=34.99"
                          "&ne_lat=32.01&ne_lon=35.01").status_code == 401

    def test_unknown_site_elevation_sampled_from_dem(self, client, monkeypatch, db_session_factory):
        monkeypatch.setattr(sites_api, "fetch_elevation", lambda lat, lon: None)
        monkeypatch.setattr(cov_api, "fetch_elevation_grid_ex", make_grid_fetcher(
            lambda lat, lon: np.full(np.shape(lat), 600.0, dtype=np.float32)))
        site = create_site(client)
        assert site["elevation_m"] is None
        res = analyze(client, site["id"], bbox_around(32.0, 35.0, 1.5), resolution=40)
        assert not any("גובה האתר" in w for w in res["warnings"])
        # same terrain, site stored at the DEM height → identical result
        db = db_session_factory()
        db.get(Site, site["id"]).elevation_m = 600.0
        db.commit(); db.close()
        res2 = analyze(client, site["id"], bbox_around(32.0, 35.0, 1.5), resolution=40)
        assert res2["rssi_avg"] == res["rssi_avg"]

    def test_unknown_site_elevation_without_dem_warns(self, client, monkeypatch):
        monkeypatch.setattr(sites_api, "fetch_elevation", lambda lat, lon: None)
        monkeypatch.setattr(cov_api, "fetch_elevation_grid_ex", lambda *a, **k: None)
        site = create_site(client)
        res = analyze(client, site["id"], bbox_around(32.0, 35.0, 1.5), resolution=40)
        assert any("גובה האתר" in w for w in res["warnings"])
        body = client.post("/api/coverage/cross-section", json=dict(
            site_id=site["id"], mode="DTM",
            waypoints=[{"lat": 32.01, "lon": 35.0}, {"lat": 32.02, "lon": 35.0}])).json()
        assert any("גובה האתר" in w for w in body["warnings"])

    def test_listing_does_not_load_raster(self, client, db_session_factory):
        site = create_site(client)
        res = analyze(client, site["id"], bbox_around(32.0, 35.0, 1.5), resolution=40)
        db = db_session_factory()
        obj = db.query(CoverageResult).first()
        assert "rssi_raster" in sa_inspect(obj).unloaded        # deferred column
        db.close()
        assert client.get("/api/coverage/").json()[0]["has_raster"] is True
        assert client.get(f"/api/coverage/history/{site['id']}").json()[0]["has_raster"] is True
        assert res["id"]
