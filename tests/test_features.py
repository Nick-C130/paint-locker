import re
from datetime import date, timedelta

from sqlalchemy import select

from app import alerts as al
from app.models import InventoryItem, Paint, Project, Recipe, ShoppingItem


def paint(session, code):
    return session.scalar(select(Paint).where(Paint.code == code))


def add_bottle(client, paint_id, **extra):
    r = client.post("/items", data={"paint_id": paint_id, "cell_id": "", **extra}, follow_redirects=False)
    assert r.status_code == 303
    return int(re.search(r"/items/(\d+)", r.headers["location"]).group(1)) if "/items/" in r.headers["location"] else None


def test_recipe_steps_and_needs(client, session):
    grey, black = paint(session, "70.995"), paint(session, "70.950")
    r = client.post("/recipes", data={"name": "Panzer grey", "tags": "german, armour"}, follow_redirects=False)
    rid = int(re.search(r"/recipes/(\d+)", r.headers["location"]).group(1))
    for tech, pid in [("basecoat", grey.id), ("wash", black.id), ("highlight", "")]:
        assert client.post(f"/recipes/{rid}/steps", data={"technique": tech, "paint_id": pid, "area": "hull"},
                           follow_redirects=False).status_code == 303
    page = client.get(f"/recipes/{rid}")
    assert page.status_code == 200 and "Basecoat" in page.text and grey.name in page.text
    assert client.get(f"/recipes/{rid}/edit").status_code == 200
    session.expire_all()
    rec = session.get(Recipe, rid)
    assert [s.technique for s in rec.steps] == ["basecoat", "wash", "highlight"]
    assert rec.tags == "german;armour"
    # move the wash up, then add missing paints to the shopping list
    client.post(f"/recipe-steps/{rec.steps[1].id}/move", data={"direction": "up"})
    session.expire_all()
    assert [s.technique for s in session.get(Recipe, rid).steps] == ["wash", "basecoat", "highlight"]
    before = session.query(ShoppingItem).count()
    client.post(f"/recipes/{rid}/shopping")
    session.expire_all()
    owned = {p for p in (grey.id, black.id) if session.scalars(select(InventoryItem).where(InventoryItem.paint_id == p)).first()}
    assert session.query(ShoppingItem).count() == before + 2 - len(owned)
    assert "/locate?" in page.text


def test_project_tree_needs_and_progress(client, session):
    army = client.post("/projects", data={"name": "Test army", "kind": "army"}, follow_redirects=False)
    aid = int(army.headers["location"].split("/")[-1].split("?")[0])
    unit = client.post("/projects", data={"name": "Test platoon", "kind": "unit", "parent_id": aid, "models_total": 4},
                       follow_redirects=False)
    uid = int(unit.headers["location"].split("/")[-1].split("?")[0])
    rid = session.scalar(select(Recipe.id).where(Recipe.name == "Panzer grey"))
    client.post(f"/projects/{uid}/recipes", data={"recipe_id": rid})
    p = paint(session, "70.888")
    client.post(f"/projects/{aid}/paints", data={"paint_id": p.id, "note": "bases"})
    client.post(f"/projects/{uid}/progress", data={"delta": 1})
    page = client.get(f"/projects/{aid}")
    assert page.status_code == 200
    assert "Test platoon" in page.text and p.name in page.text and "1 / 4 painted" in page.text
    assert "Panzer grey" in page.text  # the unit's recipe shows up in the army's needs
    # can't make the army a unit of its own platoon
    client.post(f"/projects/{aid}/edit", data={"name": "Test army", "kind": "army", "parent_id": uid})
    session.expire_all()
    assert session.get(Project, aid).parent_id is None
    assert client.get("/projects").status_code == 200


