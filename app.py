#!/usr/bin/env python3
"""Scally Tracker — Flask web server, port 5001"""

import os, subprocess, sys, json, secrets, csv, io, time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from flask import Flask, render_template, jsonify, request, Response
from sqlalchemy import func, case, and_, exists
from werkzeug.middleware.proxy_fix import ProxyFix

CT = ZoneInfo("America/Chicago")

from config import Config
from models import db, Product, Variant, Snapshot, Alert, UserItem, ManualCap, Release, User, Feedback

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)
app.config.from_object(Config)
app.config["SQLALCHEMY_DATABASE_URI"] = Config.DATABASE_URL
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db.init_app(app)

from flask_login import LoginManager, login_required, current_user

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "auth.login"


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


from auth import auth_bp
app.register_blueprint(auth_bp, url_prefix="/auth")

from admin import admin_bp
app.register_blueprint(admin_bp, url_prefix="/admin")

from flask_migrate import Migrate
migrate = Migrate(app, db)


@app.route("/")
@login_required
def index():
    return render_template("index.html")


@app.route("/ping")
def ping():
    return "ok", 200


CAT_SORT = {"caps": 0, "pins": 1, "apparel": 2, "other": 3}

# Short-lived per-user cache for /api/products — the underlying catalog only
# changes when the poller runs (every 30 min), so a small TTL avoids repeat
# full-catalog queries from rapid reloads/tab switches without serving stale
# data for long after a user toggles owned/wishlisted.
_PRODUCTS_CACHE_TTL = 30  # seconds
_products_cache = {}


def _invalidate_products_cache(user_id=None):
    if user_id is None:
        _products_cache.clear()
    else:
        for key in [k for k in _products_cache if k[0] == user_id]:
            del _products_cache[key]


