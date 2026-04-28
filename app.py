#!/usr/bin/env python3
"""Scally Tracker — Flask web server, port 5001"""

import sqlite3, os, subprocess, sys
from datetime import datetime
from zoneinfo import ZoneInfo
from flask import Flask, render_template, jsonify, request

CT = ZoneInfo("America/Chicago")

app = Flask(__name__)
DB  = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "inventory.db")

def db():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    return c

def init_user_items():
    with db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_items (
                product_id INTEGER PRIMARY KEY,
                owned      INTEGER NOT NULL DEFAULT 0,
                wishlisted INTEGER NOT NULL DEFAULT 0,
                sold       INTEGER NOT NULL DEFAULT 0,
                notes      TEXT,
                updated_at TEXT
            )
        """)
        for col, defn in [("sold", "INTEGER NOT NULL DEFAULT 0"), ("notes", "TEXT"), ("preferred_size", "TEXT")]:
            try:
                conn.execute(f"ALTER TABLE user_items ADD COLUMN {col} {defn}")
            except Exception:
                pass
        conn.execute("""
            CREATE TABLE IF NOT EXISTS manual_caps (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                name       TEXT NOT NULL,
                color      TEXT,
                style      TEXT,
                material   TEXT,
                notes      TEXT,
                image      TEXT,
                sold       INTEGER NOT NULL DEFAULT 0,
                created_at TEXT
            )
        """)
        try:
            conn.execute("ALTER TABLE manual_caps ADD COLUMN sold INTEGER NOT NULL DEFAULT 0")
        except Exception:
            pass
        try:
            conn.execute("ALTER TABLE manual_caps ADD COLUMN wishlisted INTEGER NOT NULL DEFAULT 0")
        except Exception:
            pass
        try:
            conn.execute("ALTER TABLE manual_caps ADD COLUMN cap_type TEXT NOT NULL DEFAULT 'none'")
        except Exception:
            pass
        try:
            conn.execute("ALTER TABLE manual_caps ADD COLUMN panels TEXT")
        except Exception:
            pass
        try:
            conn.execute("ALTER TABLE manual_caps ADD COLUMN images_json TEXT")
        except Exception:
            pass

init_user_items()


@app.route("/")
def index():
    return render_template("index.html")


CAT_SORT = {"caps": 0, "pins": 1, "apparel": 2, "other": 3}

@app.route("/api/products")
def api_products():
    category = request.args.get("category", "all")   # all|caps|pins|apparel|other
    avail    = request.args.get("avail",    "all")    # all|in|out
    panels   = request.args.get("panels",   "all")    # all|single|6|8
    search   = request.args.get("search",   "").strip().lower()
    sort     = request.args.get("sort",     "title")  # title|price_asc|price_desc|newest

    owned      = request.args.get("owned",      "0") == "1"
    wishlisted = request.args.get("wishlisted", "0") == "1"
    sold_view  = request.args.get("sold",       "0") == "1"

    sql_conditions = []
    sql_params = []

    # Push all filters into SQL up front so the DB returns only matching rows
    if owned:
        sql_conditions.append("u.owned = 1")
    if wishlisted:
        sql_conditions.append("u.wishlisted = 1")
    if sold_view:
        sql_conditions.append("u.sold = 1")
    if category != "all" and not owned and not wishlisted and not sold_view:
        sql_conditions.append("p.category = ?")
        sql_params.append(category)
    if panels != "all" and not owned and not wishlisted and not sold_view:
        sql_conditions.append("p.panels = ?")
        sql_params.append(panels)
    if search:
        sql_conditions.append("LOWER(p.title) LIKE ?")
        sql_params.append(f"%{search}%")

    where_clause = ("WHERE " + " AND ".join(sql_conditions)) if sql_conditions else "WHERE 1=1"

    with db() as conn:
        rows = conn.execute(f"""
            SELECT p.id, p.title, p.category, p.image_url, p.product_type,
                p.panels, p.first_seen,
                COUNT(v.id)      AS variant_count,
                SUM(v.available) AS variants_available,
                SUM(CASE WHEN v.title LIKE 'X-Large%' AND v.title NOT LIKE 'XX-Large%' THEN v.available ELSE 0 END) AS xl_available,
                SUM(CASE WHEN v.title LIKE 'XX-Large%' THEN v.available ELSE 0 END) AS xxl_available,
                MIN(v.price)     AS min_price,
                MAX(v.price)     AS max_price,
                COALESCE(u.owned,      0) AS owned,
                COALESCE(u.wishlisted, 0) AS wishlisted,
                COALESCE(u.sold,       0) AS sold,
                u.preferred_size          AS preferred_size,
                CASE WHEN EXISTS(
                    SELECT 1 FROM alerts a
                    WHERE a.product_id = p.id AND a.alert_type = 'new_product'
                    AND datetime(a.created_at) >= datetime('now', '-5 days')
                ) THEN 1 ELSE 0 END AS is_new_product,
                CASE WHEN EXISTS(
                    SELECT 1 FROM alerts a
                    WHERE a.product_id = p.id AND a.alert_type = 'back_in_stock'
                    AND datetime(a.created_at) >= datetime('now', '-1 day')
                ) THEN 1 ELSE 0 END AS is_restocked
            FROM products p
            LEFT JOIN variants   v ON v.product_id = p.id
            LEFT JOIN user_items u ON u.product_id = p.id
            {where_clause}
            GROUP BY p.id
        """, sql_params).fetchall()

    out = []
    for r in rows:
        p = dict(r)

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
        placeholders = ",".join("?" * len(wl_pids))
        with db() as conn2:
            oos_rows = conn2.execute(f"""
                SELECT v.product_id, MAX(a.created_at) AS last_oos_at
                FROM user_items u
                JOIN variants v ON v.product_id = u.product_id
                    AND v.title = u.preferred_size
                    AND v.available = 0
                JOIN alerts a ON a.variant_id = v.id AND a.alert_type = 'out_of_stock'
                WHERE u.wishlisted = 1
                    AND u.preferred_size IS NOT NULL AND u.preferred_size != ''
                    AND u.product_id IN ({placeholders})
                GROUP BY v.product_id
            """, wl_pids).fetchall()
        oos_map = {r["product_id"]: r["last_oos_at"] for r in oos_rows}
        now_ct = datetime.now(CT)
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
    }.get(sort, lambda x: (x["title"] or "").lower())
    out.sort(key=key, reverse=(sort in ("price_desc", "newest")))

    # For owned/wishlisted/sold views sort by category: caps, pins, apparel, other
    if owned or wishlisted or sold_view:
        out.sort(key=lambda x: CAT_SORT.get(x.get("category","other"), 3))

    resp = jsonify(out)
    resp.headers["Cache-Control"] = "private, no-store"
    return resp


@app.route("/api/product/<int:pid>")
def api_product(pid):
    with db() as conn:
        product = conn.execute("""
            SELECT p.*,
                COALESCE(u.owned,      0) AS owned,
                COALESCE(u.wishlisted, 0) AS wishlisted,
                COALESCE(u.sold,       0) AS sold,
                u.notes                   AS notes,
                u.preferred_size          AS preferred_size
            FROM products p
            LEFT JOIN user_items u ON u.product_id = p.id
            WHERE p.id = ?
        """, (pid,)).fetchone()
        if not product:
            return jsonify({"error": "not found"}), 404
        variants = conn.execute(
            "SELECT * FROM variants WHERE product_id=? ORDER BY price, title", (pid,)
        ).fetchall()
        history = {}
        for v in variants:
            rows = conn.execute(
                "SELECT price,available,checked_at FROM snapshots "
                "WHERE variant_id=? ORDER BY checked_at DESC LIMIT 100",
                (v["id"],),
            ).fetchall()
            # Keep only entries where price or availability changed
            rows = list(rows)
            changes = []
            prev = None
            for row in reversed(rows):
                if prev is None or row["price"] != prev["price"] or row["available"] != prev["available"]:
                    changes.append(dict(row))
                prev = row
            # Always include most recent entry
            if rows and (not changes or changes[-1]["checked_at"] != rows[0]["checked_at"]):
                changes.append(dict(rows[0]))
            history[v["id"]] = changes[-5:]  # last 5 changes
    import json
    product_dict = dict(product)
    try:
        product_dict['images'] = json.loads(product_dict.get('images_json') or '[]')
    except Exception:
        product_dict['images'] = [product_dict.get('image_url')] if product_dict.get('image_url') else []
    return jsonify({
        "product":  product_dict,
        "variants": [dict(v) for v in variants],
        "history":  history,
    })


@app.route("/api/user_item/<int:pid>", methods=["POST"])
def api_user_item(pid):
    data   = request.get_json()
    status = data.get("status", "none")
    owned      = 1 if status == "owned"      else 0
    wishlisted = 1 if status == "wishlisted" else 0
    sold       = 1 if status == "sold"       else 0
    notes          = (data.get("notes") or "").strip()
    preferred_size = (data.get("preferred_size") or "").strip() or None
    if not wishlisted:
        preferred_size = None
    from datetime import datetime
    now = datetime.now().isoformat()
    with db() as conn:
        conn.execute("""
            INSERT INTO user_items (product_id, owned, wishlisted, sold, notes, preferred_size, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(product_id) DO UPDATE SET
                owned=excluded.owned,
                wishlisted=excluded.wishlisted,
                sold=excluded.sold,
                notes=excluded.notes,
                preferred_size=excluded.preferred_size,
                updated_at=excluded.updated_at
        """, (pid, owned, wishlisted, sold, notes, preferred_size, now))
    return jsonify({"ok": True, "status": status, "notes": notes})


@app.route("/api/manual_caps")
def api_manual_caps():
    import json
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM manual_caps ORDER BY created_at DESC"
        ).fetchall()
    result = []
    for r in rows:
        cap = dict(r)
        images_json_val = cap.get('images_json')
        if images_json_val:
            try:
                cap['images'] = json.loads(images_json_val)
            except Exception:
                cap['images'] = [cap['image']] if cap.get('image') else []
        else:
            cap['images'] = [cap['image']] if cap.get('image') else []
        result.append(cap)
    return jsonify(result)


@app.route("/api/manual_cap/<int:mid>", methods=["GET"])
def api_manual_cap_get(mid):
    import json
    with db() as conn:
        row = conn.execute("SELECT * FROM manual_caps WHERE id=?", (mid,)).fetchone()
    if not row:
        return jsonify({"error": "not found"}), 404
    cap = dict(row)
    images_json_val = cap.get('images_json')
    if images_json_val:
        try:
            cap['images'] = json.loads(images_json_val)
        except Exception:
            cap['images'] = [cap['image']] if cap.get('image') else []
    else:
        cap['images'] = [cap['image']] if cap.get('image') else []
    return jsonify(cap)


@app.route("/api/manual_cap", methods=["POST"])
def api_manual_cap_create():
    import json
    data = request.get_json()
    if not data.get("name"):
        return jsonify({"error": "name is required"}), 400
    from datetime import datetime
    now = datetime.now().isoformat()
    images = [i for i in (data.get("images") or []) if i]
    images_json_str = json.dumps(images)
    first_image = images[0] if images else None
    with db() as conn:
        cur = conn.execute("""
            INSERT INTO manual_caps (name, color, style, material, notes, image, images_json, wishlisted, cap_type, panels, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            data.get("name", "").strip(),
            data.get("color",    "").strip(),
            data.get("style",    "").strip(),
            data.get("material", "").strip(),
            data.get("notes",    "").strip(),
            first_image,
            images_json_str,
            int(bool(data.get("wishlisted", False))),
            data.get("cap_type", "other").strip() or "other",
            data.get("panels",   None),
            now,
        ))
        new_id = cur.lastrowid
    return jsonify({"ok": True, "id": new_id})


