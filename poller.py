#!/usr/bin/env python3
"""
Scally Tracker — BSC-only inventory poller
Polls bostonscally.com every 30 min, 7am–7pm CT via launchd
Categories: caps | pins | apparel | other
Skips: 5-panel and baker boy caps
"""

import sqlite3, requests, os, re, logging
from html.parser import HTMLParser
from datetime import datetime
from zoneinfo import ZoneInfo

# ── Config ────────────────────────────────────────────────────────
DB_PATH     = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "inventory.db")
NTFY_TOPIC  = "sterling-scally-tracker"
NTFY_SERVER = "https://ntfy.sh"
CT          = ZoneInfo("America/Chicago")

# ── Category rules ────────────────────────────────────────────────
# Rule: check TITLE ONLY first (most reliable), then product_type.
# Never use body_html or tags for category — too many false positives.
#
# CAP title keywords — ordered longest-first to avoid partial matches
CAP_TITLE_KW = [
    "flat cap", "ivy cap", "driving cap", "baker boy", "baker-boy",
    "newsboy cap", "newsboy", "flat-cap", "scally cap",
    "cabbie cap", "gatsby cap",
    "scally", "cap", "hat",
]

# PIN detection — ONLY match whole word "pin" or "pins" in the title
PIN_TITLE_RE = re.compile(r'\bpins?\b', re.I)

# APPAREL title keywords
APPAREL_TITLE_KW = [
    "t-shirt", "tee shirt", "sweatshirt", "crewneck",
    "hoodie", "jacket", "sweater", "polo shirt",
    "shirt", "tee", "shorts", "pants", "socks",
]

# product_type values BSC uses (authoritative when present)
CAP_PTYPE    = {"cap", "caps", "hat", "hats", "flat cap", "scally cap"}
PIN_PTYPE    = {"pin", "pins", "enamel pin", "lapel pin", "cap pin"}
APPAREL_PTYPE= {"shirt", "t-shirt", "tee", "hoodie", "sweatshirt", "t-shirts",
                "jacket", "sweater", "apparel", "clothing", "bottoms",
                "apparel & accessories", "ball caps and beanies", "slides"}
OTHER_PTYPE  = {"gift card", "stickers", "sticker", "drinkware", "bags", "cap rack"}

# ── Skip rules ────────────────────────────────────────────────────
SKIP_PANELS = set()   # All panel types are now tracked

# ── Panel detection ───────────────────────────────────────────────
PANEL_RE = [
    (re.compile(r'\bbaker[- ]?boy\b',    re.I), "baker"),  # must come before 8-panel
    (re.compile(r'\b5[- ]panel\b',       re.I), "5"),      # must come before single-panel
    (re.compile(r'\bsingle[- ]panel\b', re.I), "single"),
    (re.compile(r'\b6[- ]panel\b',       re.I), "6"),
    (re.compile(r'\b8[- ]panel\b',       re.I), "8"),
]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "poller.log")
        ),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)


# ── HTML → plain text ─────────────────────────────────────────────
class _Strip(HTMLParser):
    def __init__(self):
        super().__init__()
        self._parts = []
    def handle_data(self, d):
        self._parts.append(d)
    @property
    def text(self):
        return re.sub(r'\s+', ' ', ' '.join(self._parts)).strip()

def html_to_text(raw):
    if not raw: return ""
    p = _Strip(); p.feed(raw); return p.text

def extract_field(text, label):
    m = re.search(rf'(?:^|[\r\n\s]){re.escape(label)}\s*[:\-]\s*([^\r\n]+)', text, re.I)
    return m.group(1).strip(" .") if m else ""

def detect_panel(text):
    for rx, val in PANEL_RE:
        if rx.search(text): return val
    return ""

def detect_panel_title_first(title, full_text):
    """Check title alone first; fall back to full text only if title has no match."""
    return detect_panel(title) or detect_panel(full_text)


