from functools import wraps

from flask import render_template, redirect, url_for, flash, abort, jsonify, request
from flask_login import login_required, current_user

from . import admin_bp
from models import db, User, Product, ManualCap, UserItem, Alert, Feedback


def admin_required(f):
    @wraps(f)
    @login_required
    def wrapper(*args, **kwargs):
        if not current_user.is_admin:
            abort(403)
        return f(*args, **kwargs)
    return wrapper


@admin_bp.route("/")
@admin_required
def dashboard():
    total_users = User.query.count()
    total_caps = Product.query.filter_by(category="caps").count() + ManualCap.query.count()
    total_owned = (
        UserItem.query.join(Product, Product.id == UserItem.product_id)
        .filter(UserItem.owned == 1, Product.category == "caps").count()
        + ManualCap.query.filter_by(wishlisted=0, sold=0).count()
    )
    total_wishlisted = (
        UserItem.query.join(Product, Product.id == UserItem.product_id)
        .filter(UserItem.wishlisted == 1, Product.category == "caps").count()
        + ManualCap.query.filter_by(wishlisted=1, sold=0).count()
    )

    feedback = (
        Feedback.query
        .order_by(Feedback.status.asc(), Feedback.created_at.desc())
        .all()
    )
    users_by_id = {u.id: u.username for u in User.query.all()}

    return render_template(
        "admin.html",
        total_users=total_users,
        total_caps=total_caps,
        total_owned=total_owned,
        total_wishlisted=total_wishlisted,
        activity=_recent_activity(limit=15),
        users=User.query.order_by(User.created_at.desc()).all(),
        feedback=feedback,
        users_by_id=users_by_id,
    )


@admin_bp.route("/activity")
@admin_required
def activity():
    return render_template("admin_activity.html", activity=_recent_activity(limit=100))


@admin_bp.route("/user/<int:uid>/delete", methods=["POST"])
@admin_required
def delete_user(uid):
    if uid == current_user.id:
        flash("You can't delete your own account.", "error")
        return redirect(url_for("admin.dashboard"))

    user = User.query.get_or_404(uid)
    UserItem.query.filter_by(user_id=uid).delete()
    ManualCap.query.filter_by(user_id=uid).delete()
    db.session.delete(user)
    db.session.commit()
    flash(f"Deleted user “{user.username}” and all their data.", "success")
    return redirect(url_for("admin.dashboard"))


@admin_bp.route("/feedback/<int:fid>/status", methods=["POST"])
@admin_required
def update_feedback_status(fid):
    fb = Feedback.query.get_or_404(fid)
    data = request.get_json()
    new_status = data.get("status")
    if new_status not in ("open", "in_progress", "closed"):
        return jsonify({"error": "invalid status"}), 400
    fb.status = new_status
    db.session.commit()
    return jsonify({"ok": True, "status": new_status})


@admin_bp.route("/feedback/<int:fid>/notes", methods=["POST"])
@admin_required
def update_feedback_notes(fid):
    fb = Feedback.query.get_or_404(fid)
    data = request.get_json()
    fb.admin_notes = (data.get("notes") or "").strip() or None
    db.session.commit()
    return jsonify({"ok": True})


def _recent_activity(limit=100):
    """Best-effort feed derived from existing timestamps (no dedicated audit log)."""
    events = []
    users_by_id = {u.id: u.username for u in User.query.all()}
    products_by_id = {p.id: p.title for p in Product.query.all()}

    for ui in UserItem.query.filter(UserItem.updated_at.isnot(None)).all():
        actions = []
        if ui.owned: actions.append("marked owned")
        if ui.wishlisted: actions.append("wishlisted")
        if ui.sold: actions.append("marked sold")
        if not actions: actions.append("updated")
        events.append({
            "ts": ui.updated_at,
            "username": users_by_id.get(ui.user_id, f"user#{ui.user_id}"),
            "description": f"{', '.join(actions)} “{products_by_id.get(ui.product_id, 'unknown product')}”",
        })

    for mc in ManualCap.query.filter(ManualCap.created_at.isnot(None)).all():
        events.append({
            "ts": mc.created_at,
            "username": users_by_id.get(mc.user_id, f"user#{mc.user_id}"),
            "description": f"added manual cap “{mc.name}”",
        })

    for a in Alert.query.order_by(Alert.created_at.desc()).limit(limit).all():
        events.append({"ts": a.created_at, "username": "system", "description": a.message})

    events.sort(key=lambda e: e["ts"] or "", reverse=True)
    return events[:limit]
