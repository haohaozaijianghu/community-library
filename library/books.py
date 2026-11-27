import os
import re
from itertools import islice
from pathlib import Path
from typing import Annotated, Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request, UploadFile
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field, StringConstraints

from library.dependencies import LibraryError
from library.dependencies import MAX_BLOCKS, TYPES, Files, read_blocks
from library.core import event
from library.epub import epub_blocks


def storage():
    return Files(Path(os.environ.get("LIBRARY_FILE_ROOT", ".library-data")))


class Book(BaseModel):
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=240)]
    author: str | None = Field(default=None, max_length=240)
    isbn: str | None = Field(default=None, pattern=r"^(?:[0-9]{9}[0-9X]|[0-9]{13})$")
    shelf: str | None = Field(default=None, max_length=100)
    category: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=60,
                                                pattern=r"^[^\x00-\x1f\x7f]+$")] | None = None
    audience: Literal["reader", "staff"] = "reader"

class EditedBook(Book):
    revision: Annotated[int, Field(strict=True, ge=1)]


def scoped_book(conn, tenant, book_id, *, lock=False):
    sql = "SELECT archived_at,revision FROM library_books WHERE id=%s AND tenant=%s"
    book = conn.execute(sql + (" FOR UPDATE" if lock else ""), (book_id, tenant)).fetchone()
    if book is None:
        raise HTTPException(404, "book_not_found")
    return book


def require_active(book):
    if book["archived_at"] is not None:
        raise HTTPException(409, "book_archived")


def require_revision(book, revision):
    if book["revision"] != revision:
        raise HTTPException(409, "book_revision_conflict")


CATALOG_QUESTION = re.compile(
    r"(?:请问|请列出|列出|查看)?"
    r"(?:现在|目前|当前)?(?:本馆|馆里|图书馆|北馆|南馆)?(?:现在|目前|当前)?"
    r"(?:都)?(?:有哪(?:些|几本)|有什么|(?:还)?(?:可以|能)(?:看|读|借阅)?哪些)?"
    r"(?:馆藏目录|馆藏|书籍|图书|书|(?P<ebooks>(?:已发布的?)?(?:电子书(?:籍)?|电子图书)))[吗呢]?"
)


def catalog_requested(question, tenant, *, include_ebooks=False):
    question = re.sub(r"[\s，。！？,.!?]+", "", question)
    match = CATALOG_QUESTION.fullmatch(question)
    if not match or (match["ebooks"] and not include_ebooks):
        return False
    other_branch = {"north": "南馆", "south": "北馆"}.get(tenant)
    return not other_branch or other_branch not in question


def catalog_evidence(conn, job):
    rows = conn.execute(
        """SELECT id,title,author,isbn,shelf,count(*) OVER () AS total FROM library_books
           WHERE tenant=%s AND archived_at IS NULL AND (audience='reader' OR %s='staff') ORDER BY title,id LIMIT 30""",
        (job["tenant"], job["role"]),
    ).fetchall()
    evidence = []
    for book in rows:
        metadata = "；".join(value for value in (book["author"], book["isbn"], book["shelf"]) if value)
        label = f"《{book['title']}》"
        evidence.append({"id": f"book-{book['id']}", "text": f"{label}（{metadata}）" if metadata else label})
    total = rows[0]["total"] if rows else 0
    if total > len(evidence):
        heading = f"本馆共有 {total} 条可查阅的馆藏登记，以下是前 {len(evidence)} 条。"
    elif total:
        heading = f"本馆有 {total} 条可查阅的馆藏登记。"
    else:
        heading = "本馆当前没有可查阅的馆藏登记。"
    result = {
        "answer": "\n".join([heading, *(item["text"] for item in evidence)]),
        "citations": [item["id"] for item in evidence],
        "usage": {"input_tokens": 0, "output_tokens": 0, "cost_units": 0},
    }
    return evidence, result


