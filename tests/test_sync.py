import json
import os
import sys
import tempfile
from dataclasses import replace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wegmans_bridge.norish import Grocery
from wegmans_bridge.store import Store, name_key
from wegmans_bridge.sync import quantity_for, sync_once
from wegmans_bridge.wegmans import Cart, LineItem, build_line_item, parse_cart

STORE = "store-weg"

MILK_HIT = {
    "productID": "58871", "skuId": "58871", "productName": "Wegmans Vitamin D Milk",
    "categoryNodes": {"lvl0": "Dairy"},
    "category": [{"name": "Milk", "key": "2957428"}, {"name": "Dairy", "key": "2957400"}],
    "planogram": {"aisle": "Dairy 5", "shelf": "1", "aisleSide": "L", "section": "16"},
    "price_inStore": {"amount": 2.49}, "upc": ["00077890588710"],
    "fulfilmentType": ["instore", "pickup", "delivery"], "maxQuantity": 20,
    "isSoldAtStore": True, "ebtEligible": True, "isAvailable": True, "onlineSellByUnit": "ea",
}


class FakeNorish:
    def __init__(self, groceries):
        self.g = {x.id: x for x in groceries}
        self.calls = []

    def groceries(self):
        return list(self.g.values())

    def set_done(self, g, done):
        self.calls.append(("done", g.id, done))
        self.g[g.id] = replace(self.g[g.id], is_done=done, version=g.version + 1)
        return True


class FakeWegmans:
    def __init__(self, hits):
        self.hits = hits
        self.items = {}
        self.calls = []
        self.n = 0

    def cart(self):
        return Cart("cart1", 1, "cust", "e@x", list(self.items.values()))

    def search(self, q, limit=5):
        return [h for h in self.hits if q.lower() in h["productName"].lower()][:limit]

    def product(self, sku):
        return next((h for h in self.hits if h["productID"] == sku), None)

    def add(self, hit, quantity=1):
        self.calls.append(("add", hit["productID"], quantity))
        self.n += 1
        self.items[hit["productID"]] = LineItem(f"li{self.n}", hit["productID"], hit["productName"], quantity, False, "")
        return self.cart()

    def remove(self, sku):
        self.calls.append(("remove", sku))
        self.items.pop(sku)
        return self.cart()

    def set_got_it(self, line_id, value):
        self.calls.append(("gotit", line_id, value))
        for k, li in self.items.items():
            if li.id == line_id:
                self.items[k] = replace(li, got_it=value)
        return self.cart()


def g(id="g1", name="milk", done=False, store=STORE, amount=None, unit=None):
    return Grocery(id, name, amount, unit, done, store, 1)


def fresh_store():
    return Store(os.path.join(tempfile.mkdtemp(), "t.sqlite3"))


def test_adds_new_open_grocery_once():
    n, w, s = FakeNorish([g()]), FakeWegmans([MILK_HIT]), fresh_store()
    r = sync_once(n, w, s, STORE)
    assert w.calls == [("add", "58871", 1)] and r.added
    sync_once(n, w, s, STORE)  # second pass: nothing new
    assert len(w.calls) == 1


def test_ignores_other_stores_done_items_and_unmatched():
    n = FakeNorish([g("a", store="other"), g("b", done=True), g("c", name="unobtainium")])
    w, s = FakeWegmans([MILK_HIT]), fresh_store()
    r = sync_once(n, w, s, STORE)
    assert w.calls == [] and r.unmatched == ["unobtainium"]


def test_got_it_in_wegmans_marks_done_in_norish():
    n, w, s = FakeNorish([g()]), FakeWegmans([MILK_HIT]), fresh_store()
    sync_once(n, w, s, STORE)
    w.set_got_it("li1", True); w.calls.clear()
    r = sync_once(n, w, s, STORE)
    assert n.calls == [("done", "g1", True)] and r.to_norish and w.calls == []


def test_done_in_norish_checks_off_in_wegmans_and_back():
    n, w, s = FakeNorish([g()]), FakeWegmans([MILK_HIT]), fresh_store()
    sync_once(n, w, s, STORE)
    n.g["g1"] = replace(n.g["g1"], is_done=True)
    sync_once(n, w, s, STORE)
    assert ("gotit", "li1", True) in w.calls
    n.g["g1"] = replace(n.g["g1"], is_done=False)
    sync_once(n, w, s, STORE)
    assert ("gotit", "li1", False) in w.calls and n.calls == []


