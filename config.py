import os
from datetime import timedelta
from dotenv import load_dotenv

load_dotenv()

class Config:
    DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://localhost/scally_tracker")
    SECRET_KEY   = os.environ.get("SECRET_KEY", "dev-key-change-me")
    SHOPIFY_URL  = os.environ.get("SHOPIFY_URL", "https://bostonscally.com")
    NTFY_TOPIC   = os.environ.get("NTFY_TOPIC", "")
    FLASK_ENV    = os.environ.get("FLASK_ENV", "production")
    DEBUG        = FLASK_ENV == "development"

    REMEMBER_COOKIE_DURATION  = timedelta(days=30)
    REMEMBER_COOKIE_HTTPONLY  = True
    REMEMBER_COOKIE_SAMESITE  = "Lax"
    REMEMBER_COOKIE_SECURE    = FLASK_ENV != "development"
