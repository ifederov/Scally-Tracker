# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

Scally Tracker is a personal inventory tracker for [bostonscally.com](https://bostonscally.com). It polls the store's product catalog, stores snapshots in a local SQLite database, and serves a web UI for browsing and managing a personal collection. Push notifications are sent via ntfy.sh when items come back in stock, drop in price, or are newly listed.

## Running the App

```bash
# Initial setup (installs deps, configures launchd agents, runs first poll)
bash setup.sh

# Manual poll (runs immediately, no alerts fire on very first run)
python3 poller.py

# Run web server manually (port 5001)
python3 app.py

# Watch logs
tail -f data/poller.log
tail -f data/web.log
```

## launchd Management (macOS)

The app runs as two persistent launchd agents:

```bash
# Reload web server after changes
launchctl unload ~/Library/LaunchAgents/com.scallytracker.web.plist
launchctl load  ~/Library/LaunchAgents/com.scallytracker.web.plist

# Reload poller (schedule changes, etc.)
launchctl unload ~/Library/LaunchAgents/com.scallytracker.poller.plist
launchctl load  ~/Library/LaunchAgents/com.scallytracker.poller.plist
```

Poll schedule: every 30 minutes, 7:00am–7:00pm CT (25 runs/day via `StartCalendarInterval` with explicit hour/minute entries).

## Architecture

Two independent Python processes share a single SQLite database (`data/inventory.db`):

- **`poller.py`** — fetches `bostonscally.com/products.json` (paginated, 250/page), categorizes each product, diffs against the last snapshot, logs alerts, and sends ntfy push notifications. Runs on a launchd schedule.
- **`app.py`** — Flask web server (port 5001) that reads from the DB and exposes a REST API consumed by the single-page frontend.
- **`templates/index.html`** — self-contained SPA (vanilla JS + CSS, no build step). All product browsing, filtering, and collection management happens here via fetch() calls to the Flask API.

## Database Schema

Tables created by `poller.py` (via `init_db`):
- `products` — one row per BSC product; includes `category`, `panels`, extracted fields (`style`, `color`, `material`)
- `variants` — size/color variants with current `price` and `available` flag
- `snapshots` — append-only history of every poll's price+availability per variant
- `alerts` — log of stock changes, price drops, new products

Tables created by `app.py` (via `init_user_items`):
- `user_items` — per-product `owned` and `wishlisted` flags (keyed on `product_id`)
- `manual_caps` — user-added caps not in the BSC catalog

## Categorization Logic (`poller.py`)

Products are categorized as `caps | pins | apparel | other`. Key rules:
- `product_type` field from BSC is checked first (most reliable)
- Title keywords are checked second (longest phrases first to avoid partial matches)
- Body HTML and tags are deliberately **excluded** — too many false positives
- 5-panel and baker boy caps are **skipped entirely** and never stored

## Flask API Endpoints

| Endpoint | Description |
|---|---|
| `GET /api/products` | Filtered product list. Params: `category`, `avail`, `panels`, `search`, `sort`, `owned`, `wishlisted` |
| `GET /api/product/<pid>` | Product detail with variants and price/availability history (last 5 changes) |
| `POST /api/user_item/<pid>` | Upsert owned/wishlisted flags |
| `GET /api/manual_caps` | List manually added caps |
| `POST /api/manual_cap` | Add a manual cap |
| `DELETE /api/manual_cap/<mid>` | Delete a manual cap |
| `GET /api/stats` | Dashboard counts (total, by category, in/out of stock, owned, wishlisted) |
| `GET /api/alerts` | Recent alerts log |

## Notifications

ntfy topic is configured in `poller.py` as `NTFY_TOPIC`. Wishlisted items trigger `urgent` priority notifications on back-in-stock and price drops; non-wishlisted items get `high`/`default` priority.
