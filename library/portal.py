from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StringConstraints, field_validator

from library.sources import notice_feed_choices

SHORTCUTS = ("ask-library", "reader-catalog", "borrow-guide", "community-services")
DEFAULT = {
    "title": "在这里，遇见更多知识",
    "intro": "查询本馆馆藏、馆内服务和电子书内容。",
    "shortcuts": list(SHORTCUTS),
    "external_notices": True,
    "notice_feed": None,
    "registration_enabled": True,
    "otp_login_enabled": True,
    "version": 0,
}
FIELDS = "title,intro,shortcuts,external_notices,registration_enabled,otp_login_enabled,notice_feed,version"


class PortalEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
    intro: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=240)]
    shortcuts: list[Literal["ask-library", "reader-catalog", "borrow-guide", "community-services"]] = Field(max_length=4)
    external_notices: StrictBool
    notice_feed: str | None = Field(default=None, max_length=2048)
    registration_enabled: StrictBool
    otp_login_enabled: StrictBool
    version: int = Field(strict=True, ge=0)

    @field_validator("title", "intro")
    @classmethod
    def valid_text(cls, value):
        if any(ord(c) < 32 or 0xD800 <= ord(c) <= 0xDFFF for c in value):
            raise ValueError("invalid text")
        return value

    @field_validator("shortcuts")
    @classmethod
    def distinct_shortcuts(cls, value):
        if len(set(value)) != len(value):
            raise ValueError("duplicate shortcuts")
        return value


def settings(conn, tenant):
    return conn.execute(
        f"SELECT {FIELDS} FROM library_portal_settings WHERE tenant_id=%s", (tenant,),
    ).fetchone() or DEFAULT


def feed_choices(tenant):
    try:
        return notice_feed_choices(tenant)
    except (AttributeError, TypeError, ValueError):
        raise HTTPException(503, "notice_feed_configuration_invalid") from None


def routes(identity):
    router = APIRouter()

    def staff(user=Depends(identity)):
        if user["role"] != "staff":
            raise HTTPException(403, "staff_required")
        return user

    @router.get("/v1/reader/portal")
    def reader(request: Request, response: Response, user=Depends(identity)):
        response.headers["Cache-Control"] = "no-store"
        with request.app.state.db.connection() as conn:
            config = settings(conn, user["tenant_id"])
            custom_faq = conn.execute(
                """SELECT EXISTS (SELECT 1 FROM library_community_posts
                   WHERE tenant_id=%s AND kind='faq' AND published_at IS NOT NULL) AS enabled""", (user["tenant_id"],),
            ).fetchone()["enabled"]
            faqs = conn.execute(
                """SELECT id,title,body,featured FROM library_community_posts
                   WHERE tenant_id=%s AND kind='faq' AND state='published'
                   ORDER BY sort_order,published_at DESC,id""", (user["tenant_id"],),
            ).fetchall()
        return {**{field: config[field] for field in ("title", "intro", "shortcuts")}, "faqs": faqs, "custom_faq": custom_faq}

    @router.get("/v1/admin/portal")
    def get(request: Request, response: Response, user=Depends(staff)):
        response.headers["Cache-Control"] = "no-store"
        with request.app.state.db.connection() as conn:
            return {**settings(conn, user["tenant_id"]), "notice_feed_options": feed_choices(user["tenant_id"])}

    @router.put("/v1/admin/portal")
    def edit(body: PortalEdit, request: Request, user=Depends(staff)):
        tenant = user["tenant_id"]
        if body.notice_feed and body.notice_feed not in feed_choices(tenant):
            raise HTTPException(400, "notice_feed_not_approved")
        values = (body.title, body.intro, body.shortcuts, body.external_notices,
                  body.registration_enabled, body.otp_login_enabled, body.notice_feed)
        with request.app.state.db.connection() as conn:
            if body.version == 0:
                row = conn.execute(
                    f"""INSERT INTO library_portal_settings
                        (tenant_id,title,intro,shortcuts,external_notices,registration_enabled,otp_login_enabled,notice_feed)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING {FIELDS}""",
                    (tenant, *values),
                ).fetchone()
            else:
                row = conn.execute(
                    f"""UPDATE library_portal_settings SET title=%s,intro=%s,shortcuts=%s,
                        external_notices=%s,registration_enabled=%s,otp_login_enabled=%s,notice_feed=%s,version=version+1
                        WHERE tenant_id=%s AND version=%s RETURNING {FIELDS}""",
                    (*values, tenant, body.version),
                ).fetchone()
        if row is None:
            raise HTTPException(409, "portal_settings_conflict")
        return row

    return router
