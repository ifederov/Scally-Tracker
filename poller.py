#!/usr/bin/env python3
"""
Scally Tracker — BSC-only inventory poller
Polls bostonscally.com every 30 min, 7am–7pm CT via launchd
Categories: caps | pins | apparel | other
Skips: 5-panel and baker boy caps
"""

import requests, os, re, logging
from html.parser import HTMLParser
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from flask import Flask
from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert as pg_insert

from config import Config
from models import db, Product, Variant, Snapshot, Alert, Release, UserItem, User

# ── Config ────────────────────────────────────────────────────────
NTFY_TOPIC  = Config.NTFY_TOPIC or "sterling-scally-tracker"
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
    (re.compile(r'\bbaker[- ]?boy\b',          re.I), "baker"),  # must come before 8-panel
    (re.compile(r'\b(5|five)[- ]panel\b',      re.I), "5"),      # must come before single-panel
    (re.compile(r'\bsingle[- ]panel\b',        re.I), "single"),
    (re.compile(r'\b(6|six)[- ]panel\b',       re.I), "6"),
    (re.compile(r'\b(8|eight)[- ]panel\b',     re.I), "8"),
    (re.compile(r'\btrucker\b',                re.I), "6"),      # trucker caps are 6-panel
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


# ── Flask app (for SQLAlchemy app context only — not run as a server) ─────
def create_app():
    flask_app = Flask(__name__)
    flask_app.config["SQLALCHEMY_DATABASE_URI"] = Config.DATABASE_URL
    flask_app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    db.init_app(flask_app)
    return flask_app


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


# ── Notifications ─────────────────────────────────────────────────
def ntfy(title, body, priority="default", tags="", click="", topic=None):
    topic = topic or NTFY_TOPIC
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
            f"{NTFY_SERVER}/{topic}",
            data=body.encode('utf-8'),
            headers=headers,
            timeout=10,
        )
        log.info("ntfy %d (%s): %s", r.status_code, topic, title)
    except Exception as e:
        log.error("ntfy error (%s): %s", topic, e)

def in_quiet_hours(start, end, now_hour):
    """True if now_hour falls within the [start, end) quiet-hours window (wraps past midnight)."""
    if start is None or end is None:
        return False
    if start == end:
        return False  # zero-length window = disabled
    if start < end:
        return start <= now_hour < end
    return now_hour >= start or now_hour < end  # wraps midnight


def log_alert(pid, vid, atype, msg):
    db.session.add(Alert(
        product_id=pid, variant_id=vid, alert_type=atype, message=msg,
        created_at=datetime.now(CT).isoformat(),
    ))


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


