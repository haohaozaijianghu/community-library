import hashlib
import hmac
import secrets
from typing import Annotated, Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from psycopg import errors
from pydantic import BaseModel, ConfigDict, Field, SecretStr, StringConstraints, model_validator

from library.accounts import Phone

from library.core import digest, event
from library.portal import settings as portal_settings
from library.sms import browser_auth_enabled, deliver_code

COOKIE = "library_session"
TTL = 8 * 60 * 60
CODE_KEY = secrets.token_bytes(32)
router = APIRouter(prefix="/v1/auth")


class Login(BaseModel):
    login_type: Literal["user", "admin"]
    phone: Phone


class Verification(Login):
    code: str = Field(pattern=r"^[0-9]{6}$")


Username = Annotated[str, StringConstraints(pattern=r"^[a-zA-Z][a-zA-Z0-9_-]{3,23}$")]


class PasswordReset(Verification):
    password: SecretStr = Field(min_length=12, max_length=128)


class PasswordLogin(BaseModel):
    login_type: Literal["user", "admin"]
    phone: Phone | None = None
    username: Username | None = None
    password: SecretStr = Field(min_length=1, max_length=128)

    @model_validator(mode="after")
    def one_identifier(self):
        if (self.phone is None) == (self.username is None):
            raise ValueError("provide one login identifier")
        return self


class RegistrationCode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_id: Literal["north", "south"]
    phone: Phone


class Registration(RegistrationCode):
    username: Username
    code: str = Field(pattern=r"^[0-9]{6}$")
    password: SecretStr = Field(min_length=12, max_length=128)


def derive_password(password, salt):
    return hashlib.scrypt(password.encode(), salt=salt, n=2**15, r=8, p=3, maxmem=64 * 1024**2, dklen=32)


def hash_password(password):
    salt = secrets.token_bytes(16)
    return salt.hex() + ":" + derive_password(password, salt).hex()


def verify_password(password, stored):
    salt, expected = stored.split(":") if stored else ("00" * 16, "00" * 32)
    result = hmac.compare_digest(derive_password(password, bytes.fromhex(salt)), bytes.fromhex(expected))
    return bool(stored) and result


def start_session(conn, phone):
    session = secrets.token_urlsafe(32)
    conn.execute("DELETE FROM library_web_sessions WHERE expires_at<=now()")
    conn.execute(
        """INSERT INTO library_web_sessions (digest,phone,expires_at)
           VALUES (%s,%s,now()+(%s*interval '1 second'))""",
        (digest(session), phone, TTL),
    )
    return session


def session_response(request, body, session):
    response = JSONResponse(body, headers={"Cache-Control": "no-store"})
    response.set_cookie(COOKIE, session, max_age=TTL, httponly=True, samesite="strict",
                        secure=request.url.scheme == "https")
    return response


def code_digest(phone, code):
    return hmac.digest(CODE_KEY, f"{phone}:{code}".encode(), "sha256").hex()


def ui_request(request):
    if request.headers.get("X-Library-UI") != "1":
        raise HTTPException(403, "browser_request_required")


def matching_account(conn, phone, login_type):
    return conn.execute(
        """SELECT tenant_id,user_id,role FROM library_accounts
           WHERE phone=%s AND role=%s AND active FOR SHARE""",
        (phone, "staff" if login_type == "admin" else "reader"),
    ).fetchone()


def session_user(request):
    session = request.cookies.get(COOKIE)
    if not session or not browser_auth_enabled(request):
        return None
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        ui_request(request)
    with request.app.state.db.connection() as conn:
        return conn.execute(
            """SELECT a.tenant_id,a.user_id,a.role FROM library_web_sessions s
               JOIN library_accounts a ON a.phone=s.phone AND a.active
               WHERE s.digest=%s AND s.expires_at>now()""", (digest(session),),
        ).fetchone()


def check_otp_policy(conn, account):
    if account["role"] == "reader" and not portal_settings(conn, account["tenant_id"])["otp_login_enabled"]:
        raise HTTPException(403, "otp_login_disabled")


def check_registration_policy(conn, tenant):
    if not portal_settings(conn, tenant)["registration_enabled"]:
        raise HTTPException(403, "registration_disabled")


@router.post("/code")
def send_code(body: Login, request: Request):
    ui_request(request)
    if not browser_auth_enabled(request):
        raise HTTPException(503, "phone_login_unavailable")
    code = f"{secrets.randbelow(1_000_000):06}"
    with request.app.state.db.connection() as conn:
        account = matching_account(conn, body.phone, body.login_type)
        if not account:
            raise HTTPException(401, "phone_login_mismatch")
        check_otp_policy(conn, account)
        row = conn.execute(
            """INSERT INTO library_login_codes (phone,digest,expires_at)
               VALUES (%s,%s,now()+interval '5 minutes')
               ON CONFLICT (phone) DO UPDATE SET digest=excluded.digest, attempts=0,
                   issued_at=now(), expires_at=excluded.expires_at
               WHERE library_login_codes.issued_at <= now()-interval '45 seconds'
               RETURNING phone""", (body.phone, code_digest(body.phone, code))
        ).fetchone()
        if not row:
            raise HTTPException(429, "code_cooldown")
        return deliver_code(conn, request, body.phone, code, "login")


