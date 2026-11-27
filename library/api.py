import logging
import os
import re
from contextlib import asynccontextmanager
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, StrictBool, StrictStr, field_validator

from library.accounts import routes as account_routes
from library.agent import routes as agent_routes
from library.books import routes
from library.community import routes as community_routes
from library.core import ROOT, digest, event, pool
from library.fixtures import sync_demo_data
from library.helpdesk import routes as helpdesk_routes
from library.model_connections import routes as model_routes
from library.sources import reader_notices
from library.portal import routes as portal_routes, settings as portal_settings
from library.profile import router as profile_router
from library.web_auth import router as auth_router, session_user

logging.getLogger("library").setLevel(logging.INFO)
logging.getLogger("library").addHandler(logging.StreamHandler())
bearer = HTTPBearer(auto_error=False)


def identity(request: Request, credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]):
    if request.headers.get("Authorization") is None:
        user = session_user(request)
    else:
        user = None
        if credentials and os.getenv("LIBRARY_DEMO_TOKENS", "1") == "1":
            with request.app.state.db.connection() as conn:
                user = conn.execute(
                    """SELECT a.tenant_id,a.user_id,a.role FROM library_demo_tokens t
                       JOIN library_accounts a USING (tenant_id,user_id)
                       WHERE t.digest=%s AND a.active""", (digest(credentials.credentials),),
                ).fetchone()
    if not user:
        event("access_denied", reason="invalid_credentials")
        raise HTTPException(401, "invalid_credentials", headers={"WWW-Authenticate": "Bearer"})
    return user


User = Annotated[dict, Depends(identity)]


class Submission(BaseModel):
    query: StrictStr
    web_search: StrictBool = False
    conversation_id: UUID | None = None

    @field_validator("query")
    @classmethod
    def normalize(cls, value):
        value = value.strip()
        if not 1 <= len(value) <= 200 or "\x00" in value or any(0xD800 <= ord(c) <= 0xDFFF for c in value):
            raise ValueError("query must contain 1–200 valid characters")
        return value


@asynccontextmanager
async def lifespan(app):
    with pool() as connections:
        connections.wait()
        with connections.connection() as conn:
            conn.execute((ROOT / "library/auth_schema.sql").read_text(encoding="utf-8"))
            conn.execute((ROOT / "library/agent_schema.sql").read_text(encoding="utf-8"))
            conn.execute((ROOT / "library/model_schema.sql").read_text(encoding="utf-8"))
            conn.execute((ROOT / "library/community_schema.sql").read_text(encoding="utf-8"))
            sync_demo_data(conn)
        app.state.db = connections
        yield


app = FastAPI(title="Community Library", lifespan=lifespan)
app.include_router(auth_router)
app.include_router(profile_router)
pages = ROOT / "web/library"
app.mount("/library-assets", StaticFiles(directory=pages), name="library-assets")


@app.get("/", include_in_schema=False)
@app.get("/reader", include_in_schema=False)
@app.get("/admin", include_in_schema=False)
@app.get("/admin/help", include_in_schema=False)
@app.get("/admin/portal", include_in_schema=False)
@app.get("/admin/community", include_in_schema=False)
@app.get("/admin/books", include_in_schema=False)
@app.get("/admin/knowledge", include_in_schema=False)
@app.get("/admin/agent", include_in_schema=False)
@app.get("/admin/agent/preview", include_in_schema=False)
@app.get("/admin/settings", include_in_schema=False)
@app.get("/admin/settings/models", include_in_schema=False)
@app.get("/admin/settings/accounts", include_in_schema=False)
@app.get("/admin/accounts", include_in_schema=False)
@app.get("/admin/models", include_in_schema=False)
def home():
    return FileResponse(pages / "index.html")


@app.get("/v1/me")
def current_user(user: User):
    return {key: user[key] for key in ("tenant_id", "user_id", "role")}


