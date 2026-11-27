import re
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field, StrictBool, StringConstraints, field_validator, model_validator

from library.books import catalog_requested
from library.core import ROOT, event, fixture_evidence, portal_evidence
from library.model_connections import connection

DEFAULTS = {"search_titles": True, "search_content": True, "max_sources": 6, "prompt": "", "answer_model": "default"}
TERMS = re.compile(r"[a-z0-9][a-z0-9-]*|[\u3400-\u9fff]+", re.I)
QUESTION_WORDS = re.compile(
    r"(?:请问|帮我|这本书|书里面|这段|一共|有哪些|讲了什么|是什么|"
    r"提到的|在什么地方|在哪里|在哪儿|在哪|什么时候|几点|怎么|如何|为什么|书里|书中|介绍一下|再说说|的|吗|呢|吧)"
)

EBOOK_CATEGORY_QUESTION = re.compile(
    r"(?:请问|请列出|列出|查看)?(?:现在|目前|当前)?(?:本馆|馆里|图书馆|北馆|南馆)?(?:现在|目前|当前)?"
    r"(?:都)?(?:(?:有(?:哪些|哪几类|什么))?(?:种类的|分类的|类型的)?(?:电子书籍|电子图书|电子书|书籍|图书|书)"
    r"(?:都)?(?:有(?:哪些|哪几类|什么)(?:种类|类别|分类|类型)?|(?:种类|类别|分类|类型))"
    r"|(?:有(?:哪些|哪几类|什么))(?:种类|类别|分类|类型)(?:的)?(?:电子书籍|电子图书|电子书|书籍|图书|书)?)[吗么呢]?"
)
TITLE_AVAILABILITY = re.compile(
    r"(?:(?:请问|想问)\s*)?(?:(?P<branch>本馆|馆里|图书馆|北馆|南馆)\s*)?"
    r"(?:(?:现在|目前|当前)\s*)?(?P<verb>有没有|是否有|有)\s*"
    r"(?P<title>《[^《》]+》|[^《》]+?)\s*(?P<qualifier>(?:这|那)本(?:电子)?书)?"
    r"\s*(?P<particle>[吗么呢])?\s*[。！？?!]*"
)
ELIGIBLE_BOOKS = """WITH eligible AS NOT MATERIALIZED (
    SELECT b.id AS book_id,b.title,b.author,b.isbn,b.shelf,b.category,u.id AS version
    FROM library_books b JOIN library_uploads u ON u.id=b.active_version AND u.book_id=b.id
    WHERE b.tenant=%(tenant)s AND b.archived_at IS NULL AND (b.audience='reader' OR %(role)s='staff') AND u.status='ready'
)"""


class Settings(BaseModel):
    search_titles: StrictBool
    search_content: StrictBool
    max_sources: Annotated[int, Field(strict=True, ge=1, le=12)]

    answer_model: str | None = Field(default=None, max_length=36)
    prompt: str | None = Field(default=None, max_length=4000)

    @model_validator(mode="after")
    def require_source(self):
        if not (self.search_titles or self.search_content):
            raise ValueError("至少启用一类检索")
        if self.prompt is not None:
            self.prompt = self.prompt.strip()
            if "\x00" in self.prompt:
                raise ValueError("Prompt 包含不支持的字符")
        return self


class KnowledgeSearch(BaseModel):
    query: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
    role: Literal["reader", "staff"]
    scope: Literal["books", "materials"] = "books"

    @field_validator("query")
    @classmethod
    def validate_text(cls, query):
        if "\x00" in query or any(0xD800 <= ord(char) <= 0xDFFF for char in query):
            raise ValueError("invalid query text")
        return query


def settings(conn, tenant):
    return conn.execute(
        "SELECT search_titles,search_content,max_sources,prompt,answer_model FROM library_agent_settings WHERE tenant=%s", (tenant,)
    ).fetchone() or DEFAULTS


