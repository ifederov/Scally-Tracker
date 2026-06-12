from datetime import datetime
from zoneinfo import ZoneInfo

from flask import render_template, redirect, url_for, request, flash
from flask_login import login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash

from . import auth_bp
from models import db, User

CT = ZoneInfo("America/Chicago")


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("index"))

    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""

        user = User.query.filter_by(username=username).first()
        if not user or not check_password_hash(user.password_hash, password):
            flash("Invalid username or password", "error")
            return render_template("login.html")

        user.last_login = datetime.now(CT).isoformat()
        db.session.commit()
        login_user(user)
        return redirect(url_for("index"))

    return render_template("login.html")


@auth_bp.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("index"))

    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        email    = (request.form.get("email") or "").strip()
        password = request.form.get("password") or ""
        confirm  = request.form.get("confirm") or ""

        if not username or not email or not password:
            flash("All fields are required", "error")
            return render_template("register.html")
        if password != confirm:
            flash("Passwords do not match", "error")
            return render_template("register.html")
        if User.query.filter_by(username=username).first():
            flash("Username already taken", "error")
            return render_template("register.html")
        if User.query.filter_by(email=email).first():
            flash("Email already registered", "error")
            return render_template("register.html")

        user = User(
            username=username,
            email=email,
            password_hash=generate_password_hash(password),
            is_admin=0,
            created_at=datetime.now(CT).isoformat(),
            last_login=datetime.now(CT).isoformat(),
        )
        db.session.add(user)
        db.session.commit()
        login_user(user)
        return redirect(url_for("index"))

    return render_template("register.html")


@auth_bp.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("auth.login"))
