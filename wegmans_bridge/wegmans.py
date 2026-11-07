"""Client for the (unofficial) Wegmans web APIs, as used by wegmans.com.

In-store "My List" is the shopping cart with fulfillmentType=instore. Every cart
change must carry the current cartVersion; a stale one is rejected, so mutations
re-read the cart and retry once.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Callable

import requests

log = logging.getLogger(__name__)

API = "https://api.digitaldevelopment.wegmans.cloud"
CART_VER = "2024-02-19-preview"
BROWSE_VER = "2023-09-22"
SEARCH_FILTER = "storeNumber:{s} AND fulfilmentType:instore AND excludeFromWeb:false AND isSoldAtStore:true"

BROWSER_HEADERS = {
    "accept": "application/json, text/plain, */*",
    "origin": "https://www.wegmans.com",
    "referer": "https://www.wegmans.com/",
    "user-agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/157.0.0.0 Safari/537.36"
    ),
}


@dataclass
class LineItem:
    id: str
    sku: str
    name: str
    quantity: int
    got_it: bool
    aisle: str


@dataclass
class Cart:
    id: str
    version: int
    customer_id: str
    customer_email: str
    items: list[LineItem]

    def by_sku(self, sku: str) -> LineItem | None:
        return next((i for i in self.items if i.sku == sku), None)


def _custom(fields: list[dict], name: str, default=None):
    return next((f.get("value") for f in fields or [] if f.get("name") == name), default)


def parse_cart(data: dict) -> Cart:
    g = data["grocery"]
    items = []
    for li in g.get("lineItems") or []:
        cf = (li.get("custom") or {}).get("customFieldsRaw") or []
        plano = _custom(cf, "planogram") or "{}"
        try:
            p = json.loads(plano)
            aisle = f"{p.get('aisle', '')}-{p.get('aisleSide', '')}-{p.get('section', '')}".strip("-")
        except (TypeError, ValueError):
            aisle = ""
        items.append(
            LineItem(
                id=li["id"],
                sku=str(li["variant"]["sku"]),
                name=li.get("name", ""),
                quantity=int(li.get("quantity") or 1),
                got_it=bool(_custom(cf, "gotIt", False)),
                aisle=aisle,
            )
        )
    return Cart(g["id"], int(g["version"]), g["customerId"], g["customerEmail"], items)


def build_line_item(hit: dict, store_number: str, quantity: int = 1) -> dict:
    """Turn an Algolia product hit into the lineItem the website sends when adding."""
    # The cart's category is the top-level department (e.g. Grocery / Frozen / Dairy).
    dept_name = (hit.get("categoryNodes") or {}).get("lvl0", "")
    cats = hit.get("category") or []
    dept = next((c for c in cats if c.get("name") == dept_name), cats[-1] if cats else {})
    plano = hit.get("planogram") or {}
    price = (hit.get("price_inStore") or {}).get("amount") or 0
    custom = [
        {"name": "category", "value": dept.get("name", dept_name)},
        {"name": "categoryId", "value": str(dept.get("key", ""))},
        {"name": "itemLevelAdjustments", "value": "[]"},
        {"name": "isSoldAtStore", "value": bool(hit.get("isSoldAtStore", True))},
        {"name": "ebtEligible", "value": bool(hit.get("ebtEligible", False))},
        {"name": "isAvailable", "value": bool(hit.get("isAvailable", True))},
        {
            "name": "planogram",
            "value": json.dumps(
                {
                    "aisle": plano.get("aisle", ""),
                    "shelf": plano.get("shelf", ""),
                    "aisleSide": plano.get("aisleSide", ""),
                    "section": plano.get("section", ""),
                },
                separators=(",", ":"),
            ),
        },
        {"name": "note", "value": ""},
        {"name": "bottleDeposit", "value": hit.get("bottleDeposit", 0) or 0},
        {"name": "upc", "value": hit.get("upc") or []},
        {"name": "fulfillmentTypes", "value": hit.get("fulfilmentType") or ["instore"]},
        {"name": "maxQuantity", "value": str(hit.get("maxQuantity", 99))},
    ]
    return {
        "custom": custom,
        "distributionChannelKey": f"{store_number}-Instore",
        "isAlcoholic": bool(hit.get("isAlcoholItem", False)),
        "isSoldByWeight": bool(hit.get("isSoldByWeight", False)),
        "onlineApproxUnitWeight": hit.get("onlineApproxUnitWeight", 0) or 0,
        "onlineSellByUnit": hit.get("onlineSellByUnit", "ea"),
        "quantity": quantity,
        "sku": str(hit.get("skuId") or hit["productID"]),
        "standalonePrice": int(round(float(price) * 100)),
    }


class WegmansClient:
    def __init__(self, settings, token: Callable[[], str], invalidate: Callable[[], None] = lambda: None):
        self.s = settings
        self._token = token
        self._invalidate = invalidate
        self.http = requests.Session()
        self.http.headers.update(BROWSER_HEADERS)

    # ---------- anonymous ----------
    def search(self, query: str, limit: int = 10) -> list[dict]:
        url = f"https://{self.s.algolia_app_id.lower()}-dsn.algolia.net/1/indexes/*/queries"
        body = {
            "requests": [
                {
                    "indexName": "products",
                    "query": query,
                    "hitsPerPage": limit,
                    "analytics": False,
                    "filters": SEARCH_FILTER.format(s=self.s.store_number),
                }
            ]
        }
        r = self.http.post(
            url,
            params={"x-algolia-api-key": self.s.algolia_api_key, "x-algolia-application-id": self.s.algolia_app_id},
            data=json.dumps(body),
            headers={"content-type": "text/plain"},
            timeout=20,
        )
        r.raise_for_status()
        return r.json()["results"][0]["hits"]

    def product(self, sku: str) -> dict | None:
        """Exact product lookup by SKU via Algolia (same index the site uses)."""
        url = f"https://{self.s.algolia_app_id.lower()}-dsn.algolia.net/1/indexes/*/queries"
        body = {
            "requests": [
                {
                    "indexName": "products",
                    "hitsPerPage": 1,
                    "analytics": False,
                    "filters": SEARCH_FILTER.format(s=self.s.store_number) + f" AND productID:{sku}",
                }
            ]
        }
        r = self.http.post(
            url,
            params={"x-algolia-api-key": self.s.algolia_api_key, "x-algolia-application-id": self.s.algolia_app_id},
            data=json.dumps(body),
            headers={"content-type": "text/plain"},
            timeout=20,
        )
        r.raise_for_status()
        hits = r.json()["results"][0]["hits"]
        return hits[0] if hits else None

    # ---------- authenticated ----------
    def _call(self, method: str, path: str, body: Any = None) -> dict:
        for attempt in (1, 2):
            r = self.http.request(
                method,
                f"{API}{path}",
                params={"api-version": CART_VER},
                json=body,
                headers={"authorization": f"Bearer {self._token()}"},
                timeout=30,
            )
            if r.status_code == 401 and attempt == 1:
                self._invalidate()
                continue
            if r.status_code >= 400:
                raise WegmansError(r.status_code, r.text[:500])
            return r.json() if r.content else {}
        raise WegmansError(401, "unauthorized after token refresh")

    def cart(self) -> Cart:
        return parse_cart(self._call("GET", "/commerce/cart/carts/"))

    def _retry_on_conflict(self, fn: Callable[[Cart], dict]) -> Cart:
        cart = self.cart()
        try:
            fn(cart)
        except WegmansError as e:
            if e.status not in (400, 409, 412):
                raise
            log.info("Cart changed underneath us (%s); retrying with fresh version", e.status)
            fn(self.cart())
        return self.cart()

    def add(self, hit: dict, quantity: int = 1) -> Cart:
        def do(cart: Cart):
            body = {
                "StoreKey": self.s.store_key,
                "cartData": [
                    {
                        "cartID": cart.id,
                        "cartVersion": cart.version,
                        "custom": [
                            {"name": "orderLevelAdjustments", "value": "[]"},
                            {"name": "storeNumber", "value": self.s.store_number},
                            {"name": "fulfillmentType", "value": "instore"},
                        ],
                        "isAlcoholic": bool(hit.get("isAlcoholItem", False)),
                        "lineItems": [build_line_item(hit, self.s.store_number, quantity)],
                    }
                ],
                "customerEmail": cart.customer_email,
                "customerID": cart.customer_id,
            }
            return self._call("POST", "/commerce/cart/carts/lineitems", body)

        return self._retry_on_conflict(do)

    def remove(self, sku: str) -> Cart:
        def do(cart: Cart):
            body = {
                "cartData": [
                    {
                        "cartID": cart.id,
                        "cartVersion": cart.version,
                        "custom": [{"name": "orderLevelAdjustments", "value": "[]"}],
                        "isAlcoholic": False,
                        "lineItems": [{"sku": sku}],
                    }
                ]
            }
            return self._call("PUT", "/commerce/cart/carts/itemdeletion", body)

        return self._retry_on_conflict(do)

    def set_got_it(self, line_item_id: str, value: bool) -> Cart:
        def do(cart: Cart):
            body = {
                "cartVersion": cart.version,
                "updateLineItemGotItFieldList": [{"gotItValue": value, "lineItemId": line_item_id}],
            }
            return self._call("PUT", f"/commerce/cart/carts/{cart.id}/lineitems/lineitempurchased", body)

        return self._retry_on_conflict(do)


class WegmansError(RuntimeError):
    def __init__(self, status: int, text: str):
        super().__init__(f"Wegmans API {status}: {text}")
        self.status = status
