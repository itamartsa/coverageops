"""Civil cellular sites layer — ITM conversion + record normalization."""
from app.services.itm import itm_to_wgs84
from app.services.cell_sites_import import _to_site, _techs, _date, _operator


def test_itm_known_points():
    # Jerusalem (Ramot) & Netanya — reference values from PROJ (EPSG:2039 -> 4326), within ~5 m
    lat, lon = itm_to_wgs84(217831, 630534)
    assert abs(lat - 31.76747) < 5e-5 and abs(lon - 35.18728) < 5e-5
    lat, lon = itm_to_wgs84(185808, 687241)
    assert abs(lat - 32.27835) < 5e-5 and abs(lon - 34.84727) < 5e-5


def test_normalizers():
    assert _techs("דור  2 3 4 5") == "2G,3G,4G,5G"
    assert _date("05/12/2021") == "2021-12-05"
    assert _date("2025-12-03 00:00:00") == "2025-12-03"
    assert _operator("PHI (משרת את הוט ופרטנר)") == "PHI"
    assert _operator("פלאפון") == "PELEPHONE"


def test_to_site_active_and_bad_coords():
    rec = {"ID": 1, "חברה": "סלקום", "מס' אתר": "123", "עיר": "חיפה", "X_ITM": 200000, "Y_ITM": 745000,
           "סוג אתר": "תורן קרקעי", "טכנולוגיית שידור": "דור 4 5", "תאריך היתר הפעלה": "01/02/2024"}
    s = _to_site(rec, "active")
    assert s.operator == "CELLCOM" and s.site_type == "ground_mast" and s.technologies == "4G,5G"
    assert 32.7 < s.lat < 32.9 and s.operation_permit_date == "2024-02-01"
    assert _to_site({**rec, "X_ITM": 0}, "active") is None
    assert _to_site({**rec, "X_ITM": 900000}, "active") is None
