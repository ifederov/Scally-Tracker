import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://localhost/scally_tracker")
    SECRET_KEY   = os.environ.get("SECRET_KEY", "dev-key-change-me")
    SHOPIFY_URL  = os.environ.get("SHOPIFY_URL", "https://bostonscally.com")
    NTFY_TOPIC   = os.environ.get("NTFY_TOPIC", "")
    FLASK_ENV    = os.environ.get("FLASK_ENV", "production")
    DEBUG        = FLASK_ENV == "development"