@app.get("/v1/reader/guides")
def reader_guides(request: Request, user: User):
    with request.app.state.db.connection() as conn:
        return conn.execute(
            """SELECT COALESCE(source_id,id::text) AS id,title,body AS text
               FROM library_community_posts
               WHERE tenant_id=%s AND kind='guide' AND state='published'
               ORDER BY sort_order,published_at DESC,id""",
            (user["tenant_id"],),
        ).fetchall()


@app.get("/v1/reader/notices")
def library_notices(request: Request, user: User):
    with request.app.state.db.connection() as conn:
        config = portal_settings(conn, user["tenant_id"])
    return (reader_notices(user["tenant_id"], config["notice_feed"])
            if config["external_notices"] else {"state": "disabled", "items": []})


@app.exception_handler(RequestValidationError)
async def invalid_request(request, exc):
    return JSONResponse({"detail": "invalid_request"}, status_code=400)


@app.get("/healthz")
def health(request: Request):
    with request.app.state.db.connection() as conn:
        conn.execute("SELECT 1 FROM library_jobs LIMIT 1")
    return {"status": "ok"}


@app.post("/v1/jobs", status_code=202)
def submit(
    request: Request, user: User, body: Submission,
    idempotency_key: Annotated[str | None, Header()] = None,
):
    return enqueue(request, user, body, idempotency_key, "library")


@app.post("/v1/agent/jobs", status_code=202)
def submit_agent(
    request: Request, user: User, body: Submission,
    idempotency_key: Annotated[str | None, Header()] = None,
):
    if body.web_search:
        raise HTTPException(400, "agent_uses_published_books")
    return enqueue(request, user, body, idempotency_key, "books")


def enqueue(request, user, body, idempotency_key, source_mode):
    if not idempotency_key or len(idempotency_key) > 128 or "\x00" in idempotency_key:
        raise HTTPException(400, "invalid_idempotency_key")
    if body.web_search and not re.fullmatch(r"[\w .,:()'’&-]{1,100}", body.query):
        raise HTTPException(400, "public_lookup_requires_catalog_terms")
    scope = (user["tenant_id"], user["user_id"], idempotency_key)
    job_id = uuid4().hex
    conversation = body.conversation_id.hex if body.conversation_id else None
    with request.app.state.db.connection() as conn:
        if conversation and not conn.execute(
            """SELECT id FROM library_jobs WHERE id=%s AND conversation_id IS NULL
               AND tenant=%s AND owner_id=%s AND role=%s FOR UPDATE""",
            (conversation, *scope[:2], user["role"]),
        ).fetchone():
            raise HTTPException(404, "conversation_not_found")
        created = conn.execute(
            """INSERT INTO library_jobs (id, tenant, owner_id, idem, role, query, web_search, source_mode, conversation_id)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (tenant, owner_id, idem) DO NOTHING RETURNING id""",
            (job_id, *scope, user["role"], body.query, body.web_search, source_mode, conversation),
        ).fetchone()
        if created:
            return {"job_id": job_id}
        prior = conn.execute(
            """SELECT id,query,web_search,source_mode,role,conversation_id FROM library_jobs
               WHERE tenant=%s AND owner_id=%s AND idem=%s""", scope
        ).fetchone()
        if prior["role"] == "staff" and user["role"] != "staff":
            raise HTTPException(409, "idempotency_conflict")
        if (prior["query"], prior["web_search"], prior["source_mode"], prior["conversation_id"]) != (
            body.query, body.web_search, source_mode, conversation
        ):
            raise HTTPException(409, "idempotency_conflict")
        return {"job_id": prior["id"]}


@app.get("/v1/agent/jobs")
def job_history(request: Request, user: User):
    with request.app.state.db.connection() as conn:
        return conn.execute(
            """SELECT id AS job_id,query,status,created_at FROM library_jobs
               WHERE tenant=%s AND owner_id=%s AND source_mode='books' AND (role='reader' OR %s='staff')
               ORDER BY created_at DESC,id DESC LIMIT 50""",
            (user["tenant_id"], user["user_id"], user["role"]),
        ).fetchall()


