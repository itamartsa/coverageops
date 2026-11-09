"""
Terrain tile layer tests — no network: tile downloads are mocked.
Run with: pytest tests/test_terrain*.py -q
"""
import math
import threading
from io import BytesIO

import numpy as np
import pytest
from PIL import Image

from app.services import terrain, elevation
from app.services.terrain import (
    DemAreaTooLarge, ElevationGrid, decode_terrarium, fetch_elevation_grid,
    fetch_elevation_grid_ex, fetch_tile, fetch_tiles,
)

# Small bbox around Jerusalem (~4 km)
BBOX = (31.75, 35.19, 31.79, 35.23)
# Straddles the z12 tile seam at lon 35.2441 (tile x 2448 | 2449)
SEAM_BBOX = (31.75, 35.22, 31.79, 35.27)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _encode(elev_m: float) -> tuple[int, int, int]:
    v = elev_m + 32768.0
    r = int(v // 256)
    g = int(v - r * 256)
    b = int(round((v - r * 256 - g) * 256))
    return r, g, b


def _png(rgb: tuple[int, int, int]) -> bytes:
    buf = BytesIO()
    Image.new("RGB", (256, 256), rgb).save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture(autouse=True)
def _fresh_cache():
    terrain.clear_tile_cache()
    yield
    terrain.clear_tile_cache()


@pytest.fixture
def no_network(monkeypatch):
    """Fail loudly if anything reaches urllib."""
    def boom(*a, **k):
        raise AssertionError("network access in tests")
    monkeypatch.setattr(terrain.urllib.request, "urlopen", boom)


def _mock_download(monkeypatch, fn):
    calls = []
    lock = threading.Lock()

    def fake(x, y, z, timeout):
        with lock:
            calls.append((z, x, y))
        return fn(x, y, z)
    monkeypatch.setattr(terrain, "_download_tile_png", fake)
    return calls


# ── Decode ────────────────────────────────────────────────────────────────────

class TestDecode:
    def test_sea_level(self):
        assert decode_terrarium(np.array([128, 0, 0])) == pytest.approx(0.0)

    def test_jerusalem(self):
        assert decode_terrarium(np.array([130, 242, 0])) == pytest.approx(754.0)

    def test_fraction_and_negative(self):
        assert decode_terrarium(np.array([127, 255, 128])) == pytest.approx(-0.5)

    def test_fetch_tile_decodes_png(self, monkeypatch, no_network):
        _mock_download(monkeypatch, lambda x, y, z: _png((130, 242, 0)))
        t = fetch_tile(1, 2, 12)
        assert t.shape == (256, 256) and t.dtype == np.float32
        assert np.allclose(t, 754.0)


# ── Fetch / cache ─────────────────────────────────────────────────────────────

class TestFetch:
    def test_cache_hit_avoids_second_download(self, monkeypatch, no_network):
        calls = _mock_download(monkeypatch, lambda x, y, z: _png((128, 0, 0)))
        a = fetch_tile(10, 20, 12)
        b = fetch_tile(10, 20, 12)
        assert a is b
        assert calls == [(12, 10, 20)]
        assert terrain.tile_cache_info().hits == 1

    def test_failures_not_cached(self, monkeypatch, no_network):
        state = {"fail": True}

        def dl(x, y, z):
            if state["fail"]:
                raise OSError("timeout")
            return _png((128, 0, 0))
        calls = _mock_download(monkeypatch, dl)
        assert fetch_tile(1, 1, 10) is None
        state["fail"] = False
        assert fetch_tile(1, 1, 10) is not None
        assert len(calls) == 2

    def test_cached_tile_is_read_only(self, monkeypatch, no_network):
        _mock_download(monkeypatch, lambda x, y, z: _png((128, 0, 0)))
        t = fetch_tile(0, 0, 9)
        with pytest.raises(ValueError):
            t[0, 0] = 1.0

    def test_parallel_fetch_returns_all(self, monkeypatch, no_network):
        threads = set()

        def dl(x, y, z):
            threads.add(threading.get_ident())
            return _png((128, x % 256, y % 256))
        _mock_download(monkeypatch, dl)
        coords = [(11, x, y) for x in range(4) for y in range(4)]
        res = fetch_tiles(coords, max_workers=4)
        assert set(res) == set(coords)
        assert all(v is not None for v in res.values())
        assert res[(11, 3, 2)][0, 0] == pytest.approx(3 + 2 / 256)

    def test_parallel_fetch_partial_failure(self, monkeypatch, no_network):
        def dl(x, y, z):
            if x == 1:
                raise OSError("404")
            return _png((128, 0, 0))
        _mock_download(monkeypatch, dl)
        res = fetch_tiles([(9, 0, 0), (9, 1, 0)])
        assert res[(9, 0, 0)] is not None and res[(9, 1, 0)] is None

    def test_deadline(self, monkeypatch, no_network):
        release = threading.Event()

        def dl(x, y, z):
            if x == 1:
                release.wait(5)
            return _png((128, 0, 0))
        _mock_download(monkeypatch, dl)
        try:
            res = fetch_tiles([(9, 0, 0), (9, 1, 0)], deadline_s=0.3)
            assert res[(9, 0, 0)] is not None and res[(9, 1, 0)] is None
        finally:
            release.set()


# ── Zoom selection ────────────────────────────────────────────────────────────

class TestZoom:
    @pytest.mark.parametrize("max_tiles", [1, 4, 9, 16, 64, 256])
    def test_respects_budget(self, max_tiles):
        bbox = (31.0, 34.5, 32.0, 35.5)
        z = terrain._choose_zoom(*bbox, max_tiles=max_tiles)
        assert terrain.MIN_ZOOM <= z <= terrain.MAX_ZOOM
        if z > terrain.MIN_ZOOM:
            assert terrain._tile_count(*bbox, z) <= max_tiles
        if z < terrain.MAX_ZOOM:
            assert terrain._tile_count(*bbox, z + 1) > max_tiles

    def test_small_bbox_max_zoom(self):
        assert terrain._choose_zoom(*BBOX, max_tiles=64) == 12

    def test_huge_bbox_floor(self):
        assert terrain._choose_zoom(25.0, 30.0, 40.0, 45.0, max_tiles=4) == 8

    def test_grid_fetches_within_budget(self, monkeypatch, no_network):
        calls = _mock_download(monkeypatch, lambda x, y, z: _png((128, 0, 0)))
        fetch_elevation_grid(31.0, 34.5, 32.0, 35.5, 50, 50, max_tiles=9)
        assert 0 < len(calls) <= 9


# ── Elevation grid ────────────────────────────────────────────────────────────

class TestGrid:
    def test_all_fail_returns_none(self, monkeypatch, no_network):
        def dl(x, y, z):
            raise OSError("down")
        _mock_download(monkeypatch, dl)
        assert fetch_elevation_grid(*BBOX, 40, 40) is None
        assert fetch_elevation_grid_ex(*BBOX, 40, 40) is None

    def test_all_ok(self, monkeypatch, no_network):
        _mock_download(monkeypatch, lambda x, y, z: _png((130, 242, 0)))
        g = fetch_elevation_grid_ex(*BBOX, 40, 30)
        assert isinstance(g, ElevationGrid)
        assert g.elev.shape == (30, 40) and g.elev.dtype == np.float32
        assert np.allclose(g.elev, 754.0)
        assert g.valid_pct == pytest.approx(100.0)
        assert g.zoom == 12 and g.source == "terrarium/z12"
        assert 30 < g.m_per_px < 40          # ~33 m/px at z12, lat 31.8°

    def test_decode_before_resize(self, monkeypatch, no_network):
        """Neighbouring tiles at 255 m and 256 m straddle the G→R carry.
        Resizing RGB first would produce ~ -32k/+32k garbage at the seam."""
        z = 12
        x_min, x_max, _, _ = terrain._tile_range(*SEAM_BBOX, z)
        assert x_max > x_min
        split = x_min + 1

        def dl(x, y, z):
            return _png(_encode(255.0) if x < split else _encode(256.0))
        _mock_download(monkeypatch, dl)
        e = fetch_elevation_grid(*SEAM_BBOX, 200, 50)
        assert np.nanmin(e) >= 255.0 - 1e-3
        assert np.nanmax(e) <= 256.0 + 1e-3

    def test_partial_failure_nan_only_there(self, monkeypatch, no_network):
        z = 12
        x_min, x_max, _, _ = terrain._tile_range(*SEAM_BBOX, z)
        assert x_max > x_min
        bad_x = x_min                       # western column of tiles fails

        def dl(x, y, z):
            if x == bad_x:
                raise OSError("404")
            return _png((130, 242, 0))
        _mock_download(monkeypatch, dl)
        g = fetch_elevation_grid_ex(*SEAM_BBOX, 100, 100)
        assert g is not None
        nan = np.isnan(g.elev)
        assert nan.any() and not nan.all()
        # Valid pixels are exact — no bleed of missing tiles into neighbours
        assert np.allclose(g.elev[~nan], 754.0)
        assert (g.elev[~nan] > -1000).all()
        # NaN region is the west side: in each row, NaNs form a left prefix
        for row in nan:
            k = row.argmin() if not row.all() else len(row)
            assert row[:k].all() and not row[k:].any()
        # Boundary column ≈ where tile x_min ends
        left_px = terrain._deg2px(SEAM_BBOX[0], SEAM_BBOX[1], z)[0]
        right_px = terrain._deg2px(SEAM_BBOX[2], SEAM_BBOX[3], z)[0]
        frac = ((bad_x + 1) * 256 - left_px) / (right_px - left_px)
        assert abs(nan[0].sum() - frac * 100) <= 1.5
        assert g.valid_pct == pytest.approx(100.0 * (~nan).mean())

    def test_never_returns_minus_32768(self, monkeypatch, no_network):
        def dl(x, y, z):
            if (x + y) % 2:
                raise OSError("x")
            return _png((128, 10, 0))
        _mock_download(monkeypatch, dl)
        e = fetch_elevation_grid(31.0, 34.5, 32.0, 35.5, 64, 64, max_tiles=16)
        assert e is not None
        assert np.nanmin(e) == pytest.approx(10.0)


# ── Area budget ───────────────────────────────────────────────────────────────

class TestAreaBudget:
    def test_world_bbox_raises_without_download(self, monkeypatch, no_network):
        calls = _mock_download(monkeypatch, lambda x, y, z: _png((128, 0, 0)))
        with pytest.raises(DemAreaTooLarge) as ei:
            fetch_elevation_grid_ex(-85.0, -180.0, 85.0, 179.99, 100, 100)
        assert calls == []
        e = ei.value
        assert isinstance(e, ValueError)
        assert e.tiles > e.max_tiles == terrain.MAX_DEM_TILES
        assert e.zoom == terrain.MIN_ZOOM
        assert e.message_he and any("\u0590" <= ch <= "\u05ff" for ch in e.message_he)

    def test_compat_wrapper_raises(self, monkeypatch, no_network):
        calls = _mock_download(monkeypatch, lambda x, y, z: _png((128, 0, 0)))
        with pytest.raises(DemAreaTooLarge):
            fetch_elevation_grid(25.0, 30.0, 40.0, 45.0, 50, 50, max_tiles=4)
        assert calls == []

    def test_huge_max_tiles_hits_pixel_cap(self, monkeypatch, no_network):
        calls = _mock_download(monkeypatch, lambda x, y, z: _png((128, 0, 0)))
        monkeypatch.setattr(terrain, "MAX_DEM_PIXELS", 3 * 256 * 256)
        with pytest.raises(DemAreaTooLarge) as ei:
            fetch_elevation_grid_ex(31.0, 34.5, 32.0, 35.5, 50, 50, max_tiles=10_000)
        assert calls == [] and ei.value.pixels > ei.value.max_pixels

    def test_default_pixel_cap(self):
        assert terrain.MAX_DEM_PIXELS <= 100_000_000

    def test_output_size_cap(self, monkeypatch, no_network):
        calls = _mock_download(monkeypatch, lambda x, y, z: _png((128, 0, 0)))
        monkeypatch.setattr(terrain, "MAX_DEM_PIXELS", 10_000_000)
        with pytest.raises(DemAreaTooLarge):
            fetch_elevation_grid_ex(*BBOX, 5000, 5000)
        assert calls == []

    def test_within_budget_ok(self, monkeypatch, no_network):
        _mock_download(monkeypatch, lambda x, y, z: _png((128, 0, 0)))
        assert fetch_elevation_grid_ex(*BBOX, 10, 10) is not None


# ── Mercator → lat/lon resampling geometry ───────────────────────────────────

def _tile_lat(ty: float, z: int) -> float:
    """Latitude of global tile-y coordinate ``ty`` (fractional ok)."""
    return math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * ty / 2 ** z))))


