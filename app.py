"""
Alkansya — Your digital piggy bank.

An online banking simulator built with Flask. It upgrades the console
"Simple Banking System" (deposit / withdraw / check balance) into a web app
with admin + customer logins, PIN security and a database-backed ledger.
"""
import os
import re
import secrets
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from functools import wraps

from dotenv import load_dotenv
from flask import (Flask, abort, flash, g, jsonify, redirect, render_template,
                   request, session, url_for)
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import func, or_
from sqlalchemy.orm import joinedload
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash, generate_password_hash

load_dotenv()

# --------------------------------------------------------------------------
# Settings
# --------------------------------------------------------------------------
BANK_NAME = "Alkansya"
TAGLINE = "Your digital piggy bank"

IDLE_TIMEOUT = 5 * 60           # seconds of inactivity before automatic sign-out
RECEIPT_SIGNOUT = 30            # seconds before sign-out after each transaction
MAX_PIN_ATTEMPTS = 3            # wrong PINs before the account locks
MAX_DEPOSIT = 100_000 * 100     # per transaction, in centavos
MAX_WITHDRAWAL = 50_000 * 100   # per transaction, in centavos
PH_OFFSET = timedelta(hours=8)  # times are shown in Philippine time (UTC+8)

PIN_RE = re.compile(r"\d{4,6}")
USERNAME_RE = re.compile(r"[a-z0-9_.]{3,20}")

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)  # Render runs behind a proxy

database_url = os.getenv("DATABASE_URL") or "sqlite:///alkansya.db"
# Use the psycopg2 driver (psycopg2-binary). SQLAlchemy 2.1 would otherwise default to psycopg 3.
database_url = re.sub(r"^postgres(ql)?://", "postgresql+psycopg2://", database_url)

app.config.update(
    SECRET_KEY=os.getenv("SECRET_KEY") or "dev-only-secret-change-me",
    SQLALCHEMY_DATABASE_URI=database_url,
    SQLALCHEMY_ENGINE_OPTIONS={"pool_pre_ping": True},
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=bool(os.getenv("RENDER")),  # HTTPS-only cookie on Render
)
db = SQLAlchemy(app)


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def ph_now():
    return utcnow() + PH_OFFSET


# --------------------------------------------------------------------------
# Database models
# --------------------------------------------------------------------------
class User(db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(40), unique=True, nullable=False, index=True)
    full_name = db.Column(db.String(80), nullable=False)
    role = db.Column(db.String(10), nullable=False, default="customer")  # admin | customer
    account_number = db.Column(db.String(12), unique=True, index=True)
    secret_hash = db.Column(db.String(255), nullable=False)  # hashed PIN (or admin password)
    balance_cents = db.Column(db.BigInteger, nullable=False, default=0)
    must_change_pin = db.Column(db.Boolean, nullable=False, default=False)
    failed_attempts = db.Column(db.Integer, nullable=False, default=0)
    is_locked = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    last_login = db.Column(db.DateTime)

    def set_secret(self, raw):
        self.secret_hash = generate_password_hash(raw)

    def check_secret(self, raw):
        return check_password_hash(self.secret_hash, str(raw or ""))

    @property
    def is_admin(self):
        return self.role == "admin"

    @property
    def first_name(self):
        return self.full_name.split()[0]


class Transaction(db.Model):
    __tablename__ = "transactions"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    kind = db.Column(db.String(12), nullable=False)  # deposit | withdrawal
    amount_cents = db.Column(db.BigInteger, nullable=False)
    balance_after_cents = db.Column(db.BigInteger, nullable=False)
    reference = db.Column(db.String(24), unique=True, nullable=False)
    note = db.Column(db.String(80))
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)


class AuditLog(db.Model):
    """Every sign-in, transaction and admin action ends up here."""
    __tablename__ = "audit_log"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), index=True)
    actor = db.Column(db.String(80))
    action = db.Column(db.String(30), nullable=False)
    detail = db.Column(db.String(255))
    ip = db.Column(db.String(64))
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)
    user = db.relationship("User")