def routes(identity):
    router = APIRouter(prefix="/v1/agent")

    def manager(user=Depends(identity)):
        if user["role"] != "staff":
            event("access_denied", reason="agent_settings_staff_required")
            raise HTTPException(403, "staff_required")
        return user

    @router.get("/settings")
    def read_settings(request: Request, user=Depends(manager)):
        with request.app.state.db.connection() as conn:
            return settings(conn, user["tenant_id"])

    @router.put("/settings")
    def save_settings(request: Request, body: Settings, user=Depends(manager)):
        with request.app.state.db.connection() as conn:
            selection = body.answer_model
            if selection is not None and selection not in {"default", "mock"}:
                try:
                    model_id = UUID(selection)
                except ValueError as error:
                    raise HTTPException(400, "invalid_answer_model") from error
                if not connection(conn, user["tenant_id"], model_id, for_answer=True):
                    raise HTTPException(400, "invalid_answer_model")
                selection = str(model_id)
            saved = conn.execute(
                """INSERT INTO library_agent_settings (tenant,search_titles,search_content,max_sources,prompt,answer_model)
                   VALUES (%s,%s,%s,%s,COALESCE(%s::text,''),COALESCE(%s::text,'default'))
                   ON CONFLICT (tenant) DO UPDATE SET search_titles=excluded.search_titles,
                   search_content=excluded.search_content,max_sources=excluded.max_sources,
                   prompt=COALESCE(%s::text,library_agent_settings.prompt),
                   answer_model=COALESCE(%s::text,library_agent_settings.answer_model)
                   RETURNING search_titles,search_content,max_sources,prompt,answer_model""",
                (user["tenant_id"], body.search_titles, body.search_content, body.max_sources,
                 body.prompt, selection, body.prompt, selection),
            ).fetchone()
        event("agent_settings_saved", tenant=user["tenant_id"], actor=user["user_id"],
              search_titles=body.search_titles, search_content=body.search_content,
              max_sources=body.max_sources, prompt_configured=bool(saved["prompt"]))
        return saved

    @router.get("/knowledge")
    def knowledge(request: Request, response: Response, user=Depends(manager)):
        response.headers["Cache-Control"] = "no-store"
        tenant = user["tenant_id"]
        with request.app.state.db.connection() as conn:
            books = conn.execute(
                """SELECT b.id,b.title,b.author,b.isbn,b.shelf,b.category,b.audience,active.id AS active_version,
                          latest.id AS latest_id,latest.status AS latest_status,
                          (SELECT count(*) FROM library_passages p WHERE p.version_id=active.id) AS passages
                   FROM library_books b
                   LEFT JOIN library_uploads active
                     ON active.id=b.active_version AND active.book_id=b.id AND active.status='ready'
                   LEFT JOIN LATERAL (
                       SELECT id,status FROM library_uploads
                       WHERE book_id=b.id ORDER BY created_at DESC,id DESC LIMIT 1
                   ) latest ON true
                   WHERE b.tenant=%s AND b.archived_at IS NULL ORDER BY b.title,b.id""",
                (tenant,),
            ).fetchall()
            materials = conn.execute(
                """SELECT id,title,kind,state,source_id
                   FROM library_community_posts WHERE tenant_id=%s
                   ORDER BY sort_order,updated_at DESC,id""", (tenant,),
            ).fetchall()
            config = settings(conn, tenant)
        return {"books": books, "materials": materials,
                "search_titles": config["search_titles"], "search_content": config["search_content"]}

    @router.get("/knowledge/books/{book_id}/versions/{version_id}/passages")
    def indexed_passages(book_id: str, version_id: str, request: Request, response: Response,
                         offset: Annotated[int, Query(ge=0, le=10000)] = 0, user=Depends(manager)):
        response.headers["Cache-Control"] = "no-store"
        with request.app.state.db.connection() as conn:
            book = conn.execute(
                """SELECT b.title,b.author,b.isbn,b.shelf,b.category,b.audience FROM library_books b
                   JOIN library_uploads u ON u.id=b.active_version AND u.book_id=b.id AND u.status='ready'
                   WHERE b.id=%s AND b.tenant=%s AND b.archived_at IS NULL AND u.id=%s FOR SHARE OF b""",
                (book_id, user["tenant_id"], version_id),
            ).fetchone()
            if not book:
                raise HTTPException(404, "knowledge_version_unavailable")
            page = conn.execute(
                """SELECT ordinal, locator, body AS text FROM library_passages
                   WHERE version_id=%s ORDER BY ordinal LIMIT 21 OFFSET %s""",
                (version_id, offset),
            ).fetchall()
        return {**book, "items": page[:20], "next_offset": offset + 20 if len(page) > 20 else None}

    @router.get("/knowledge/materials/{material_id}")
    def material(material_id: UUID, request: Request, response: Response, user=Depends(manager)):
        response.headers["Cache-Control"] = "no-store"
        with request.app.state.db.connection() as conn:
            item = conn.execute(
                """SELECT title,body,kind,state,keywords,source_id FROM library_community_posts
                   WHERE tenant_id=%s AND id=%s""", (user["tenant_id"], material_id),
            ).fetchone()
        if not item:
            raise HTTPException(404, "community_post_not_found")
        return item

    @router.post("/knowledge/search")
    def search_knowledge(body: KnowledgeSearch, request: Request, response: Response, user=Depends(manager)):
        response.headers["Cache-Control"] = "no-store"
        tenant = user["tenant_id"]
        with request.app.state.db.connection() as conn:
            job = {"query": body.query, "tenant": tenant, "role": body.role}
            if body.scope == "materials":
                return {"evidence": portal_evidence(conn, job) + fixture_evidence(conn, job), "catalog": False}
            evidence, catalog = retrieve(conn, job, settings(conn, tenant))
        return {"evidence": evidence, "catalog": catalog is not None}

    @router.get("/model-workbench")
    def workbench(user=Depends(manager)):
        return {
            "mode": "mock",
            "url": None,
            "default_prompt": (ROOT / "prompt.md").read_text(encoding="utf-8"),
        }

    return router