@app.route("/api/products")
@login_required
def api_products():
    cache_key = (current_user.id, request.query_string.decode())
    cached = _products_cache.get(cache_key)
    if cached and (time.monotonic() - cached[0]) < _PRODUCTS_CACHE_TTL:
        resp = jsonify(cached[1])
        resp.headers["Cache-Control"] = "private, no-store"
        return resp

    category = request.args.get("category", "all")   # all|caps|pins|apparel|other
    avail    = request.args.get("avail",    "all")    # all|in|out
    panels   = request.args.get("panels",   "all")    # all|single|6|8
    search   = request.args.get("search",   "").strip().lower()
    sort     = request.args.get("sort",     "title")  # title|price_asc|price_desc|newest

    owned      = request.args.get("owned",      "0") == "1"
    wishlisted = request.args.get("wishlisted", "0") == "1"
    sold_view  = request.args.get("sold",       "0") == "1"

    now_ct = datetime.now(CT)
    new_cutoff     = (now_ct - timedelta(days=5)).isoformat()
    restock_cutoff = (now_ct - timedelta(days=1)).isoformat()

    # Batched lookups instead of a per-row correlated EXISTS — one short
    # query each against the (alert_type, created_at) index.
    new_pids = {
        pid for (pid,) in db.session.query(Alert.product_id)
        .filter(Alert.alert_type == "new_product", Alert.created_at >= new_cutoff)
        .distinct()
    }
    restock_pids = {
        pid for (pid,) in db.session.query(Alert.product_id)
        .filter(Alert.alert_type == "back_in_stock", Alert.created_at >= restock_cutoff)
        .distinct()
    }

    xl_sum = func.sum(case(
        (and_(Variant.title.ilike('x-large%'), ~Variant.title.ilike('xx-large%')), Variant.available),
        else_=0,
    ))
    xxl_sum = func.sum(case(
        (Variant.title.ilike('xx-large%'), Variant.available),
        else_=0,
    ))

    q = (
        db.session.query(
            Product.id, Product.title, Product.category, Product.image_url,
            Product.product_type, Product.panels, Product.first_seen,
            func.count(Variant.id).label("variant_count"),
            func.sum(Variant.available).label("variants_available"),
            xl_sum.label("xl_available"),
            xxl_sum.label("xxl_available"),
            func.min(Variant.price).label("min_price"),
            func.max(Variant.price).label("max_price"),
            func.coalesce(UserItem.owned, 0).label("owned"),
            func.coalesce(UserItem.wishlisted, 0).label("wishlisted"),
            func.coalesce(UserItem.sold, 0).label("sold"),
            UserItem.preferred_size.label("preferred_size"),
            UserItem.updated_at.label("collection_updated_at"),
            UserItem.sold_price.label("sold_price"),
            UserItem.sold_date.label("sold_date"),
        )
        .outerjoin(Variant, Variant.product_id == Product.id)
        .outerjoin(UserItem, and_(UserItem.product_id == Product.id, UserItem.user_id == current_user.id))
        .group_by(
            Product.id, Product.title, Product.category, Product.image_url,
            Product.product_type, Product.panels, Product.first_seen,
            UserItem.owned, UserItem.wishlisted, UserItem.sold, UserItem.preferred_size,
            UserItem.updated_at, UserItem.sold_price, UserItem.sold_date,
        )
    )

    # Push all filters into SQL up front so the DB returns only matching rows
    if owned:
        q = q.filter(UserItem.owned == 1)
    if wishlisted:
        q = q.filter(UserItem.wishlisted == 1)
    if sold_view:
        q = q.filter(UserItem.sold == 1)
    if category != "all" and not owned and not wishlisted and not sold_view:
        q = q.filter(Product.category == category)
    if panels != "all" and not owned and not wishlisted and not sold_view:
        q = q.filter(Product.panels == panels)
    if search:
        q = q.filter(func.lower(Product.title).like(f"%{search}%"))

    rows = q.all()

    out = []
    for row in rows:
        p = dict(row._mapping)

        # Category filter
        if category != "all" and p["category"] != category:
            continue

        # Panel sub-filter (caps only)
        if panels != "all":
            if p["category"] != "caps":
                continue
            if (p["panels"] or "") != panels:
                continue

        p["in_stock"] = bool(p["variants_available"])
        p["is_new_product"] = 1 if p["id"] in new_pids else 0
        p["is_restocked"] = 1 if p["id"] in restock_pids else 0

        # My-size availability: XL/XXL rules for caps/apparel (kids use general stock)
        is_kids = (p.get("title") or "").lower().startswith("kids")
        if is_kids or p["category"] not in ("caps", "apparel"):
            p["my_size_in_stock"] = p["in_stock"]
        elif p["category"] == "caps" and (p.get("panels") or "") == "single":
            p["my_size_in_stock"] = bool(p["xxl_available"])
        else:
            p["my_size_in_stock"] = bool(p["xl_available"])

        if avail == "in"  and not p["my_size_in_stock"]: continue
        if avail == "out" and     p["my_size_in_stock"]: continue

        out.append(p)

    # Compute OOS days for wishlisted items that have a preferred_size and are currently OOS
    wl_pids = [p["id"] for p in out if p.get("wishlisted") and p.get("preferred_size")]
    if wl_pids:
        oos_rows = (
            db.session.query(Variant.product_id, func.max(Alert.created_at).label("last_oos_at"))
            .join(UserItem, and_(
                UserItem.product_id == Variant.product_id,
                UserItem.preferred_size == Variant.title,
                UserItem.user_id == current_user.id,
            ))
            .join(Alert, and_(Alert.variant_id == Variant.id, Alert.alert_type == "out_of_stock"))
            .filter(
                Variant.available == 0,
                UserItem.wishlisted == 1,
                UserItem.user_id == current_user.id,
                UserItem.preferred_size.isnot(None),
                UserItem.preferred_size != "",
                Variant.product_id.in_(wl_pids),
            )
            .group_by(Variant.product_id)
            .all()
        )
        oos_map = {r.product_id: r.last_oos_at for r in oos_rows}
        for p in out:
            last_oos = oos_map.get(p["id"])
            if last_oos:
                try:
                    dt = datetime.fromisoformat(last_oos)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=CT)
                    p["oos_days"] = max((now_ct - dt).days, 1)
                except Exception:
                    pass

    key = {
        "title":      lambda x: (x["title"] or "").lower(),
        "price_asc":  lambda x: x["min_price"] or 0,
        "price_desc": lambda x: x["max_price"] or 0,
        "newest":     lambda x: x["first_seen"] or "",
        "added":      lambda x: x["collection_updated_at"] or "",
    }.get(sort, lambda x: (x["title"] or "").lower())
    out.sort(key=key, reverse=(sort in ("price_desc", "newest", "added")))

    # For owned/wishlisted/sold views sort by category: caps, pins, apparel, other
    # (skipped when sorting by recently-added, which should ignore category grouping)
    if (owned or wishlisted or sold_view) and sort != "added":
        out.sort(key=lambda x: CAT_SORT.get(x.get("category", "other"), 3))

    _products_cache[cache_key] = (time.monotonic(), out)

    resp = jsonify(out)
    resp.headers["Cache-Control"] = "private, no-store"
    return resp