# action -> (lucide icon, colour tone, label)
EVENTS = {
    "LOGIN_SUCCESS": ("log-in", "forest", "Signed in"),
    "LOGIN_FAILED": ("shield-alert", "clay", "Failed sign-in"),
    "LOGIN_BLOCKED": ("shield-x", "rose", "Blocked sign-in (locked)"),
    "PIN_FAILED": ("shield-alert", "clay", "Wrong PIN entered"),
    "ACCOUNT_LOCKED": ("lock", "rose", "Account locked"),
    "ACCOUNT_UNLOCKED": ("lock-open", "forest", "Account unlocked"),
    "LOGOUT": ("log-out", "muted", "Signed out"),
    "SESSION_TIMEOUT": ("timer-off", "muted", "Auto signed out (idle)"),
    "DEPOSIT": ("arrow-down-left", "forest", "Deposit"),
    "WITHDRAWAL": ("arrow-up-right", "clay", "Withdrawal"),
    "BALANCE_INQUIRY": ("eye", "muted", "Balance inquiry"),
    "PIN_CHANGED": ("key-round", "forest", "PIN changed"),
    "PIN_RESET": ("rotate-ccw", "clay", "Temporary PIN issued"),
    "CUSTOMER_CREATED": ("user-plus", "forest", "Customer created"),
}
SECURITY_EVENTS = [k for k in EVENTS if k not in ("DEPOSIT", "WITHDRAWAL", "CUSTOMER_CREATED")]
TONES = {
    "forest": "bg-forest-light text-forest",
    "clay": "bg-clay-light text-clay-dark",
    "rose": "bg-rose-100 text-rose-700",
    "muted": "bg-sand text-muted",
}


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def log_event(user, action, detail="", actor=None):
    db.session.add(AuditLog(
        user_id=user.id if user else None,
        actor=(actor or (user.username if user else "unknown"))[:80],
        action=action,
        detail=(detail or "")[:255],
        ip=(request.remote_addr or "")[:64],
    ))


def csrf_token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(32)
    return session["csrf"]


def wants_json():
    return request.path.startswith(("/api/", "/admin/api/")) or request.is_json


def payload():
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def json_error(message, status=400, **extra):
    return jsonify(ok=False, error=message, **extra), status


def current_user():
    if "user" not in g:
        uid = session.get("uid")
        g.user = db.session.get(User, uid) if uid else None
    return g.user


def parse_amount(raw):
    """Turn user input like '1,500.50' into centavos (int). Raises ValueError."""
    text = str(raw if raw is not None else "").replace(",", "").replace("₱", "").strip()
    try:
        amount = Decimal(text)
    except (InvalidOperation, ValueError):
        raise ValueError("Please enter a valid amount (e.g. 500 or 250.75).")
    if not amount.is_finite() or amount <= 0:
        raise ValueError("Amount must be greater than zero.")
    if amount > 10_000_000:
        raise ValueError("That amount is way too large.")
    if amount.as_tuple().exponent < -2:
        raise ValueError("Amounts can only have up to 2 decimal places.")
    return int(amount * 100)


def new_pin_problem(user, new_pin, confirm_pin):
    new_pin, confirm_pin = str(new_pin or ""), str(confirm_pin or "")
    if not PIN_RE.fullmatch(new_pin):
        return "Your PIN must be 4 to 6 digits."
    if new_pin != confirm_pin:
        return "The new PINs don't match."
    if len(set(new_pin)) == 1 or new_pin in "0123456789" or new_pin in "9876543210":
        return "That PIN is too easy to guess (like 1111 or 1234). Try another one."
    if user.check_secret(new_pin):
        return "Your new PIN must be different from your current PIN."
    return None


def random_pin(length=6):
    while True:
        pin = "".join(secrets.choice("0123456789") for _ in range(length))
        if len(set(pin)) > 2:
            return pin


def new_account_number():
    while True:
        number = "8" + "".join(secrets.choice("0123456789") for _ in range(11))
        if not User.query.filter_by(account_number=number).first():
            return number


def new_reference():
    return f"ALK-{ph_now():%y%m%d}-{secrets.token_hex(3).upper()}"


def ph_day_start_utc():
    return ph_now().replace(hour=0, minute=0, second=0, microsecond=0) - PH_OFFSET


def ph_month_start_utc():
    return ph_now().replace(day=1, hour=0, minute=0, second=0, microsecond=0) - PH_OFFSET


