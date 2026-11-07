"""One sync pass between Norish (master list) and the Wegmans in-store list.

Rules
- An open Norish grocery assigned to the Wegmans store gets added to the Wegmans list
  (matched to a product via your saved mappings, else the top search hit).
- Done/got-it is two-way: whichever side changed since the last pass wins.
- Deleting a grocery in Norish (or clearing done items) removes it from the Wegmans list.
- Removing an item in the Wegmans app is respected: it won't be re-added, and Norish
  is left alone.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from .store import Link, Store

log = logging.getLogger(__name__)


@dataclass
class Report:
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    to_wegmans: list[str] = field(default_factory=list)
    to_norish: list[str] = field(default_factory=list)
    unmatched: list[str] = field(default_factory=list)

    def changed(self) -> bool:
        return any([self.added, self.removed, self.to_wegmans, self.to_norish, self.unmatched])

    def __str__(self) -> str:
        parts = [
            f"{label}: {', '.join(v)}"
            for label, v in [
                ("added to Wegmans", self.added),
                ("removed from Wegmans", self.removed),
                ("checked off in Wegmans", self.to_wegmans),
                ("checked off in Norish", self.to_norish),
                ("no Wegmans match", self.unmatched),
            ]
            if v
        ]
        return "; ".join(parts) or "no changes"


def quantity_for(amount, unit) -> int:
    """Only count-style amounts ('3 lemons') become a quantity; '2 cups flour' is one item."""
    if not unit and amount and float(amount).is_integer() and 1 <= amount <= 20:
        return int(amount)
    return 1


def resolve(name: str, store: Store, wegmans) -> dict | None:
    hit_map = store.lookup(name)
    if hit_map:
        hit = wegmans.product(hit_map[0])
        if hit:
            return hit
        log.warning("Saved product %s for %r no longer found; searching again", hit_map[0], name)
    hits = wegmans.search(name, limit=5)
    if not hits:
        return None
    top = hits[0]
    store.remember(name, str(top.get("skuId") or top["productID"]), top.get("productName", ""), confirmed=False)
    return top


def sync_once(norish, wegmans, store: Store, store_id: str, dry_run: bool = False) -> Report:
    rep = Report()
    groceries = [g for g in norish.groceries() if g.store_id == store_id and g.name]
    cart = wegmans.cart()
    links = store.links()
    seen = set()

    for g in groceries:
        seen.add(g.id)
        link = links.get(g.id)

        # ---- new grocery ----
        if link is None:
            if g.is_done:
                continue
            hit = resolve(g.name, store, wegmans)
            if hit is None:
                rep.unmatched.append(g.name)
                continue
            sku = str(hit.get("skuId") or hit["productID"])
            if cart.by_sku(sku) is None:
                if not dry_run:
                    cart = wegmans.add(hit, quantity_for(g.amount, g.unit))
                rep.added.append(f"{g.name} → {hit.get('productName', sku)}")
            if not dry_run:
                store.save_link(Link(g.id, sku, last_done=False, removed_remote=False))
            continue

        if link.removed_remote:
            continue

        line = cart.by_sku(link.sku)
        if line is None:
            # You deleted it in the Wegmans app (or the list was cleared). Respect that.
            log.info("%r is gone from the Wegmans list; not re-adding", g.name)
            if not dry_run:
                link.removed_remote = True
                store.save_link(link)
            continue

        n, w, last = g.is_done, line.got_it, link.last_done
        if n == w:
            new = n
        elif n != last:  # changed in Norish
            if not dry_run:
                cart = wegmans.set_got_it(line.id, n)
            rep.to_wegmans.append(f"{g.name} ({'got it' if n else 'get it'})")
            new = n
        else:  # changed in Wegmans
            if not dry_run and not norish.set_done(g, w):
                log.info("Norish version for %r was stale; will retry next pass", g.name)
                continue
            rep.to_norish.append(f"{g.name} ({'done' if w else 'not done'})")
            new = w
        if new != link.last_done and not dry_run:
            link.last_done = new
            store.save_link(link)

    # ---- groceries deleted (or moved off the Wegmans store) in Norish ----
    still_used = {l.sku for gid, l in links.items() if gid in seen}
    for gid, link in links.items():
        if gid in seen:
            continue
        if not link.removed_remote and link.sku not in still_used and cart.by_sku(link.sku):
            name = cart.by_sku(link.sku).name
            if not dry_run:
                cart = wegmans.remove(link.sku)
            rep.removed.append(name)
        if not dry_run:
            store.drop_link(gid)

    return rep
