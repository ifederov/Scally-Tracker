#!/usr/bin/env python3
"""
One-off verification script: hits every route via Flask's test client
against the Postgres-backed app and prints status codes + a quick shape
check. Run this after the SQLAlchemy/Postgres rewrite to confirm parity
before switching the live launchd services over.

Usage:
    python3 verify_routes.py
"""

from app import app

def check(client, method, path, **kwargs):
    resp = client.open(path, method=method, **kwargs)
    print(f"{method:6} {path:45} -> {resp.status_code}")
    return resp

def main():
    client = app.test_client()

    r = check(client, "GET", "/")
    print(f"  HTML length: {len(r.data)}")

    r = check(client, "GET", "/api/products")
    data = r.get_json()
    print(f"  products returned: {len(data)}")
    if data:
        sample = data[0]
        print(f"  sample keys: {sorted(sample.keys())}")
        pid = sample["id"]
    else:
        pid = None

    check(client, "GET", "/api/products?category=caps")
    check(client, "GET", "/api/products?category=caps&panels=single")
    check(client, "GET", "/api/products?avail=in")
    check(client, "GET", "/api/products?avail=out")
    check(client, "GET", "/api/products?search=cap")
    check(client, "GET", "/api/products?sort=price_asc")
    check(client, "GET", "/api/products?sort=price_desc")
    check(client, "GET", "/api/products?sort=newest")
    check(client, "GET", "/api/products?owned=1")
    check(client, "GET", "/api/products?wishlisted=1")
    check(client, "GET", "/api/products?sold=1")

    if pid:
        r = check(client, "GET", f"/api/product/{pid}")
        d = r.get_json()
        print(f"  product keys: {sorted(d['product'].keys())}")
        print(f"  variants: {len(d['variants'])}, history entries: {len(d['history'])}")

        r = check(client, "POST", f"/api/user_item/{pid}", json={"status": "wishlisted", "notes": "test", "preferred_size": "Large"})
        print(f"  -> {r.get_json()}")
        # revert
        r = check(client, "POST", f"/api/user_item/{pid}", json={"status": "none", "notes": ""})
        print(f"  -> {r.get_json()}")

    check(client, "GET", "/api/manual_caps")

    r = check(client, "POST", "/api/manual_cap", json={"name": "__verify_test__", "color": "black"})
    new_id = r.get_json().get("id")
    print(f"  created manual_cap id={new_id}")

    if new_id:
        check(client, "GET", f"/api/manual_cap/{new_id}")
        check(client, "POST", f"/api/manual_cap/{new_id}", json={"status": "wishlisted"})
        check(client, "POST", f"/api/manual_cap/{new_id}/edit", json={"name": "__verify_test__ edited", "status": "owned"})
        check(client, "DELETE", f"/api/manual_cap/{new_id}")

    r = check(client, "GET", "/api/stats")
    print(f"  stats: {r.get_json()}")

    r = check(client, "GET", "/api/alerts")
    print(f"  alerts returned: {len(r.get_json())}")

    r = check(client, "GET", "/api/releases")
    releases = r.get_json()
    print(f"  releases returned: {len(releases)}")

    check(client, "GET", "/api/releases?limited=yes")
    check(client, "GET", "/api/releases?years=2025,2026")
    check(client, "GET", "/api/releases?search=cap")

    r = check(client, "POST", "/api/release", json={"name": "__verify_release__", "release_date": "1/1/26"})
    rel = r.get_json()
    print(f"  -> {rel}")
    rid = rel.get("id")
    if rid:
        check(client, "PUT", f"/api/release/{rid}", json={"name": "__verify_release__ edited", "release_date": "1/2/26"})
        check(client, "DELETE", f"/api/release/{rid}")

    print("\nDone. Review status codes above — all should be 200 (or 404 only where expected).")

if __name__ == "__main__":
    main()