@app.route("/api/product/<int:pid>")
@login_required
def api_product(pid):
    row = (
        db.session.query(
            Product,
            func.coalesce(UserItem.owned, 0).label("owned"),
            func.coalesce(UserItem.wishlisted, 0).label("wishlisted"),
            func.coalesce(UserItem.sold, 0).label("sold"),
            UserItem.notes.label("notes"),
            UserItem.preferred_size.label("preferred_size"),
            UserItem.sold_price.label("sold_price"),
            UserItem.sold_date.label("sold_date"),
        )
        .outerjoin(UserItem, and_(UserItem.product_id == Product.id, UserItem.user_id == current_user.id))
        .filter(Product.id == pid)
        .first()
    )
    if not row:
        return jsonify({"error": "not found"}), 404

    product, owned, wishlisted, sold, notes, preferred_size, sold_price, sold_date = row

    variants = (
        Variant.query.filter_by(product_id=pid)
        .order_by(Variant.price, Variant.title)
        .all()
    )

    history = {}
    for v in variants:
        snaps = (
            Snapshot.query.filter_by(variant_id=v.id)
            .order_by(Snapshot.checked_at.desc())
            .limit(100)
            .all()
        )
        rows_list = [{"price": s.price, "available": s.available, "checked_at": s.checked_at} for s in snaps]
        # Keep only entries where price or availability changed
        changes = []
        prev = None
        for row_ in reversed(rows_list):
            if prev is None or row_["price"] != prev["price"] or row_["available"] != prev["available"]:
                changes.append(row_)
            prev = row_
        # Always include most recent entry
        if rows_list and (not changes or changes[-1]["checked_at"] != rows_list[0]["checked_at"]):
            changes.append(rows_list[0])
        history[v.id] = changes[-5:]  # last 5 changes

    product_dict = {c.name: getattr(product, c.name) for c in Product.__table__.columns}
    product_dict["owned"] = owned
    product_dict["wishlisted"] = wishlisted
    product_dict["sold"] = sold
    product_dict["notes"] = notes
    product_dict["preferred_size"] = preferred_size
    product_dict["sold_price"] = sold_price
    product_dict["sold_date"] = sold_date
    try:
        product_dict['images'] = json.loads(product_dict.get('images_json') or '[]')
    except Exception:
        product_dict['images'] = [product_dict.get('image_url')] if product_dict.get('image_url') else []

    return jsonify({
        "product":  product_dict,
        "variants": [{c.name: getattr(v, c.name) for c in Variant.__table__.columns} for v in variants],
        "history":  history,
    })


@app.route("/api/user_item/<int:pid>", methods=["POST"])
@login_required
def api_user_item(pid):
    data   = request.get_json()
    status = data.get("status", "none")
    owned      = 1 if status == "owned"      else 0
    wishlisted = 1 if status == "wishlisted" else 0
    sold       = 1 if status == "sold"       else 0

    item = UserItem.query.filter_by(product_id=pid, user_id=current_user.id).first()

    # Partial updates (e.g. bulk status changes) may omit notes/preferred_size/sold
    # fields entirely — in that case, preserve the existing values.
    if "notes" in data:
        notes = (data.get("notes") or "").strip()
    else:
        notes = item.notes if item else ""

    if "preferred_size" in data:
        preferred_size = (data.get("preferred_size") or "").strip() or None
    else:
        preferred_size = item.preferred_size if item else None
    if not wishlisted:
        preferred_size = None
    elif not preferred_size:
        # Fall back to the user's collection default size for this category
        product = Product.query.get(pid)
        category = product.category if product else None
        if category == "caps":
            preferred_size = current_user.default_size_caps or None
        elif category == "apparel":
            preferred_size = current_user.default_size_apparel or None

    if "sold_price" in data or "sold_date" in data:
        sold_price = data.get("sold_price")
        sold_date  = (data.get("sold_date") or "").strip() or None
        if sold:
            try:
                sold_price = float(sold_price) if sold_price not in (None, "") else None
            except (TypeError, ValueError):
                sold_price = None
        else:
            sold_price = None
            sold_date = None
    else:
        sold_price = item.sold_price if item else None
        sold_date  = item.sold_date if item else None
        if not sold:
            sold_price = None
            sold_date = None

    now = datetime.now().isoformat()

    if item:
        item.owned = owned
        item.wishlisted = wishlisted
        item.sold = sold
        item.notes = notes
        item.preferred_size = preferred_size
        item.sold_price = sold_price
        item.sold_date = sold_date
        item.updated_at = now
    else:
        db.session.add(UserItem(
            product_id=pid, user_id=current_user.id, owned=owned, wishlisted=wishlisted, sold=sold,
            notes=notes, preferred_size=preferred_size, sold_price=sold_price, sold_date=sold_date,
            updated_at=now,
        ))
    db.session.commit()
    _invalidate_products_cache(current_user.id)
    return jsonify({"ok": True, "status": status, "notes": notes})


