import re

from sqlalchemy import select

from app.models import BottleFormat, ColourRequest, InventoryItem, MixRecipe, Paint, ProductLine, StorageCell, StorageUnit


def paint_by_code(session, code):
    return session.scalar(select(Paint).where(Paint.code == code))


def fmt(session, name):
    return session.scalar(select(BottleFormat).where(BottleFormat.name == name))


def test_catalogue_seeded(session):
    assert session.scalar(select(Paint).where(Paint.code == "70.950")).name == "Black"
    brands = {l.brand.name for l in session.scalars(select(ProductLine)).all()}
    for b in ["Vallejo", "The Army Painter", "Citadel", "AK Interactive", "Scale 75", "Turbo Dork", "Tamiya"]:
        assert b in brands


def test_pages_render(client, session):
    p = paint_by_code(session, "70.950")
    for url in ["/", "/paints", "/storage", "/mixes", "/mixes/lab", "/settings", "/references", "/shopping",
                f"/paints/{p.id}", "/colour-requests", "/import", "/paints/new", f"/add?paint_id={p.id}"]:
        r = client.get(url)
        assert r.status_code == 200, url


def test_search_by_code_name_and_reference(client):
    r = client.get("/api/search", params={"q": "70.950"}).json()
    assert r[0]["code"] == "70.950"
    r = client.get("/search", params={"q": "dunkelgelb"})
    assert "RAL 7028" in r.text
    # diacritics folded: "olivgrun" finds Olivgrün
    assert "Olivgrün" in client.get("/search", params={"q": "olivgrun"}).text


def make_unit(client, name, rows, cols, formats=()):
    r = client.post("/storage", data={"name": name, "rows": rows, "cols": cols, "label_scheme": "A1",
                                      "format_ids": [str(f) for f in formats]}, follow_redirects=False)
    assert r.status_code == 303
    return int(re.search(r"/storage/(\d+)", r.headers["location"]).group(1))


def test_storage_add_and_fit(client, session):
    dropper = fmt(session, "17-18 ml dropper")
    pot = fmt(session, "Citadel pot")
    pots_id = make_unit(client, "Pot rack", 2, 3, [pot.id])
    drop_id = make_unit(client, "Dropper rack", 3, 4, [dropper.id])
    session.expire_all()
    unit = session.get(StorageUnit, drop_id)
    assert len(unit.cells) == 12
    assert sorted(c.label for c in unit.cells)[:3] == ["A1", "A2", "A3"]

    black = paint_by_code(session, "70.950")
    # auto-placement goes to the dropper rack (tagged for the Vallejo bottle), not the pot rack
    r = client.post("/items", data={"paint_id": black.id, "cell_id": "auto", "quantity": 2}, follow_redirects=False)
    assert r.status_code == 303
    session.expire_all()
    items = session.scalars(select(InventoryItem).where(InventoryItem.paint_id == black.id)).all()
    assert len(items) == 2
    assert {i.cell.unit_id for i in items} == {drop_id}
    assert {i.cell.label for i in items} == {"A1", "A2"}

    # moving it into the pot rack works but warns
    pot_cell = session.scalars(select(StorageCell).where(StorageCell.unit_id == pots_id).order_by(StorageCell.id.desc())).first()
    r = client.post(f"/items/{items[0].id}/move", json={"cell_id": pot_cell.id})
    body = r.json()
    assert body["ok"] and "may not fit" in body["warning"]

    # cell is full now: a second bottle can't go in
    r = client.post(f"/items/{items[1].id}/move", json={"cell_id": pot_cell.id})
    assert r.status_code == 409

    # swap with drag-and-drop
    r = client.post(f"/items/{items[1].id}/move", json={"cell_id": pot_cell.id, "swap": True})
    assert r.json()["ok"]

    # location is searchable
    hits = client.get("/search", params={"q": "Pot rack"}).text
    assert "70.950" in hits

    # storage grid renders with highlight
    assert client.get(f"/storage/{drop_id}?highlight=paint:{black.id}").status_code == 200
    assert client.get(f"/cells/{pot_cell.id}").status_code == 200

    # can't shrink a rack over occupied cells
    r = client.post(f"/storage/{pots_id}/edit", data={"name": "Pot rack", "rows": 1, "cols": 1}, follow_redirects=False)
    assert "shrink" in r.headers["location"]
    session.expire_all()
    occupied = [c for c in session.get(StorageUnit, pots_id).cells if c.items]
    assert occupied  # still there