def test_deleted_in_norish_removes_from_wegmans():
    n, w, s = FakeNorish([g()]), FakeWegmans([MILK_HIT]), fresh_store()
    sync_once(n, w, s, STORE)
    n.g.clear()
    r = sync_once(n, w, s, STORE)
    assert ("remove", "58871") in w.calls and r.removed and s.links() == {}


def test_removed_in_wegmans_is_not_readded():
    n, w, s = FakeNorish([g()]), FakeWegmans([MILK_HIT]), fresh_store()
    sync_once(n, w, s, STORE)
    w.items.clear(); w.calls.clear()
    sync_once(n, w, s, STORE); sync_once(n, w, s, STORE)
    assert w.calls == [] and n.calls == []


def test_existing_cart_item_is_linked_not_duplicated():
    n, w, s = FakeNorish([g()]), FakeWegmans([MILK_HIT]), fresh_store()
    w.items["58871"] = LineItem("pre", "58871", "milk", 1, False, "")
    sync_once(n, w, s, STORE)
    assert w.calls == [] and "g1" in s.links()


def test_pinned_mapping_wins_over_search():
    other = dict(MILK_HIT, productID="999", skuId="999", productName="Oat milk")
    n, w, s = FakeNorish([g()]), FakeWegmans([MILK_HIT, other]), fresh_store()
    s.remember("Milk", "999", "Oat milk", confirmed=True)
    sync_once(n, w, s, STORE)
    assert w.calls == [("add", "999", 1)]


def test_dry_run_changes_nothing():
    n, w, s = FakeNorish([g()]), FakeWegmans([MILK_HIT]), fresh_store()
    r = sync_once(n, w, s, STORE, dry_run=True)
    assert r.added and w.calls == [] and s.links() == {}


def test_quantity_rules():
    assert quantity_for(3, None) == 3
    assert quantity_for(2, "cups") == 1
    assert quantity_for(0.5, None) == 1
    assert quantity_for(None, None) == 1


def test_name_key():
    assert name_key("  Whole Milk (optional)!") == "whole milk"


def test_line_item_matches_captured_shape():
    li = build_line_item(MILK_HIT, "53")
    names = [c["name"] for c in li["custom"]]
    # Same custom fields, in the same order, as the add request captured from wegmans.com
    assert names == ["category", "categoryId", "itemLevelAdjustments", "isSoldAtStore", "ebtEligible",
                     "isAvailable", "planogram", "note", "bottleDeposit", "upc", "fulfillmentTypes", "maxQuantity"]
    assert li["custom"][0]["value"] == "Dairy"
    assert li["custom"][1]["value"] == "2957400"
    assert json.loads(li["custom"][6]["value"]) == {"aisle": "Dairy 5", "shelf": "1", "aisleSide": "L", "section": "16"}
    assert li["standalonePrice"] == 249 and li["sku"] == "58871"
    assert li["distributionChannelKey"] == "53-Instore" and li["onlineSellByUnit"] == "ea"


def test_parse_real_cart_shape():
    # Trimmed from the real GET /commerce/cart/carts/ response
    data = {"grocery": {"id": "c", "version": 39515, "customerId": "cu", "customerEmail": "e",
            "lineItems": [{"id": "l1", "name": "Popcorn", "quantity": 1, "variant": {"sku": "900147"},
                           "custom": {"customFieldsRaw": [
                               {"name": "planogram", "value": "{\"aisle\":\"06B\",\"shelf\":\"5\",\"aisleSide\":\"R\",\"section\":\"3\"}"},
                               {"name": "gotIt", "value": True}]}},
                          {"id": "l2", "name": "Amy's", "quantity": 1, "variant": {"sku": "279213"},
                           "custom": {"customFieldsRaw": []}}]}}
    c = parse_cart(data)
    assert c.version == 39515 and c.items[0].got_it and c.items[0].aisle == "06B-R-3"
    assert c.items[1].got_it is False and c.by_sku("279213").id == "l2"
