"""
One-off script to remove "Kilkenney Baker Boy Cap" from the database.

Usage:
    python3 remove_kilkenney.py            # dry run - shows what would be deleted
    python3 remove_kilkenney.py --confirm  # actually deletes

Checks both the `products` table (catalog items, with their variants/snapshots)
and `manual_caps` (user-added caps not from the BSC catalog).
"""
import sys
from app import app, db
from models import Product, Variant, Snapshot, ManualCap

CONFIRM = "--confirm" in sys.argv

with app.app_context():
    products = Product.query.filter(Product.title.ilike("%kilkenney%baker%")).all()
    if not products:
        products = Product.query.filter(Product.title.ilike("%kilkenney%")).all()

    manual = ManualCap.query.filter(ManualCap.name.ilike("%kilkenney%")).all()

    if not products and not manual:
        print("No matching items found in products or manual_caps.")
        sys.exit(0)

    for p in products:
        variant_count = Variant.query.filter_by(product_id=p.id).count()
        print(f"[products] id={p.id} title={p.title!r} category={p.category} panels={p.panels} variants={variant_count}")

    for m in manual:
        print(f"[manual_caps] id={m.id} name={m.name!r} panels={getattr(m, 'panels', None)}")

    if not CONFIRM:
        print("\nDry run only. Re-run with --confirm to delete the above.")
        sys.exit(0)

    for p in products:
        variant_ids = [v.id for v in Variant.query.filter_by(product_id=p.id).all()]
        if variant_ids:
            Snapshot.query.filter(Snapshot.variant_id.in_(variant_ids)).delete(synchronize_session=False)
            Variant.query.filter(Variant.id.in_(variant_ids)).delete(synchronize_session=False)
        db.session.delete(p)

    for m in manual:
        db.session.delete(m)

    db.session.commit()
    print("\nDeleted.")