@app.get("/v1/admin/overview")
def admin_overview(request: Request, user: User):
    if user["role"] != "staff":
        raise HTTPException(403, "staff_required")
    with request.app.state.db.connection() as conn:
        return conn.execute(
            """WITH books AS MATERIALIZED (
                   SELECT id,title,active_version FROM library_books WHERE tenant=%(tenant)s AND archived_at IS NULL
               ), waiting AS MATERIALIZED (
                   SELECT b.id AS book_id,b.title,u.id AS version_id,u.filename,u.created_at
                   FROM books b JOIN library_uploads u ON u.book_id=b.id
                   WHERE u.status='ready' AND u.rights_basis IS NULL AND u.id IS DISTINCT FROM b.active_version
               ), questions AS (
                   SELECT count(*) AS total,
                          count(*) FILTER (WHERE status IN ('queued','running')) AS pending,
                          count(*) FILTER (WHERE status='succeeded') AS completed,
                          count(*) FILTER (WHERE status='failed') AS failed
                   FROM library_jobs WHERE tenant=%(tenant)s AND created_at >= now() - interval '7 days'
               )
               SELECT (SELECT count(*) FROM books WHERE active_version IS NOT NULL) AS published_books,
                      (SELECT count(*) FROM waiting) AS pending_versions,
                      (SELECT count(*) FROM library_accounts WHERE tenant_id=%(tenant)s AND active) AS active_accounts,
                      to_jsonb(questions) AS questions,
                      COALESCE(
                          (SELECT jsonb_agg(to_jsonb(recent) ORDER BY recent.created_at DESC,recent.version_id DESC)
                           FROM (SELECT * FROM waiting ORDER BY created_at DESC,version_id DESC LIMIT 8) recent),
                          '[]'::jsonb
                      ) AS review_queue
               FROM questions""",
            {"tenant": user["tenant_id"]},
        ).fetchone()


@app.get("/v1/question-history")
def question_history(request: Request, user: User, job_id: str | None = None):
    with request.app.state.db.connection() as conn:
        return conn.execute(
            """SELECT id AS job_id,query,status,created_at,source_mode FROM library_jobs
               WHERE tenant=%s AND owner_id=%s AND (role='reader' OR %s='staff')
                 AND (%s::text IS NULL OR id=%s)
               ORDER BY created_at DESC,id DESC LIMIT 50""",
            (user["tenant_id"], user["user_id"], user["role"], job_id, job_id),
        ).fetchall()


@app.get("/v1/conversations")
def conversations(request: Request, user: User, search: str = ""):
    with request.app.state.db.connection() as conn:
        return conn.execute(
            """SELECT root.id AS conversation_id,root.query,latest.id AS job_id,
                      latest.status,latest.created_at,latest.source_mode
               FROM library_jobs root
               CROSS JOIN LATERAL (
                   SELECT j.id,j.status,j.created_at,j.source_mode
                   FROM library_jobs j
                   WHERE (j.id=root.id OR j.conversation_id=root.id)
                     AND j.tenant=root.tenant AND j.owner_id=root.owner_id AND j.role=root.role
                   ORDER BY j.created_at DESC,j.id DESC LIMIT 1
               ) latest
               WHERE root.tenant=%s AND root.owner_id=%s AND root.role=%s AND root.conversation_id IS NULL
                 AND (%s='' OR EXISTS (
                   SELECT 1 FROM library_jobs j WHERE (j.id=root.id OR j.conversation_id=root.id)
                     AND j.tenant=root.tenant AND j.owner_id=root.owner_id AND j.role=root.role
                     AND strpos(lower(j.query),lower(%s))>0
                 ))
               ORDER BY latest.created_at DESC,latest.id DESC LIMIT 50""",
            (user["tenant_id"], user["user_id"], user["role"], search, search),
        ).fetchall()