class TestResampleGeometry:
    def test_rows_sampled_at_exact_latitude(self):
        """Mosaic elevation = f(latitude), linear. At lat 32° over a 2° bbox
        rows equally spaced in Mercator would be off by several metres; the
        resampler must hit f(row-centre latitude)."""
        z = 10
        sw_lat, sw_lon, ne_lat, ne_lon = 31.0, 35.0, 33.0, 35.3
        x_min, x_max, y_min, y_max = terrain._tile_range(sw_lat, sw_lon, ne_lat, ne_lon, z)
        H = (y_max - y_min + 1) * 256
        W = (x_max - x_min + 1) * 256
        f = lambda lat: 1000.0 * (lat - 31.0)        # 1 m per 0.001°
        row_lat = np.array([_tile_lat(y_min + (k + 0.5) / 256, z) for k in range(H)])
        mosaic = np.repeat(f(row_lat)[:, None], W, axis=1).astype(np.float32)
        valid = np.ones_like(mosaic)

        out_h, out_w = 40, 8
        elev, ok = terrain._resample_to_latlon(mosaic, valid, z, x_min, y_min,
                                               sw_lat, sw_lon, ne_lat, ne_lon,
                                               out_w, out_h)
        assert ok.all()
        dlat = (ne_lat - sw_lat) / out_h
        want = f(ne_lat - (np.arange(out_h) + 0.5) * dlat)
        assert np.abs(elev - want[:, None]).max() < 0.5          # metres

        # Sanity: the old Mercator-linear row placement is measurably wrong here.
        py_n = terrain._deg2px(ne_lat, sw_lon, z)[1]
        py_s = terrain._deg2px(sw_lat, sw_lon, z)[1]
        merc_rows = py_n + (np.arange(out_h) + 0.5) / out_h * (py_s - py_n)
        naive = f(np.array([_tile_lat(p / 256, z) for p in merc_rows]))
        assert np.abs(naive - want).max() > 3.0

    def test_columns_at_cell_centre_longitude(self):
        z = 10
        sw_lat, sw_lon, ne_lat, ne_lon = 31.9, 34.0, 32.1, 36.0
        x_min, x_max, y_min, y_max = terrain._tile_range(sw_lat, sw_lon, ne_lat, ne_lon, z)
        H = (y_max - y_min + 1) * 256
        W = (x_max - x_min + 1) * 256
        col_lon = (x_min * 256 + np.arange(W) + 0.5) / (2 ** z * 256) * 360.0 - 180.0
        mosaic = np.repeat((100.0 * col_lon)[None, :], H, axis=0).astype(np.float32)
        out_w = 25
        elev, ok = terrain._resample_to_latlon(mosaic, np.ones_like(mosaic), z,
                                               x_min, y_min, sw_lat, sw_lon,
                                               ne_lat, ne_lon, out_w, 3)
        want = 100.0 * (sw_lon + (np.arange(out_w) + 0.5) * (ne_lon - sw_lon) / out_w)
        assert ok.all() and np.abs(elev - want[None, :]).max() < 0.05

    @pytest.mark.parametrize("north_frac, expect_north", [(0.7, True), (0.3, False)])
    def test_cell_centre_convention_rows(self, monkeypatch, no_network,
                                         north_frac, expect_north):
        """Constant-per-tile mosaic; a single output row straddles a tile-row
        border. Its value must come from the tile containing the cell CENTRE
        (not the top or bottom edge)."""
        z = 12
        ty_border = 1690                       # border between tile rows 1689|1690
        b_lat = _tile_lat(ty_border, z)        # ≈ 32.4°
        d = 0.01
        ne_lat = b_lat + north_frac * d
        sw_lat = ne_lat - d
        lon0 = 35.21

        def dl(x, y, z):
            return _png(_encode(100.0) if y < ty_border else _encode(200.0))
        _mock_download(monkeypatch, dl)
        g = fetch_elevation_grid_ex(sw_lat, lon0, ne_lat, lon0 + 0.01, 3, 1)
        assert g is not None and g.zoom == 12 and g.elev.shape == (1, 3)
        assert np.allclose(g.elev, 100.0 if expect_north else 200.0)

    @pytest.mark.parametrize("west_frac, expect_west", [(0.7, True), (0.3, False)])
    def test_cell_centre_convention_cols(self, monkeypatch, no_network,
                                         west_frac, expect_west):
        z = 12
        tx_border = 2449                       # lon ≈ 35.2441
        b_lon = tx_border / 2 ** z * 360.0 - 180.0
        d = 0.01
        sw_lon = b_lon - west_frac * d

        def dl(x, y, z):
            return _png(_encode(100.0) if x < tx_border else _encode(200.0))
        _mock_download(monkeypatch, dl)
        g = fetch_elevation_grid_ex(31.77, sw_lon, 31.78, sw_lon + d, 1, 3)
        assert g is not None and g.zoom == 12
        assert np.allclose(g.elev, 100.0 if expect_west else 200.0)