def search_terms(query):
    tokens = TERMS.findall(QUESTION_WORDS.sub(" ", query.lower()))
    titles = [title.strip().lower() for title in re.findall(r"《([^》]+)》", query)]
    return list(dict.fromkeys(
        text for text in [*titles, *tokens] if len(text) >= 2 or text.isascii()
    ))[:12]


def evidence_for_book(row, text, ordinal=None, locator=None):
    evidence_id = f"book-version-{row['version']}"
    result = {
        "id": evidence_id + (f"-{ordinal}" if ordinal is not None else "-title"),
        "text": text,
        "title": row["title"],
        "version_id": row["version"],
    }
    if locator is not None:
        result["locator"] = locator
    return result


def category_requested(query, tenant):
    normalized = re.sub(r"[\s，。！？,.!?]+", "", query)
    other_branch = {"north": "南馆", "south": "北馆"}.get(tenant)
    return bool(EBOOK_CATEGORY_QUESTION.fullmatch(normalized) and
                (not other_branch or other_branch not in normalized))


def availability_title(query, tenant):
    match = TITLE_AVAILABILITY.fullmatch(query.strip())
    if not match:
        return None
    other_branch = {"north": "南馆", "south": "北馆"}.get(tenant)
    if other_branch and match["branch"] == other_branch:
        return None
    name = match["title"].strip()
    if name.startswith("《"):
        return name[1:-1].strip() or None
    if re.search(r"(?:这|那)本(?:电子)?书", name):
        return None
    if match["qualifier"] or (match["verb"] != "有" and not name.startswith(("关于", "有关", "介绍"))):
        return name or None
    return None


def title_evidence(row):
    metadata = "；".join(f"{label} {row[key]}" for key, label in (
        ("author", "作者"), ("category", "分类"), ("isbn", "ISBN"), ("shelf", "馆藏位置"),
    ) if row[key])
    text = f"已发布电子书《{row['title']}》" + (f"（{metadata}）" if metadata else "")
    return evidence_for_book(row, text)


def direct_result(answer, evidence):
    return evidence, {
        "answer": answer, "citations": [doc["id"] for doc in evidence],
        "usage": {"input_tokens": 0, "output_tokens": 0, "cost_units": 0},
    }


def category_result(conn, params):
    rows = conn.execute(
        ELIGIBLE_BOOKS + """
        SELECT category, books, representative, count(*) OVER () AS total_categories FROM (
            SELECT COALESCE(NULLIF(btrim(category),''),'未分类') AS category, count(*) AS books,
                   min(version) AS representative
            FROM eligible GROUP BY 1
        ) categories ORDER BY books DESC,category LIMIT %(limit)s""", params,
    ).fetchall()
    evidence = [
        {"id": f"book-version-{row['representative']}-category", "title": row["category"],
         "locator": "已发布书目分类汇总", "text": f"{row['category']}：{row['books']} 本"}
        for row in rows
    ]
    if not rows:
        return direct_result("本馆当前没有可查阅的已发布电子书。", [])
    total = rows[0]["total_categories"]
    heading = f"当前可查阅的已发布电子书分为 {total} 类。"
    if total > len(rows):
        heading += f"以下列出前 {len(rows)} 类。"
    return direct_result("\n".join([heading, *(item["text"] for item in evidence)]), evidence)


def referenced_book_evidence(conn, query, context, scope, config):
    reference = r"[这那](?:一)?本(?:电子书|图书|书)?|这部书|该书|(?<!其)它"
    metadata = r"作者|ISBN|馆藏位置|书架|分类"
    if "《" in query or not (
        re.search(reference, query) or re.match(rf"^(?:那|那么)?(?:{metadata})", query, re.I)
    ):
        return None
    for turn in reversed(context):
        if turn["source_mode"] != "books":
            continue
        citations = (turn["result"] or {}).get("citations", ())
        versions = {doc["version_id"] for doc in turn["evidence"] or ()
                    if doc["id"] in citations and doc.get("version_id")}
        if not versions:
            continue
        book_ids = conn.execute(
            """SELECT DISTINCT u.book_id FROM library_uploads u
               JOIN library_books b ON b.id=u.book_id
               WHERE u.id=ANY(%s) AND b.tenant=%s""",
            (list(versions), scope["tenant"]),
        ).fetchall()
        if len(book_ids) != 1:
            return None
        params = {**scope, "book": book_ids[0]["book_id"]}
        book = conn.execute(ELIGIBLE_BOOKS + " SELECT * FROM eligible WHERE book_id=%(book)s", params).fetchone()
        if not book:
            return []
        if not config["search_content"] or (config["search_titles"] and re.search(metadata, query, re.I)):
            return [title_evidence(book)]
        terms = search_terms(re.sub(rf"{reference}|讲|说|内容|介绍", "", query))
        parts = conn.execute(
            """SELECT ordinal,locator,body FROM library_passages WHERE version_id=%s
               ORDER BY (SELECT count(*) FROM unnest(%s::text[]) t(term)
                         WHERE strpos(lower(body),term)>0) DESC, ordinal LIMIT %s""",
            (book["version"], terms, scope["limit"]),
        ).fetchall()
        return [evidence_for_book(book, f"《{book['title']}》{part['locator']}：{part['body']}",
                                  part["ordinal"], part["locator"]) for part in parts]
    return None