def _settings_payload():
    return {
        "ntfy_topic":           current_user.ntfy_topic,
        "notify_wishlist":      bool(current_user.notify_wishlist),
        "notify_new_products":  bool(current_user.notify_new_products),
        "quiet_hours_start":    current_user.quiet_hours_start,
        "quiet_hours_end":      current_user.quiet_hours_end,
        "default_size_caps":    current_user.default_size_caps,
        "default_size_apparel": current_user.default_size_apparel,
        "username":             current_user.username,
        "email":                current_user.email,
    }


@app.route("/api/settings", methods=["GET"])
@login_required
def api_settings_get():
    return jsonify(_settings_payload())


@app.route("/api/settings", methods=["POST"])
@login_required
def api_settings_update():
    data = request.get_json() or {}
    notify_wishlist     = bool(data.get("notify_wishlist", False))
    notify_new_products = bool(data.get("notify_new_products", False))

    ntfy_topic = (data.get("ntfy_topic") or "").strip()
    if (notify_wishlist or notify_new_products) and not ntfy_topic and not current_user.ntfy_topic:
        # auto-generate a unique topic name the first time notifications are enabled
        ntfy_topic = f"scallytracker-{current_user.username}-{secrets.token_hex(4)}"

    if ntfy_topic:
        current_user.ntfy_topic = ntfy_topic
    current_user.notify_wishlist     = 1 if notify_wishlist else 0
    current_user.notify_new_products = 1 if notify_new_products else 0

    # Quiet hours — expect integers 0-23, or null/empty to clear
    for field in ("quiet_hours_start", "quiet_hours_end"):
        if field in data:
            val = data.get(field)
            if val in (None, "", "null"):
                setattr(current_user, field, None)
            else:
                try:
                    ival = int(val)
                    if 0 <= ival <= 23:
                        setattr(current_user, field, ival)
                except (TypeError, ValueError):
                    pass

    # Collection size defaults
    for field in ("default_size_caps", "default_size_apparel"):
        if field in data:
            val = (data.get(field) or "").strip()
            setattr(current_user, field, val or None)

    db.session.commit()
    return jsonify({"ok": True, **_settings_payload()})


@app.route("/api/account/password", methods=["POST"])
@login_required
def api_account_password():
    from werkzeug.security import generate_password_hash, check_password_hash

    data = request.get_json() or {}
    current_pw = data.get("current_password") or ""
    new_pw     = data.get("new_password") or ""
    confirm_pw = data.get("confirm_password") or ""

    if not check_password_hash(current_user.password_hash, current_pw):
        return jsonify({"error": "Current password is incorrect"}), 400
    if not new_pw or len(new_pw) < 8:
        return jsonify({"error": "New password must be at least 8 characters"}), 400
    if new_pw != confirm_pw:
        return jsonify({"error": "New passwords do not match"}), 400

    current_user.password_hash = generate_password_hash(new_pw)
    db.session.commit()
    return jsonify({"ok": True})


@app.route("/api/account/email", methods=["POST"])
@login_required
def api_account_email():
    data = request.get_json() or {}
    new_email = (data.get("email") or "").strip()

    if not new_email or "@" not in new_email:
        return jsonify({"error": "Please enter a valid email address"}), 400

    existing = User.query.filter(User.email == new_email, User.id != current_user.id).first()
    if existing:
        return jsonify({"error": "That email is already in use"}), 400

    current_user.email = new_email
    db.session.commit()
    return jsonify({"ok": True, "email": current_user.email})