@app.get("/v1/conversations/{job_id}")
def conversation_turns(request: Request, user: User, job_id: UUID):
    with request.app.state.db.connection() as conn:
        anchor = conn.execute(
            """SELECT COALESCE(conversation_id,id) AS root,role FROM library_jobs
               WHERE id=%s AND tenant=%s AND owner_id=%s AND role=%s""",
            (job_id.hex, user["tenant_id"], user["user_id"], user["role"]),
        ).fetchone()
        if not anchor:
            raise HTTPException(404, "conversation_not_found")
        root = anchor["root"]
        return conn.execute(
            """SELECT id AS job_id,%s::text AS conversation_id,query,status,created_at,source_mode,result
               FROM library_jobs WHERE (id=%s OR conversation_id=%s)
                 AND tenant=%s AND owner_id=%s AND role=%s
               ORDER BY created_at,id""",
            (root, root, root, user["tenant_id"], user["user_id"], anchor["role"]),
        ).fetchall()


@app.get("/v1/agent/jobs/{job_id}/diagnostics")
def question_diagnostics(request: Request, job_id: str, user: User):
    if user["role"] != "staff":
        raise HTTPException(403, "staff_required")
    with request.app.state.db.connection() as conn:
        job = conn.execute(
            """SELECT evidence FROM library_jobs
               WHERE id=%s AND tenant=%s AND owner_id=%s AND source_mode='books'""",
            (job_id, user["tenant_id"], user["user_id"]),
        ).fetchone()
        if not job:
            raise HTTPException(404, "job_not_found")
        attempts = conn.execute(
            """SELECT a.ordinal,a.provider,a.outcome,a.model_id,
                      managed.model_name AS name
               FROM library_attempts a
               LEFT JOIN library_model_connections managed ON managed.id=a.model_id AND managed.tenant=%s
               WHERE a.job_id=%s ORDER BY a.ordinal""",
            (user["tenant_id"], job_id),
        ).fetchall()
        return {"evidence": job["evidence"] or [], "attempts": attempts}


@app.get("/v1/jobs/{job_id}")
def get_job(request: Request, job_id: str, user: User):
    with request.app.state.db.connection() as conn:
        job = conn.execute(
            """SELECT id AS job_id, status, result, error_code FROM library_jobs
               WHERE id=%s AND tenant=%s AND owner_id=%s AND (role='reader' OR %s='staff')""",
            (job_id, user["tenant_id"], user["user_id"], user["role"]),
        ).fetchone()
    if not job:
        event("access_denied", reason="job_not_visible", job_id=job_id)
        raise HTTPException(404, "job_not_found")
    if job["error_code"] is None:
        del job["error_code"]
    return job


@app.get("/v1/jobs/{job_id}/citations/{citation_id}")
def cited_evidence(request: Request, job_id: str, citation_id: str, user: User):
    with request.app.state.db.connection() as conn:
        job = conn.execute(
            """SELECT evidence,result FROM library_jobs
               WHERE id=%s AND tenant=%s AND owner_id=%s AND (role='reader' OR %s='staff')""",
            (job_id, user["tenant_id"], user["user_id"], user["role"]),
        ).fetchone()
    if job and job["result"] and citation_id in job["result"]["citations"]:
        for evidence in job["evidence"] or ():
            if evidence["id"] == citation_id:
                return evidence
    event("access_denied", reason="citation_not_visible", job_id=job_id)
    raise HTTPException(404, "citation_not_found")


app.include_router(routes(identity))
app.include_router(agent_routes(identity))

app.include_router(account_routes(identity))
app.include_router(community_routes(identity))
app.include_router(portal_routes(identity))
app.include_router(helpdesk_routes(identity))

app.include_router(model_routes(identity))
