import hmac
import secrets
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from psycopg import errors
from pydantic import BaseModel, Field, SecretStr, StringConstraints

from library.accounts import Phone
from library.core import digest, event
from library.web_auth import CODE_KEY, COOKIE, hash_password, session_response, start_session, ui_request, verify_password

from library.sms import browser_auth_enabled, deliver_code

router = APIRouter(prefix="/v1/profile")
Code = Annotated[str, Field(pattern=r"^[0-9]{6}$")]


class ProfileEdit(BaseModel):
    display_name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=40, pattern=r"^[^\x00-\x1f\x7f]+$")]


class SecurityCode(BaseModel):
    purpose: Literal["phone-old", "phone-new", "password"]
    new_phone: Phone | None = None


class PhoneEdit(BaseModel):
    new_phone: Phone
    old_code: Code
    new_code: Code


class PasswordEdit(BaseModel):
    code: Code
    current_password: SecretStr | None = Field(default=None, max_length=128)
    new_password: SecretStr = Field(min_length=12, max_length=128)


def browser_session(request):
    token = request.cookies.get(COOKIE)
    if not token or not browser_auth_enabled(request):
        raise HTTPException(401, "invalid_credentials")
    if request.method != "GET":
        ui_request(request)
    return digest(token)


def account_scope(account):
    return account["tenant_id"], account["user_id"]


def current_account(conn, session, *, lock=False):
    row = conn.execute(
        """SELECT a.tenant_id,a.user_id,a.role,a.phone,a.display_name,a.password_hash
           FROM library_web_sessions s JOIN library_accounts a ON a.phone=s.phone AND a.active
           WHERE s.digest=%s AND s.expires_at>now()""" + (" FOR UPDATE OF a" if lock else ""),
        (session,),
    ).fetchone()
    if not row or (lock and not conn.execute(
        "SELECT 1 FROM library_web_sessions WHERE digest=%s AND phone=%s AND expires_at>now()",
        (session, row["phone"]),
    ).fetchone()):
        raise HTTPException(401, "invalid_credentials")
    return row


def public_profile(account):
    phone = account["phone"]
    return {"display_name": account["display_name"], "phone_masked": phone[:3] + "****" + phone[-4:],
            "has_password": account["password_hash"] is not None}


def proof_digest(account, purpose, phone, code):
    value = ":".join((*account_scope(account), purpose, phone, code))
    return hmac.digest(CODE_KEY, value.encode(), "sha256").hex()


def check_proof(conn, account, purpose, phone, code):
    scope = (*account_scope(account), purpose)
    row = conn.execute(
        """SELECT phone,digest FROM library_profile_codes
           WHERE tenant_id=%s AND user_id=%s AND purpose=%s AND expires_at>now() AND attempts<5""", scope,
    ).fetchone()
    if not row or row["phone"] != phone:
        return False
    if secrets.compare_digest(row["digest"], proof_digest(account, purpose, phone, code)):
        return True
    conn.execute(
        """UPDATE library_profile_codes SET attempts=attempts+1 WHERE tenant_id=%s AND user_id=%s AND purpose=%s""", scope,
    )
    return False


def invalidate_security(conn, account):
    conn.execute("DELETE FROM library_profile_codes WHERE tenant_id=%s AND user_id=%s", account_scope(account))
    conn.execute("DELETE FROM library_login_codes WHERE phone=%s", (account["phone"],))
    conn.execute("DELETE FROM library_password_reset_codes WHERE phone=%s", (account["phone"],))
    conn.execute("DELETE FROM library_web_sessions WHERE phone=%s", (account["phone"],))


@router.get("")
def get_profile(request: Request):
    session = browser_session(request)
    with request.app.state.db.connection() as conn:
        account = current_account(conn, session)
    return JSONResponse(public_profile(account), headers={"Cache-Control": "no-store"})


@router.patch("")
def edit_profile(body: ProfileEdit, request: Request):
    session = browser_session(request)
    with request.app.state.db.connection() as conn:
        account = current_account(conn, session, lock=True)
        conn.execute("UPDATE library_accounts SET display_name=%s WHERE tenant_id=%s AND user_id=%s",
                     (body.display_name, *account_scope(account)))
    return JSONResponse(public_profile({**account, "display_name": body.display_name}), headers={"Cache-Control": "no-store"})