@app.route("/api/manual_cap/<int:mid>", methods=["POST"])
def api_manual_cap_update(mid):
    data = request.get_json()
    with db() as conn:
        status = data.get("status")
        if status == "owned":
            conn.execute("UPDATE manual_caps SET sold=0, wishlisted=0 WHERE id=?", (mid,))
            return jsonify({"ok": True, "status": "owned"})
        if status == "wishlisted":
            conn.execute("UPDATE manual_caps SET sold=0, wishlisted=1 WHERE id=?", (mid,))
            return jsonify({"ok": True, "status": "wishlisted"})
        if status == "sold":
            conn.execute("UPDATE manual_caps SET sold=1, wishlisted=0 WHERE id=?", (mid,))
            return jsonify({"ok": True, "status": "sold"})
        # legacy field-level updates
        if "wishlisted" in data:
            wishlisted = int(bool(data["wishlisted"]))
            conn.execute("UPDATE manual_caps SET wishlisted=? WHERE id=?", (wishlisted, mid))
            return jsonify({"ok": True, "wishlisted": wishlisted})
        sold = int(bool(data.get("sold", False)))
        conn.execute("UPDATE manual_caps SET sold=? WHERE id=?", (sold, mid))
    return jsonify({"ok": True, "sold": sold})


@app.route("/api/manual_cap/<int:mid>/edit", methods=["POST"])
def api_manual_cap_edit(mid):
    import json
    data = request.get_json()
    if not data.get("name"):
        return jsonify({"error": "name is required"}), 400
    images = [i for i in (data.get("images") or []) if i]
    images_json_str = json.dumps(images)
    first_image = images[0] if images else None
    status = data.get("status", "owned")
    sold = 1 if status == "sold" else 0
    wishlisted = 1 if status == "wishlisted" else 0
    with db() as conn:
        conn.execute("""
            UPDATE manual_caps SET name=?, color=?, style=?, material=?, notes=?, image=?, images_json=?, cap_type=?, panels=?, sold=?, wishlisted=?
            WHERE id=?
        """, (
            data.get("name","").strip(),
            data.get("color","").strip(),
            data.get("style","").strip(),
            data.get("material","").strip(),
            data.get("notes","").strip(),
            first_image,
            images_json_str,
            data.get("cap_type", "other").strip() or "other",
            data.get("panels", None),
            sold, wishlisted,
            mid,
        ))
    return jsonify({"ok": True})


