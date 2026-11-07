"""Client for a self-hosted Norish instance's /api/v1 (API key auth).

Field names come from Norish's own source (packages/trpc/src/routers/groceries):
groceries carry id, name, amount, unit, isDone, storeId and an integer `version`
that must be echoed on every change. A stale version returns {"stale": true}.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import requests

log = logging.getLogger(__name__)


@dataclass
class Grocery:
    id: str
    name: str
    amount: float | None
    unit: str | None
    is_done: bool
    store_id: str | None
    version: int


class NorishClient:
    def __init__(self, base_url: str, api_key: str):
        if not base_url or not api_key:
            raise SystemExit("Set NORISH_URL and NORISH_API_KEY.")
        self.base = f"{base_url}/api/v1"
        self.http = requests.Session()
        self.http.headers.update({"x-api-key": api_key, "accept": "application/json"})

    def _req(self, method: str, path: str, json=None):
        r = self.http.request(method, f"{self.base}{path}", json=json, timeout=20)
        r.raise_for_status()
        return r.json() if r.content else None

    def stores(self) -> list[dict]:
        return self._req("GET", "/stores")

    def store_id(self, name: str) -> str:
        for s in self.stores():
            if s.get("name", "").strip().lower() == name.strip().lower():
                return s["id"]
        raise SystemExit(f"No Norish store named {name!r}. Create it in Norish (Groceries → Stores) first.")

    def groceries(self) -> list[Grocery]:
        out = []
        for g in self._req("GET", "/groceries"):
            out.append(
                Grocery(
                    id=g["id"],
                    name=(g.get("name") or "").strip(),
                    amount=g.get("amount"),
                    unit=g.get("unit"),
                    is_done=bool(g.get("isDone")),
                    store_id=g.get("storeId"),
                    version=int(g.get("version") or 1),
                )
            )
        return out

    def set_done(self, g: Grocery, done: bool) -> bool:
        """Returns False if Norish reported the version as stale (caller retries next cycle)."""
        res = self._req("PATCH", f"/groceries/{g.id}/{'done' if done else 'undone'}", {"version": g.version})
        return not (res or {}).get("stale", False)
