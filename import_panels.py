#!/usr/bin/env python3
"""
Import panel corrections from a CSV file into the inventory database.

Usage:
    python3 import_panels.py data/panels_undetected.csv
    python3 import_panels.py data/panels_8panel.csv

CSV format: id, title, panels
- Rows with an empty panels column are skipped.
- Rows with a panels value update both `panels` and `panels_override`
  so the poller never overwrites the correction.
"""

import csv, os, sys

from app import app
from models import db, Product

VALID_PANELS = {"single", "5", "6", "8", "baker"}
ALIASES      = {"1": "single", "bb": "baker"}  # normalize shorthand values

def main():
    if len(sys.argv) < 2:
        print("Usage: python3 import_panels.py <csv_file>")
        sys.exit(1)

    csv_file = sys.argv[1]
    if not os.path.exists(csv_file):
        print(f"File not found: {csv_file}")
        sys.exit(1)

    updated, skipped, invalid = 0, 0, []

    with app.app_context():
        with open(csv_file, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                pid    = row.get("id", "").strip()
                panels = row.get("panels", "").strip().lower()

                if not panels:
                    skipped += 1
                    continue

                panels = ALIASES.get(panels, panels)

                if panels not in VALID_PANELS:
                    invalid.append(f"  ID {pid}: '{panels}' — must be one of {sorted(VALID_PANELS)}")
                    continue

                db.session.query(Product).filter(Product.id == int(pid)).update(
                    {"panels": panels, "panels_override": panels}
                )
                updated += 1

        db.session.commit()

    print(f"\nDone.")
    print(f"  Updated : {updated}")
    print(f"  Skipped (blank): {skipped}")
    if invalid:
        print(f"  Invalid values ({len(invalid)}):")
        for msg in invalid:
            print(msg)

if __name__ == "__main__":
    main()