# ── Point elevation ───────────────────────────────────────────────────────────

class TestPointElevation:
    def test_none_on_failure(self, monkeypatch, no_network):
        def dl(x, y, z):
            raise OSError("down")
        _mock_download(monkeypatch, dl)
        assert elevation.fetch_elevation(31.77, 35.21) is None

    def test_value_and_zoom(self, monkeypatch, no_network):
        calls = _mock_download(monkeypatch, lambda x, y, z: _png((130, 242, 0)))
        assert elevation.fetch_elevation(31.77, 35.21) == 754.0
        assert calls[0][0] == 13

    def test_exact_pixel(self, monkeypatch, no_network):
        lat, lon = 31.7767, 35.2345
        tx, ty = elevation._deg2tile(lat, lon, 13)
        px, py = elevation._deg2px_in_tile(lat, lon, 13, tx, ty)
        arr = np.full((256, 256, 3), (128, 0, 0), dtype=np.uint8)
        arr[py, px] = (130, 242, 128)       # 754.5 m only at the target pixel

        def dl(x, y, z):
            buf = BytesIO()
            Image.fromarray(arr).save(buf, format="PNG")
            return buf.getvalue()
        _mock_download(monkeypatch, dl)
        assert elevation.fetch_elevation(lat, lon) == 754.5

    def test_uses_shared_cache(self, monkeypatch, no_network):
        calls = _mock_download(monkeypatch, lambda x, y, z: _png((128, 0, 0)))
        elevation.fetch_elevation(31.77, 35.21)
        elevation.fetch_elevation(31.7701, 35.2101)
        assert len(calls) == 1
