import re

from library.agent import title_evidence
from library.books import catalog_label, registration_metadata


BOOK_REFERENCE = re.compile(r"book-([a-f0-9]{32})")
VERSION_REFERENCE = re.compile(r"book-version-([a-f0-9]{32})-(title|[0-9]+)")


def readable_answers(conn, job, context):
    """Retain questions while withholding answers backed by stale or inaccessible evidence."""
    snapshots = {
        (doc["id"], doc["text"])
        for turn in context for doc in turn["evidence"] or ()
        if turn["result"] and not doc.get("url")
    }
    book_refs = {}
    version_refs = {}
    community_refs = set()
    for citation, _ in snapshots:
        if match := BOOK_REFERENCE.fullmatch(citation):
            book_refs[citation] = match[1]
        elif match := VERSION_REFERENCE.fullmatch(citation):
            version_refs[citation] = (match[1], match[2])
        elif not citation.startswith("book-version-"):
            community_refs.add(citation)

    scope = (job["tenant"], job["role"])
    allowed = set()
    if community_refs:
        rows = conn.execute(
            """SELECT COALESCE(source_id,'community-' || id::text) AS id,
                      CASE WHEN source_id IS NULL THEN title || '：' || body ELSE body END AS text
               FROM library_community_posts
               WHERE tenant_id=%s AND state='published'
                 AND (kind IN ('notice','activity','guide','faq') OR %s='staff')
                 AND COALESCE(source_id,'community-' || id::text)=ANY(%s)""",
            (*scope, list(community_refs)),
        )
        allowed.update((row["id"], row["text"]) for row in rows)

    if book_refs or version_refs:
        rows = conn.execute(
            """SELECT b.id,b.title,b.author,b.isbn,b.shelf,b.category,u.id AS version
               FROM library_books b LEFT JOIN library_uploads u
                 ON u.id=b.active_version AND u.book_id=b.id AND u.status='ready'
               WHERE b.tenant=%s AND b.archived_at IS NULL AND (b.audience='reader' OR %s='staff')
                 AND (b.id=ANY(%s) OR u.id=ANY(%s))""",
            (*scope, list(book_refs.values()), list({version for version, _ in version_refs.values()})),
        ).fetchall()
        by_id = {row["id"]: row for row in rows}
        by_version = {row["version"]: row for row in rows if row["version"]}
        for citation, text in snapshots:
            if citation in book_refs and (book := by_id.get(book_refs[citation])):
                if text in {catalog_label(book), f"本馆馆藏登记：{registration_metadata(book)}"}:
                    allowed.add((citation, text))
            elif citation in version_refs:
                version, part = version_refs[citation]
                if not (book := by_version.get(version)):
                    continue
                if part == "title":
                    if text == title_evidence(book)["text"]:
                        allowed.add((citation, text))
                elif text.startswith(f"《{book['title']}》"):
                    # An indexed passage is immutable within an active version.
                    allowed.add((citation, text))

    return [
        turn if turn["result"] and turn["evidence"] and all(
            not doc.get("url") and (doc["id"], doc["text"]) in allowed for doc in turn["evidence"]
        ) else {**turn, "result": None}
        for turn in context
    ]