@app.route("/api/export/csv")
@login_required
def api_export_csv():
    # Optional view filter: all|owned|wishlist|sold — lets the Collection page
    # export just the currently active tab.
    view = request.args.get("view", "all")
    status_filter = {"owned": "owned", "wishlist": "wishlisted", "sold": "sold"}.get(view)

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "source", "id", "title", "category", "status", "preferred_size",
        "style", "color", "material", "panels", "notes", "sold_price", "sold_date", "updated_at",
    ])

    rows = (
        db.session.query(UserItem, Product)
        .join(Product, Product.id == UserItem.product_id)
        .filter(
            UserItem.user_id == current_user.id,
            (UserItem.owned == 1) | (UserItem.wishlisted == 1) | (UserItem.sold == 1),
        )
        .all()
    )
    for item, product in rows:
        status = "sold" if item.sold else ("wishlisted" if item.wishlisted else "owned")
        if status_filter and status != status_filter:
            continue
        writer.writerow([
            "catalog", product.id, product.title, product.category, status,
            item.preferred_size or "", product.style or "", product.color or "",
            product.material or "", product.panels or "", item.notes or "",
            item.sold_price if item.sold_price is not None else "",
            item.sold_date or "", item.updated_at or "",
        ])

    caps = ManualCap.query.filter_by(user_id=current_user.id).all()
    for cap in caps:
        status = "sold" if cap.sold else ("wishlisted" if cap.wishlisted else "owned")
        if status_filter and status != status_filter:
            continue
        writer.writerow([
            "manual", cap.id, cap.name, "caps", status,
            "", cap.style or "", cap.color or "", cap.material or "", cap.panels or "",
            cap.notes or "",
            cap.sold_price if cap.sold_price is not None else "",
            cap.sold_date or "", cap.created_at or "",
        ])

    suffix = f"-{view}" if status_filter else ""
    filename = f"scally-collection-{current_user.username}{suffix}-{datetime.now(CT).strftime('%Y%m%d')}.csv"
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@app.route("/api/manual_caps")
@login_required
def api_manual_caps():
    rows = ManualCap.query.filter_by(user_id=current_user.id).order_by(ManualCap.created_at.desc()).all()
    result = []
    for r in rows:
        # List view only needs the primary thumbnail (`image`); the full
        # gallery (`images_json`) is large (base64) and only needed when a
        # specific cap is opened via /api/manual_cap/<id>.
        cap = {c.name: getattr(r, c.name) for c in ManualCap.__table__.columns if c.name != 'images_json'}
        result.append(cap)
    return jsonify(result)


@app.route("/api/manual_cap/<int:mid>", methods=["GET"])
@login_required
def api_manual_cap_get(mid):
    r = ManualCap.query.filter_by(id=mid, user_id=current_user.id).first()
    if not r:
        return jsonify({"error": "not found"}), 404
    cap = {c.name: getattr(r, c.name) for c in ManualCap.__table__.columns}
    if cap.get('images_json'):
        try:
            cap['images'] = json.loads(cap['images_json'])
        except Exception:
            cap['images'] = [cap['image']] if cap.get('image') else []
    else:
        cap['images'] = [cap['image']] if cap.get('image') else []
    return jsonify(cap)


@app.route("/api/manual_cap", methods=["POST"])
@login_required
def api_manual_cap_create():
    data = request.get_json()
    if not data.get("name"):
        return jsonify({"error": "name is required"}), 400
    now = datetime.now().isoformat()
    images = [i for i in (data.get("images") or []) if i]
    images_json_str = json.dumps(images)
    first_image = images[0] if images else None

    cap = ManualCap(
        user_id=current_user.id,
        name=data.get("name", "").strip(),
        color=data.get("color", "").strip(),
        style=data.get("style", "").strip(),
        material=data.get("material", "").strip(),
        notes=data.get("notes", "").strip(),
        image=first_image,
        images_json=images_json_str,
        wishlisted=int(bool(data.get("wishlisted", False))),
        cap_type=data.get("cap_type", "other").strip() or "other",
        panels=data.get("panels", None),
        created_at=now,
    )
    db.session.add(cap)
    db.session.commit()
    return jsonify({"ok": True, "id": cap.id})


