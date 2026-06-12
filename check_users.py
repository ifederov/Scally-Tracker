#!/usr/bin/env python3
"""
One-off check: list rows in the users table.

Usage:
    python3 check_users.py
"""

from app import app
from models import User

with app.app_context():
    users = User.query.all()
    if not users:
        print("No users found.")
    for u in users:
        print(f"id={u.id}  username={u.username!r}  email={u.email!r}  "
              f"is_admin={u.is_admin}  created_at={u.created_at}  last_login={u.last_login}")
