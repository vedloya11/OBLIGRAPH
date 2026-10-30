import re
from urllib.parse import urlparse
from flask import Blueprint, render_template, request, redirect, url_for, flash, session
from flask_login import login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash
from app.database import get_db
from app.models import create_user, get_user_by_email, limiter

auth_bp = Blueprint("auth", __name__)

EMAIL_REGEX = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")


def is_valid_email(email: str) -> bool:
    return bool(email and EMAIL_REGEX.match(email.strip()))


def _is_safe_next_url(target: str) -> bool:
    if not target:
        return False
    parsed = urlparse(target)
    return parsed.scheme == "" and parsed.netloc == "" and target.startswith("/")


@auth_bp.route("/signup", methods=["GET", "POST"])
@limiter.limit("10 per minute")
def signup():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))

    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""
        confirm_password = request.form.get("confirm_password")

        if not name:
            flash("Please enter your full name.", "danger")
            return render_template("auth/signup.html", name=name, email=email), 400

        if not is_valid_email(email):
            flash("Please enter a valid email address.", "danger")
            return render_template("auth/signup.html", name=name, email=email), 400

        if len(password) < 8:
            flash("Password must be at least 8 characters long.", "danger")
            return render_template("auth/signup.html", name=name, email=email), 400

        if confirm_password is not None and confirm_password != "" and password != confirm_password:
            flash("Passwords do not match.", "danger")
            return render_template("auth/signup.html", name=name, email=email), 400

        existing = get_user_by_email(email)
        if existing:
            flash("An account with that email address already exists. Please log in instead.", "danger")
            return render_template("auth/signup.html", name=name, email=email), 400

        user = create_user(name=name, email=email, password=password)
        session.clear()
        login_user(user)
        flash(f"Welcome to OBLIGRAPH, {user.name}! Your isolated compliance workspace is ready.", "success")
        return redirect(url_for("main.dashboard"))

    return render_template("auth/signup.html")


@auth_bp.route("/login", methods=["GET", "POST"])
@limiter.limit("10 per minute")
def login():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))

    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""
        remember = bool(request.form.get("remember"))

        if not email or not password:
            flash("Please enter both your email address and password.", "danger")
            return render_template("auth/login.html", email=email), 400

        user = get_user_by_email(email)
        if not user or not user.check_password(password):
            flash("Invalid email or password. Please check your credentials and try again.", "danger")
            return render_template("auth/login.html", email=email), 401

        session.clear()
        login_user(user, remember=remember)
        flash(f"Welcome back, {user.name}!", "success")

        next_url = request.args.get("next") or request.form.get("next")
        if _is_safe_next_url(next_url):
            return redirect(next_url)
        return redirect(url_for("main.dashboard"))

    return render_template("auth/login.html")


@auth_bp.route("/logout", methods=["GET", "POST"])
def logout():
    logout_user()
    session.clear()
    flash("You have been signed out.", "info")
    return redirect(url_for("main.landing"))


@auth_bp.route("/account", methods=["GET", "POST"])
@login_required
def account():
    db = get_db()
    if request.method == "POST":
        action = request.form.get("action", "profile")

        if action == "password" or (request.form.get("new_password") and not request.form.get("name")):
            current_pw = request.form.get("current_password") or ""
            new_pw = request.form.get("new_password") or ""
            confirm_pw = request.form.get("confirm_password")

            if not current_user.check_password(current_pw):
                flash("Current password is incorrect.", "danger")
                return render_template("auth/account.html"), 400

            if len(new_pw) < 8:
                flash("New password must be at least 8 characters long.", "danger")
                return render_template("auth/account.html"), 400

            if confirm_pw is not None and confirm_pw != "" and new_pw != confirm_pw:
                flash("New passwords do not match.", "danger")
                return render_template("auth/account.html"), 400

            pw_hash = generate_password_hash(new_pw)
            db.execute("UPDATE users SET password_hash = ? WHERE id = ?", (pw_hash, current_user.id))
            db.commit()
            flash("Your password has been updated successfully.", "success")
            return redirect(url_for("auth.account"))

        else:
            new_name = (request.form.get("name") or "").strip()
            if not new_name:
                flash("Display name cannot be empty.", "danger")
                return render_template("auth/account.html"), 400

            db.execute("UPDATE users SET name = ? WHERE id = ?", (new_name, current_user.id))
            # Also support changing password in the same form if provided
            new_pw = request.form.get("new_password") or ""
            if new_pw:
                current_pw = request.form.get("current_password") or ""
                if current_pw and not current_user.check_password(current_pw):
                    flash("Current password is incorrect.", "danger")
                    return render_template("auth/account.html"), 400
                if len(new_pw) < 8:
                    flash("New password must be at least 8 characters long.", "danger")
                    return render_template("auth/account.html"), 400
                pw_hash = generate_password_hash(new_pw)
                db.execute("UPDATE users SET password_hash = ? WHERE id = ?", (pw_hash, current_user.id))

            db.commit()
            current_user.name = new_name
            flash("Account settings updated successfully.", "success")
            return redirect(url_for("auth.account"))

    return render_template("auth/account.html")