@app.route("/api/manual_cap/<int:mid>", methods=["DELETE"])
def api_manual_cap_delete(mid):
    with db() as conn:
        conn.execute("DELETE FROM manual_caps WHERE id=?", (mid,))
    return jsonify({"ok": True})


@app.route("/api/stats")
def api_stats():
    today = datetime.now(CT).strftime("%Y-%m-%d")
    with db() as conn:
        # All product/user counts in one pass
        counts = conn.execute("""
            SELECT
                COUNT(DISTINCT p.id) AS total,
                SUM(CASE WHEN p.category='caps'    THEN 1 ELSE 0 END) AS caps,
                SUM(CASE WHEN p.category='pins'    THEN 1 ELSE 0 END) AS pins,
                SUM(CASE WHEN p.category='apparel' THEN 1 ELSE 0 END) AS apparel,
                SUM(CASE WHEN EXISTS(
                    SELECT 1 FROM variants v WHERE v.product_id=p.id AND v.available=1
                ) THEN 1 ELSE 0 END) AS in_stock,
                SUM(CASE WHEN NOT EXISTS(
                    SELECT 1 FROM variants v WHERE v.product_id=p.id AND v.available=1
                ) THEN 1 ELSE 0 END) AS out_of_stock,
                SUM(CASE WHEN u.owned=1      AND p.category='caps' THEN 1 ELSE 0 END) AS owned,
                SUM(CASE WHEN u.wishlisted=1 AND p.category='caps' THEN 1 ELSE 0 END) AS wishlisted
            FROM products p
            LEFT JOIN user_items u ON u.product_id = p.id
        """).fetchone()
        # Time-sensitive scalars in one query
        misc = conn.execute("""
            SELECT
                (SELECT COUNT(*) FROM alerts
                 WHERE date(created_at, 'localtime') = ?) AS alerts_today,
                (SELECT MAX(checked_at) FROM snapshots)   AS last_poll,
                (SELECT COUNT(DISTINCT a.product_id) FROM alerts a
                 JOIN user_items u ON u.product_id = a.product_id
                 WHERE a.alert_type='back_in_stock' AND u.wishlisted=1
                 AND datetime(a.created_at) >= datetime('now', '-7 days')
                 AND EXISTS(
                     SELECT 1 FROM variants v
                     WHERE v.product_id = a.product_id AND v.available=1
                 )
                ) AS wishlisted_restocks
        """, (today,)).fetchone()
    resp = jsonify({
        "total":               counts["total"],
        "caps":                counts["caps"],
        "pins":                counts["pins"],
        "apparel":             counts["apparel"],
        "in_stock":            counts["in_stock"],
        "out_of_stock":        counts["out_of_stock"],
        "owned":               counts["owned"],
        "wishlisted":          counts["wishlisted"],
        "alerts_today":        misc["alerts_today"],
        "last_poll":           misc["last_poll"],
        "wishlisted_restocks": misc["wishlisted_restocks"],
    })
    resp.headers["Cache-Control"] = "private, max-age=55"
    return resp


