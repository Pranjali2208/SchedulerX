"""Authentication for the Re-Exam Scheduler.

Registration, sign in, logout, change password and token-based password
recovery.  Accounts are stored in the same JSON files the project already used
(``instance/admin_credentials.json`` and ``instance/registered_users.json``);
no recovery answer is stored or required any more.

Security model
--------------
* Passwords are hashed with Werkzeug (scrypt by default, existing hashes of any
  Werkzeug method keep working).
* Sessions are signed cookies *plus* a server-side registry
  (``instance/active_sessions.json``) so that logout, a password change or a
  password reset really invalidates a session.
* Every state-changing request needs an ``X-CSRF-Token`` header.
* Password reset uses random, single-use, time-limited tokens.  Only a SHA-256
  hash of the token is stored.  Tokens are delivered by e-mail (SMTP settings
  come from environment variables) or, for the server operator, by
  ``python manage.py reset-link``.
* Sign in, registration and recovery endpoints are rate limited (in-process).
"""
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import smtplib
import ssl
import threading
import time
from datetime import datetime
from email.message import EmailMessage
from functools import wraps

from flask import Blueprint, current_app, jsonify, request, session
from werkzeug.security import check_password_hash, generate_password_hash

log = logging.getLogger("reexam.auth")
auth_bp = Blueprint("auth", __name__)

DEFAULT_ADMIN_USERNAME = "admin"
DEFAULT_ADMIN_PASSWORD = "admin"
SESSION_SECONDS = 30 * 60
MAX_PASSWORD_LENGTH = 128
MAX_FIELD_LENGTH = 254
EMAIL_RE = re.compile(r"[^\s@]+@[^\s@]+\.[^\s@]+")
PASSWORD_RULE_MESSAGE = ("Password must be 8–128 characters and include uppercase, "
                         "lowercase, and a number.")
GENERIC_LOGIN_ERROR = "Invalid email/username or password."
GENERIC_SERVER_ERROR = "Something went wrong on the server. Please try again."
INVALID_TOKEN_ERROR = "This password reset link is invalid or has expired. Please request a new one."
FORGOT_MESSAGE = ("If an account exists for that address, a password reset link has been "
                  "sent. The link is valid for a limited time and can be used once.")
NO_EMAIL_MESSAGE = ("Email delivery is not configured on this server, so reset links cannot "
                    "be emailed. Please ask the administrator to generate a one-time reset "
                    "link from the server (see README).")

_LOCK = threading.RLock()
_DUMMY_HASH = generate_password_hash(secrets.token_hex(16))  # equalises timing for unknown accounts


class AuthStoreError(Exception):
    """An authentication data file is unreadable or malformed."""


# =========================================================
# CONFIGURATION / SMALL HELPERS
# =========================================================

def _cfg(key):
    return current_app.config[key]


def _env_flag(name):
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def reset_token_minutes():
    try:
        return max(5, min(24 * 60, int(os.environ.get("RESET_TOKEN_MINUTES", "30"))))
    except ValueError:
        return 30


def _now():
    return time.time()


def _sha256(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _fingerprint(password_hash):
    """Short value that changes whenever the stored password changes."""
    return _sha256(str(password_hash))[:20]


def _safe_equal(a, b):
    return hmac.compare_digest(str(a).encode("utf-8"), str(b).encode("utf-8"))


def _read_json(path, default, strict=True):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError) as exc:
        if strict:
            raise AuthStoreError(f"Unreadable data file: {os.path.basename(path)}") from exc
        log.error("Ignoring unreadable data file %s", os.path.basename(path))
        return default


def _write_json(path, data):
    temp = f"{path}.{secrets.token_hex(4)}.tmp"
    with open(temp, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, ensure_ascii=False)
    try:
        os.chmod(temp, 0o600)
    except OSError:
        pass
    os.replace(temp, path)


def _payload():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        data = request.form.to_dict() if request.form else {}
    return data


def _field(data, key, max_length=MAX_FIELD_LENGTH + 1):
    value = data.get(key, "")
    return value[:max_length] if isinstance(value, str) else ""