# ── Categorize ────────────────────────────────────────────────────
def categorize(title: str, ptype: str) -> str:
    """
    Determine category from title and product_type ONLY.
    Body text / tags deliberately excluded — they cause too many false positives.
    
    Priority: product_type (when it's a clear signal) > title keywords.
    """
    title_l = title.lower().strip()
    ptype_l = ptype.lower().strip()

    # ── product_type is definitive when BSC fills it in ──────────
    if ptype_l in OTHER_PTYPE:
        return "other"
    if ptype_l in PIN_PTYPE:
        return "pins"
    if ptype_l in CAP_PTYPE:
        return "caps"
    if ptype_l in APPAREL_PTYPE:
        return "apparel"
    # Partial match for multi-word product types
    for k in OTHER_PTYPE:
        if k in ptype_l: return "other"
    for k in PIN_PTYPE:
        if k in ptype_l: return "pins"
    if "cap pin" in ptype_l: return "pins"
    for k in CAP_PTYPE:
        if k in ptype_l: return "caps"
    for k in APPAREL_PTYPE:
        if k in ptype_l: return "apparel"

    # ── Title: check pins first (regex, whole-word) ───────────────
    # Pins are checked before caps because "enamel pin cap" would be a pin.
    if PIN_TITLE_RE.search(title_l):
        return "pins"

    # ── Title: caps (longest phrases first to avoid partial overlap) ─
    for kw in CAP_TITLE_KW:
        if kw in title_l:
            return "caps"

    # ── Title: apparel ────────────────────────────────────────────
    for kw in APPAREL_TITLE_KW:
        if kw in title_l:
            return "apparel"

    return "other"


# ── Database ──────────────────────────────────────────────────────
def get_db():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c