def process(product, known_ids, latest_snaps, panels_overrides, wishlist, user_wishlist=None, new_product_topics=None):
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

    # BSC sometimes tags a product "unavailable" (pulling it off active sale)
    # without zeroing its Shopify inventory count, so the feed's own
    # `available` flag stays true even though the storefront shows Sold Out.
    # Treat the tag as an override so we don't report false in-stock.
    force_unavailable = any(t.strip().lower() == "unavailable" for t in tags)

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
    effective_panels = panels_overrides.get(pid) or panels

    stmt = pg_insert(Product.__table__).values(
        id=pid, title=title, handle=handle, product_type=ptype, category=category,
        image_url=image_url, images_json=images_json, vendor=vendor,
        tags=tags_str, description=description, style=style, color=color,
        material=material, panels=effective_panels, first_seen=now, updated_at=now,
        panels_override=panels_overrides.get(pid),
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["id"],
        set_=dict(
            title=stmt.excluded.title, handle=stmt.excluded.handle,
            product_type=stmt.excluded.product_type, category=stmt.excluded.category,
            image_url=stmt.excluded.image_url, images_json=stmt.excluded.images_json,
            vendor=stmt.excluded.vendor,
            tags=stmt.excluded.tags, description=stmt.excluded.description,
            style=stmt.excluded.style, color=stmt.excluded.color,
            material=stmt.excluded.material,
            panels=func.coalesce(Product.__table__.c.panels_override, stmt.excluded.panels),
            updated_at=stmt.excluded.updated_at,
        ),
    )
    db.session.execute(stmt)

    if is_new:
        vs        = product.get("variants", [])
        prices    = [float(v.get("price", 0)) for v in vs]
        avail_cnt = sum(1 for v in vs if v.get("available"))
        lo = hi   = 0.0
        if prices: lo, hi = min(prices), max(prices)
        price_str = f"${lo:.2f}" if lo == hi else f"${lo:.2f}–${hi:.2f}"
        emoji     = {"caps": "🧢", "apparel": "👕", "pins": "📌"}.get(category, "🛍")
        log_alert(pid, None, "new_product", f"NEW: {title} ({price_str})")
        log.info("NEW [%s]: %s", category, title)
        new_url = f"https://bostonscally.com/products/{handle}"
        ntfy("🆕 New on BSC!",
             f"{emoji} {title}\n{price_str} · {avail_cnt} variant(s) available",
             priority="urgent", tags="new,tada",
             click=new_url)

        # Per-user opt-in notifications for new product listings
        for u_topic in (new_product_topics or []):
            ntfy("🆕 New on BSC!",
                 f"{emoji} {title}\n{price_str} · {avail_cnt} variant(s) available",
                 priority="default", tags="new,tada", click=new_url, topic=u_topic)

        # Auto-add a release entry for new caps
        if category == "caps":
            _now = datetime.now(CT)
            rel_date = f"{_now.month}/{_now.day}/{str(_now.year)[2:]}"
            # Strip "Boston Scally Cap" from the release name
            rel_name = re.sub(r'\s*Boston Scally Cap\b', '', title, flags=re.IGNORECASE).strip()
            rel_name = re.sub(r'\s{2,}', ' ', rel_name).strip()
            try:
                rel_stmt = pg_insert(Release.__table__).values(
                    name=rel_name, release_date=rel_date, is_limited=0,
                    notes="Auto-added from new product alert",
                )
                rel_stmt = rel_stmt.on_conflict_do_nothing(index_elements=["name", "release_date"])
                db.session.execute(rel_stmt)
                log.info("RELEASE auto-added: %s on %s", rel_name, rel_date)
            except Exception as e:
                log.warning("Could not auto-add release for %s: %s", title, e)

    for v in product.get("variants", []):
        vid   = v["id"]
        price = float(v.get("price", 0))
        avail = 1 if (v.get("available") and not force_unavailable) else 0
        vtit  = v.get("title", "Default")

        var_stmt = pg_insert(Variant.__table__).values(
            id=vid, product_id=pid, title=vtit, price=price, available=avail, updated_at=now,
        )
        var_stmt = var_stmt.on_conflict_do_update(
            index_elements=["id"],
            set_=dict(price=var_stmt.excluded.price, available=var_stmt.excluded.available,
                      updated_at=var_stmt.excluded.updated_at),
        )
        db.session.execute(var_stmt)

        prev = latest_snaps.get(vid)

        if prev and not is_new:
            oa, op = prev["available"], prev["price"]
            if oa == 0 and avail == 1:
                log_alert(pid, vid, "back_in_stock", f"RESTOCKED: {title} — {vtit}")
                if is_notify_size(vtit, category, effective_panels):
                    url = f"https://bostonscally.com/products/{handle}"
                    emoji = {"caps": "🧢", "apparel": "👕", "pins": "📌"}.get(category, "🛍")
                    wl = pid in wishlist
                    ps = wishlist.get(pid)
                    if wl and ps and vtit == ps:
                        ntfy("⭐ Your Size is Back!",
                             f"{emoji} {title}\n{vtit}",
                             priority="urgent", tags="star,tada,shopping", click=url)
                    elif wl and ps:
                        ntfy("⭐ Wishlist Restock",
                             f"{emoji} {title}\n{vtit} (not your size)",
                             priority="high", tags="star,tada,shopping", click=url)
                    elif wl:
                        ntfy("⭐ Wishlist Restock!",
                             f"{emoji} {title}\n{vtit}",
                             priority="urgent", tags="star,tada,shopping", click=url)
                    else:
                        ntfy("🎉 Restock!",
                             f"{emoji} {title}\n{vtit}",
                             priority="high", tags="tada,shopping", click=url)

                    # Per-user opt-in notifications for anyone who wishlisted this product
                    for entry in (user_wishlist or {}).get(pid, []):
                        u_topic = entry["ntfy_topic"]
                        u_ps    = entry["preferred_size"]
                        if u_ps and vtit == u_ps:
                            ntfy("⭐ Your Size is Back!",
                                 f"{emoji} {title}\n{vtit}",
                                 priority="urgent", tags="star,tada,shopping", click=url, topic=u_topic)
                        elif u_ps:
                            ntfy("⭐ Wishlist Restock",
                                 f"{emoji} {title}\n{vtit} (not your size)",
                                 priority="high", tags="star,tada,shopping", click=url, topic=u_topic)
                        else:
                            ntfy("⭐ Wishlist Restock!",
                                 f"{emoji} {title}\n{vtit}",
                                 priority="urgent", tags="star,tada,shopping", click=url, topic=u_topic)
            elif oa == 1 and avail == 0:
                log_alert(pid, vid, "out_of_stock", f"OUT OF STOCK: {title} — {vtit}")

        if not prev or prev["price"] != price or prev["available"] != avail:
            db.session.add(Snapshot(
                variant_id=vid, product_id=pid, price=price, available=avail, checked_at=now,
            ))