@app.route("/api/alerts")
def api_alerts():
    limit = min(int(request.args.get("limit", 100)), 500)
    with db() as conn:
        rows = conn.execute("""
            SELECT a.*, p.title AS product_title, p.handle, p.category, v.title AS variant_title
            FROM alerts a
            LEFT JOIN products p ON p.id = a.product_id
            LEFT JOIN variants v ON v.id = a.variant_id
            ORDER BY a.created_at DESC LIMIT ?
        """, (limit,)).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/api/poll", methods=["POST"])
def api_poll():
    poller = os.path.join(os.path.dirname(os.path.abspath(__file__)), "poller.py")
    subprocess.Popen([sys.executable, poller])
    return jsonify({"ok": True})


@app.route("/api/release", methods=["POST"])
def api_release_create():
    data = request.get_json()
    name = (data.get("name") or "").strip()
    release_date = (data.get("release_date") or "").strip()
    if not name or not release_date:
        return jsonify({"error": "name and release_date are required"}), 400
    is_limited = 1 if data.get("is_limited") else 0
    notes = (data.get("notes") or "").strip() or None
    try:
        with db() as conn:
            cur = conn.execute(
                "INSERT INTO releases (name, release_date, is_limited, notes) VALUES (?, ?, ?, ?)",
                (name, release_date, is_limited, notes)
            )
            return jsonify({"ok": True, "id": cur.lastrowid})
    except Exception as e:
        return jsonify({"error": str(e)}), 409


