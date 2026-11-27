"""Check the published-ebook Agent on a disposable stack with the original fixtures."""
from uuid import uuid4

from check_library import req, terminal, trace, upload_sample, wait_for


def run():
    prefix = uuid4().hex[:12]
    title = f"阅读活动手册 {prefix}"
    keyword = "readingevent" + prefix
    admin = "north-staff"
    settings_path = "/v1/agent/settings"
    overview_path = "/v1/admin/overview"
    knowledge_path = "/v1/agent/knowledge"
    req(overview_path, status=403)
    req(knowledge_path, None, status=401)
    req(knowledge_path, status=403)
    assert any(row["kind"] == "handbook" for row in req(knowledge_path, admin)["materials"])
    baseline = req(overview_path, admin)
    book = req("/v1/books", admin, {"title": title, "audience": "reader"}, status=201)["book_id"]
    version = upload_sample(book, admin, keyword)
    wait_for(lambda: req(f"/v1/ingestion-jobs/{version}", admin)["status"] == "ready")

    def ask(query, user="north-reader"):
        job_id = req("/v1/agent/jobs", user, {"query": query}, uuid4().hex, 202)["job_id"]
        row = terminal(job_id, user)
        assert row["status"] == "succeeded", row
        return row

    overview = req(overview_path, admin)
    assert overview["pending_versions"] == baseline["pending_versions"] + 1
    assert any(item["book_id"] == book and item["version_id"] == version
               for item in overview["review_queue"])
    assert all(item["book_id"] != book for item in req(overview_path, "south-staff")["review_queue"])
    preview = f"/v1/books/{book}/versions/{version}/preview"
    assert req(preview, admin) == [{"locator": "第 1 行", "text": keyword + " original page"}]
    req(preview, "north-reader", status=403)
    req(preview, "south-staff", status=404)
    assert next(row for row in req("/v1/admin/books", admin) if row["id"] == book)["latest_status"] == "ready"

    pending = next(row for row in req(knowledge_path, admin)["books"] if row["id"] == book)
    assert pending["active_version"] is None and pending["latest_id"] == version and pending["passages"] == 0
    assert pending["latest_status"] == "ready"
    assert all(row["id"] != book for row in req(knowledge_path, "south-staff")["books"])
    passages_path = f"{knowledge_path}/books/{book}/versions/{version}/passages"
    search_path = knowledge_path + "/search"
    req(passages_path, admin, status=404)
    req(search_path, payload={"query": keyword, "role": "reader"}, status=403)
    assert not req(search_path, admin, {"query": keyword, "role": "reader"})["evidence"]

    before = ask(f"《{title}》")
    assert not before["result"]["citations"] and not trace(before["job_id"])[0]
    req(f"/v1/books/{book}/versions/{version}/publish", admin,
        {"rights_basis": "Test-owned content"}, form=True)
    overview = req(overview_path, admin)
    assert overview["published_books"] == baseline["published_books"] + 1
    assert overview["pending_versions"] == baseline["pending_versions"]
    assert all(item["version_id"] != version for item in overview["review_queue"])
    assert overview["questions"]["total"] == baseline["questions"]["total"] + 1
    assert set(overview["questions"]) == {"total", "pending", "completed", "failed"}
    published = next(row for row in req(knowledge_path, admin)["books"] if row["id"] == book)
    assert published["active_version"] == version and published["passages"] == 1

    batch = req(passages_path, admin)
    assert batch["title"] == title and batch["items"] == [{"ordinal": 0, "locator": "第 1 行", "text": keyword + " original page"}]
    assert batch["next_offset"] is None and not req(passages_path + "?offset=1", admin)["items"]
    req(passages_path, status=403)
    req(passages_path, "south-staff", status=404)
    by_title = ask(f"《{title}》")
    assert by_title["result"]["citations"] == [f"book-version-{version}-title"], by_title
    by_content = ask(f"书中提到的 {keyword} 是什么？")
    citation = f"book-version-{version}-0"
    assert by_content["result"]["citations"] == [citation], by_content
    citation_path = f"/v1/jobs/{by_content['job_id']}/citations/{citation}"
    evidence = req(citation_path)
    assert evidence["title"] == title and evidence["locator"] == "第 1 行" and keyword in evidence["text"]
    req(citation_path, "north-staff", status=404)
    assert not ask(keyword, "south-reader")["result"]["citations"]
    assert by_content["job_id"] in {row["job_id"] for row in req("/v1/agent/jobs")}
    assert by_content["job_id"] not in {row["job_id"] for row in req("/v1/agent/jobs", admin)}
    (record,) = req("/v1/question-history?job_id=" + by_content["job_id"])
    assert record["source_mode"] == "books" and record["status"] == "succeeded"
    staff_job = ask(keyword, admin)
    diagnostics_path = f"/v1/agent/jobs/{staff_job['job_id']}/diagnostics"
    diagnostics = req(diagnostics_path, admin)
    assert diagnostics["evidence"] == [evidence]
    assert [(row["provider"], row["outcome"]) for row in diagnostics["attempts"]] == [("primary", "success")]
    req(diagnostics_path, "north-reader", status=403)
    req(diagnostics_path, "south-staff", status=404)

    private = req("/v1/books", admin, {"title": "仅馆员 " + prefix, "audience": "staff"}, status=201)["book_id"]
    private_version = upload_sample(private, admin, keyword + "-staff")
    wait_for(lambda: req(f"/v1/ingestion-jobs/{private_version}", admin)["status"] == "ready")
    req(f"/v1/books/{private}/versions/{private_version}/publish", admin,
        {"rights_basis": "Test-owned content"}, form=True)
    assert not ask(keyword + "-staff")["result"]["citations"]
    assert ask(keyword + "-staff", admin)["result"]["citations"] == [f"book-version-{private_version}-0"]

    def search(query, role="reader", user=admin):
        return req(search_path, user, {"query": query, "role": role})

    assert not search(keyword + "-staff")["evidence"]
    assert [row["id"] for row in search(keyword + "-staff", "staff")["evidence"]] == [
        f"book-version-{private_version}-0"
    ]
    assert not search(keyword, user="south-staff")["evidence"]
    assert search("现在有哪些书")["catalog"]
    req(search_path, admin, {"query": "   ", "role": "reader"}, status=400)
    req(search_path, admin, {"query": keyword, "role": "admin"}, status=400)
    handbook = next(item for item in req(knowledge_path, admin)["materials"] if item["kind"] == "handbook")
    material_path = f"{knowledge_path}/materials/{handbook['id']}"
    assert req(material_path, admin)["body"]
    req(material_path, "north-reader", status=403)
    req(material_path, "south-staff", status=404)

    for branch, other in (("north", "south"), ("south", "north")):
        account = f"{branch}-staff"
        sources = req(knowledge_path, account)["materials"]
        guide, = [row for row in sources if row["source_id"] == f"{branch}-locker-guide"]
        assert req(f"{knowledge_path}/materials/{guide['id']}", account)["source_id"] == guide["source_id"]
        for role, suffixes in (("reader", ["guide"]), ("staff", ["guide", "staff"])):
            payload = {"query": "locker", "scope": "materials", "role": role}
            matches = req(search_path, account, payload)["evidence"]
            assert sorted(row["id"] for row in matches) == [f"{branch}-locker-{suffix}" for suffix in suffixes]
            assert req(search_path, account, {**payload, "query": f"{other}-locker-staff"})["evidence"] == []
        assert req(search_path, account, {"scope": "materials", "role": "reader",
                                         "query": f"{branch}-locker-staff"})["evidence"] == []

    draft = req("/v1/books", admin, {"title": "未发布目录项 " + prefix}, status=201)["book_id"]
    draft_version = upload_sample(draft, admin, "unreleased" + prefix)
    wait_for(lambda: req(f"/v1/ingestion-jobs/{draft_version}", admin)["status"] == "ready")
    public_title, staff_title = f"book-version-{version}-title", f"book-version-{private_version}-title"
    draft_title = f"book-version-{draft_version}-title"
    for query, user, expected, excluded in (
        ("现在有哪些书", "north-reader", public_title, [staff_title, draft_title]),
        ("北馆目前有哪些已发布电子书？", admin, staff_title, [draft_title]),
        ("现在有哪些电子书？", "south-staff", None, [public_title, staff_title, draft_title]),
    ):
        listing = ask(query, user)
        citations = listing["result"]["citations"]
        assert expected is None or expected in citations, listing
        assert set(excluded).isdisjoint(citations), listing
        assert listing["result"]["usage"]["cost_units"] == 0 and not trace(listing["job_id"])[0]

    initial = req(settings_path, admin)
    desired = {"search_titles": True, "search_content": False, "max_sources": 4, "prompt": "简明回答并指出页码。", "answer_model": "default"}
    req(settings_path, payload=desired, status=403, method="PUT")
    try:
        req(settings_path, admin, desired, method="PUT")
        assert req(settings_path, admin) == desired
        partial = {key: value for key, value in desired.items() if key != "prompt"}
        assert req(settings_path, admin, partial, method="PUT") == desired
        assert not ask(keyword)["result"]["citations"]
        assert ask(f"《{title}》")["result"]["citations"] == [f"book-version-{version}-title"]
    finally:
        req(settings_path, admin, initial, method="PUT")
    manage = f"/v1/admin/books/{book}"

    def current_book():
        return next(row for row in req("/v1/admin/books", admin) if row["id"] == book)

    edit = {"title": title + " 修订版", "author": "社区馆", "isbn": "9781234567890",
            "shelf": "A9", "audience": "staff", "revision": current_book()["revision"]}
    for viewer, status in (("south-staff", 404), ("north-reader", 403)):
        req(manage, viewer, edit, method="PUT", status=status)
        req(manage + "?revision=1", viewer, method="DELETE", status=status)
        req(manage + "/restore?revision=1", viewer, method="POST", status=status)
    req(manage, admin, edit, method="PUT")
    req(manage, admin, edit, method="PUT", status=409)
    assert all(row["id"] != book for row in req("/v1/books"))
    edited_hits = ask(f"《{edit['title']}》", admin)["result"]["citations"]
    assert edited_hits[0] == f"book-version-{version}-title", edited_hits
    req(manage, admin, {**edit, "audience": "reader", "author": None, "revision": current_book()["revision"]}, method="PUT")
    assert next(row for row in req("/v1/books") if row["id"] == book)["author"] is None

    deletion = manage + f"?revision={current_book()['revision']}"
    req(deletion, admin, method="DELETE")
    req(deletion, admin, method="DELETE")
    assert current_book()["archived_at"]
    assert all(row["id"] != book for row in req(knowledge_path, admin)["books"])
    assert all(row["id"] != book for row in req("/v1/books", admin))
    assert req(overview_path, admin)["published_books"] == baseline["published_books"] + 1
    assert not ask(keyword)["result"]["citations"]
    legacy = req("/v1/jobs", payload={"query": "查看本馆馆藏目录"}, key=uuid4().hex, status=202)["job_id"]
    assert f"book-{book}" not in terminal(legacy)["result"]["citations"]
    legacy = req("/v1/jobs", payload={"query": keyword}, key=uuid4().hex, status=202)["job_id"]
    assert not terminal(legacy)["result"]["citations"]
    assert req(citation_path) == evidence
    req(f"/v1/books/{book}/versions/{version}/publish", admin, {"rights_basis": "Test-owned content"},
        form=True, status=409)
    req(f"/v1/books/{book}/versions/{version}/retry", admin, method="POST", status=409)
    req(manage + f"/restore?revision={current_book()['revision']}", admin, method="POST")
    assert current_book()["archived_at"] is None
    assert ask(keyword)["result"]["citations"] == [citation]
    successor = upload_sample(book, admin, "\n".join(f"next {number}" for number in range(22)))
    wait_for(lambda: req(f"/v1/ingestion-jobs/{successor}", admin)["status"] == "ready")
    next_path = f"{knowledge_path}/books/{book}/versions/{successor}/passages"
    req(next_path, admin, status=404)
    assert req(passages_path, admin)["items"] == batch["items"]
    req(f"/v1/books/{book}/versions/{successor}/publish", admin,
        {"rights_basis": "Test-owned content"}, form=True)
    req(passages_path, admin, status=404)
    page = req(next_path, admin)
    assert len(page["items"]) == 20 and page["next_offset"] == 20
    last = req(next_path + "?offset=20", admin)
    assert [row["ordinal"] for row in last["items"]] == [20, 21] and last["next_offset"] is None
    uploads = req(f"/v1/books/{book}/uploads", admin)
    historic = next(item for item in uploads if item["job_id"] == version)
    assert historic["was_published"] and not historic["active"]
    print("Book edits, role isolation, archive/restore, retrieval and historical citations passed")
    print("Agent: published title and text, draft and role isolation, citation locations, history and settings passed")


if __name__ == "__main__":
    run()