def password_problem(password):
    if (not isinstance(password, str) or not 8 <= len(password) <= MAX_PASSWORD_LENGTH
            or not re.search(r"[a-z]", password) or not re.search(r"[A-Z]", password)
            or not re.search(r"\d", password)):
        return PASSWORD_RULE_MESSAGE
    return None


def _client_ip():
    return request.remote_addr or "unknown"


# =========================================================
# RATE LIMITING (in-process; use a shared store if you run several workers)
# =========================================================

class _RateLimiter:
    def __init__(self):
        self._hits = {}
        self._lock = threading.Lock()

    def _recent(self, key, window):
        cutoff = _now() - window
        hits = [t for t in self._hits.get(key, []) if t > cutoff]
        if hits:
            self._hits[key] = hits
        else:
            self._hits.pop(key, None)
        return hits

    def blocked(self, key, limit, window):
        with self._lock:
            return len(self._recent(key, window)) >= limit

    def hit(self, key, window):
        with self._lock:
            hits = self._recent(key, window)
            hits.append(_now())
            self._hits[key] = hits

    def clear(self, key):
        with self._lock:
            self._hits.pop(key, None)

    def reset(self):
        with self._lock:
            self._hits.clear()


limiter = _RateLimiter()


def _too_many():
    response = jsonify({"success": False, "error": "Too many attempts. Please wait a few minutes and try again."})
    response.status_code = 429
    response.headers["Retry-After"] = "300"
    return response


def _throttle(key, limit, window):
    """Count one attempt; return a 429 response when the limit is exceeded."""
    if limiter.blocked(key, limit, window):
        return _too_many()
    limiter.hit(key, window)
    return None


# =========================================================
# ACCOUNT STORES
# =========================================================

def load_credentials():
    """The administrator account (created on first start)."""
    path = _cfg("AUTH_FILE")
    with _LOCK:
        if not os.path.exists(path):
            username = os.environ.get("ADMIN_USERNAME", "").strip() or DEFAULT_ADMIN_USERNAME
            password = os.environ.get("ADMIN_PASSWORD", "") or DEFAULT_ADMIN_PASSWORD
            if password == DEFAULT_ADMIN_PASSWORD:
                log.warning("Administrator created with the default password. Change it after first login "
                            "or set ADMIN_PASSWORD before the first start.")
            data = {"username": username, "password_hash": generate_password_hash(password)}
            save_credentials(data)
            return data
        data = _read_json(path, None)
        if (not isinstance(data, dict) or not data.get("username") or not data.get("password_hash")):
            raise AuthStoreError("Administrator credentials file is malformed")
        return data


def save_credentials(data):
    with _LOCK:
        _write_json(_cfg("AUTH_FILE"), data)


def load_registered_users():
    data = _read_json(_cfg("REGISTERED_USERS_FILE"), [])
    if not isinstance(data, list):
        raise AuthStoreError("Registered users file is malformed")
    return data


def save_registered_users(users):
    with _LOCK:
        _write_json(_cfg("REGISTERED_USERS_FILE"), users)


def find_user(email):
    email = str(email).strip().lower()
    return next((u for u in load_registered_users()
                 if isinstance(u, dict) and str(u.get("email", "")).lower() == email), None)


def migrate_legacy_fields():
    """Remove obsolete recovery-answer data. Never deletes accounts. Safe to re-run."""
    removed = 0
    with _LOCK:
        path = _cfg("AUTH_FILE")
        if os.path.exists(path):
            creds = _read_json(path, None)
            if isinstance(creds, dict) and "recovery_answer_hash" in creds:
                creds.pop("recovery_answer_hash", None)
                save_credentials(creds)
                removed += 1
        path = _cfg("REGISTERED_USERS_FILE")
        if os.path.exists(path):
            users = _read_json(path, [])
            changed = False
            if isinstance(users, list):
                for user in users:
                    if isinstance(user, dict) and "recovery_answer_hash" in user:
                        user.pop("recovery_answer_hash", None)
                        removed += 1
                        changed = True
            if changed:
                save_registered_users(users)
    return removed


# =========================================================
# SERVER-SIDE SESSION REGISTRY
# =========================================================