def greeting():
    hour = ph_now().hour
    if 5 <= hour < 12:
        return "Magandang umaga"
    if hour == 12:
        return "Magandang tanghali"
    if 13 <= hour < 18:
        return "Magandang hapon"
    return "Magandang gabi"


def failed_attempt(user, where):
    """Count a wrong PIN. Returns (locked, message)."""
    user.failed_attempts += 1
    if user.is_admin:
        log_event(user, "LOGIN_FAILED", f"Wrong admin password ({where})")
        db.session.commit()
        return False, "Wrong username or password."
    left = MAX_PIN_ATTEMPTS - user.failed_attempts
    if left <= 0:
        user.is_locked = True
        log_event(user, "ACCOUNT_LOCKED", f"Locked after {MAX_PIN_ATTEMPTS} wrong PINs during {where}")
        db.session.commit()
        return True, "Too many wrong PINs, so your account is now locked. Please contact the bank admin."
    log_event(user, "LOGIN_FAILED" if where == "sign-in" else "PIN_FAILED", f"Wrong PIN during {where}")
    db.session.commit()
    return False, f"Wrong PIN. {left} attempt{'s' if left != 1 else ''} left before your account is locked."


def check_pin_or_fail(user, pin, where):
    """Verify the PIN for a transaction. Returns None if OK, else a JSON error response."""
    if user.check_secret(pin):
        user.failed_attempts = 0
        return None
    locked, message = failed_attempt(user, where)
    if locked:
        session.clear()
        flash(message, "error")
        return json_error(message, 403, redirect=url_for("login"))
    return json_error(message)


# --------------------------------------------------------------------------
# Template helpers
# --------------------------------------------------------------------------
@app.template_filter("peso")
def peso(cents):
    cents = cents or 0
    sign = "-" if cents < 0 else ""
    return f"{sign}₱{Decimal(abs(cents)) / 100:,.2f}"


@app.template_filter("phtime")
def phtime(dt, fmt="%b %d, %Y · %I:%M %p"):
    return (dt + PH_OFFSET).strftime(fmt) if dt else "—"


@app.template_filter("acct")
def acct(number):
    return " ".join(number[i:i + 4] for i in range(0, len(number), 4)) if number else ""


@app.template_filter("mask")
def mask(number):
    return f"•••• •••• {number[-4:]}" if number else ""


@app.context_processor
def inject_globals():
    return dict(
        bank_name=BANK_NAME, tagline=TAGLINE, csrf_token=csrf_token,
        events_meta=EVENTS, tones=TONES, year=ph_now().year,
        idle_timeout=IDLE_TIMEOUT if session.get("uid") else 0,
        max_deposit=MAX_DEPOSIT, max_withdrawal=MAX_WITHDRAWAL,
    )


# --------------------------------------------------------------------------
# Security gate: CSRF + idle timeout + headers
# --------------------------------------------------------------------------
@app.before_request
def security_gate():
    if request.endpoint == "static":
        return None

    if request.method == "POST":
        sent = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token", "")
        expected = session.get("csrf", "")
        if not sent or not expected or not secrets.compare_digest(sent.encode(), expected.encode()):
            if wants_json():
                if "uid" not in session:  # signed out (e.g. in another tab)
                    return json_error("Your session ended. Please sign in again.", 401, redirect=url_for("login"))
                return json_error("Your security token expired. Please refresh the page.")
            flash("Your form expired for security reasons. Please try again.", "error")
            return redirect(url_for("login"))

    if "uid" in session:
        now = time.time()
        if now - session.get("last_active", now) > IDLE_TIMEOUT:
            user = db.session.get(User, session["uid"])
            if user:
                log_event(user, "SESSION_TIMEOUT", "Signed out after 5 minutes of inactivity")
                db.session.commit()
            session.clear()
            flash("You were signed out after 5 minutes of inactivity. Ingat!", "info")
            if wants_json():
                return json_error("Your session expired.", 401, redirect=url_for("login"))
            return redirect(url_for("login"))
        session["last_active"] = now
    return None


@app.after_request
def security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    if request.endpoint != "static":
        # Don't let the back button show account pages after signing out.
        response.headers["Cache-Control"] = "no-store"
    return response


