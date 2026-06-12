#!/usr/bin/env python3
"""
One-off script: mark a user as admin (is_admin=1).

Usage:
    python3 set_admin.py <username>
"""

import sys

from app import app
from models import db, User


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 set_admin.py <username>")
        sys.exit(1)

    username = sys.argv[1]

    with app.app_context():
        user = User.query.filter_by(username=username).first()
        if not user:
            print(f"No user found with username={username!r}")
            sys.exit(1)

        user.is_admin = 1
        db.session.commit()
        print(f"Updated: id={user.id} username={user.username!r} is_admin={user.is_admin}")


if __name__ == "__main__":
    main()