@app.route("/api/release/<int:rid>", methods=["PUT"])
def api_release_update(rid):
    data = request.get_json()
    name = (data.get("name") or "").strip()
    release_date = (data.get("release_date") or "").strip()
    if not name or not release_date:
        return jsonify({"error": "name and release_date are required"}), 400
    is_limited = 1 if data.get("is_limited") else 0
    notes = (data.get("notes") or "").strip() or None
    try:
        with db() as conn:
            conn.execute(
                "UPDATE releases SET name=?, release_date=?, is_limited=?, notes=? WHERE id=?",
                (name, release_date, is_limited, notes, rid)
            )
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 409


@app.route("/api/release/<int:rid>", methods=["DELETE"])
def api_release_delete(rid):
    with db() as conn:
        conn.execute("DELETE FROM releases WHERE id=?", (rid,))
    return jsonify({"ok": True})


@app.route("/api/releases")
def api_releases():
    from datetime import datetime
    search     = request.args.get("search",  "").strip().lower()
    limited    = request.args.get("limited", "all")   # all|yes|no
    year       = request.args.get("year",    "all")
    month      = request.args.get("month",   "all")   # all|1-12
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

    with db() as conn:
        rows = conn.execute("SELECT * FROM releases").fetchall()

    out = []
    for r in rows:
        rec = dict(r)
        pd = parse_date(rec["release_date"])
        if search  and search not in rec["name"].lower():  continue
        if limited == "yes" and not rec["is_limited"]:     continue
        if limited == "no"  and     rec["is_limited"]:     continue
        if year_set:
            if pd.year not in year_set: continue
        elif year != "all" and str(pd.year) != year:
            continue
        if month_set:
            if pd.month not in month_set: continue
        elif month != "all" and str(pd.month) != month:
            continue
        out.append(rec)

    out.sort(key=lambda r: parse_date(r["release_date"]), reverse=True)
    return jsonify(out)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001, debug=False)
