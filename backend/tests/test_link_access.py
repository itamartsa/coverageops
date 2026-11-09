"""/api/link — owner scoping of Site transmitters and saved link results."""
from test_api_link import (  # noqa: F401  (fixtures + helpers)
    RX_CLEAR, _user, add_cell, client, create_site, factory, link, setup,
)
from app.models.user import UserRole


def test_operator_cannot_use_foreign_site(setup):
    client, state = setup
    site = create_site(client)                               # owned by admin (id 1)
    state["user"] = _user(2, UserRole.OPERATOR)
    link(client, {"kind": "site", "id": site["id"]}, RX_CLEAR, save=False, status=403)
    own = create_site(client, name="שלי")
    link(client, {"kind": "site", "id": own["id"]}, RX_CLEAR, save=False)
    state["user"] = _user()                                  # ADMIN may use any site
    link(client, {"kind": "site", "id": own["id"]}, RX_CLEAR, save=False)


def test_saved_links_scoped_to_owner(setup, factory):
    client, state = setup
    cid = add_cell(factory)
    a = link(client, {"kind": "cell_site", "id": cid}, RX_CLEAR)              # admin's
    state["user"] = _user(2, UserRole.OPERATOR)
    b = link(client, {"kind": "cell_site", "id": cid}, RX_CLEAR)              # operator's
    assert [x["id"] for x in client.get("/api/link/").json()] == [b["id"]]
    assert client.get(f"/api/link/{a['id']}").status_code == 404
    assert client.get(f"/api/link/{a['id']}/docx").status_code == 404
    assert client.get(f"/api/link/{b['id']}").status_code == 200
    state["user"] = _user(3, UserRole.VIEWER)
    assert client.get("/api/link/").json() == []
    state["user"] = _user()
    assert {x["id"] for x in client.get("/api/link/").json()} == {a["id"], b["id"]}
    assert client.get(f"/api/link/{b['id']}").status_code == 200


def test_cell_freq_estimated_unless_manual(setup, factory):
    client, _state = setup
    est = add_cell(factory, technologies="2G,4G", frequency_mhz=900)
    tx = link(client, {"kind": "cell_site", "id": est, "frequency_mhz": 900}, RX_CLEAR, save=False)["transmitter"]
    assert "frequency_mhz" in tx["estimated_fields"]            # sent explicitly, equals estimate
    man = add_cell(factory, site_code="P2", frequency_mhz=1800, frequency_source="manual")
    tx = link(client, {"kind": "cell_site", "id": man}, RX_CLEAR, save=False)["transmitter"]
    assert tx["frequency_mhz"] == 1800 and "frequency_mhz" not in tx["estimated_fields"]
    assert "tx_power_dbm" in tx["estimated_fields"]             # tx power still estimated
