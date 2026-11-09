"""
Israeli TM Grid (ITM, EPSG:2039) -> WGS84 (EPSG:4326), pure Python (no pyproj needed).

Inverse Transverse Mercator on GRS80, then a 7-parameter Helmert shift
(Israel 1993 -> WGS84). Projection math matches PROJ to < 1 mm; the datum
shift agrees with PROJ's default transformation to within ~3 m, which is
well below the positional accuracy of the antenna data itself.
"""
import math

# GRS80
_A = 6378137.0
_F = 1 / 298.257222101
_E2 = _F * (2 - _F)
_EP2 = _E2 / (1 - _E2)

# ITM projection parameters
_LAT0 = math.radians(31 + 44 / 60 + 3.817 / 3600)
_LON0 = math.radians(35 + 12 / 60 + 16.261 / 3600)
_K0 = 1.0000067
_FE = 219529.584
_FN = 626907.390

# Israel 1993 -> WGS84 (towgs84, position-vector convention)
_TX, _TY, _TZ = -24.0024, -17.1032, -17.8444
_RX, _RY, _RZ = (math.radians(s / 3600) for s in (-0.33077, -1.85269, 1.66969))
_DS = 5.4248e-6

_WGS_A = 6378137.0
_WGS_F = 1 / 298.257223563
_WGS_E2 = _WGS_F * (2 - _WGS_F)


def _meridian_arc(phi):
    e2, e4, e6 = _E2, _E2 ** 2, _E2 ** 3
    return _A * ((1 - e2 / 4 - 3 * e4 / 64 - 5 * e6 / 256) * phi
                 - (3 * e2 / 8 + 3 * e4 / 32 + 45 * e6 / 1024) * math.sin(2 * phi)
                 + (15 * e4 / 256 + 45 * e6 / 1024) * math.sin(4 * phi)
                 - (35 * e6 / 3072) * math.sin(6 * phi))


_M0 = _meridian_arc(_LAT0)


def _itm_to_grs80(x, y):
    m = _M0 + (y - _FN) / _K0
    mu = m / (_A * (1 - _E2 / 4 - 3 * _E2 ** 2 / 64 - 5 * _E2 ** 3 / 256))
    e1 = (1 - math.sqrt(1 - _E2)) / (1 + math.sqrt(1 - _E2))
    phi1 = (mu + (3 * e1 / 2 - 27 * e1 ** 3 / 32) * math.sin(2 * mu)
            + (21 * e1 ** 2 / 16 - 55 * e1 ** 4 / 32) * math.sin(4 * mu)
            + (151 * e1 ** 3 / 96) * math.sin(6 * mu)
            + (1097 * e1 ** 4 / 512) * math.sin(8 * mu))
    s, c, t = math.sin(phi1), math.cos(phi1), math.tan(phi1)
    n1 = _A / math.sqrt(1 - _E2 * s * s)
    r1 = _A * (1 - _E2) / (1 - _E2 * s * s) ** 1.5
    t1, c1 = t * t, _EP2 * c * c
    d = (x - _FE) / (n1 * _K0)
    lat = phi1 - (n1 * t / r1) * (d ** 2 / 2
                                  - (5 + 3 * t1 + 10 * c1 - 4 * c1 ** 2 - 9 * _EP2) * d ** 4 / 24
                                  + (61 + 90 * t1 + 298 * c1 + 45 * t1 ** 2 - 252 * _EP2 - 3 * c1 ** 2) * d ** 6 / 720)
    lon = _LON0 + (d - (1 + 2 * t1 + c1) * d ** 3 / 6
                   + (5 - 2 * c1 + 28 * t1 - 3 * c1 ** 2 + 8 * _EP2 + 24 * t1 ** 2) * d ** 5 / 120) / c
    return lat, lon


def itm_to_wgs84(x, y):
    """(X_ITM, Y_ITM) in meters -> (lat, lon) in WGS84 degrees."""
    lat, lon = _itm_to_grs80(float(x), float(y))
    n = _A / math.sqrt(1 - _E2 * math.sin(lat) ** 2)
    X = n * math.cos(lat) * math.cos(lon)
    Y = n * math.cos(lat) * math.sin(lon)
    Z = n * (1 - _E2) * math.sin(lat)
    k = 1 + _DS
    X2 = _TX + k * (X - _RZ * Y + _RY * Z)
    Y2 = _TY + k * (_RZ * X + Y - _RX * Z)
    Z2 = _TZ + k * (-_RY * X + _RX * Y + Z)
    p = math.hypot(X2, Y2)
    lon2 = math.atan2(Y2, X2)
    lat2 = math.atan2(Z2, p * (1 - _WGS_E2))
    for _ in range(5):
        n = _WGS_A / math.sqrt(1 - _WGS_E2 * math.sin(lat2) ** 2)
        h = p / math.cos(lat2) - n
        lat2 = math.atan2(Z2, p * (1 - _WGS_E2 * n / (n + h)))
    return math.degrees(lat2), math.degrees(lon2)