def _load_sessions():
    data = _read_json(_cfg("SESSIONS_FILE"), {}, strict=False)
    if not isinstance(data, dict):
        return {}
    now = _now()
    return {k: v for k, v in data.items() if isinstance(v, dict) and v.get("expires", 0) > now}


def _account_password_hash(account):
    """Current password hash of an account key, or None if the account is gone."""
    kind, _, ident = account.partition(":")
    if kind == "admin":
        creds = load_credentials()
        return creds["password_hash"] if creds["username"] == ident else None
    user = next((u for u in load_registered_users() if isinstance(u, dict) and u.get("id") == ident), None)
    return user.get("password_hash") if user else None


def _start_session(account, role, username, full_name, email, password_hash):
    csrf = session.get("csrf") or secrets.token_urlsafe(32)
    sid = secrets.token_urlsafe(32)
    with _LOCK:
        sessions = _load_sessions()
        sessions[_sha256(sid)] = {
            "account": account, "role": role, "fp": _fingerprint(password_hash),
            "expires": _now() + SESSION_SECONDS, "seen": _now(),
        }
        _write_json(_cfg("SESSIONS_FILE"), sessions)
    session.clear()  # drop anything from before sign in (prevents session fixation)
    session.permanent = True
    session.update({"csrf": csrf, "sid": sid, "authenticated": True, "username": username,
                    "full_name": full_name, "email": email, "role": role})


def _end_session():
    sid = session.get("sid")
    if sid:
        with _LOCK:
            sessions = _load_sessions()
            if sessions.pop(_sha256(sid), None) is not None:
                _write_json(_cfg("SESSIONS_FILE"), sessions)
    csrf = session.get("csrf")
    session.clear()
    if csrf:
        session["csrf"] = csrf


def current_session():
    """The valid server-side session entry for this request, or None."""
    sid = session.get("sid")
    if not sid or not session.get("authenticated"):
        return None
    with _LOCK:
        sessions = _load_sessions()
        entry = sessions.get(_sha256(sid))
        if not entry:
            return None
        password_hash = _account_password_hash(entry["account"])
        if password_hash is None or entry.get("fp") != _fingerprint(password_hash):
            return None  # account removed, or password changed since sign in
        if entry["expires"] - _now() < SESSION_SECONDS - 60:  # sliding expiry, throttled writes
            entry["expires"] = _now() + SESSION_SECONDS
            entry["seen"] = _now()
            _write_json(_cfg("SESSIONS_FILE"), sessions)
        return entry


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if current_session() is None:
            session.pop("authenticated", None)
            if request.path.startswith("/download/") or request.path.startswith("/generate"):
                return jsonify({"success": False, "error": "Your session has expired. Please log in again."}), 401
            return jsonify({"success": False, "error": "Authentication required."}), 401
        session.permanent = True
        return view(*args, **kwargs)
    return wrapped