@app.route("/api/manual_cap/<int:mid>", methods=["POST"])
@login_required
def api_manual_cap_update(mid):
    data = request.get_json()
    cap = ManualCap.query.filter_by(id=mid, user_id=current_user.id).first()
    if not cap:
        return jsonify({"error": "not found"}), 404

    status = data.get("status")
    if status == "owned":
        cap.sold = 0
        cap.wishlisted = 0
        cap.sold_price = None
        cap.sold_date = None
        db.session.commit()
        return jsonify({"ok": True, "status": "owned"})
    if status == "wishlisted":
        cap.sold = 0
        cap.wishlisted = 1
        cap.sold_price = None
        cap.sold_date = None
        db.session.commit()
        return jsonify({"ok": True, "status": "wishlisted"})
    if status == "sold":
        cap.sold = 1
        cap.wishlisted = 0
        sold_price = data.get("sold_price")
        try:
            cap.sold_price = float(sold_price) if sold_price not in (None, "") else None
        except (TypeError, ValueError):
            cap.sold_price = None
        cap.sold_date = (data.get("sold_date") or "").strip() or None
        db.session.commit()
        return jsonify({"ok": True, "status": "sold"})
    # legacy field-level updates
    if "wishlisted" in data:
        wishlisted = int(bool(data["wishlisted"]))
        cap.wishlisted = wishlisted
        db.session.commit()
        return jsonify({"ok": True, "wishlisted": wishlisted})
    sold = int(bool(data.get("sold", False)))
    cap.sold = sold
    db.session.commit()
    return jsonify({"ok": True, "sold": sold})


@app.route("/api/manual_cap/<int:mid>/edit", methods=["POST"])
@login_required
def api_manual_cap_edit(mid):
    data = request.get_json()
    if not data.get("name"):
        return jsonify({"error": "name is required"}), 400
    cap = ManualCap.query.filter_by(id=mid, user_id=current_user.id).first()
    if not cap:
        return jsonify({"error": "not found"}), 404

    images = [i for i in (data.get("images") or []) if i]
    images_json_str = json.dumps(images)
    first_image = images[0] if images else None
    status = data.get("status", "owned")

    cap.name = data.get("name", "").strip()
    cap.color = data.get("color", "").strip()
    cap.style = data.get("style", "").strip()
    cap.material = data.get("material", "").strip()
    cap.notes = data.get("notes", "").strip()
    cap.image = first_image
    cap.images_json = images_json_str
    cap.cap_type = data.get("cap_type", "other").strip() or "other"
    cap.panels = data.get("panels", None)
    cap.sold = 1 if status == "sold" else 0
    cap.wishlisted = 1 if status == "wishlisted" else 0
    if status == "sold":
        sold_price = data.get("sold_price")
        try:
            cap.sold_price = float(sold_price) if sold_price not in (None, "") else None
        except (TypeError, ValueError):
            cap.sold_price = None
        cap.sold_date = (data.get("sold_date") or "").strip() or None
    else:
        cap.sold_price = None
        cap.sold_date = None
    db.session.commit()
    return jsonify({"ok": True})


@app.route("/api/manual_cap/<int:mid>/notes", methods=["POST"])
@login_required
def api_manual_cap_notes(mid):
    cap = ManualCap.query.filter_by(id=mid, user_id=current_user.id).first()
    if not cap:
        return jsonify({"error": "not found"}), 404
    data = request.get_json()
    cap.notes = (data.get("notes") or "").strip() or None
    db.session.commit()
    return jsonify({"ok": True})


@app.route("/api/manual_cap/<int:mid>", methods=["DELETE"])
@login_required
def api_manual_cap_delete(mid):
    ManualCap.query.filter_by(id=mid, user_id=current_user.id).delete()
    db.session.commit()
    return jsonify({"ok": True})


