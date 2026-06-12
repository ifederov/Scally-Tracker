#!/usr/bin/env python3
"""
One-time data migration: copy data/inventory.db (SQLite) into Postgres
(DATABASE_URL from config.py / .env), preserving primary keys.

Usage:
    python3 migrate_data.py
"""

import sqlite3, os
from sqlalchemy import text
from app import app
from models import db, Product, Variant, Snapshot, Alert, UserItem, ManualCap, Release

SQLITE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "inventory.db")

# Order matters: products before variants (FK), etc.
TABLES = [
    ("products",    Product),
    ("variants",    Variant),
    ("snapshots",   Snapshot),
    ("alerts",      Alert),
    ("user_items",  UserItem),
    ("manual_caps", ManualCap),
    ("releases",    Release),
]


def main():
    sconn = sqlite3.connect(SQLITE_PATH)
    sconn.row_factory = sqlite3.Row

    with app.app_context():
        for table_name, model in TABLES:
            rows = [dict(r) for r in sconn.execute(f"SELECT * FROM {table_name}").fetchall()]
            if not rows:
                print(f"{table_name}: 0 rows, skipping")
                continue
            db.session.execute(model.__table__.insert(), rows)
            db.session.commit()
            print(f"{table_name}: inserted {len(rows)} rows")

        # Reset sequences for serial PK columns so future inserts don't collide
        for table_name, model in TABLES:
            pk_col = list(model.__table__.primary_key.columns)[0].name
            seq = db.session.execute(
                text("SELECT pg_get_serial_sequence(:t, :c)"),
                {"t": table_name, "c": pk_col},
            ).scalar()
            if seq:
                db.session.execute(
                    text(f"SELECT setval(:seq, COALESCE((SELECT MAX({pk_col}) FROM {table_name}), 1), true)"),
                    {"seq": seq},
                )
        db.session.commit()
        print("Sequences reset.")

    sconn.close()


if __name__ == "__main__":
    main()