@router.post("/code")
def send_security_code(body: SecurityCode, request: Request):
    session = browser_session(request)
    code = f"{secrets.randbelow(1_000_000):06}"
    with request.app.state.db.connection() as conn:
        account = current_account(conn, session, lock=True)
        phone = account["phone"]
        if body.purpose == "phone-new":
            phone = body.new_phone
            if not phone or phone == account["phone"]:
                raise HTTPException(400, "invalid_new_phone")
            if conn.execute("SELECT 1 FROM library_accounts WHERE phone=%s", (phone,)).fetchone():
                raise HTTPException(409, "phone_unavailable")
        row = conn.execute(
            """INSERT INTO library_profile_codes (tenant_id,user_id,purpose,phone,digest,expires_at)
               VALUES (%s,%s,%s,%s,%s,now()+interval '5 minutes')
               ON CONFLICT (tenant_id,user_id,purpose) DO UPDATE
                   SET phone=excluded.phone,digest=excluded.digest,expires_at=excluded.expires_at,attempts=0,issued_at=now()
               WHERE library_profile_codes.issued_at<=now()-interval '45 seconds' RETURNING purpose""",
            (*account_scope(account), body.purpose, phone, proof_digest(account, body.purpose, phone, code)),
        ).fetchone()
        if not row:
            raise HTTPException(429, "code_cooldown")
        return deliver_code(conn, request, phone, code, "profile-" + body.purpose)


@router.post("/phone")
def edit_phone(body: PhoneEdit, request: Request):
    session = browser_session(request)
    try:
        with request.app.state.db.connection() as conn:
            account = current_account(conn, session, lock=True)
            if body.new_phone == account["phone"]:
                raise HTTPException(400, "invalid_new_phone")
            old_ok = check_proof(conn, account, "phone-old", account["phone"], body.old_code)
            new_ok = check_proof(conn, account, "phone-new", body.new_phone, body.new_code)
            if old_ok and new_ok:
                conn.execute(
                    """UPDATE library_accounts SET phone=%s,version=version+1,password_failures=0,password_blocked_until=NULL
                       WHERE tenant_id=%s AND user_id=%s""", (body.new_phone, *account_scope(account)),
                )
                invalidate_security(conn, account)
                replacement = start_session(conn, body.new_phone)
    except errors.UniqueViolation as error:
        raise HTTPException(409, "phone_unavailable") from error
    if not (old_ok and new_ok):
        raise HTTPException(401, "invalid_code")
    event("profile_phone_changed", tenant=account["tenant_id"], actor=account["user_id"])
    return session_response(request, public_profile({**account, "phone": body.new_phone}), replacement)


@router.post("/password")
def edit_password(body: PasswordEdit, request: Request):
    session = browser_session(request)
    replacement = None
    with request.app.state.db.connection() as conn:
        account = current_account(conn, session, lock=True)
        valid = check_proof(conn, account, "password", account["phone"], body.code)
        if valid:
            conn.execute(
                "UPDATE library_profile_codes SET attempts=attempts+1 WHERE tenant_id=%s AND user_id=%s AND purpose='password'",
                account_scope(account),
            )
            previous = account["password_hash"]
            if previous is None or (body.current_password and verify_password(body.current_password.get_secret_value(), previous)):
                hashed = hash_password(body.new_password.get_secret_value())
                conn.execute(
                    """UPDATE library_accounts SET password_hash=%s,password_failures=0,password_blocked_until=NULL
                       WHERE tenant_id=%s AND user_id=%s""", (hashed, *account_scope(account)),
                )
                invalidate_security(conn, account)
                replacement = start_session(conn, account["phone"])
    if not valid:
        raise HTTPException(401, "invalid_code")
    if not replacement:
        raise HTTPException(401, "invalid_current_password")
    event("profile_password_changed", tenant=account["tenant_id"], actor=account["user_id"])
    return session_response(request, public_profile({**account, "password_hash": hashed}), replacement)
