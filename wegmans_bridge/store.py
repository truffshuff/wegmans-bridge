"""Local state: learned name→product matches and links between Norish rows and cart lines."""
from __future__ import annotations

import os
import re
import sqlite3
from dataclasses import dataclass

SCHEMA = """
CREATE TABLE IF NOT EXISTS product_map (
    name_key   TEXT PRIMARY KEY,      -- normalised grocery name, e.g. "whole milk"
    sku        TEXT NOT NULL,
    product    TEXT NOT NULL,
    confirmed  INTEGER NOT NULL DEFAULT 0   -- 1 = set by you, 0 = auto-picked top search hit
);
CREATE TABLE IF NOT EXISTS link (
    grocery_id     TEXT PRIMARY KEY,
    sku            TEXT NOT NULL,
    last_done      INTEGER NOT NULL,  -- agreed done/got-it state after the last sync
    removed_remote INTEGER NOT NULL DEFAULT 0  -- you deleted it from the Wegmans list; don't re-add
);
"""


def name_key(name: str) -> str:
    n = name.lower()
    n = re.sub(r"\(.*?\)", " ", n)          # drop "(optional)" etc.
    n = re.sub(r"[^a-z0-9 ]+", " ", n)
    return re.sub(r"\s+", " ", n).strip()


@dataclass
class Link:
    grocery_id: str
    sku: str
    last_done: bool
    removed_remote: bool


class Store:
    def __init__(self, path: str):
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.executescript(SCHEMA)

    # product map
    def lookup(self, name: str) -> tuple[str, str, bool] | None:
        row = self.db.execute(
            "SELECT sku, product, confirmed FROM product_map WHERE name_key=?", (name_key(name),)
        ).fetchone()
        return (row[0], row[1], bool(row[2])) if row else None

    def remember(self, name: str, sku: str, product: str, confirmed: bool) -> None:
        self.db.execute(
            "INSERT INTO product_map(name_key, sku, product, confirmed) VALUES(?,?,?,?) "
            "ON CONFLICT(name_key) DO UPDATE SET sku=excluded.sku, product=excluded.product, "
            "confirmed=excluded.confirmed",
            (name_key(name), sku, product, int(confirmed)),
        )
        self.db.commit()

    def mappings(self) -> list[tuple]:
        return self.db.execute("SELECT name_key, sku, product, confirmed FROM product_map ORDER BY name_key").fetchall()

    # links
    def links(self) -> dict[str, Link]:
        rows = self.db.execute("SELECT grocery_id, sku, last_done, removed_remote FROM link").fetchall()
        return {r[0]: Link(r[0], r[1], bool(r[2]), bool(r[3])) for r in rows}

    def save_link(self, l: Link) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO link VALUES(?,?,?,?)",
            (l.grocery_id, l.sku, int(l.last_done), int(l.removed_remote)),
        )
        self.db.commit()

    def drop_link(self, grocery_id: str) -> None:
        self.db.execute("DELETE FROM link WHERE grocery_id=?", (grocery_id,))
        self.db.commit()
