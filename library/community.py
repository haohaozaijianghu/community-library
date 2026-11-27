from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

from library.core import event

Kind = Literal["notice", "activity", "guide", "faq", "handbook"]
POST_FIELDS = "id,kind,title,body,state,version,updated_at,published_at,sort_order,keywords,featured,starts_at,location,capacity,source_id"


class PostContent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Kind
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
    body: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=5000)]

    sort_order: int = Field(default=0, strict=True, ge=0, le=9999)
    keywords: list[Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=60)]] = Field(default_factory=list, max_length=24)
    featured: bool = False
    starts_at: AwareDatetime | None = None
    location: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160)] | None = None
    capacity: int | None = Field(default=None, strict=True, ge=1, le=10000)

    @model_validator(mode="after")
    def activity_details(self):
        if self.kind != "activity" and (self.capacity or self.starts_at or self.location):
            raise ValueError("activity fields require activity kind")
        if self.capacity and not (self.starts_at and self.location):
            raise ValueError("enrollment requires a date and location")
        return self

    @field_validator("keywords")
    @classmethod
    def valid_keywords(cls, values):
        return list(dict.fromkeys(cls.valid_text(value).casefold() for value in values))

    @field_validator("title", "body", "location")
    @classmethod
    def valid_text(cls, value):
        if value is not None and any((ord(c) < 32 and c not in "\n\r\t") or 0xD800 <= ord(c) <= 0xDFFF for c in value):
            raise ValueError("invalid text")
        return value


class PostEdit(PostContent):
    version: int = Field(strict=True, ge=1)


class PostState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = Field(strict=True, ge=1)
    state: Literal["published", "withdrawn"]


def checked_post(conn, tenant, post_id, version):
    row = conn.execute(
        """SELECT state,version,kind,capacity,starts_at,source_id FROM library_community_posts WHERE tenant_id=%s AND id=%s FOR UPDATE""",
        (tenant, post_id),
    ).fetchone()
    if not row:
        raise HTTPException(404, "community_post_not_found")
    if row["version"] != version:
        raise HTTPException(409, "community_post_conflict")
    return row


def enrollment_count(conn, tenant, post_id):
    return conn.execute(
        "SELECT count(*) AS total FROM library_activity_enrollments WHERE tenant_id=%s AND post_id=%s",
        (tenant, post_id),
    ).fetchone()["total"]