@app.route("/api/stats")
@login_required
def api_stats():
    now_ct = datetime.now(CT)
    today_start = now_ct.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    today_end   = (now_ct.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)).isoformat()
    cutoff_7d = (now_ct - timedelta(days=7)).isoformat()
    cutoff_5d = (now_ct - timedelta(days=5)).isoformat()

    total   = db.session.query(func.count(Product.id)).scalar()
    caps    = db.session.query(func.count(Product.id)).filter(Product.category == "caps").scalar()
    pins    = db.session.query(func.count(Product.id)).filter(Product.category == "pins").scalar()
    apparel = db.session.query(func.count(Product.id)).filter(Product.category == "apparel").scalar()

    in_stock_sub = exists().where(and_(Variant.product_id == Product.id, Variant.available == 1))
    in_stock = db.session.query(func.count(Product.id)).filter(in_stock_sub).scalar()
    out_of_stock = total - in_stock

    owned = (
        db.session.query(func.count(UserItem.product_id))
        .join(Product, Product.id == UserItem.product_id)
        .filter(UserItem.owned == 1, UserItem.user_id == current_user.id, Product.category == "caps")
        .scalar()
    )
    wishlisted = (
        db.session.query(func.count(UserItem.product_id))
        .join(Product, Product.id == UserItem.product_id)
        .filter(UserItem.wishlisted == 1, UserItem.user_id == current_user.id, Product.category == "caps")
        .scalar()
    )

    alerts_today = (
        db.session.query(func.count(Alert.id))
        .filter(Alert.created_at >= today_start, Alert.created_at < today_end)
        .scalar()
    )
    last_poll = db.session.query(func.max(Snapshot.checked_at)).scalar()

    wishlisted_restocks = (
        db.session.query(func.count(func.distinct(Alert.product_id)))
        .join(UserItem, UserItem.product_id == Alert.product_id)
        .filter(
            Alert.alert_type == "back_in_stock",
            UserItem.wishlisted == 1,
            UserItem.user_id == current_user.id,
            Alert.created_at >= cutoff_7d,
            exists().where(and_(Variant.product_id == Alert.product_id, Variant.available == 1)),
        )
        .scalar()
    )

    new_caps = (
        db.session.query(func.count(func.distinct(Alert.product_id)))
        .join(Product, Product.id == Alert.product_id)
        .filter(
            Alert.alert_type == "new_product",
            Product.category == "caps",
            Alert.created_at >= cutoff_5d,
        )
        .scalar()
    )

    open_feedback = (
        db.session.query(func.count(Feedback.id))
        .filter(Feedback.status == "open")
        .scalar()
    ) if current_user.is_admin else 0

    resp = jsonify({
        "total":               total,
        "caps":                caps,
        "pins":                pins,
        "apparel":             apparel,
        "in_stock":            in_stock,
        "out_of_stock":        out_of_stock,
        "owned":               owned,
        "wishlisted":          wishlisted,
        "alerts_today":        alerts_today,
        "last_poll":           last_poll,
        "wishlisted_restocks": wishlisted_restocks,
        "new_caps":            new_caps,
        "open_feedback":       open_feedback,
    })
    resp.headers["Cache-Control"] = "private, max-age=55"
    return resp


@app.route("/api/feedback", methods=["POST"])
@login_required
def api_feedback_submit():
    data = request.get_json()
    fb_type  = (data.get("type") or "").strip()
    title    = (data.get("title") or "").strip()
    body     = (data.get("body") or "").strip()
    if fb_type not in ("bug", "feature"):
        return jsonify({"error": "type must be bug or feature"}), 400
    if not title:
        return jsonify({"error": "title is required"}), 400
    fb = Feedback(
        user_id=current_user.id,
        type=fb_type,
        title=title[:200],
        body=body[:2000] if body else None,
        status="open",
        created_at=datetime.now(CT).isoformat(),
    )
    db.session.add(fb)
    db.session.commit()
    return jsonify({"ok": True})


@app.route("/api/alerts")
@login_required
def api_alerts():
    limit = min(int(request.args.get("limit", 100)), 500)
    rows = (
        db.session.query(
            Alert, Product.title.label("product_title"), Product.handle.label("handle"),
            Product.category.label("category"), Variant.title.label("variant_title"),
            Product.image_url.label("image_url"),
        )
        .outerjoin(Product, Product.id == Alert.product_id)
        .outerjoin(Variant, Variant.id == Alert.variant_id)
        .order_by(Alert.created_at.desc())
        .limit(limit)
        .all()
    )
    out = []
    for alert, product_title, handle, category, variant_title, image_url in rows:
        d = {c.name: getattr(alert, c.name) for c in Alert.__table__.columns}
        d["product_title"]  = product_title
        d["handle"]         = handle
        d["category"]       = category
        d["variant_title"]  = variant_title
        d["image_url"]      = image_url
        out.append(d)
    return jsonify(out)


@app.route("/api/poll", methods=["POST"])
@login_required
def api_poll():
    poller = os.path.join(os.path.dirname(os.path.abspath(__file__)), "poller.py")
    subprocess.Popen([sys.executable, poller])
    return jsonify({"ok": True})


