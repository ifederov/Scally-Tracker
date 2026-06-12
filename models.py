from datetime import datetime
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin

db = SQLAlchemy()


class Product(db.Model):
    __tablename__ = "products"

    id           = db.Column(db.BigInteger, primary_key=True)  # Shopify product id (not autoincrement)
    title        = db.Column(db.Text, nullable=False)
    handle       = db.Column(db.Text, nullable=False)
    product_type = db.Column(db.Text)
    category     = db.Column(db.Text)
    image_url    = db.Column(db.Text)
    images_json  = db.Column(db.Text)
    vendor       = db.Column(db.Text)
    tags         = db.Column(db.Text)
    description  = db.Column(db.Text)
    style        = db.Column(db.Text)
    color        = db.Column(db.Text)
    material     = db.Column(db.Text)
    panels       = db.Column(db.Text)
    panels_override = db.Column(db.Text)
    first_seen   = db.Column(db.Text)
    updated_at   = db.Column(db.Text)

    variants = db.relationship("Variant", backref="product", lazy=True)


class Variant(db.Model):
    __tablename__ = "variants"

    id         = db.Column(db.BigInteger, primary_key=True)  # Shopify variant id
    product_id = db.Column(db.BigInteger, db.ForeignKey("products.id"), nullable=False)
    title      = db.Column(db.Text, nullable=False)
    price      = db.Column(db.Float, nullable=False)
    available  = db.Column(db.Integer, nullable=False, default=0)
    updated_at = db.Column(db.Text)


class Snapshot(db.Model):
    __tablename__ = "snapshots"

    id         = db.Column(db.Integer, primary_key=True, autoincrement=True)
    variant_id = db.Column(db.BigInteger, nullable=False)
    product_id = db.Column(db.BigInteger, nullable=False)
    price      = db.Column(db.Float, nullable=False)
    available  = db.Column(db.Integer, nullable=False)
    checked_at = db.Column(db.Text, nullable=False)


class Alert(db.Model):
    __tablename__ = "alerts"

    id         = db.Column(db.Integer, primary_key=True, autoincrement=True)
    product_id = db.Column(db.BigInteger)
    variant_id = db.Column(db.BigInteger)
    alert_type = db.Column(db.Text, nullable=False)
    message    = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.Text, nullable=False)


class UserItem(db.Model):
    __tablename__ = "user_items"

    product_id     = db.Column(db.BigInteger, db.ForeignKey("products.id"), primary_key=True)
    user_id        = db.Column(db.Integer, db.ForeignKey("users.id"), primary_key=True)
    owned          = db.Column(db.Integer, nullable=False, default=0)
    wishlisted     = db.Column(db.Integer, nullable=False, default=0)
    sold           = db.Column(db.Integer, nullable=False, default=0)
    notes          = db.Column(db.Text)
    preferred_size = db.Column(db.Text)
    sold_price     = db.Column(db.Float)
    sold_date      = db.Column(db.Text)
    updated_at     = db.Column(db.Text)


class ManualCap(db.Model):
    __tablename__ = "manual_caps"

    id          = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id     = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    name        = db.Column(db.Text, nullable=False)
    color       = db.Column(db.Text)
    style       = db.Column(db.Text)
    material    = db.Column(db.Text)
    notes       = db.Column(db.Text)
    image       = db.Column(db.Text)
    images_json = db.Column(db.Text)
    sold        = db.Column(db.Integer, nullable=False, default=0)
    wishlisted  = db.Column(db.Integer, nullable=False, default=0)
    cap_type    = db.Column(db.Text, nullable=False, default="none")
    panels      = db.Column(db.Text)
    sold_price  = db.Column(db.Float)
    sold_date   = db.Column(db.Text)
    created_at  = db.Column(db.Text)


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id            = db.Column(db.Integer, primary_key=True, autoincrement=True)
    username      = db.Column(db.Text, unique=True, nullable=False)
    email         = db.Column(db.Text, unique=True, nullable=False)
    password_hash = db.Column(db.Text, nullable=False)
    is_admin      = db.Column(db.Integer, nullable=False, default=0)
    created_at    = db.Column(db.Text)
    last_login    = db.Column(db.Text)
    ntfy_topic      = db.Column(db.Text)
    notify_wishlist = db.Column(db.Integer, nullable=False, default=0)
    notify_new_products = db.Column(db.Integer, nullable=False, default=0)
    quiet_hours_start   = db.Column(db.Integer)  # 0-23, CT, inclusive start
    quiet_hours_end     = db.Column(db.Integer)  # 0-23, CT, exclusive end
    default_size_caps    = db.Column(db.Text)
    default_size_apparel = db.Column(db.Text)


class Release(db.Model):
    __tablename__ = "releases"

    id           = db.Column(db.Integer, primary_key=True)
    name         = db.Column(db.Text, nullable=False)
    release_date = db.Column(db.Text, nullable=False)
    is_limited   = db.Column(db.Integer, nullable=False, default=0)
    notes        = db.Column(db.Text)

    __table_args__ = (db.UniqueConstraint("name", "release_date"),)