def test_bulk_fill_and_disabled(client, session):
    uid = make_unit(client, "Bulk rack", 1, 3)
    session.expire_all()
    cells = sorted(session.get(StorageUnit, uid).cells, key=lambda c: c.col)
    assert client.post(f"/cells/{cells[1].id}/toggle-disabled", follow_redirects=False).status_code == 303
    p = paint_by_code(session, "70.951")
    r = client.post(f"/api/cells/{cells[0].id}/fill", json={"paint_id": p.id}).json()
    assert r["ok"]
    r = client.post(f"/api/cells/{cells[0].id}/fill", json={"paint_id": p.id}).json()
    assert not r["ok"]  # full
    r = client.post(f"/api/cells/{cells[1].id}/fill", json={"paint_id": p.id}).json()
    assert not r["ok"]  # disabled
    assert client.get(f"/storage/{uid}/bulk").status_code == 200


def test_mix_save_batch_and_log(client, session):
    blue = paint_by_code(session, "70.925")
    yellow = paint_by_code(session, "70.915")
    before = session.query(ColourRequest).count()
    r = client.post("/api/mixes", json={
        "name": "Test green", "unit": "drops", "target_hex": "#4B6B3A", "components": [
            {"kind": "paint", "id": blue.id, "amount": 2},
            {"kind": "paint", "id": yellow.id, "amount": 1},
        ]})
    assert r.status_code == 200, r.text
    mid = r.json()["id"]
    assert r.json()["estimated_hex"].startswith("#")
    session.expire_all()
    assert session.query(ColourRequest).count() == before + 1

    # nested mix, and cycles are refused
    r = client.post("/api/mixes", json={"name": "Nested", "components": [{"kind": "mix", "id": mid, "amount": 1},
                                                                          {"kind": "paint", "id": blue.id, "amount": 1}]})
    nested = r.json()["id"]
    r = client.post("/api/mixes", json={"id": mid, "name": "Test green", "components": [{"kind": "mix", "id": nested, "amount": 1}]})
    assert r.status_code == 400

    page = client.get(f"/mixes/{mid}?ml=12")
    assert page.status_code == 200 and "drops" in page.text

    r = client.post(f"/mixes/{mid}/batch", data={"cell_id": "auto", "volume_ml": "10"}, follow_redirects=False)
    assert r.status_code == 303
    session.expire_all()
    m = session.get(MixRecipe, mid)
    assert len(m.items) == 1

    client.post(f"/mixes/{mid}/measured", data={"measured_hex": "#405f33"})
    session.expire_all()
    assert session.get(MixRecipe, mid).measured_hex == "#405F33"

    assert client.post("/api/colour-requests", json={"hex": "#9C8853", "source": "mix_target"}).json()["ok"]


def test_colour_search_logs(client, session):
    before = session.query(ColourRequest).count()
    r = client.get("/colours", params={"hex": "#9C8853"})
    assert r.status_code == 200 and "Closest paints" in r.text
    session.expire_all()
    assert session.query(ColourRequest).count() == before + 1


def test_notes_and_search(client, session):
    p = paint_by_code(session, "70.950")
    r = client.post(f"/notes/paint/{p.id}", data={"body": "Great for zimmerit shading"}, headers={"HX-Request": "true"})
    assert r.status_code == 200 and "zimmerit" in r.text
    assert "70.950" in client.get("/search", params={"q": "zimmerit"}).text
    # HTML is escaped
    r = client.post(f"/notes/paint/{p.id}", data={"body": "<script>x</script>"}, headers={"HX-Request": "true"})
    assert "<script>x" not in r.text


def test_used_up_to_shopping(client, session):
    p = paint_by_code(session, "70.951")
    item = session.scalars(select(InventoryItem).where(InventoryItem.paint_id == p.id)).first()
    client.post(f"/items/{item.id}/used-up", data={"shopping": "1"})
    assert p.name in client.get("/shopping").text


def test_user_paint_and_csv_import(client, session):
    r = client.post("/paints", data={"brand": "Two Thin Coats", "line": "Wave 1", "code": "", "name": "Test Ochre",
                                     "hex": "#c08a1e", "type": "acrylic", "finish": "matt"}, follow_redirects=False)
    assert r.status_code == 303
    csv_data = b"code,name,hex\nAB01,Panzer Test,#445566\n"
    r = client.post("/import", data={"brand": "Abteilung 502", "line": "Oils"}, files={"file": ("x.csv", csv_data, "text/csv")},
                    follow_redirects=False)
    assert "Imported+1" in r.headers["location"] or "Imported%201" in r.headers["location"]
    assert client.get("/api/search", params={"q": "Panzer Test"}).json()[0]["code"] == "AB01"


def test_exports(client):
    j = client.get("/export/json").json()
    assert "inventory_items" in j["tables"]
    assert client.get("/export/inventory.csv").text.startswith("item_id")
    assert client.get("/export/db").content[:15] == b"SQLite format 3"
    assert client.post("/backup-now", follow_redirects=False).status_code == 303
