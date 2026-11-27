from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, StringConstraints, field_validator


class Reply(BaseModel):
    reply: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]

    @field_validator("reply")
    @classmethod
    def valid_text(cls, value):
        if any((ord(c) < 32 and c not in "\n\r\t") or 0xD800 <= ord(c) <= 0xDFFF for c in value):
            raise ValueError("invalid reply")
        return value


def routes(identity):
    router = APIRouter()

    def reader(user=Depends(identity)):
        if user["role"] != "reader":
            raise HTTPException(403, "reader_required")
        return user

    def staff(user=Depends(identity)):
        if user["role"] != "staff":
            raise HTTPException(403, "staff_required")
        return user

    @router.put("/v1/reader/help/{job_id}")
    def request_help(job_id: str, request: Request, user=Depends(reader)):
        with request.app.state.db.connection() as conn:
            row = conn.execute(
                """INSERT INTO library_help_requests (job_id,tenant_id,user_id)
                   SELECT id,tenant,owner_id FROM library_jobs
                   WHERE id=%s AND tenant=%s AND owner_id=%s AND role='reader'
                     AND status IN ('succeeded','failed')
                   ON CONFLICT (job_id) DO NOTHING RETURNING job_id""",
                (job_id, user["tenant_id"], user["user_id"]),
            ).fetchone()
            if not row and not conn.execute(
                "SELECT 1 FROM library_help_requests WHERE job_id=%s AND tenant_id=%s AND user_id=%s",
                (job_id, user["tenant_id"], user["user_id"]),
            ).fetchone():
                raise HTTPException(404, "job_not_visible")
        return {"job_id": job_id}

    @router.get("/v1/reader/help/{job_id}")
    def read_help(job_id: str, request: Request, response: Response, user=Depends(reader)):
        response.headers["Cache-Control"] = "no-store"
        with request.app.state.db.connection() as conn:
            return conn.execute(
                """SELECT created_at,reply,answered_at FROM library_help_requests
                   WHERE job_id=%s AND tenant_id=%s AND user_id=%s""",
                (job_id, user["tenant_id"], user["user_id"]),
            ).fetchone()

    @router.get("/v1/admin/help")
    def inbox(request: Request, response: Response, user=Depends(staff)):
        response.headers["Cache-Control"] = "no-store"
        with request.app.state.db.connection() as conn:
            return conn.execute(
                """SELECT h.job_id,j.query,h.created_at,h.reply,h.answered_at FROM library_help_requests h
                   JOIN library_jobs j ON j.id=h.job_id WHERE h.tenant_id=%s
                   ORDER BY h.answered_at IS NOT NULL,h.created_at DESC,h.job_id""",
                (user["tenant_id"],),
            ).fetchall()

    @router.post("/v1/admin/help/{job_id}/reply")
    def reply(job_id: str, body: Reply, request: Request, user=Depends(staff)):
        with request.app.state.db.connection() as conn:
            row = conn.execute(
                """UPDATE library_help_requests SET reply=%s,actor_id=%s,answered_at=now()
                   WHERE job_id=%s AND tenant_id=%s AND answered_at IS NULL RETURNING answered_at""",
                (body.reply, user["user_id"], job_id, user["tenant_id"]),
            ).fetchone()
            if not row:
                exists = conn.execute(
                    "SELECT 1 FROM library_help_requests WHERE job_id=%s AND tenant_id=%s",
                    (job_id, user["tenant_id"]),
                ).fetchone()
                if not exists:
                    raise HTTPException(404, "job_not_visible")
                raise HTTPException(409, "help_already_answered")
        return row

    return router