def safe_route(view):
    """Turn storage problems and unexpected errors into a clean JSON 500."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        try:
            return view(*args, **kwargs)
        except (AuthStoreError, OSError):
            log.exception("Authentication storage error")
        except Exception:
            log.exception("Unexpected authentication error")
        return jsonify({"success": False, "error": GENERIC_SERVER_ERROR}), 500
    return wrapped


# =========================================================
# PASSWORD-RESET TOKENS
# =========================================================

def _load_tokens():
    data = _read_json(_cfg("RESET_TOKENS_FILE"), [], strict=False)
    now = _now()
    if not isinstance(data, list):
        return []
    return [t for t in data if isinstance(t, dict) and t.get("expires", 0) > now]


def create_reset_token(account, password_hash):
    """Create a new token for the account (invalidating earlier ones); returns the raw token."""
    token = secrets.token_urlsafe(32)
    with _LOCK:
        tokens = [t for t in _load_tokens() if t.get("account") != account]
        tokens.append({"hash": _sha256(token), "account": account,
                       "fp": _fingerprint(password_hash),
                       "created": _now(), "expires": _now() + reset_token_minutes() * 60})
        _write_json(_cfg("RESET_TOKENS_FILE"), tokens)
    return token


def _find_token(tokens, token):
    digest = _sha256(token)
    return next((t for t in tokens if _safe_equal(t.get("hash", ""), digest)), None)


def reset_token_is_valid(token):
    if not token or len(token) > 200:
        return False
    with _LOCK:
        entry = _find_token(_load_tokens(), token)
        if not entry:
            return False
        current = _account_password_hash(entry["account"])
        return current is not None and entry.get("fp") == _fingerprint(current)


def consume_reset_token(token, new_password):
    """Set the new password if the token is valid. The token is single-use."""
    if not token or len(token) > 200:
        return False
    with _LOCK:
        tokens = _load_tokens()
        entry = _find_token(tokens, token)
        if not entry:
            return False
        account = entry["account"]
        new_hash = generate_password_hash(new_password)
        kind, _, ident = account.partition(":")
        ok = False
        if kind == "admin":
            creds = load_credentials()
            if creds["username"] == ident and entry.get("fp") == _fingerprint(creds["password_hash"]):
                creds["password_hash"] = new_hash
                save_credentials(creds)
                ok = True
        else:
            users = load_registered_users()
            user = next((u for u in users if isinstance(u, dict) and u.get("id") == ident), None)
            if user and entry.get("fp") == _fingerprint(user.get("password_hash", "")):
                user["password_hash"] = new_hash
                user["password_changed_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
                save_registered_users(users)
                ok = True
        # The token is consumed whether or not it could be applied; so is any other token of the account.
        remaining = [t for t in tokens if t.get("account") != account]
        _write_json(_cfg("RESET_TOKENS_FILE"), remaining)
        return ok


def build_reset_link(token, require_base_url=True):
    base = os.environ.get("APP_BASE_URL", "").strip().rstrip("/")
    if not base:
        if require_base_url:
            raise AuthStoreError("APP_BASE_URL is not configured")
        base = "http://127.0.0.1:5000"
    return f"{base}/reset-password?token={token}"


def create_reset_link_for(identifier):
    """For the server operator (manage.py): a one-time link for an account, or None."""
    identifier = identifier.strip().lower()
    creds = load_credentials()
    if identifier == creds["username"].lower():
        account, password_hash = "admin:" + creds["username"], creds["password_hash"]
    else:
        user = find_user(identifier)
        if not user:
            return None
        account, password_hash = "user:" + user["id"], user["password_hash"]
    return build_reset_link(create_reset_token(account, password_hash), require_base_url=False)


# =========================================================
# E-MAIL DELIVERY
# =========================================================

def email_delivery_enabled():
    return bool(os.environ.get("SMTP_HOST", "").strip() and os.environ.get("MAIL_FROM", "").strip()
                and os.environ.get("APP_BASE_URL", "").strip())


def _send_reset_email(to_address, link, minutes):
    message = EmailMessage()
    message["Subject"] = "Reset your Re-Exam Scheduler password"
    message["From"] = os.environ["MAIL_FROM"].strip()
    message["To"] = to_address
    message.set_content(
        "A password reset was requested for your Re-Exam Scheduler account.\n\n"
        f"Open this link to choose a new password (valid for {minutes} minutes, one use only):\n{link}\n\n"
        "If you did not request this, you can ignore this email; your password will not change.\n")
    host = os.environ["SMTP_HOST"].strip()
    port = int(os.environ.get("SMTP_PORT", "587") or 587)
    security = os.environ.get("SMTP_SECURITY", "starttls").strip().lower()
    username = os.environ.get("SMTP_USERNAME", "")
    password = os.environ.get("SMTP_PASSWORD", "")
    context = ssl.create_default_context()
    if security == "ssl":
        server = smtplib.SMTP_SSL(host, port, timeout=15, context=context)
    else:
        server = smtplib.SMTP(host, port, timeout=15)
    try:
        if security == "starttls":
            server.starttls(context=context)
        if username:
            server.login(username, password)
        server.send_message(message)
    finally:
        try:
            server.quit()
        except Exception:
            pass


def _send_reset_email_async(to_address, link, minutes):
    def worker():
        try:
            _send_reset_email(to_address, link, minutes)
        except Exception as exc:  # never log the link/token
            log.error("Could not send password reset email (%s)", type(exc).__name__)
    # Sent in the background so response time does not reveal whether the account exists.
    threading.Thread(target=worker, daemon=True).start()


def _issue_and_send_reset(identifier):
    creds = load_credentials()
    if identifier == creds["username"].lower():
        recipient = os.environ.get("ADMIN_EMAIL", "").strip()
        if not recipient:
            log.info("Administrator password reset requested but ADMIN_EMAIL is not set; "
                     "use 'python manage.py reset-link'.")
            return
        account, password_hash = "admin:" + creds["username"], creds["password_hash"]
    else:
        user = find_user(identifier)
        if not user:
            return
        recipient, account, password_hash = user["email"], "user:" + user["id"], user["password_hash"]
    token = create_reset_token(account, password_hash)
    _send_reset_email_async(recipient, build_reset_link(token), reset_token_minutes())


# =========================================================
# ROUTES
# =========================================================

@auth_bp.route("/auth/csrf", methods=["GET"])
def auth_csrf():
    if not session.get("csrf"):
        session["csrf"] = secrets.token_urlsafe(32)
    return jsonify({"csrf_token": session["csrf"]})


@auth_bp.route("/auth/session", methods=["GET"])
@safe_route
def auth_session():
    entry = current_session()
    if not session.get("csrf"):
        session["csrf"] = secrets.token_urlsafe(32)
    body = {"authenticated": bool(entry), "csrf_token": session["csrf"],
            "email_reset_enabled": email_delivery_enabled()}
    if entry:
        body.update({"username": session.get("username", ""), "full_name": session.get("full_name", ""),
                     "email": session.get("email", ""), "role": entry.get("role", "user")})
    else:
        body.update({"username": "", "full_name": "", "email": "", "role": "user"})
    return jsonify(body)


@auth_bp.route("/auth/register", methods=["POST"])
@safe_route
def auth_register():
    blocked = _throttle("register-ip:" + _client_ip(), 10, 3600)
    if blocked:
        return blocked
    data = _payload()
    full_name = _field(data, "full_name", 101).strip()
    email = _field(data, "email").strip().lower()
    password = _field(data, "password", MAX_PASSWORD_LENGTH + 1)
    confirm_password = _field(data, "confirm_password", MAX_PASSWORD_LENGTH + 1)

    if not full_name or not email or not password or not confirm_password:
        return jsonify({"success": False, "error": "Please complete all required fields."}), 400
    if len(full_name) < 2 or len(full_name) > 100:
        return jsonify({"success": False, "error": "Enter your full name (2–100 characters)."}), 400
    if len(email) > MAX_FIELD_LENGTH or not EMAIL_RE.fullmatch(email):
        return jsonify({"success": False, "error": "Enter a valid email address."}), 400
    problem = password_problem(password)
    if problem:
        return jsonify({"success": False, "error": problem}), 400
    if password != confirm_password:
        return jsonify({"success": False, "error": "Password and confirm password do not match."}), 400

    with _LOCK:
        if email == load_credentials()["username"].lower() or find_user(email):
            return jsonify({"success": False, "error": "An account with this email already exists. Please sign in."}), 409
        users = load_registered_users()
        users.append({
            "id": secrets.token_hex(16),
            "full_name": full_name,
            "email": email,
            "password_hash": generate_password_hash(password),
            "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "role": "user",  # public registration can never create an administrator
        })
        save_registered_users(users)
    return jsonify({"success": True, "message": "Registration successful. Please sign in with your email and password."}), 201


@auth_bp.route("/auth/login", methods=["POST"])
@safe_route
def auth_login():
    ip = _client_ip()
    if limiter.blocked("login-ip:" + ip, 30, 600):
        return _too_many()
    limiter.hit("login-ip:" + ip, 600)

    data = _payload()
    identifier = _field(data, "email", MAX_FIELD_LENGTH + 1) or _field(data, "username", MAX_FIELD_LENGTH + 1)
    identifier = identifier.strip()
    password = _field(data, "password", MAX_PASSWORD_LENGTH + 1)
    if not identifier or not password:
        return jsonify({"success": False, "error": "Please enter your email/administrator username and password."}), 400

    pair_key = f"login-fail:{ip}:{identifier.lower()}"
    account_key = f"login-fail-acct:{identifier.lower()}"
    if limiter.blocked(pair_key, 5, 900) or limiter.blocked(account_key, 25, 900):
        return _too_many()

    creds = load_credentials()
    account = role = username = full_name = email = password_hash = None
    if identifier == creds["username"]:
        password_hash = creds["password_hash"]
        if check_password_hash(password_hash, password):
            account, role, username, full_name, email = "admin:" + creds["username"], "administrator", creds["username"], "Administrator", ""
    else:
        user = find_user(identifier)
        if user:
            password_hash = str(user.get("password_hash", ""))
            if check_password_hash(password_hash, password):
                account, role = "user:" + user["id"], "user"
                username, full_name, email = user["email"], user.get("full_name", ""), user["email"]
        else:
            check_password_hash(_DUMMY_HASH, password)  # same work for unknown accounts

    if not account:
        limiter.hit(pair_key, 900)
        limiter.hit(account_key, 900)
        return jsonify({"success": False, "error": GENERIC_LOGIN_ERROR}), 401

    limiter.clear(pair_key)
    _start_session(account, role, username, full_name, email, password_hash)
    return jsonify({"success": True, "username": username, "full_name": full_name, "email": email, "role": role})


@auth_bp.route("/auth/logout", methods=["POST"])
@safe_route
def auth_logout():
    _end_session()
    return jsonify({"success": True})


@auth_bp.route("/auth/registered-users", methods=["GET"])
@login_required
@safe_route
def auth_registered_users():
    entry = current_session()
    if not entry or entry.get("role") != "administrator":
        return jsonify({"success": False, "error": "Only the administrator can view the registered accounts list."}), 403
    users = [u for u in load_registered_users() if isinstance(u, dict)]
    public_users = [{
        "full_name": u.get("full_name", ""),
        "email": u.get("email", ""),
        "created_at": u.get("created_at", ""),
        "password_status": "Password securely hashed",
    } for u in users]
    return jsonify({"success": True, "users": public_users, "count": len(public_users)})


@auth_bp.route("/auth/forgot-password", methods=["POST"])
@safe_route
def auth_forgot_password():
    blocked = _throttle("forgot-ip:" + _client_ip(), 5, 900)
    if blocked:
        return blocked
    identifier = _field(_payload(), "email").strip().lower() or _field(_payload(), "username").strip().lower()
    if not identifier or len(identifier) > MAX_FIELD_LENGTH:
        return jsonify({"success": False, "error": "Enter your registered email address."}), 400
    blocked = _throttle("forgot-acct:" + _sha256(identifier), 3, 3600)  # same limit whether or not it exists
    if blocked:
        return blocked
    if not email_delivery_enabled():
        return jsonify({"success": True, "email_enabled": False, "message": NO_EMAIL_MESSAGE})
    try:
        _issue_and_send_reset(identifier)
    except (AuthStoreError, OSError):
        log.exception("Could not issue a password reset token")
    return jsonify({"success": True, "email_enabled": True, "message": FORGOT_MESSAGE})


@auth_bp.route("/auth/reset-password/check", methods=["POST"])
@safe_route
def auth_reset_check():
    blocked = _throttle("reset-ip:" + _client_ip(), 20, 900)
    if blocked:
        return blocked
    valid = reset_token_is_valid(_field(_payload(), "token", 201).strip())
    return jsonify({"success": valid, **({} if valid else {"error": INVALID_TOKEN_ERROR})}), (200 if valid else 400)


@auth_bp.route("/auth/reset-password", methods=["POST"])
@safe_route
def auth_reset_password():
    blocked = _throttle("reset-ip:" + _client_ip(), 20, 900)
    if blocked:
        return blocked
    data = _payload()
    token = _field(data, "token", 201).strip()
    new_password = _field(data, "new_password", MAX_PASSWORD_LENGTH + 1)
    confirm_password = _field(data, "confirm_password", MAX_PASSWORD_LENGTH + 1)
    if not token:
        return jsonify({"success": False, "error": INVALID_TOKEN_ERROR}), 400
    problem = password_problem(new_password)
    if problem:
        return jsonify({"success": False, "error": problem}), 400
    if new_password != confirm_password:
        return jsonify({"success": False, "error": "New password and confirm password do not match."}), 400
    if not consume_reset_token(token, new_password):
        return jsonify({"success": False, "error": INVALID_TOKEN_ERROR}), 400
    _end_session()
    return jsonify({"success": True, "message": "Password reset successfully. Please sign in with your new password."})


@auth_bp.route("/auth/change-password", methods=["POST"])
@login_required
@safe_route
def auth_change_password():
    entry = current_session()
    account = entry["account"]
    key = "change-pw-fail:" + account
    if limiter.blocked(key, 5, 900):
        return _too_many()
    data = _payload()
    current = _field(data, "current_password", MAX_PASSWORD_LENGTH + 1)
    new_password = _field(data, "new_password", MAX_PASSWORD_LENGTH + 1)
    confirm_password = _field(data, "confirm_password", MAX_PASSWORD_LENGTH + 1)
    if not current or not new_password or not confirm_password:
        return jsonify({"success": False, "error": "Please complete all required fields."}), 400
    problem = password_problem(new_password)
    if problem:
        return jsonify({"success": False, "error": problem}), 400
    if new_password != confirm_password:
        return jsonify({"success": False, "error": "New password and confirm password do not match."}), 400
    if new_password == current:
        return jsonify({"success": False, "error": "Choose a new password that is different from the current one."}), 400

    with _LOCK:
        old_hash = _account_password_hash(account)
        if old_hash is None or not check_password_hash(old_hash, current):
            limiter.hit(key, 900)
            return jsonify({"success": False, "error": "The current password is incorrect."}), 400
        new_hash = generate_password_hash(new_password)
        kind = account.partition(":")[0]
        if kind == "admin":
            creds = load_credentials()
            creds["password_hash"] = new_hash
            save_credentials(creds)
        else:
            users = load_registered_users()
            user = next(u for u in users if isinstance(u, dict) and u.get("id") == account.partition(":")[2])
            user["password_hash"] = new_hash
            user["password_changed_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
            save_registered_users(users)
        # Sessions signed in with the old password stop working; keep this one.
        sessions = _load_sessions()
        for sid_hash, other in list(sessions.items()):
            if other.get("account") == account:
                if sid_hash == _sha256(session.get("sid", "")):
                    other["fp"] = _fingerprint(new_hash)
                else:
                    del sessions[sid_hash]
        _write_json(_cfg("SESSIONS_FILE"), sessions)
        # Pending reset links for this account are no longer needed.
        _write_json(_cfg("RESET_TOKENS_FILE"), [t for t in _load_tokens() if t.get("account") != account])
    limiter.clear(key)
    return jsonify({"success": True, "message": "Password changed successfully."})


# =========================================================
# APP INTEGRATION
# =========================================================

def _csrf_guard():
    if request.method in ("GET", "HEAD", "OPTIONS") or request.endpoint is None:
        return None
    sent = request.headers.get("X-CSRF-Token", "")
    expected = session.get("csrf", "")
    if not sent or not expected or not _safe_equal(sent, expected):
        return jsonify({"success": False, "error": "Your security token is missing or expired. Please try again.",
                        "error_code": "csrf"}), 403
    return None


def _security_headers(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    if request.path.startswith("/auth/") or request.path == "/reset-password":
        response.headers["Cache-Control"] = "no-store"
    return response


def init_auth(app, instance_dir):
    app.config.update(
        AUTH_FILE=os.path.join(instance_dir, "admin_credentials.json"),
        REGISTERED_USERS_FILE=os.path.join(instance_dir, "registered_users.json"),
        RESET_TOKENS_FILE=os.path.join(instance_dir, "password_reset_tokens.json"),
        SESSIONS_FILE=os.path.join(instance_dir, "active_sessions.json"),
        SESSION_COOKIE_SECURE=_env_flag("COOKIE_SECURE"),
    )
    app.register_blueprint(auth_bp)
    app.before_request(_csrf_guard)
    app.after_request(_security_headers)
    with app.app_context():
        try:
            removed = migrate_legacy_fields()
            if removed:
                log.info("Removed obsolete recovery-answer data from %d record(s).", removed)
        except (AuthStoreError, OSError):
            log.exception("Could not migrate legacy authentication data")