@router.post("/login")
def login(body: Verification, request: Request):
    ui_request(request)
    if not browser_auth_enabled(request):
        raise HTTPException(503, "phone_login_unavailable")
    with request.app.state.db.connection() as conn:
        account = matching_account(conn, body.phone, body.login_type)
        if not account:
            raise HTTPException(401, "phone_login_mismatch")
        check_otp_policy(conn, account)
        challenge = conn.execute(
            """SELECT digest FROM library_login_codes
               WHERE phone=%s AND expires_at>now() AND attempts<5 FOR UPDATE""",
            (body.phone,),
        ).fetchone()
        valid = challenge and secrets.compare_digest(challenge["digest"], code_digest(body.phone, body.code))
        if valid:
            conn.execute("DELETE FROM library_login_codes WHERE phone=%s", (body.phone,))
            session = start_session(conn, body.phone)
        elif challenge:
            conn.execute("UPDATE library_login_codes SET attempts=attempts+1 WHERE phone=%s", (body.phone,))
    if not valid:
        raise HTTPException(401, "invalid_code")
    return session_response(request, account, session)


def registration_digest(tenant, phone, code):
    return code_digest(f"registration:{tenant}:{phone}", code)


@router.post("/register/code")
def send_registration_code(body: RegistrationCode, request: Request):
    ui_request(request)
    if not browser_auth_enabled(request):
        raise HTTPException(503, "phone_login_unavailable")
    code = f"{secrets.randbelow(1_000_000):06}"
    with request.app.state.db.connection() as conn:
        check_registration_policy(conn, body.tenant_id)
        if conn.execute("SELECT 1 FROM library_accounts WHERE phone=%s", (body.phone,)).fetchone():
            raise HTTPException(409, "phone_unavailable")
        row = conn.execute(
            """INSERT INTO library_registration_codes (phone,tenant_id,digest,expires_at)
               VALUES (%s,%s,%s,now()+interval '5 minutes')
               ON CONFLICT (phone) DO UPDATE SET tenant_id=excluded.tenant_id, digest=excluded.digest,
                   attempts=0, issued_at=now(), expires_at=excluded.expires_at
               WHERE library_registration_codes.issued_at<=now()-interval '45 seconds'
               RETURNING phone""",
            (body.phone, body.tenant_id, registration_digest(body.tenant_id, body.phone, code)),
        ).fetchone()
        if not row:
            raise HTTPException(429, "code_cooldown")
        return deliver_code(conn, request, body.phone, code, "registration")


@router.post("/register", status_code=201)
def register(body: Registration, request: Request):
    ui_request(request)
    if not browser_auth_enabled(request):
        raise HTTPException(503, "phone_login_unavailable")
    with request.app.state.db.connection() as conn:
        check_registration_policy(conn, body.tenant_id)
        challenge = conn.execute(
            """SELECT digest FROM library_registration_codes
               WHERE phone=%s AND tenant_id=%s AND expires_at>now() AND attempts<5 FOR UPDATE""",
            (body.phone, body.tenant_id),
        ).fetchone()
        valid = challenge and secrets.compare_digest(
            challenge["digest"], registration_digest(body.tenant_id, body.phone, body.code),
        )
        if valid:
            try:
                account = conn.execute(
                    """INSERT INTO library_accounts (tenant_id,user_id,phone,role,username,password_hash)
                       VALUES (%s,%s,%s,'reader',%s,%s)
                       RETURNING tenant_id,user_id,role""",
                    (body.tenant_id, uuid4().hex, body.phone, body.username.lower(),
                     hash_password(body.password.get_secret_value())),
                ).fetchone()
            except errors.UniqueViolation as error:
                detail = "username_unavailable" if error.diag.constraint_name == "library_accounts_username" else "phone_unavailable"
                raise HTTPException(409, detail) from error
            conn.execute("DELETE FROM library_registration_codes WHERE phone=%s", (body.phone,))
            session = start_session(conn, body.phone)
        elif challenge:
            conn.execute("UPDATE library_registration_codes SET attempts=attempts+1 WHERE phone=%s", (body.phone,))
    if not valid:
        raise HTTPException(401, "invalid_code")
    event("reader_registered", tenant=account["tenant_id"], actor=account["user_id"])
    response = session_response(request, account, session)
    response.status_code = 201
    return response