def routes(identity):
    router = APIRouter()

    def staff(user=Depends(identity)):
        if user["role"] != "staff":
            raise HTTPException(403, "staff_required")
        return user

    @router.get("/v1/reader/community-posts")
    def published(request: Request, response: Response, user=Depends(identity)):
        response.headers["Cache-Control"] = "no-store"
        with request.app.state.db.connection() as conn:
            return conn.execute(
                """SELECT p.id,p.kind,p.title,p.body,p.published_at,p.starts_at,p.location,p.capacity,
                          EXISTS (SELECT 1 FROM library_activity_enrollments e
                                  WHERE (e.tenant_id,e.post_id,e.user_id)=(p.tenant_id,p.id,%s)) AS enrolled,
                          (SELECT count(*) FROM library_activity_enrollments e
                           WHERE (e.tenant_id,e.post_id)=(p.tenant_id,p.id)) AS enrolled_count
                   FROM library_community_posts p
                   WHERE p.tenant_id=%s AND p.state='published' AND p.kind IN ('notice','activity')
                   ORDER BY p.published_at DESC,p.id""",
                (user["user_id"], user["tenant_id"]),
            ).fetchall()

    @router.get("/v1/admin/community-posts")
    def admin_list(request: Request, response: Response, user=Depends(staff)):
        response.headers["Cache-Control"] = "no-store"
        with request.app.state.db.connection() as conn:
            return conn.execute(
                f"""SELECT {POST_FIELDS} FROM library_community_posts
                    WHERE tenant_id=%s ORDER BY updated_at DESC,id""", (user["tenant_id"],),
            ).fetchall()

    @router.post("/v1/admin/community-posts", status_code=201)
    def create(body: PostContent, request: Request, user=Depends(staff)):
        with request.app.state.db.connection() as conn:
            post = conn.execute(
                f"""INSERT INTO library_community_posts (tenant_id,id,kind,title,body,sort_order,keywords,featured,starts_at,location,capacity)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING {POST_FIELDS}""",
                (user["tenant_id"], uuid4(), body.kind, body.title, body.body, body.sort_order, body.keywords, body.featured,
                 body.starts_at, body.location, body.capacity),
            ).fetchone()
        event("community_post_created", tenant=user["tenant_id"], actor=user["user_id"], post_id=str(post["id"]))
        return post

    @router.patch("/v1/admin/community-posts/{post_id}")
    def edit(post_id: UUID, body: PostEdit, request: Request, user=Depends(staff)):
        tenant = user["tenant_id"]
        with request.app.state.db.connection() as conn:
            prior = checked_post(conn, tenant, post_id, body.version)
            if prior["source_id"] and prior["kind"] != body.kind:
                raise HTTPException(409, "guide_visibility_fixed")
            taken = enrollment_count(conn, tenant, post_id)
            if taken and (body.kind != "activity" or body.capacity is None or taken > body.capacity):
                raise HTTPException(409, "activity_has_enrollments")
            post = conn.execute(
                f"""UPDATE library_community_posts SET kind=%s,title=%s,body=%s,sort_order=%s,keywords=%s,featured=%s,
                        starts_at=%s,location=%s,capacity=%s,version=version+1,updated_at=now()
                    WHERE tenant_id=%s AND id=%s RETURNING {POST_FIELDS}""",
                (body.kind, body.title, body.body, body.sort_order, body.keywords, body.featured,
                 body.starts_at, body.location, body.capacity, tenant, post_id),
            ).fetchone()
        event("community_post_edited", tenant=tenant, actor=user["user_id"], post_id=str(post_id))
        return post

    @router.post("/v1/admin/community-posts/{post_id}/state")
    def set_state(post_id: UUID, body: PostState, request: Request, user=Depends(staff)):
        tenant = user["tenant_id"]
        with request.app.state.db.connection() as conn:
            prior = checked_post(conn, tenant, post_id, body.version)
            if prior["state"] == body.state or (body.state == "withdrawn" and prior["state"] != "published"):
                raise HTTPException(409, "community_post_state_invalid")
            post = conn.execute(
                f"""UPDATE library_community_posts
                    SET state=%s, version=version+1, updated_at=now(),
                        published_at=CASE WHEN %s='published' THEN now() ELSE published_at END
                    WHERE tenant_id=%s AND id=%s RETURNING {POST_FIELDS}""",
                (body.state, body.state, tenant, post_id),
            ).fetchone()
        event("community_post_state_changed", tenant=tenant, actor=user["user_id"],
              post_id=str(post_id), state=body.state)
        return post

    @router.get("/v1/admin/community-posts/{post_id}/enrollments")
    def roster(post_id: UUID, request: Request, response: Response, user=Depends(staff)):
        tenant = user["tenant_id"]
        response.headers["Cache-Control"] = "no-store"
        with request.app.state.db.connection() as conn:
            if not conn.execute(
                "SELECT 1 FROM library_community_posts WHERE tenant_id=%s AND id=%s AND kind='activity'",
                (tenant, post_id),
            ).fetchone():
                raise HTTPException(404, "community_post_not_found")
            return conn.execute(
                """SELECT a.user_id,a.display_name,a.username,a.phone,e.enrolled_at
                   FROM library_activity_enrollments e JOIN library_accounts a USING (tenant_id,user_id)
                   WHERE e.tenant_id=%s AND e.post_id=%s ORDER BY e.enrolled_at,a.user_id""",
                (tenant, post_id),
            ).fetchall()

    @router.put("/v1/reader/activities/{post_id}/enrollment")
    def enroll(post_id: UUID, request: Request, user=Depends(identity)):
        tenant, reader = user["tenant_id"], user["user_id"]
        if user["role"] != "reader":
            raise HTTPException(403, "reader_required")
        with request.app.state.db.connection() as conn:
            post = conn.execute(
                """SELECT state,capacity,starts_at>now() AS upcoming FROM library_community_posts
                   WHERE tenant_id=%s AND id=%s AND kind='activity' FOR UPDATE""", (tenant, post_id),
            ).fetchone()
            if not post or post["state"] != "published":
                raise HTTPException(404, "community_post_not_found")
            scope = (tenant, post_id, reader)
            if conn.execute(
                "SELECT 1 FROM library_activity_enrollments WHERE tenant_id=%s AND post_id=%s AND user_id=%s", scope,
            ).fetchone():
                return {"enrolled": True}
            if not post["capacity"] or not post["upcoming"]:
                raise HTTPException(409, "enrollment_closed")
            if enrollment_count(conn, tenant, post_id) >= post["capacity"]:
                raise HTTPException(409, "activity_full")
            conn.execute("INSERT INTO library_activity_enrollments (tenant_id,post_id,user_id) VALUES (%s,%s,%s)", scope)
        return {"enrolled": True}

    @router.delete("/v1/reader/activities/{post_id}/enrollment")
    def cancel(post_id: UUID, request: Request, user=Depends(identity)):
        with request.app.state.db.connection() as conn:
            conn.execute(
                "DELETE FROM library_activity_enrollments WHERE tenant_id=%s AND post_id=%s AND user_id=%s",
                (user["tenant_id"], post_id, user["user_id"]),
            )
        return {"enrolled": False}

    return router