def routes(identity):
    router = APIRouter()

    def staff(user=Depends(identity)):
        if user["role"] != "staff":
            event("access_denied", reason="staff_required")
            raise HTTPException(403, "staff_required")
        return user

    @router.get("/v1/books")
    def list_books(request: Request, user=Depends(identity)):
        with request.app.state.db.connection() as conn:
            return conn.execute(
                """SELECT id, title, author, isbn, shelf, category, audience, active_version
                   FROM library_books WHERE tenant=%s AND archived_at IS NULL AND (audience='reader' OR %s='staff')
                   ORDER BY title,id""", (user["tenant_id"], user["role"]),
            ).fetchall()

    @router.get("/v1/admin/books")
    def book_workflow(request: Request, user=Depends(staff)):
        with request.app.state.db.connection() as conn:
            return conn.execute(
                """SELECT b.id,b.title,b.author,b.isbn,b.shelf,b.category,b.audience,b.active_version,
                          b.archived_at,b.revision,latest.id AS latest_id,latest.status AS latest_status
                   FROM library_books b
                   LEFT JOIN LATERAL (
                       SELECT id,status FROM library_uploads WHERE book_id=b.id
                       ORDER BY created_at DESC,id LIMIT 1
                   ) latest ON true
                   WHERE b.tenant=%s ORDER BY b.title,b.id""",
                (user["tenant_id"],),
            ).fetchall()

    @router.put("/v1/admin/books/{book_id}")
    def edit_book(request: Request, book_id: str, body: EditedBook, user=Depends(staff)):
        with request.app.state.db.connection() as conn:
            book = scoped_book(conn, user["tenant_id"], book_id, lock=True)
            require_revision(book, body.revision)
            require_active(book)
            conn.execute(
                """UPDATE library_books SET title=%s,author=%s,isbn=%s,shelf=%s,audience=%s,
                   category=CASE WHEN %s THEN %s ELSE category END,revision=revision+1
                   WHERE id=%s""",
                (body.title, body.author, body.isbn, body.shelf, body.audience,
                 "category" in body.model_fields_set, body.category, book_id),
            )
        return {"book_id": book_id}

    def change_archive(request, user, book_id, revision, archived):
        with request.app.state.db.connection() as conn:
            book = scoped_book(conn, user["tenant_id"], book_id, lock=True)
            if archived == (book["archived_at"] is not None):
                return {"book_id": book_id}
            require_revision(book, revision)
            conn.execute(
                """UPDATE library_books SET archived_at=CASE WHEN %s THEN now() END, revision=revision+1
                   WHERE id=%s""", (archived, book_id),
            )
        event("book_archived" if archived else "book_restored", tenant=user["tenant_id"],
              actor=user["user_id"], book_id=book_id)
        return {"book_id": book_id}

    @router.delete("/v1/admin/books/{book_id}")
    def archive_book(request: Request, book_id: str, revision: Annotated[int, Query(ge=1)], user=Depends(staff)):
        return change_archive(request, user, book_id, revision, True)

    @router.post("/v1/admin/books/{book_id}/restore")
    def restore_book(request: Request, book_id: str, revision: Annotated[int, Query(ge=1)], user=Depends(staff)):
        return change_archive(request, user, book_id, revision, False)

    @router.post("/v1/books", status_code=201)
    def create(request: Request, body: Book, user=Depends(staff)):
        book_id = uuid4().hex
        with request.app.state.db.connection() as conn:
            conn.execute(
                """INSERT INTO library_books (id,tenant,title,author,isbn,shelf,audience,category)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                (book_id, user["tenant_id"], body.title, body.author, body.isbn, body.shelf, body.audience, body.category),
            )
        return {"book_id": book_id}

    @router.post("/v1/books/{book_id}/uploads", status_code=202)
    def upload(request: Request, book_id: str, file: UploadFile, user=Depends(staff)):
        with request.app.state.db.connection() as conn:
            require_active(scoped_book(conn, user["tenant_id"], book_id))
        files = storage()
        try:
            filename, key, _, checksum = files.write(file.filename or "", file.file, 20 * 1024 * 1024,
                                                  allowed_types=TYPES | {".epub"})
        except LibraryError as exc:
            raise HTTPException(exc.status, exc.code) from exc
        try:
            with request.app.state.db.connection() as conn:
                require_active(scoped_book(conn, user["tenant_id"], book_id, lock=True))
                row = conn.execute(
                    """INSERT INTO library_uploads (id,book_id,filename,file_key,checksum)
                       VALUES (%s,%s,%s,%s,%s) ON CONFLICT(book_id,checksum)
                       DO UPDATE SET checksum=EXCLUDED.checksum RETURNING id,file_key""",
                    (uuid4().hex, book_id, filename, key, checksum),
                ).fetchone()
        except HTTPException:
            files.path(key).unlink(missing_ok=True)
            raise
        if row["file_key"] != key:
            files.path(key).unlink(missing_ok=True)
        return {"job_id": row["id"]}

    @router.get("/v1/books/{book_id}/uploads")
    def list_uploads(request: Request, book_id: str, user=Depends(staff)):
        with request.app.state.db.connection() as conn:
            book = conn.execute("SELECT active_version FROM library_books WHERE id=%s AND tenant=%s",
                                (book_id, user["tenant_id"])).fetchone()
            if not book:
                raise HTTPException(404, "book_not_found")
            return conn.execute(
                """SELECT id AS job_id,filename,status,error_code,id=%s AS active,rights_basis IS NOT NULL AS was_published
                   FROM library_uploads WHERE book_id=%s ORDER BY created_at DESC,id""",
                (book["active_version"], book_id),
            ).fetchall()

    @router.get("/v1/books/{book_id}/versions/{upload_id}/preview")
    def preview(request: Request, book_id: str, upload_id: str, user=Depends(staff)):
        with request.app.state.db.connection() as conn:
            version = conn.execute(
                """SELECT u.status FROM library_uploads u JOIN library_books b ON b.id=u.book_id
                   WHERE b.id=%s AND u.id=%s AND b.tenant=%s""",
                (book_id, upload_id, user["tenant_id"]),
            ).fetchone()
            if not version:
                raise HTTPException(404, "upload_not_found")
            if version["status"] != "ready":
                raise HTTPException(409, "version_not_ready")
            return conn.execute(
                """SELECT locator,left(body,1200) AS text FROM library_passages
                   WHERE version_id=%s ORDER BY ordinal LIMIT 8""",
                (upload_id,),
            ).fetchall()

    @router.get("/v1/ingestion-jobs/{upload_id}")
    def ingest_status(request: Request, upload_id: str, user=Depends(staff)):
        with request.app.state.db.connection() as conn:
            row = conn.execute(
                """SELECT u.id AS job_id,u.book_id,u.status,u.error_code FROM library_uploads u
                   JOIN library_books b ON b.id=u.book_id WHERE u.id=%s AND b.tenant=%s""",
                (upload_id, user["tenant_id"]),
            ).fetchone()
        if not row:
            raise HTTPException(404, "upload_not_found")
        return row

    @router.post("/v1/books/{book_id}/versions/{upload_id}/retry", status_code=202)
    def retry_upload(request: Request, book_id: str, upload_id: str, user=Depends(staff)):
        with request.app.state.db.connection() as conn:
            require_active(scoped_book(conn, user["tenant_id"], book_id, lock=True))
            upload = conn.execute(
                """SELECT u.id,u.status,u.error_code FROM library_uploads u
                   JOIN library_books b ON b.id=u.book_id
                   WHERE b.id=%s AND u.id=%s AND b.tenant=%s FOR UPDATE OF u""",
                (book_id, upload_id, user["tenant_id"]),
            ).fetchone()
            if not upload:
                raise HTTPException(404, "upload_not_found")
            if upload["status"] != "failed" or upload["error_code"] != "file_unavailable":
                raise HTTPException(409, "upload_not_retryable")
            conn.execute(
                "UPDATE library_uploads SET status='queued',error_code=NULL WHERE id=%s", (upload_id,),
            )
        return {"job_id": upload_id}

    @router.post("/v1/books/{book_id}/versions/{upload_id}/publish")
    def publish(
        request: Request, book_id: str, upload_id: str,
        rights_basis: Annotated[str, Form(min_length=1, max_length=500)], user=Depends(staff),
    ):
        if not rights_basis.strip():
            raise HTTPException(400, "rights_basis_required")
        with request.app.state.db.connection() as conn:
            require_active(scoped_book(conn, user["tenant_id"], book_id, lock=True))
            row = conn.execute(
                """SELECT u.status FROM library_uploads u JOIN library_books b ON b.id=u.book_id
                   WHERE b.id=%s AND u.id=%s AND b.tenant=%s""",
                (book_id, upload_id, user["tenant_id"]),
            ).fetchone()
            if not row:
                raise HTTPException(404, "upload_not_found")
            if row["status"] != "ready":
                raise HTTPException(409, "version_not_ready")
            conn.execute("UPDATE library_uploads SET rights_basis=%s WHERE id=%s",
                         (rights_basis.strip(), upload_id))
            conn.execute("UPDATE library_books SET active_version=%s WHERE id=%s", (upload_id, book_id))
        return {"version_id": upload_id}

    return router


def ingest(conn):
    with conn.transaction():
        upload = conn.execute(
            """SELECT id,file_key FROM library_uploads WHERE status IN ('queued','running')
               ORDER BY created_at,id LIMIT 1"""
        ).fetchone()
        if not upload:
            return False
        conn.execute("UPDATE library_uploads SET status='running' WHERE id=%s", (upload["id"],))
    try:
        path = storage().path(upload["file_key"])
        extracted = list(islice(epub_blocks(path), MAX_BLOCKS + 1)) if path.suffix == ".epub" else read_blocks(path)
        if not extracted:
            raise LibraryError(422, "no_extractable_text", "文件没有可提取的正文")
        if len(extracted) > MAX_BLOCKS:
            raise LibraryError(422, "document_limit", "原文块数量超过上限")
        blocks = [
            {"text": block.text, "locator": block.locator}
            for block in extracted
        ]
        status, error = "ready", None
    except (LibraryError, OSError) as exc:
        status, error = "failed", exc.code if isinstance(exc, LibraryError) else "file_unavailable"
        blocks = None
    with conn.transaction():
        if blocks:
            with conn.cursor() as cursor:
                cursor.executemany(
                    """INSERT INTO library_passages (version_id,ordinal,locator,body)
                       VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
                    [(upload["id"], index, block["locator"], block["text"]) for index, block in enumerate(blocks)],
                )
        conn.execute("UPDATE library_uploads SET status=%s,error_code=%s,blocks=%s WHERE id=%s",
                     (status, error, Jsonb(blocks), upload["id"]))
    return True


def published_evidence(conn, job):
    books = conn.execute(
        """SELECT b.id,b.title,b.author,b.isbn,b.shelf,u.id AS version,u.blocks FROM library_books b
           LEFT JOIN library_uploads u
             ON u.id=b.active_version AND u.book_id=b.id AND u.status='ready'
           WHERE b.tenant=%s AND b.archived_at IS NULL AND (b.audience='reader' OR %s='staff') ORDER BY b.id""",
        (job["tenant"], job["role"]),
    ).fetchall()
    words = re.findall(r"[\w-]+", job["query"].casefold())
    evidence = []
    for book in books:
        catalog = "；".join(value for value in (book["title"], book["author"], book["isbn"], book["shelf"]) if value)
        if any(word in catalog.casefold() for word in words):
            evidence.append({"id": f"book-{book['id']}", "text": f"本馆馆藏登记：{catalog}"})
        for i, block in enumerate(book["blocks"] or []):
            if any(word in block["text"].casefold() for word in words):
                evidence.append({
                    "id": f"book-version-{book['version']}-{i}",
                    "text": f"《{book['title']}》{block['locator']}：{block['text']}",
                })
    return evidence[:6]
