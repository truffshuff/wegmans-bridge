"""Command line: python -m wegmans_bridge <command>

  login                 Sign in to Wegmans once in a visible browser (saves the session)
  search <words>        Search your store's products (no login needed)
  map "<name>" <sku>    Pin a grocery name to a specific Wegmans product
  mappings              Show saved name → product matches (auto picks marked '?')
  cart                  Show your Wegmans list
  sync [--dry-run]      Run one sync pass
  run                   Sync forever every SYNC_INTERVAL seconds
"""
from __future__ import annotations

import logging
import sys
import time

import requests

from .config import Settings
from .norish import NorishClient
from .store import Store
from .sync import sync_once
from .token import BrowserTokenProvider, LoginRequired, interactive_login
from .wegmans import WegmansClient

log = logging.getLogger("wegmans_bridge")


def notify(settings: Settings, message: str) -> None:
    log.warning(message)
    if settings.ha_webhook_url:
        try:
            requests.post(settings.ha_webhook_url, json={"message": message}, timeout=10)
        except requests.RequestException as e:
            log.error("Home Assistant webhook failed: %s", e)


def main(argv: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    s = Settings()
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    cmd, args = argv[0], argv[1:]

    if cmd == "login":
        interactive_login(s.profile_dir)
        return 0

    tokens = BrowserTokenProvider(s.profile_dir)
    weg = WegmansClient(s, tokens.get, tokens.invalidate)
    store = Store(s.db_path)

    if cmd == "search":
        for h in weg.search(" ".join(args), limit=15):
            p = h.get("planogram") or {}
            price = (h.get("price_inStore") or {}).get("amount")
            print(f"{h['productID']:>8}  ${price:<6}  {p.get('aisle', ''):<8}  {h.get('productName')}  ({h.get('packSize', '')})")
        return 0

    if cmd == "map":
        if len(args) != 2:
            print('usage: map "<grocery name>" <sku>')
            return 2
        hit = weg.product(args[1])
        if not hit:
            print(f"No product {args[1]} at store {s.store_number}")
            return 1
        store.remember(args[0], args[1], hit.get("productName", ""), confirmed=True)
        print(f"{args[0]!r} → {hit.get('productName')}")
        return 0

    if cmd == "mappings":
        for key, sku, product, confirmed in store.mappings():
            print(f"{' ' if confirmed else '?'} {key:<30} {sku:>8}  {product}")
        return 0

    if cmd == "cart":
        c = weg.cart()
        for i in sorted(c.items, key=lambda i: i.aisle):
            print(f"[{'x' if i.got_it else ' '}] {i.aisle:<14} {i.quantity} × {i.name}  (sku {i.sku})")
        return 0

    if cmd in ("sync", "run"):
        nor = NorishClient(s.norish_url, s.norish_api_key)
        store_id = nor.store_id(s.norish_store_name)
        dry = s.dry_run or "--dry-run" in args
        login_alerted = False
        while True:
            try:
                rep = sync_once(nor, weg, store, store_id, dry_run=dry)
                if rep.changed() or cmd == "sync":
                    log.info("%s%s", "[dry run] " if dry else "", rep)
                login_alerted = False
            except LoginRequired as e:
                if not login_alerted:
                    notify(s, f"Wegmans bridge: {e}")
                    login_alerted = True
            except Exception:
                log.exception("Sync pass failed")
            if cmd == "sync":
                return 0
            time.sleep(s.interval_seconds)

    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