def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with get_db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS products (
                id           INTEGER PRIMARY KEY,
                title        TEXT NOT NULL,
                handle       TEXT NOT NULL,
                product_type TEXT,
                category     TEXT,
                image_url    TEXT,
                vendor       TEXT,
                tags         TEXT,
                description  TEXT,
                style        TEXT,
                color        TEXT,
                material     TEXT,
                panels       TEXT,
                first_seen   TEXT,
                updated_at   TEXT
            );
            CREATE TABLE IF NOT EXISTS variants (
                id         INTEGER PRIMARY KEY,
                product_id INTEGER NOT NULL,
                title      TEXT NOT NULL,
                price      REAL NOT NULL,
                available  INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT
            );
            CREATE TABLE IF NOT EXISTS snapshots (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                variant_id INTEGER NOT NULL,
                product_id INTEGER NOT NULL,
                price      REAL NOT NULL,
                available  INTEGER NOT NULL,
                checked_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS alerts (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                product_id INTEGER,
                variant_id INTEGER,
                alert_type TEXT NOT NULL,
                message    TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_snap ON snapshots(variant_id, checked_at);
            CREATE INDEX IF NOT EXISTS idx_prod_cat ON products(category);
            CREATE INDEX IF NOT EXISTS idx_prod_pan ON products(panels);
        """)
        try:
            conn.execute("ALTER TABLE products ADD COLUMN images_json TEXT")
        except Exception:
            pass
        try:
            conn.execute("ALTER TABLE products ADD COLUMN panels_override TEXT")
        except Exception:
            pass
    log.info("DB ready: %s", DB_PATH)


# ── Notifications ─────────────────────────────────────────────────
def ntfy(title, body, priority="default", tags="", click=""):
    try:
        headers = {
            "Title":        title.encode('utf-8'),
            "Priority":     priority,
            "Tags":         tags,
            "Content-Type": "text/plain; charset=utf-8",
        }
        if click:
            headers["Click"] = click
        r = requests.post(
            f"{NTFY_SERVER}/{NTFY_TOPIC}",
            data=body.encode('utf-8'),
            headers=headers,
            timeout=10,
        )
        log.info("ntfy %d: %s", r.status_code, title)
    except Exception as e:
        log.error("ntfy error: %s", e)

def log_alert(conn, pid, vid, atype, msg):
    conn.execute(
        "INSERT INTO alerts (product_id,variant_id,alert_type,message,created_at) VALUES (?,?,?,?,?)",
        (pid, vid, atype, msg, datetime.now(CT).isoformat()),
    )


# ── Fetch ─────────────────────────────────────────────────────────
def fetch_bsc():
    products, page = [], 1
    while True:
        url = f"https://bostonscally.com/products.json?limit=250&page={page}"
        try:
            r = requests.get(url, timeout=20, headers={"User-Agent": "ScallyTracker/4.0"})
            r.raise_for_status()
            batch = r.json().get("products", [])
            if not batch: break
            products.extend(batch)
            page += 1
        except Exception as e:
            log.error("Fetch error page %d: %s", page, e)
            break
    log.info("Fetched %d products from BSC", len(products))
    return products


# ── Process ───────────────────────────────────────────────────────
# Sizes that trigger push notifications by category/panels.
# XL  = "X-Large" or standalone "XL" (not XX-Large/XXL)
# XXL = "XX-Large" or standalone "XXL"/"2XL" (not XXX-Large/XXXL)
_RE_XXXL = re.compile(r'\b(XXX-?Large|XXXL|3XL)\b', re.IGNORECASE)
_RE_XXL  = re.compile(r'\b(XX-?Large|XXL|2XL)\b',   re.IGNORECASE)
_RE_XL   = re.compile(r'\b(X-?Large|XL)\b',          re.IGNORECASE)

def is_notify_size(vtit, category, panels):
    """Return True if this variant restock should fire a push notification."""
    if category == 'pins':
        return True
    if _RE_XXXL.search(vtit):
        return False  # never notify for XXXL
    if category == 'caps':
        if panels == 'single':
            return bool(_RE_XXL.search(vtit))   # single-panel: notify XXL
        else:
            return bool(_RE_XL.search(vtit)) and not _RE_XXL.search(vtit)  # other caps: XL only
    if category == 'apparel':
        return bool(_RE_XL.search(vtit)) and not _RE_XXL.search(vtit)  # apparel: XL only
    return False


def get_wishlist_info(product_id):
    """Returns (is_wishlisted, preferred_size). preferred_size is None if not set."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT preferred_size FROM user_items WHERE product_id=? AND wishlisted=1",
            (product_id,)
        ).fetchone()
        if row is None:
            return False, None
        return True, row["preferred_size"]


def process(conn, product, known_ids):
    now    = datetime.now(CT).isoformat()
    pid    = product["id"]
    title  = product["title"]
    handle = product["handle"]
    ptype  = product.get("product_type", "") or ""
    vendor = product.get("vendor", "") or ""
    tags   = product.get("tags", [])
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]
    tags_str = ", ".join(tags)

    body_text   = html_to_text(product.get("body_html", "") or "")
    description = body_text[:600].strip()
    style       = extract_field(body_text, "Style")
    color       = extract_field(body_text, "Color")
    material    = extract_field(body_text, "Material")
    panels      = detect_panel_title_first(title, f"{title} {style} {body_text[:300]}")

    # Skip 5-panel and baker boy
    if panels in SKIP_PANELS:
        log.debug("SKIP (panels=%s): %s", panels, title)
        return

    # Categorize using title + product_type only
    category    = categorize(title, ptype)
    images      = product.get("images") or []
    image_url   = images[0].get("src", "") if images else ""
    images_json = __import__('json').dumps([img.get("src","") for img in images if img.get("src")])
    is_new      = pid not in known_ids

    # Respect panels_override if set — never overwrite a manual correction
    override_row = conn.execute(
        "SELECT panels_override FROM products WHERE id=?", (pid,)
    ).fetchone()
    effective_panels = (override_row["panels_override"] if override_row and override_row["panels_override"] else panels)

    conn.execute("""
        INSERT INTO products
            (id,title,handle,product_type,category,image_url,images_json,vendor,
             tags,description,style,color,material,panels,first_seen,updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(id) DO UPDATE SET
            title=excluded.title, handle=excluded.handle,
            product_type=excluded.product_type, category=excluded.category,
            image_url=excluded.image_url, images_json=excluded.images_json,
            vendor=excluded.vendor,
            tags=excluded.tags, description=excluded.description,
            style=excluded.style, color=excluded.color,
            material=excluded.material,
            panels=COALESCE((SELECT panels_override FROM products WHERE id=excluded.id), excluded.panels),
            updated_at=excluded.updated_at
    """, (pid, title, handle, ptype, category, image_url, images_json, vendor,
          tags_str, description, style, color, material, effective_panels, now, now))

    if is_new:
        vs        = product.get("variants", [])
        prices    = [float(v.get("price", 0)) for v in vs]
        avail_cnt = sum(1 for v in vs if v.get("available"))
        lo = hi   = 0.0
        if prices: lo, hi = min(prices), max(prices)
        price_str = f"${lo:.2f}" if lo == hi else f"${lo:.2f}–${hi:.2f}"
        emoji     = {"caps": "🧢", "apparel": "👕", "pins": "📌"}.get(category, "🛍")
        log_alert(conn, pid, None, "new_product", f"NEW: {title} ({price_str})")
        log.info("NEW [%s]: %s", category, title)
        ntfy("🆕 New on BSC!",
             f"{emoji} {title}\n{price_str} · {avail_cnt} variant(s) available",
             priority="urgent", tags="new,tada",
             click=f"https://bostonscally.com/products/{handle}")

        # Auto-add a release entry for new caps
        if category == "caps":
            _now = datetime.now(CT)
            rel_date = f"{_now.month}/{_now.day}/{str(_now.year)[2:]}"
            # Strip "Boston Scally Cap" from the release name
            rel_name = re.sub(r'\s*Boston Scally Cap\b', '', title, flags=re.IGNORECASE).strip()
            rel_name = re.sub(r'\s{2,}', ' ', rel_name).strip()
            try:
                conn.execute(
                    "INSERT OR IGNORE INTO releases (name, release_date, is_limited, notes) "
                    "VALUES (?, ?, 0, 'Auto-added from new product alert')",
                    (rel_name, rel_date)
                )
                log.info("RELEASE auto-added: %s on %s", rel_name, rel_date)
            except Exception as e:
                log.warning("Could not auto-add release for %s: %s", title, e)

    for v in product.get("variants", []):
        vid   = v["id"]
        price = float(v.get("price", 0))
        avail = 1 if v.get("available") else 0
        vtit  = v.get("title", "Default")

        conn.execute("""
            INSERT INTO variants (id,product_id,title,price,available,updated_at)
            VALUES (?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
                price=excluded.price, available=excluded.available,
                updated_at=excluded.updated_at
        """, (vid, pid, vtit, price, avail, now))

        prev = conn.execute(
            "SELECT price,available FROM snapshots WHERE variant_id=? ORDER BY checked_at DESC LIMIT 1",
            (vid,)
        ).fetchone()

        if prev and not is_new:
            oa, op = prev["available"], prev["price"]
            if oa == 0 and avail == 1:
                log_alert(conn, pid, vid, "back_in_stock", f"RESTOCKED: {title} — {vtit}")
                if is_notify_size(vtit, category, effective_panels):
                    url = f"https://bostonscally.com/products/{handle}"
                    emoji = {"caps": "🧢", "apparel": "👕", "pins": "📌"}.get(category, "🛍")
                    wl, ps = get_wishlist_info(pid)
                    if wl and ps and vtit == ps:
                        ntfy("⭐ Your Size is Back!",
                             f"{emoji} {title}\n{vtit}",
                             priority="urgent", tags="star,tada,shopping", click=url)
                    elif wl and ps:
                        ntfy("⭐ Wishlisted Restock",
                             f"{emoji} {title}\n{vtit} (not your size)",
                             priority="high", tags="star,tada,shopping", click=url)
                    elif wl:
                        ntfy("⭐ Wishlisted Restock!",
                             f"{emoji} {title}\n{vtit}",
                             priority="urgent", tags="star,tada,shopping", click=url)
                    else:
                        ntfy("🎉 Restock!",
                             f"{emoji} {title}\n{vtit}",
                             priority="high", tags="tada,shopping", click=url)
            elif oa == 1 and avail == 0:
                log_alert(conn, pid, vid, "out_of_stock", f"OUT OF STOCK: {title} — {vtit}")
            elif price < op:
                log_alert(conn, pid, vid, "price_drop",
                          f"PRICE DROP: {title} — {vtit}: ${op:.2f}→${price:.2f}")
                url = f"https://bostonscally.com/products/{handle}"
                if is_wishlisted(pid):
                    ntfy("⭐ Wishlisted Price Drop!",
                         f"{title} — {vtit}\n${op:.2f} → ${price:.2f}",
                         priority="urgent", tags="star,chart_with_downwards_trend", click=url)
                else:
                    ntfy("📉 Price Drop!",
                         f"{title} — {vtit}\n${op:.2f} → ${price:.2f}",
                         tags="chart_with_downwards_trend", click=url)
            elif price > op:
                log_alert(conn, pid, vid, "price_increase",
                          f"PRICE UP: {title} — {vtit}: ${op:.2f}→${price:.2f}")

        conn.execute(
            "INSERT INTO snapshots (variant_id,product_id,price,available,checked_at) VALUES (?,?,?,?,?)",
            (vid, pid, price, avail, now),
        )


# ── Main ──────────────────────────────────────────────────────────
def run_poll():
    log.info("══ Scally Tracker — %s ══", datetime.now(CT).strftime("%Y-%m-%d %I:%M %p CT"))
    init_db()
    with get_db() as conn:
        known = {r[0] for r in conn.execute("SELECT id FROM products")}
        first_run = len(known) == 0

        products = fetch_bsc()
        if not products:
            log.warning("No products returned — aborting")
            return

        if first_run:
            log.info("First run — building baseline, no alerts will fire")
            known = {p["id"] for p in products}

        for p in products:
            process(conn, p, known)

        # Purge alerts older than 30 days
        deleted = conn.execute(
            "DELETE FROM alerts WHERE created_at < datetime('now', '-30 days')"
        ).rowcount
        if deleted:
            log.info("Purged %d alert(s) older than 30 days", deleted)

        # Summary
        counts = {r[0]: r[1] for r in conn.execute(
            "SELECT category, COUNT(*) FROM products GROUP BY category"
        )}
        log.info("Categories: %s", counts)

    log.info("══ Poll complete ══\n")


if __name__ == "__main__":
    run_poll()