@router.post("/password-login")
def password_login(body: PasswordLogin, request: Request):
    ui_request(request)
    if not browser_auth_enabled(request):
        raise HTTPException(503, "phone_login_unavailable")
    role = "staff" if body.login_type == "admin" else "reader"
    with request.app.state.db.connection() as conn:
        account = conn.execute(
            """SELECT tenant_id,user_id,role,phone,password_hash,
                      COALESCE(password_blocked_until>now(),false) AS blocked
               FROM library_accounts
               WHERE (phone=%s OR username=%s) AND role=%s AND active AND phone IS NOT NULL FOR UPDATE""",
            (body.phone, body.username.lower() if body.username else None, role),
        ).fetchone()
        if account and account["blocked"]:
            raise HTTPException(429, "password_login_blocked")
        valid = verify_password(body.password.get_secret_value(), account["password_hash"] if account else None)
        if valid:
            conn.execute(
                """UPDATE library_accounts SET password_failures=0,password_blocked_until=NULL WHERE phone=%s""",
                (account["phone"],),
            )
            session = start_session(conn, account["phone"])
        elif account and account["password_hash"]:
            conn.execute(
                """UPDATE library_accounts
                   SET password_failures=CASE WHEN password_blocked_until<=now() THEN 1 ELSE password_failures+1 END,
                       password_blocked_until=CASE
                           WHEN password_blocked_until IS NULL AND password_failures>=4 THEN now()+interval '15 minutes' END
                   WHERE phone=%s""", (account["phone"],),
            )
    if not valid:
        raise HTTPException(401, "invalid_password")
    return session_response(request, {key: account[key] for key in ("tenant_id", "user_id", "role")}, session)


@router.post("/password-reset/code")
def send_reset_code(body: Login, request: Request):
    ui_request(request)
    if not browser_auth_enabled(request):
        raise HTTPException(503, "phone_login_unavailable")
    code = f"{secrets.randbelow(1_000_000):06}"
    with request.app.state.db.connection() as conn:
        if not matching_account(conn, body.phone, body.login_type):
            raise HTTPException(401, "phone_login_mismatch")
        row = conn.execute(
            """INSERT INTO library_password_reset_codes (phone,digest,expires_at)
               VALUES (%s,%s,now()+interval '5 minutes')
               ON CONFLICT (phone) DO UPDATE SET digest=excluded.digest,attempts=0,
                   issued_at=now(),expires_at=excluded.expires_at
               WHERE library_password_reset_codes.issued_at<=now()-interval '45 seconds'
               RETURNING phone""",
            (body.phone, code_digest(f"reset:{body.login_type}:{body.phone}", code)),
        ).fetchone()
        if not row:
            raise HTTPException(429, "code_cooldown")
        return deliver_code(conn, request, body.phone, code, "password-reset")


@router.post("/password-reset")
def reset_password(body: PasswordReset, request: Request):
    ui_request(request)
    if not browser_auth_enabled(request):
        raise HTTPException(503, "phone_login_unavailable")
    role = "staff" if body.login_type == "admin" else "reader"
    with request.app.state.db.connection() as conn:
        account = conn.execute(
            """SELECT tenant_id,user_id FROM library_accounts WHERE phone=%s AND role=%s AND active FOR UPDATE""",
            (body.phone, role),
        ).fetchone()
        if not account:
            raise HTTPException(401, "phone_login_mismatch")
        challenge = conn.execute(
            """SELECT digest FROM library_password_reset_codes WHERE phone=%s AND expires_at>now() AND attempts<5 FOR UPDATE""",
            (body.phone,),
        ).fetchone()
        valid = challenge and secrets.compare_digest(
            challenge["digest"], code_digest(f"reset:{body.login_type}:{body.phone}", body.code),
        )
        if valid:
            conn.execute(
                """UPDATE library_accounts SET password_hash=%s,password_failures=0,password_blocked_until=NULL
                   WHERE phone=%s""", (hash_password(body.password.get_secret_value()), body.phone),
            )
            conn.execute("DELETE FROM library_password_reset_codes WHERE phone=%s", (body.phone,))
            conn.execute("DELETE FROM library_login_codes WHERE phone=%s", (body.phone,))
            conn.execute("DELETE FROM library_web_sessions WHERE phone=%s", (body.phone,))
            conn.execute("DELETE FROM library_profile_codes WHERE tenant_id=%s AND user_id=%s",
                         (account["tenant_id"], account["user_id"]))
        elif challenge:
            conn.execute("UPDATE library_password_reset_codes SET attempts=attempts+1 WHERE phone=%s", (body.phone,))
    if not valid:
        raise HTTPException(401, "invalid_code")
    event("password_reset", tenant=account["tenant_id"], actor=account["user_id"])
    return JSONResponse({"status": "ok"}, headers={"Cache-Control": "no-store"})


@router.post("/logout")
def logout(request: Request):
    ui_request(request)
    session = request.cookies.get(COOKIE)
    if session:
        with request.app.state.db.connection() as conn:
            conn.execute("DELETE FROM library_web_sessions WHERE digest=%s", (digest(session),))
    response = JSONResponse({"status": "ok"})
    response.delete_cookie(COOKIE, httponly=True, samesite="strict", secure=request.url.scheme == "https")
    return response
