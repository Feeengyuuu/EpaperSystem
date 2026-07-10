"""Administrator setup, login, logout, and password rotation routes."""

from __future__ import annotations

from urllib.parse import urlsplit

from flask import (
    Blueprint,
    current_app,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from security.credentials import CredentialError, InvalidPassword, InvalidToken
from security.request_guard import (
    ADMIN_SESSION_KEY,
    current_admin_authenticated,
    ensure_csrf_token,
    rotate_csrf_token,
)


auth_bp = Blueprint("auth", __name__, url_prefix="/auth")


def _store():
    return current_app.config["CREDENTIAL_STORE"]


def _has_admin():
    store = _store()
    if not getattr(store, "available", True):
        return None
    try:
        return bool(store.has_admin())
    except CredentialError:
        return None


def _payload():
    if request.is_json:
        data = request.get_json(silent=True)
        return data if isinstance(data, dict) else {}
    return request.form.to_dict()


def _json_requested():
    if request.is_json:
        return True
    accepted = request.accept_mimetypes
    return accepted["application/json"] > accepted["text/html"]


def _error(message, code, status):
    if _json_requested():
        return jsonify({"success": False, "error": message, "error_code": code}), status
    templates = {
        "auth.setup": "setup_admin.html",
        "auth.recover": "recover_admin.html",
    }
    template = templates.get(request.endpoint, "login.html")
    return render_template(template, error=message), status


def _authenticate_session():
    session.clear()
    session.permanent = True
    session[ADMIN_SESSION_KEY] = "admin"
    rotate_csrf_token()


def _safe_next(value):
    if (
        not isinstance(value, str)
        or not value.startswith("/")
        or "\\" in value
        or any(ord(character) < 32 for character in value)
    ):
        return "/"
    parsed = urlsplit(value)
    if parsed.scheme or parsed.netloc or value.startswith("//"):
        return "/"
    return value


@auth_bp.route("/setup", methods=["GET", "POST"])
def setup():
    store = _store()
    has_admin = _has_admin()
    if has_admin is None:
        return _error(
            "Administrator credential storage is unavailable.",
            "credential_store_failed",
            503,
        )
    if request.method == "GET":
        if has_admin:
            return redirect(url_for("auth.login"))
        ensure_csrf_token()
        return render_template("setup_admin.html", error=None)

    if has_admin:
        return _error(
            "Administrator setup is already complete.",
            "setup_already_complete",
            409,
        )
    data = _payload()
    password = data.get("password", "")
    confirmation = data.get("confirm_password")
    if confirmation is not None and confirmation != password:
        return _error("Passwords do not match.", "password_mismatch", 400)
    try:
        store.consume_bootstrap_token(data.get("bootstrap_token", ""), password)
    except InvalidToken:
        return _error(
            "Pairing token is invalid or expired.",
            "invalid_bootstrap_token",
            401,
        )
    except InvalidPassword as error:
        return _error(str(error), "invalid_password", 400)
    except CredentialError:
        return _error(
            "Administrator setup could not be completed.",
            "credential_store_failed",
            500,
        )
    _authenticate_session()
    if _json_requested():
        return jsonify({"success": True, "authenticated": True}), 201
    return redirect("/")


@auth_bp.route("/recover", methods=["GET", "POST"])
def recover():
    store = _store()
    has_admin = _has_admin()
    if has_admin is None:
        return _error(
            "Administrator credential storage is unavailable.",
            "credential_store_failed",
            503,
        )
    if not has_admin:
        return redirect(url_for("auth.setup"))
    if request.method == "GET":
        ensure_csrf_token()
        return render_template("recover_admin.html", error=None)

    data = _payload()
    password = data.get("password", "")
    confirmation = data.get("confirm_password")
    if confirmation is not None and confirmation != password:
        return _error("Passwords do not match.", "password_mismatch", 400)
    try:
        store.consume_recovery_token(data.get("recovery_token", ""), password)
    except InvalidToken:
        return _error(
            "Recovery token is invalid or expired.",
            "invalid_recovery_token",
            401,
        )
    except InvalidPassword as error:
        return _error(str(error), "invalid_password", 400)
    except CredentialError:
        return _error(
            "Administrator recovery could not be completed.",
            "credential_store_failed",
            500,
        )
    _authenticate_session()
    if _json_requested():
        return jsonify({"success": True, "authenticated": True})
    return redirect("/")


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    store = _store()
    has_admin = _has_admin()
    if has_admin is None:
        return _error(
            "Administrator credential storage is unavailable.",
            "credential_store_failed",
            503,
        )
    if request.method == "GET":
        if not has_admin:
            return redirect(url_for("auth.setup"))
        if current_admin_authenticated():
            return redirect("/")
        ensure_csrf_token()
        return render_template("login.html", error=None)

    if not has_admin:
        return _error(
            "Administrator setup is required.",
            "setup_required",
            409,
        )
    data = _payload()
    if not store.verify_admin_password(data.get("password", "")):
        return _error(
            "Administrator password is incorrect.",
            "invalid_credentials",
            401,
        )
    next_url = _safe_next(data.get("next") or request.args.get("next"))
    _authenticate_session()
    if _json_requested():
        return jsonify({"success": True, "authenticated": True})
    return redirect(next_url)


@auth_bp.post("/logout")
def logout():
    session.clear()
    ensure_csrf_token()
    if _json_requested():
        return jsonify({"success": True, "authenticated": False})
    return redirect(url_for("auth.login"))


@auth_bp.post("/password")
def password():
    data = _payload()
    try:
        _store().rotate_admin_password(
            data.get("current_password", ""),
            data.get("new_password", ""),
        )
    except InvalidPassword as error:
        return _error(str(error), "invalid_password", 400)
    except CredentialError:
        return _error(
            "Administrator password could not be changed.",
            "credential_store_failed",
            500,
        )
    return jsonify({"success": True, "authenticated": True})


@auth_bp.get("/status")
def status():
    has_admin = _has_admin()
    return jsonify(
        {
            "authenticated": current_admin_authenticated(),
            "setup_required": has_admin is False,
            "credential_store_available": has_admin is not None,
            "csrf_token": ensure_csrf_token(),
        }
    )