@app.route("/api/cron-poll", methods=["GET", "POST"])
def api_cron_poll():
    """Unauthenticated poll trigger for external uptime monitors.

    Requires a shared-secret `token` query param matching CRON_SECRET.
    Only runs the poller during 7am-7pm CT, and only if at least 30
    minutes have passed since the last snapshot, so a 5-minute monitor
    ping doesn't spam the poller.
    """
    secret = Config.CRON_SECRET
    if not secret or request.args.get("token") != secret:
        return jsonify({"ok": False, "error": "unauthorized"}), 403

    now = datetime.now(CT)
    if not (7 <= now.hour < 19):
        return jsonify({"ok": True, "ran": False, "reason": "outside poll hours"})

    last = db.session.query(func.max(Snapshot.checked_at)).scalar()
    if last:
        try:
            last_dt = datetime.fromisoformat(last)
            if last_dt.tzinfo is None:
                last_dt = last_dt.replace(tzinfo=CT)
            elapsed = (now - last_dt).total_seconds()
            if elapsed < 30 * 60:
                return jsonify({"ok": True, "ran": False, "reason": "polled recently",
                                 "minutes_ago": round(elapsed / 60, 1)})
        except ValueError:
            pass

    poller = os.path.join(os.path.dirname(os.path.abspath(__file__)), "poller.py")
    subprocess.Popen([sys.executable, poller])
    return jsonify({"ok": True, "ran": True})


@app.route("/api/release", methods=["POST"])
@login_required
def api_release_create():
    data = request.get_json()
    name = (data.get("name") or "").strip()
    release_date = (data.get("release_date") or "").strip()
    if not name or not release_date:
        return jsonify({"error": "name and release_date are required"}), 400
    is_limited = 1 if data.get("is_limited") else 0
    notes = (data.get("notes") or "").strip() or None
    try:
        rel = Release(name=name, release_date=release_date, is_limited=is_limited, notes=notes)
        db.session.add(rel)
        db.session.commit()
        return jsonify({"ok": True, "id": rel.id})
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 409


@app.route("/api/release/<int:rid>", methods=["PUT"])
@login_required
def api_release_update(rid):
    data = request.get_json()
    name = (data.get("name") or "").strip()
    release_date = (data.get("release_date") or "").strip()
    if not name or not release_date:
        return jsonify({"error": "name and release_date are required"}), 400
    is_limited = 1 if data.get("is_limited") else 0
    notes = (data.get("notes") or "").strip() or None
    try:
        rel = Release.query.get(rid)
        if not rel:
            return jsonify({"error": "not found"}), 404
        rel.name = name
        rel.release_date = release_date
        rel.is_limited = is_limited
        rel.notes = notes
        db.session.commit()
        return jsonify({"ok": True})
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 409


@app.route("/api/release/<int:rid>", methods=["DELETE"])
@login_required
def api_release_delete(rid):
    Release.query.filter_by(id=rid).delete()
    db.session.commit()
    return jsonify({"ok": True})


@app.route("/api/release_names")
@login_required
def api_release_names():
    names = {r.name for r in db.session.query(Release.name).distinct()}
    return jsonify(sorted(names))


@app.route("/api/releases")
@login_required
def api_releases():
    search     = request.args.get("search",  "").strip().lower()
    limited    = request.args.get("limited", "all")   # all|yes|no
    months_str = request.args.get("months",  "")      # comma-separated, e.g. "4,5,6"
    years_str  = request.args.get("years",   "")      # comma-separated, e.g. "2024,2025"

    def parse_date(s):
        try:
            m, d, y = s.split("/")
            return datetime(2000 + int(y), int(m), int(d))
        except Exception:
            return datetime.min

    month_set = set()
    for tok in months_str.split(","):
        tok = tok.strip()
        if tok.isdigit():
            month_set.add(int(tok))

    year_set = set()
    for tok in years_str.split(","):
        tok = tok.strip()
        if tok.isdigit():
            year_set.add(int(tok))

    rows = Release.query.all()

    out = []
    for r in rows:
        rec = {c.name: getattr(r, c.name) for c in Release.__table__.columns}
        pd = parse_date(rec["release_date"])
        if search  and search not in rec["name"].lower():  continue
        if limited == "yes" and not rec["is_limited"]:     continue
        if limited == "no"  and     rec["is_limited"]:     continue
        if year_set and pd.year not in year_set:
            continue
        if month_set and pd.month not in month_set:
            continue
        out.append(rec)

    out.sort(key=lambda r: parse_date(r["release_date"]), reverse=True)
    return jsonify(out)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001, debug=False)