def test_qr_labels_and_short_links(client, session):
    r = client.post("/storage", data={"name": "Label rack", "rows": 2, "cols": 2, "label_scheme": "A1"}, follow_redirects=False)
    unit_id = int(re.search(r"/storage/(\d+)", r.headers["location"]).group(1))
    page = client.get("/labels", params={"unit": unit_id})
    assert page.status_code == 200
    assert page.text.count('class="label ') == 4 and "<svg" in page.text
    assert "/q/c/" in page.text
    cell_id = int(re.search(r"/q/c/(\d+)", page.text).group(1))
    r = client.get(f"/q/c/{cell_id}", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith(f"/storage/{unit_id}")
    client.post("/labels/public-url", data={"public_url": "https://paints.example"})
    assert "https://paints.example/q/c/" in client.get("/labels", params={"unit": unit_id}).text
    client.post("/labels/public-url", data={"public_url": ""})
    assert client.get("/labels", params={"mixes": "all"}).status_code == 200


def test_capture_page_and_measured_colour(client, session):
    p = paint(session, "70.893")
    assert client.get("/capture", params={"paint_id": p.id}).status_code == 200
    assert client.get("/capture").status_code == 200
    client.post(f"/paints/{p.id}/measured", data={"measured_hex": "#4a5a3a"})
    session.expire_all()
    p = session.get(Paint, p.id)
    assert p.measured_hex.lower() == "#4a5a3a" and p.ok_l is not None
    client.post(f"/paints/{p.id}/measured", data={"measured_hex": ""})


def test_alerts(client, session):
    a, b = paint(session, "70.862"), paint(session, "70.863")
    i1 = add_bottle(client, a.id, acquired_on=(date.today() - timedelta(days=365 * 4)).isoformat())
    add_bottle(client, a.id)
    client.post(f"/items/{i1}/level", data={"level": "low"})
    kinds = {x.kind for x in al.compute(session)}
    assert {"low", "duplicate", "age"} <= kinds
    page = client.get("/alerts")
    assert page.status_code == 200 and "Running low" in page.text
    # "still good" resets the age alert; dismiss hides the duplicate
    client.post(f"/items/{i1}/checked")
    session.expire_all()
    assert not [x for x in al.compute(session) if x.kind == "age" and x.item_id == i1]
    dup = next(x for x in al.compute(session) if x.kind == "duplicate" and a.full_label in x.text)
    client.post("/alerts/dismiss", data={"key": dup.key})
    session.expire_all()
    assert dup.key not in {x.key for x in al.compute(session)}
    assert b  # near-identical detection is covered by the unit test below


def test_similar_paint_alert(client, session):
    a, b = paint(session, "70.890"), paint(session, "70.891")
    assert a.type == b.type == "acrylic"
    add_bottle(client, a.id)
    add_bottle(client, b.id)
    client.post(f"/paints/{a.id}/measured", data={"measured_hex": "#556b2f"})
    client.post(f"/paints/{b.id}/measured", data={"measured_hex": "#566c2f"})
    session.expire_all()
    lo, hi = sorted((a.id, b.id))
    assert f"similar:{lo}:{hi}" in {x.key for x in al.compute(session)}
    for p in (a, b):
        client.post(f"/paints/{p.id}/measured", data={"measured_hex": ""})


def test_shopping_by_shop(client, session):
    from app.models import Brand, Shop

    # catalogue lines name Frontline Hobbies in stocked_by, so it's created on startup
    assert session.scalar(select(Shop).where(Shop.name == "Frontline Hobbies"))
    tamiya = session.scalar(select(Brand).where(Brand.name == "Tamiya"))
    r = client.post("/shops", data={"name": "Model Shop", "search_url": "https://ms.example/s?q={q}", "brand_ids": [tamiya.id]})
    assert r.status_code == 200
    shop = session.scalar(select(Shop).where(Shop.name == "Model Shop"))
    tp = session.scalar(select(Paint).join(Paint.line).where(Paint.line.has(brand_id=tamiya.id)))
    client.post("/shopping", data={"paint_id": tp.id})
    page = client.get("/shopping").text
    assert "Model Shop" in page and "https://ms.example/s?q=Tamiya" in page
    item = session.scalars(select(ShoppingItem).where(ShoppingItem.paint_id == tp.id)).first()
    client.post(f"/shopping/{item.id}/quantity", data={"delta": 2})
    session.expire_all()
    assert session.get(ShoppingItem, item.id).quantity == 3
    # Frontline also stocks Tamiya (catalogue stocked_by) and sorts first; pick Model Shop by hand
    client.post(f"/shopping/{item.id}/shop", data={"shop_id": shop.id})
    one = client.get("/shopping", params={"shop": shop.id}).text
    assert "× 3" in one and "Model Shop" in one and "also Frontline Hobbies" in one and "<h2>Frontline" not in one
    assert client.get("/shops").status_code == 200


def test_stats_page(client):
    r = client.get("/stats")
    assert r.status_code == 200
    assert "Bottles by brand" in r.text and "Colour families" in r.text


def test_import_collection_csv(client, session):
    csv = (
        b"Brand,Range,Code,Paint Name,Qty,Owned,Rack,Slot\n"
        b"Vallejo,Model Color,70.950,Black,2,yes,,\n"
        b"Vallejo,,70.951,,1,yes,,\n"
        b"Citadel,Base,,Abaddon Black,1,yes,,\n"
        b"Vallejo,Model Color,70.952,Lemon Yellow,1,no,,\n"
        b"Nobody,,ZZ-999,Unobtainium,1,yes,,\n"
    )
    r = client.post("/import/collection", files={"file": ("paints.csv", csv, "text/csv")}, follow_redirects=False)
    assert r.status_code == 303
    token = r.headers["location"].rsplit("/", 1)[-1]
    page = client.get(f"/import/collection/{token}")
    assert page.status_code == 200
    assert "not owned" in page.text and "no match" in page.text and "Abaddon Black" in page.text
    before = session.query(InventoryItem).count()
    form = {"map_0": "brand", "map_1": "line", "map_2": "code", "map_3": "name", "map_4": "quantity", "map_5": "owned",
            "map_6": "storage", "map_7": "cell", "place": "none", "guesses": "1", "row": ["1", "2", "3", "4", "5"]}
    r = client.post(f"/import/collection/{token}/confirm", data=form, follow_redirects=False)
    assert r.status_code == 303
    session.expire_all()
    assert session.query(InventoryItem).count() == before + 4  # 2 + 1 + 1; not-owned and unmatched skipped
    assert client.get(f"/import/collection/{token}").status_code == 404


def test_import_pasted_list(client, session):
    from app import importer as imp

    header, rows = imp.parse_text("70.950\n2x Abaddon Black\nMatt White x3")
    assert header == ["item", "quantity"] and rows[1] == ["Abaddon Black", "2"] and rows[2] == ["Matt White", "3"]
    matched = imp.match_rows(session, header, rows, imp.guess_mapping(header))
    assert matched[0].paint.code == "70.950" and matched[0].how == "code"
    assert matched[1].paint and "Abaddon" in matched[1].paint.name and matched[1].quantity == 2
    r = client.post("/import/collection", data={"text": "70.950"}, follow_redirects=False)
    assert r.status_code == 303 and client.get(r.headers["location"]).status_code == 200


def test_search_finds_recipes_and_projects(client):
    r = client.get("/search", params={"q": "Panzer grey"})
    assert "/recipes/" in r.text
    assert "/projects/" in client.get("/search", params={"q": "Test platoon"}).text


def test_import_xlsx(client, session):
    import io

    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.append(["Manufacturer", "SKU", "Colour", "Quantity"])
    ws.append(["Vallejo", "70.950", "Black", 1])
    buf = io.BytesIO()
    wb.save(buf)
    r = client.post("/import/collection", files={"file": ("p.xlsx", buf.getvalue(), "application/octet-stream")}, follow_redirects=False)
    page = client.get(r.headers["location"])
    assert page.status_code == 200 and "by code" in page.text
