# wegmans-bridge

Two-way sync between your **Norish** grocery list and your **Wegmans in-store list**
(the "My List" in the Wegmans app, which is really the cart in in-store mode).

```
Norish groceries (store = "Wegmans")  ──add──►  Wegmans My List (sorted by aisle)
              ▲                                        │
              └──────── done ◄──── Got it ─────────────┘   (works both ways)
```

| You do this | Bridge does this |
|---|---|
| Add a grocery in Norish and assign it to the **Wegmans** store | Finds the product and adds it to the Wegmans list |
| Tick **Got it** in the Wegmans app at the store | Marks it done in Norish |
| Mark it done in Norish | Ticks it off in Wegmans |
| Delete it / clear done items in Norish | Removes it from the Wegmans list |
| Delete it in the Wegmans app | Leaves it alone and doesn't re-add it |

Uses unofficial Wegmans APIs (the ones wegmans.com itself calls). They can change
without notice, and automating your account may conflict with Wegmans' terms.
It's for personal use only.

## 1. Norish setup
1. In Norish, create a store named **Wegmans** (or set `NORISH_STORE_NAME`).
2. Create an API key in your Norish account settings.
3. Assign groceries to the Wegmans store. Norish remembers the store per ingredient, so
   after a week or two most items land there automatically.

## 2. Sign in to Wegmans once
Logging in needs a visible browser, so do it on your Mac, then copy the saved session to the server:

```bash
cd wegmans-bridge
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt && playwright install chromium
DATA_DIR=./data python -m wegmans_bridge login     # sign in, tick "keep me signed in"
rsync -a data/ server:/opt/wegmans-bridge/data/   # copies data/browser-profile
```

The bridge reuses that session to get a fresh one-hour token whenever it needs one.
When the session finally expires, it logs a warning (and pings `HA_WEBHOOK_URL` if set),
and you rerun `login`.

## 3. Run it (Docker on Proxmox)
```bash
cp .env.example .env    # fill in NORISH_URL / NORISH_API_KEY
docker compose run --rm wegmans-bridge sync --dry-run   # see what it would do
docker compose up -d
docker compose logs -f
```

## Product matching
The first time it sees a name (say "milk"), the bridge takes the top search result at
your store and remembers it. Check and correct what it picked:

```bash
docker compose run --rm wegmans-bridge mappings          # '?' = auto-picked
docker compose run --rm wegmans-bridge search whole milk
docker compose run --rm wegmans-bridge map "milk" 58871  # pin "milk" to that product
docker compose run --rm wegmans-bridge cart              # your list, by aisle
```

Search works without logging in. Pinned matches are always used ahead of search results.

## Quantities
A plain count ("3 lemons", with no unit) becomes quantity 3. Recipe amounts like
"2 cups flour" become one item.

## Home Assistant
Optional: create an automation with a **Webhook** trigger and put its URL in
`HA_WEBHOOK_URL` to get a phone notification when the Wegmans sign-in needs redoing.
For voice/dashboard adds, use Norish's API (`POST /api/v1/groceries` with
`storeId` = your Wegmans store id) from a `rest_command`.

## How it talks to Wegmans
| Action | Request |
|---|---|
| Search | Algolia `products` index (public site key) |
| Read list | `GET /commerce/cart/carts/` |
| Add | `POST /commerce/cart/carts/lineitems` |
| Remove | `PUT /commerce/cart/carts/itemdeletion` |
| Got it | `PUT /commerce/cart/carts/{id}/lineitems/lineitempurchased` |

All on `api.digitaldevelopment.wegmans.cloud?api-version=2024-02-19-preview`. Every change
carries `cartVersion`; on a conflict the bridge re-reads the list and retries once.

## Tests
```bash
pip install pytest requests && python -m pytest -q tests
```