def retrieve(conn, job, config, context=()):
    scope = {"tenant": job["tenant"], "role": job["role"], "limit": config["max_sources"]}
    if category_requested(job["query"], job["tenant"]):
        if not config["search_titles"]:
            return direct_result("当前检索配置未启用书目信息，无法列出电子书分类。", [])
        return category_result(conn, scope)
    name = availability_title(job["query"], job["tenant"])
    if name and config["search_titles"]:
        rows = conn.execute(
            ELIGIBLE_BOOKS + """ SELECT * FROM eligible WHERE lower(title)=lower(%(title)s)
                                 ORDER BY title,version LIMIT %(limit)s""",
            {**scope, "title": name},
        ).fetchall()
        evidence = [title_evidence(row) for row in rows]
        answer = ("有。\n" + "\n".join(doc["text"] for doc in evidence)) if evidence else (
            f"当前可查阅的已发布电子书中未找到《{name}》。"
        )
        return direct_result(answer, evidence)
    if context:
        evidence = referenced_book_evidence(conn, job["query"], context, scope, config)
        if evidence is not None:
            return evidence, None
    catalog = catalog_requested(job["query"], job["tenant"], include_ebooks=True)
    title_lookup = re.fullmatch(r"《[^》]+》[。！？?!]*", job["query"].strip()) is not None
    terms = search_terms(job["query"])
    patterns = ["%" + re.sub(r"([%_\\])", r"\\\1", term) + "%" for term in terms]
    params = {
        **scope, "titles": config["search_titles"],
        "content": config["search_content"] and not (config["search_titles"] and title_lookup),
        "patterns": patterns, "terms": terms, "catalog": catalog,
    }
    rows = conn.execute(
        ELIGIBLE_BOOKS + """
           SELECT * FROM (
               SELECT e.*,NULL::integer AS ordinal,NULL::text AS locator,NULL::text AS body,
                      CASE WHEN lower(e.title)=ANY(%(terms)s::text[]) THEN 3 ELSE 1 END AS priority,
                      (SELECT count(*) FROM unnest(%(patterns)s::text[]) q(pattern)
                       WHERE lower(e.title) LIKE q.pattern
                          OR lower(concat_ws(' ',e.author,e.category,e.isbn,e.shelf)) LIKE q.pattern) AS hits
               FROM eligible e
               WHERE %(titles)s AND (%(catalog)s OR lower(e.title) LIKE ANY(%(patterns)s::text[])
                   OR lower(concat_ws(' ',e.author,e.category,e.isbn,e.shelf)) LIKE ANY(%(patterns)s::text[]))
               UNION ALL
               SELECT e.*,p.ordinal,p.locator,p.body, 2 AS priority,
                      (SELECT count(*) FROM unnest(%(patterns)s::text[]) q(pattern)
                       WHERE lower(p.body) LIKE q.pattern) AS hits
               FROM library_passages p JOIN eligible e ON e.version=p.version_id
               WHERE %(content)s AND NOT %(catalog)s AND lower(p.body) LIKE ANY(%(patterns)s::text[])
           ) candidates ORDER BY priority DESC,hits DESC,title,version,ordinal NULLS FIRST LIMIT %(limit)s""",
        params,
    ).fetchall()
    evidence = []
    for row in rows:
        if row["ordinal"] is None:
            evidence.append(title_evidence(row))
        else:
            text = f"《{row['title']}》{row['locator']}：{row['body']}"
            evidence.append(evidence_for_book(row, text, row["ordinal"], row["locator"]))
    if catalog:
        answer = "\n".join(doc["text"] for doc in evidence)
        if not evidence:
            answer = "当前检索配置下，没有可列出的已发布电子书。"
        elif len(evidence) == config["max_sources"]:
            answer = f"以下为本次列出的 {len(evidence)} 本已发布电子书。\n" + answer
        return direct_result(answer, evidence)
    return evidence, None