def login_required(role=None):
    def decorator(view):
        @wraps(view)
        def wrapper(*args, **kwargs):
            user = current_user()
            if not user:
                if wants_json():
                    return json_error("Please sign in again.", 401, redirect=url_for("login"))
                return redirect(url_for("login"))
            if user.is_locked:
                session.clear()
                flash("This account is locked. Please contact the bank admin.", "error")
                if wants_json():
                    return json_error("This account is locked.", 403, redirect=url_for("login"))
                return redirect(url_for("login"))
            if role and user.role != role:
                if wants_json():
                    return json_error("You're not allowed to do that.", 403)
                return redirect(url_for("home"))
            if user.must_change_pin and request.endpoint not in ("change_pin", "logout"):
                if wants_json():
                    return json_error("Please set a new PIN first.", 403, redirect=url_for("change_pin"))
                return redirect(url_for("change_pin"))
            return view(user, *args, **kwargs)
        return wrapper
    return decorator


# --------------------------------------------------------------------------
# Auth routes
# --------------------------------------------------------------------------
@app.get("/")
def home():
    user = current_user()
    if not user:
        return redirect(url_for("login"))
    return redirect(url_for("admin_dashboard" if user.is_admin else "dashboard"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user():
        return redirect(url_for("home"))

    if request.method == "POST":
        ident = request.form.get("username", "").strip().lower().replace(" ", "")
        secret = request.form.get("secret", "")
        user = User.query.filter(or_(User.username == ident, User.account_number == ident)).first()

        if not user:
            log_event(None, "LOGIN_FAILED", "Unknown username or account number", actor=ident or "unknown")
            db.session.commit()
            flash("We couldn't match that username/account number and PIN.", "error")
            return redirect(url_for("login", u=ident))
        if user.is_locked:
            log_event(user, "LOGIN_BLOCKED", "Sign-in attempt on a locked account")
            db.session.commit()
            flash("This account is locked after too many wrong PINs. Please ask the bank admin to unlock it.", "error")
            return redirect(url_for("login", u=ident))
        if not user.check_secret(secret):
            _, message = failed_attempt(user, "sign-in")
            flash(message, "error")
            return redirect(url_for("login", u=ident))

        user.failed_attempts = 0
        user.last_login = utcnow()
        log_event(user, "LOGIN_SUCCESS", f"Signed in as {user.role}")
        db.session.commit()

        session.clear()  # fresh session on every sign-in
        session.update(uid=user.id, role=user.role, last_active=time.time())
        csrf_token()
        return redirect(url_for("change_pin" if user.must_change_pin else "home"))

    return render_template("login.html")


LOGOUT_REASONS = {
    "timeout": ("SESSION_TIMEOUT", "Signed out after 5 minutes of inactivity",
                "You were signed out after 5 minutes of inactivity. Ingat!"),
    "auto": ("LOGOUT", "Auto sign-out after a transaction",
             "We signed you out automatically after your transaction. Stay safe!"),
    "done": ("LOGOUT", "Signed out after a transaction",
             "Signed out. Salamat for banking with Alkansya!"),
}


@app.get("/logout")
def logout():
    action, detail, message = LOGOUT_REASONS.get(
        request.args.get("reason"), ("LOGOUT", "Signed out", "You have been signed out. Ingat!"))
    user = current_user()
    if user:
        log_event(user, action, detail)
        db.session.commit()
    session.clear()
    flash(message, "info")
    return redirect(url_for("login"))


@app.route("/change-pin", methods=["GET", "POST"])
@login_required("customer")
def change_pin(user):
    """First sign-in: the customer must replace the default PIN from the admin."""
    if not user.must_change_pin:
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        form = request.form
        if not user.check_secret(form.get("current_pin", "")):
            locked, message = failed_attempt(user, "PIN change")
            if locked:
                session.clear()
            flash(message, "error")
            return redirect(url_for("login" if locked else "change_pin"))
        problem = new_pin_problem(user, form.get("new_pin"), form.get("confirm_pin"))
        if problem:
            flash(problem, "error")
            return redirect(url_for("change_pin"))
        user.set_secret(form["new_pin"])
        user.must_change_pin = False
        user.failed_attempts = 0
        log_event(user, "PIN_CHANGED", "Replaced the default PIN on first sign-in")
        db.session.commit()
        flash(f"Your new PIN is set. Welcome to {BANK_NAME}!", "success")
        return redirect(url_for("dashboard"))

    return render_template("change_pin.html", user=user)


# --------------------------------------------------------------------------
# Customer
# --------------------------------------------------------------------------
@app.get("/dashboard")
@login_required("customer")
def dashboard(user):
    txns = (Transaction.query.filter_by(user_id=user.id)
            .order_by(Transaction.created_at.desc(), Transaction.id.desc()).limit(8).all())
    events = (AuditLog.query.filter(AuditLog.user_id == user.id, AuditLog.action.in_(SECURITY_EVENTS))
              .order_by(AuditLog.id.desc()).limit(6).all())
    totals = dict(
        db.session.query(Transaction.kind, func.coalesce(func.sum(Transaction.amount_cents), 0))
        .filter(Transaction.user_id == user.id, Transaction.created_at >= ph_month_start_utc())
        .group_by(Transaction.kind).all()
    )
    return render_template("dashboard.html", user=user, txns=txns, events=events,
                           money_in=totals.get("deposit", 0), money_out=totals.get("withdrawal", 0),
                           greeting=greeting())


@app.post("/api/transaction")
@login_required("customer")
def api_transaction(user):
    data = payload()
    kind = data.get("type")
    if kind not in ("deposit", "withdrawal"):
        return json_error("Unknown transaction type.")
    try:
        cents = parse_amount(data.get("amount"))
    except ValueError as exc:
        return json_error(str(exc))
    limit = MAX_DEPOSIT if kind == "deposit" else MAX_WITHDRAWAL
    if cents > limit:
        return json_error(f"For your security, the maximum {kind} is {peso(limit)} per transaction.")

    # Lock the row so two withdrawals can't race each other (PostgreSQL).
    user = db.session.get(User, user.id, with_for_update=True, populate_existing=True)
    denied = check_pin_or_fail(user, data.get("pin"), kind)
    if denied:
        return denied
    if kind == "withdrawal" and cents > user.balance_cents:
        db.session.commit()
        return json_error(f"Insufficient funds. Your current balance is {peso(user.balance_cents)}.")

    user.balance_cents += cents if kind == "deposit" else -cents
    now = utcnow()
    txn = Transaction(user_id=user.id, kind=kind, amount_cents=cents,
                      balance_after_cents=user.balance_cents, reference=new_reference(), created_at=now)
    db.session.add(txn)
    log_event(user, kind.upper(), f"{peso(cents)} · Ref {txn.reference}")
    db.session.commit()

    return jsonify(
        ok=True,
        signout_in=RECEIPT_SIGNOUT,
        receipt=dict(kind=kind, title=f"{'Deposit' if kind == 'deposit' else 'Withdrawal'} successful",
                     amount=peso(cents), balance=peso(user.balance_cents), reference=txn.reference,
                     date=phtime(now), account=mask(user.account_number)),
        row=render_template("_txn_row.html", t=txn),
    )


@app.post("/api/balance")
@login_required("customer")
def api_balance(user):
    denied = check_pin_or_fail(user, payload().get("pin"), "balance inquiry")
    if denied:
        return denied
    log_event(user, "BALANCE_INQUIRY", "Checked balance")
    db.session.commit()
    return jsonify(ok=True, balance=peso(user.balance_cents), hide_in=30)


@app.post("/api/change-pin")
@login_required("customer")
def api_change_pin(user):
    data = payload()
    denied = check_pin_or_fail(user, data.get("current_pin"), "PIN change")
    if denied:
        return denied
    problem = new_pin_problem(user, data.get("new_pin"), data.get("confirm_pin"))
    if problem:
        db.session.commit()
        return json_error(problem)
    user.set_secret(str(data["new_pin"]))
    log_event(user, "PIN_CHANGED", "Changed PIN from the dashboard")
    db.session.commit()
    session.clear()  # security: sign in again with the new PIN
    flash("PIN changed! For your security, please sign in again with your new PIN.", "success")
    return jsonify(ok=True, redirect=url_for("login"))


@app.post("/api/ping")
@login_required()
def api_ping(user):
    return jsonify(ok=True)


# --------------------------------------------------------------------------
# Admin
# --------------------------------------------------------------------------
@app.get("/admin")
@login_required("admin")
def admin_dashboard(user):
    customers = User.query.filter_by(role="customer").order_by(User.created_at.desc()).all()
    stats = dict(
        customers=len(customers),
        holdings=sum(c.balance_cents for c in customers),
        today=Transaction.query.filter(Transaction.created_at >= ph_day_start_utc()).count(),
        locked=sum(1 for c in customers if c.is_locked),
    )
    events = AuditLog.query.options(joinedload(AuditLog.user)).order_by(AuditLog.id.desc()).limit(40).all()
    return render_template("admin.html", user=user, customers=customers, stats=stats,
                           events=events, suggested_pin=random_pin())


@app.post("/admin/api/customers")
@login_required("admin")
def admin_create_customer(admin):
    data = payload()
    name = " ".join(str(data.get("full_name") or "").split())
    username = str(data.get("username") or "").strip().lower()
    pin = str(data.get("pin") or "").strip()
    opening_raw = str(data.get("opening") or "").strip()

    if not 2 <= len(name) <= 80:
        return json_error("Please enter the customer's full name.")
    if not USERNAME_RE.fullmatch(username):
        return json_error("Username must be 3–20 characters: letters, numbers, dots or underscores.")
    if User.query.filter_by(username=username).first():
        return json_error("That username is already taken.")
    if not PIN_RE.fullmatch(pin):
        return json_error("The default PIN must be 4 to 6 digits.")
    opening = 0
    if opening_raw:
        try:
            opening = parse_amount(opening_raw)
        except ValueError as exc:
            return json_error(f"Opening deposit: {exc}")
        if opening > MAX_DEPOSIT:
            return json_error(f"The opening deposit can't be more than {peso(MAX_DEPOSIT)}.")

    customer = User(username=username, full_name=name, role="customer",
                    account_number=new_account_number(), must_change_pin=True, balance_cents=opening)
    customer.set_secret(pin)
    db.session.add(customer)
    db.session.flush()
    if opening:
        db.session.add(Transaction(user_id=customer.id, kind="deposit", amount_cents=opening,
                                   balance_after_cents=opening, reference=new_reference(),
                                   note="Opening deposit"))
    log_event(customer, "CUSTOMER_CREATED",
              f"Created by {admin.username}" + (f" with {peso(opening)} opening deposit" if opening else ""),
              actor=admin.username)
    db.session.commit()
    return jsonify(ok=True, customer=dict(name=name, username=username,
                                          account=acct(customer.account_number), pin=pin))


def _get_customer(cid):
    customer = db.get_or_404(User, cid)
    if customer.is_admin:
        abort(404)
    return customer


@app.post("/admin/api/customers/<int:cid>/unlock")
@login_required("admin")
def admin_unlock(admin, cid):
    customer = _get_customer(cid)
    customer.is_locked = False
    customer.failed_attempts = 0
    log_event(customer, "ACCOUNT_UNLOCKED", f"Unlocked by {admin.username}", actor=admin.username)
    db.session.commit()
    return jsonify(ok=True, message=f"{customer.full_name}'s account is unlocked.")


@app.post("/admin/api/customers/<int:cid>/reset-pin")
@login_required("admin")
def admin_reset_pin(admin, cid):
    customer = _get_customer(cid)
    pin = random_pin()
    customer.set_secret(pin)
    customer.must_change_pin = True
    customer.is_locked = False
    customer.failed_attempts = 0
    log_event(customer, "PIN_RESET", f"Temporary PIN issued by {admin.username}", actor=admin.username)
    db.session.commit()
    return jsonify(ok=True, customer=dict(name=customer.full_name, username=customer.username,
                                          account=acct(customer.account_number), pin=pin))


# --------------------------------------------------------------------------
# Database setup (runs on start-up, locally and on Render)
# --------------------------------------------------------------------------
def init_db():
    db.create_all()
    if not User.query.filter_by(role="admin").first():
        admin = User(username=os.getenv("ADMIN_USERNAME", "admin").strip().lower(),
                     full_name="Bank Admin", role="admin")
        admin.set_secret(os.getenv("ADMIN_PASSWORD", "admin123"))
        db.session.add(admin)
        db.session.commit()


with app.app_context():
    init_db()


if __name__ == "__main__":
    app.run(debug=True)
