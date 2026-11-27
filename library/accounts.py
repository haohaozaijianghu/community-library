from typing import Annotated, Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from psycopg import errors
from pydantic import BaseModel, Field, StrictBool

from library.core import event

Role = Literal["reader", "staff"]
Phone = Annotated[str, Field(pattern=r"^1[3-9][0-9]{9}$")]


class NewAccount(BaseModel):
    phone: Phone
    role: Role


class AccountUpdate(BaseModel):
    role: Role
    active: StrictBool
    version: Annotated[int, Field(strict=True, ge=1)]


def lock_management(conn, actor):
    tenant = actor["tenant_id"]
    conn.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended('library-accounts:' || %s, 0))", (tenant,)
    )
    row = conn.execute(
        """SELECT 1 FROM library_accounts WHERE tenant_id=%s AND user_id=%s
           AND role='staff' AND active""", (tenant, actor["user_id"]),
    ).fetchone()
    if not row:
        raise HTTPException(403, "staff_required")


def routes(identity):
    router = APIRouter(prefix="/v1/admin/accounts")

    def manager(user=Depends(identity)):
        if user["role"] != "staff":
            event("access_denied", reason="accounts_staff_required")
            raise HTTPException(403, "staff_required")
        return user

    @router.get("")
    def list_accounts(request: Request, response: Response, user=Depends(manager)):
        response.headers["Cache-Control"] = "no-store"
        with request.app.state.db.connection() as conn:
            return conn.execute(
                """SELECT user_id,phone,role,active,version FROM library_accounts
                   WHERE tenant_id=%s ORDER BY active DESC,role DESC,phone""", (user["tenant_id"],),
            ).fetchall()

    @router.post("", status_code=201)
    def create_account(request: Request, body: NewAccount, user=Depends(manager)):
        try:
            with request.app.state.db.connection() as conn:
                lock_management(conn, user)
                account = conn.execute(
                    """INSERT INTO library_accounts (tenant_id,user_id,phone,role)
                       VALUES (%s,%s,%s,%s) RETURNING user_id,phone,role,active,version""",
                    (user["tenant_id"], uuid4().hex, body.phone, body.role),
                ).fetchone()
        except errors.UniqueViolation as error:
            raise HTTPException(409, "phone_unavailable") from error
        event("account_created", tenant_id=user["tenant_id"], actor=user["user_id"],
              target=account["user_id"], role=account["role"])
        return account

    @router.patch("/{user_id}")
    def update_account(request: Request, user_id: str, body: AccountUpdate, user=Depends(manager)):
        tenant = user["tenant_id"]
        with request.app.state.db.connection() as conn:
            lock_management(conn, user)
            prior = conn.execute(
                """SELECT user_id,phone,role,active,version FROM library_accounts
                   WHERE tenant_id=%s AND user_id=%s FOR UPDATE""", (tenant, user_id),
            ).fetchone()
            if not prior:
                raise HTTPException(404, "account_not_found")
            if body.version != prior["version"]:
                raise HTTPException(409, "account_version_conflict")
            if (prior["role"], prior["active"]) == (body.role, body.active):
                return prior
            if user_id == user["user_id"]:
                raise HTTPException(409, "cannot_change_own_account")
            updated = conn.execute(
                """UPDATE library_accounts SET role=%s,active=%s,version=version+1
                   WHERE tenant_id=%s AND user_id=%s RETURNING user_id,phone,role,active,version""",
                (body.role, body.active, tenant, user_id),
            ).fetchone()
            conn.execute("DELETE FROM library_login_codes WHERE phone=%s", (prior["phone"],))
            conn.execute("DELETE FROM library_password_reset_codes WHERE phone=%s", (prior["phone"],))
            conn.execute("DELETE FROM library_profile_codes WHERE tenant_id=%s AND user_id=%s", (tenant, user_id))
            conn.execute("DELETE FROM library_web_sessions WHERE phone=%s", (prior["phone"],))
        event("account_updated", tenant_id=tenant, actor=user["user_id"], target=user_id,
              role=updated["role"], active=updated["active"], version=updated["version"])
        return updated

    return router