# ── Main ──────────────────────────────────────────────────────────
def run_poll():
    log.info("══ Scally Tracker — %s ══", datetime.now(CT).strftime("%Y-%m-%d %I:%M %p CT"))

    flask_app = create_app()
    with flask_app.app_context():
        known = {row[0] for row in db.session.query(Product.id).all()}
        first_run = len(known) == 0

        products = fetch_bsc()
        if not products:
            log.warning("No products returned — aborting")
            return

        if first_run:
            log.info("First run — building baseline, no alerts will fire")
            known = {p["id"] for p in products}

        # Bulk pre-loads — replaces per-row queries inside process()
        latest_id_subq = (
            db.session.query(func.max(Snapshot.id))
            .group_by(Snapshot.variant_id)
            .subquery()
        )
        snap_rows = (
            db.session.query(Snapshot.variant_id, Snapshot.price, Snapshot.available)
            .filter(Snapshot.id.in_(db.session.query(latest_id_subq)))
            .all()
        )
        latest_snaps = {r.variant_id: {"price": r.price, "available": r.available} for r in snap_rows}

        override_rows = (
            db.session.query(Product.id, Product.panels_override)
            .filter(Product.panels_override.isnot(None))
            .all()
        )
        panels_overrides = {r.id: r.panels_override for r in override_rows}

        wish_rows = (
            db.session.query(UserItem.product_id, UserItem.preferred_size)
            .filter(UserItem.wishlisted == 1)
            .all()
        )
        wishlist = {r.product_id: r.preferred_size for r in wish_rows}

        now_hour = datetime.now(CT).hour

        # Per-user opt-in ntfy notifications for wishlisted restocks
        notify_rows = (
            db.session.query(
                UserItem.product_id, UserItem.preferred_size, User.ntfy_topic,
                User.quiet_hours_start, User.quiet_hours_end,
            )
            .join(User, User.id == UserItem.user_id)
            .filter(
                UserItem.wishlisted == 1,
                User.notify_wishlist == 1,
                User.ntfy_topic.isnot(None),
                User.ntfy_topic != "",
            )
            .all()
        )
        user_wishlist = {}
        for r in notify_rows:
            if in_quiet_hours(r.quiet_hours_start, r.quiet_hours_end, now_hour):
                continue
            user_wishlist.setdefault(r.product_id, []).append(
                {"preferred_size": r.preferred_size, "ntfy_topic": r.ntfy_topic}
            )

        # Per-user opt-in ntfy notifications for new products
        new_product_rows = (
            db.session.query(User.ntfy_topic, User.quiet_hours_start, User.quiet_hours_end)
            .filter(
                User.notify_new_products == 1,
                User.ntfy_topic.isnot(None),
                User.ntfy_topic != "",
            )
            .all()
        )
        new_product_topics = [
            r.ntfy_topic for r in new_product_rows
            if not in_quiet_hours(r.quiet_hours_start, r.quiet_hours_end, now_hour)
        ]

        seen_ids = set()
        for p in products:
            process(p, known, latest_snaps, panels_overrides, wishlist, user_wishlist, new_product_topics)
            seen_ids.add(p["id"])

        # Mark variants unavailable for products that vanished from the feed
        if not first_run:
            missing_ids = known - seen_ids
            for pid in missing_ids:
                variants = Variant.query.filter_by(product_id=pid, available=1).all()
                if not variants:
                    continue
                prod = Product.query.get(pid)
                title = prod.title if prod else f"#{pid}"
                now = datetime.now(CT).isoformat()
                for v in variants:
                    v.available = 0
                    v.updated_at = now
                    db.session.add(Snapshot(
                        variant_id=v.id, product_id=pid, price=v.price, available=0, checked_at=now,
                    ))
                    log_alert(pid, v.id, "out_of_stock",
                              f"OUT OF STOCK (delisted): {title} — {v.title}")
                log.info("DELISTED [unavailable]: %s (%d variant(s) zeroed)", title, len(variants))

        # Purge alerts older than 5 days
        cutoff = (datetime.now(CT) - timedelta(days=5)).isoformat()
        deleted = Alert.query.filter(Alert.created_at < cutoff).delete(synchronize_session=False)
        if deleted:
            log.info("Purged %d alert(s) older than 5 days", deleted)

        db.session.commit()

        # Summary
        counts = dict(
            db.session.query(Product.category, func.count(Product.id))
            .group_by(Product.category)
            .all()
        )
        log.info("Categories: %s", counts)

    log.info("══ Poll complete ══\n")


if __name__ == "__main__":
    run_poll()
