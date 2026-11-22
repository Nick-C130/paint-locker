import re

from sqlalchemy import select

from app.models import (
    BottleFormat,
    ColourRequest,
    InventoryItem,
    MixRecipe,
    Paint,
    ProductLine,
    StorageCell,
    StorageUnit,
)


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


def test_barcode_scan_flow(client, session):
    p = paint_by_code(session, "70.882")
    r = client.get("/api/barcode/8429551708821").json()
    assert r["found"] is False
    r = client.post("/api/barcode/link", json={"barcode": "8429551708821", "paint_id": p.id}).json()
    assert r["ok"] and r["paint"]["id"] == p.id
    r = client.get("/api/barcode/8429551708821").json()
    assert r["found"] and r["paint"]["id"] == p.id
    # UPC-A / EAN-13 leading-zero equivalence
    client.post("/api/barcode/link", json={"barcode": "012345678905", "paint_id": p.id})
    assert client.get("/api/barcode/0012345678905").json()["found"]
    # scan-to-add places a bottle
    r = client.post("/api/barcode/add", json={"paint_id": p.id}).json()
    assert r["ok"] and r["paint"]["owned"] >= 1
    # typing the barcode in global search jumps to the paint
    r = client.get("/search", params={"q": "8429551708821"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == f"/paints/{p.id}"
    # links survive a catalogue re-import
    client.post("/catalogue/reimport")
    assert client.get("/api/barcode/8429551708821").json()["found"]
    page = client.get(f"/paints/{p.id}").text
    assert "8429551708821" in page
    assert client.get("/scan").status_code == 200
    assert client.get(f"/scan?link={p.id}").status_code == 200


def test_water_and_flow_improver_dilute_not_tint(client, session):
    blue = paint_by_code(session, "70.925")
    yellow = paint_by_code(session, "70.915")
    flow = session.scalar(
        select(Paint).join(ProductLine).where(ProductLine.name == "Auxiliaries", Paint.name == "Flow Improver")
    )
    assert flow is not None and flow.type == "medium"
    # thinners/mediums are findable for the mix lab
    r = client.get("/api/search", params={"ptypes": "medium,varnish", "kinds": "paint"}).json()
    assert any(x["name"] == "Flow Improver" for x in r)
    # "Medium Green" style colour names are not mistaken for mediums
    assert all("Medium Green" not in x["name"] for x in r)

    plain = client.post("/api/mixes", json={"name": "Plain", "components": [
        {"kind": "paint", "id": blue.id, "amount": 2}, {"kind": "paint", "id": yellow.id, "amount": 1}]}).json()
    thinned = client.post("/api/mixes", json={"name": "Thinned", "components": [
        {"kind": "paint", "id": blue.id, "amount": 2}, {"kind": "paint", "id": yellow.id, "amount": 1},
        {"kind": "additive", "id": "water", "amount": 1}, {"kind": "paint", "id": flow.id, "amount": 1}]}).json()
    assert thinned["estimated_hex"] == plain["estimated_hex"]
    session.expire_all()
    m = session.get(MixRecipe, thinned["id"])
    assert m.ratio == "2 : 1 : 1 : 1"
    assert abs(m.dilution - 0.4) < 1e-9
    page = client.get(f"/mixes/{m.id}").text
    assert "2 : 1 : 1 : 1" in page and "Water" in page and "40%" in page
    # lab reloads the water component
    assert '"kind": "additive"' in client.get(f"/mixes/lab?mix_id={m.id}").text.replace("&#34;", '"')
    # water alone isn't a mix
    r = client.post("/api/mixes", json={"name": "Just water", "components": [{"kind": "additive", "id": "water", "amount": 1}]})
    assert r.status_code == 400
    r = client.post("/api/mixes", json={"name": "Bad", "components": [
        {"kind": "paint", "id": blue.id, "amount": 1}, {"kind": "additive", "id": "gin", "amount": 1}]})
    assert r.status_code == 400


def test_dashboard_mini_rack(client):
    html = client.get("/").text
    assert "mini-rack" in html and 'class="mc cat-' in html and 'class="legend' in html
    assert "mini-rack" in client.get("/storage").text


def test_mix_suggestions_from_owned_paints(client, session):
    # own a few basics so there is something to mix from
    for code in ("70.915", "70.925", "70.950", "70.951", "70.988", "70.921"):
        p = paint_by_code(session, code)
        client.post("/items", data={"paint_id": p.id, "cell_id": ""})
    r = client.get("/api/mix-suggestions", params={"hex": "#9C8853"}).json()
    assert r["owned_mixable"] >= 5
    s = r["suggestions"]
    assert s and s[0]["delta_e"] < 10
    assert all(1 <= len(x["components"]) <= 3 for x in s)
    assert s[0]["ratio"].count(":") == len(s[0]["components"]) - 1
    two = client.get("/api/mix-suggestions", params={"hex": "#9C8853", "max_paints": 2}).json()["suggestions"]
    assert all(len(x["components"]) <= 2 for x in two)
    # colours page shows them and links into the lab with the combo preloaded
    page = client.get("/colours", params={"hex": "#9C8853"}).text
    assert "Mixes you could make" in page
    combo = ",".join(f'{c["id"]}:{c["amount"]:g}' for c in s[0]["components"])
    lab = client.get("/mixes/lab", params={"target": "#9C8853", "combo": combo}).text
    assert s[0]["components"][0]["label"].split(": ")[-1] in lab


def test_locate_paints_for_a_mix(client, session):
    uid = make_unit(client, "Locate rack", 2, 3)
    blue = paint_by_code(session, "70.925")
    white = paint_by_code(session, "70.951")
    session.expire_all()
    cell = sorted(session.get(StorageUnit, uid).cells, key=lambda c: (c.row, c.col))[0]
    client.post(f"/api/cells/{cell.id}/fill", json={"paint_id": blue.id})
    r = client.post("/api/mixes", json={"name": "Locate me", "components": [
        {"kind": "paint", "id": blue.id, "amount": 1}, {"kind": "paint", "id": white.id, "amount": 1}]}).json()
    page = client.get(f"/mixes/{r['id']}").text
    assert "Find paints in racks" in page
    url = re.search(r'href="(/locate\?[^"]+)"', page).group(1).replace("&amp;", "&")
    html = client.get(url).text
    assert "Locate rack" in html and " hl" in html and "cat-acrylic" in html


def test_purchase_recommender(client, session):
    page = client.get("/recommend")
    assert page.status_code == 200
    r = client.get("/recommend", params={"run": 1, "count": 3, "scope": "lines"})
    assert r.status_code == 200
    picks = re.findall(r'action="/recommend/(\d+)/dismiss"', r.text)
    assert len(picks) == 3 and len(set(picks)) == 3
    owned_ids = {i.paint_id for i in session.scalars(select(InventoryItem)).all()}
    assert not ({int(p) for p in picks} & owned_ids)
    assert "colour range" in r.text
    # dismiss removes it from the next run; shopping-list picks are skipped too
    client.post(f"/recommend/{picks[0]}/dismiss")
    client.post(f"/recommend/{picks[1]}/shopping")
    r2 = client.get("/recommend", params={"run": 1, "count": 3})
    again = re.findall(r'action="/recommend/(\d+)/dismiss"', r2.text)
    assert picks[0] not in again and picks[1] not in again
    client.post("/recommend/dismissed/clear")


def test_recommender_unit_demand():
    from datetime import datetime

    from app.recommender import recommend

    class P:
        def __init__(self, i, hx):
            self.id, self.display_hex = i, hx

    class Req:
        def __init__(self, hx):
            self.target_hex, self.context, self.created_at = hx, "red markings", datetime.now()

    owned = [P(1, "#FFFFFF"), P(2, "#000000"), P(3, "#1F3A93")]
    cands = [P(10, "#D2232A"), P(11, "#2E8B57"), P(12, "#1E3A90")]
    # Demand only: repeatedly asked for red -> red first
    picks = recommend(owned, cands, [Req("#C0392B")] * 3, count=2, weight_reach=0.0)
    assert picks[0].paint.id == 10 and picks[0].helped
    # The near-duplicate blue adds almost nothing
    picks = recommend(owned, cands, [], count=3, weight_reach=1.0)
    assert picks[-1].paint.id == 12 or len(picks) < 3
