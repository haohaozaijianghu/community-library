"""Verify optional sample books in a disposable local Compose project."""
from collections import Counter
from urllib.parse import quote, urlsplit
from uuid import uuid4

from check_library import BASE, req, terminal, trace
from seed_demo_books import BOOKS, seed


def verify_answer(user, title, version=None, keyword=None):
    question = f"《{title}》" + (f" {keyword}" if keyword else "")
    job = req("/v1/agent/jobs", user, {"query": question}, key=uuid4().hex, status=202)
    result = terminal(job["job_id"], user)
    assert result["status"] == "succeeded", result
    citations = result["result"]["citations"]
    if version is None:
        assert not citations, result
        return
    expected = f"book-version-{version}-"
    assert citations and all(citation.startswith(expected) for citation in citations), result
    if keyword:
        passages = [req(f"/v1/jobs/{job['job_id']}/citations/{citation}", user)["text"] for citation in citations]
        assert any(keyword in text for text in passages), passages


def direct_lookup(user, query):
    job_id = req("/v1/agent/jobs", user, {"query": query}, key=uuid4().hex, status=202)["job_id"]
    job = terminal(job_id, user)
    assert job["status"] == "succeeded", job
    assert job["result"]["usage"] == {"input_tokens": 0, "output_tokens": 0, "cost_units": 0}, job
    assert not trace(job_id)[0], job
    return job_id, job["result"]


def run():
    parsed = urlsplit(BASE)
    assert parsed.hostname in {"127.0.0.1", "localhost", "::1"} and parsed.port not in {None, 8080}, (
        "Run against a disposable local project on a non-default port"
    )
    titles = {branch: {book[0] for book in examples} for branch, examples in BOOKS.items()}
    for branch in BOOKS:
        seed(BASE, branch)
        before = req("/v1/admin/books", branch + "-staff")
        seed(BASE, branch)
        after = req("/v1/admin/books", branch + "-staff")
        assert before == after, "Re-running the sample loader changed existing books"

    for branch, examples in BOOKS.items():
        staff, reader = branch + "-staff", branch + "-reader"
        other_reader = "north-reader" if branch == "south" else "south-reader"
        staff_catalog = req("/v1/admin/books", staff)
        published_reader = {row["title"] for row in req("/v1/books", reader) if row["active_version"]}
        admin_titles = Counter(row["title"] for row in staff_catalog)
        assert all(admin_titles[title] == 1 for title in titles[branch])
        other_branch = other_reader.split("-")[0]
        assert titles[other_branch].isdisjoint(admin_titles)
        rows = {row["title"]: row for row in staff_catalog}
        for title, author, category, shelf, audience, state, *_ in examples:
            row = rows[title]
            assert (row["author"], row["category"], row["shelf"], row["audience"]) == (author, category, shelf, audience)
            assert bool(row["active_version"]) == (state == "published")
            assert row["latest_status"] == (None if state == "empty" else "ready")
            assert (title in published_reader) == (state == "published" and audience == "reader")
            if state != "empty":
                preview = f"/v1/books/{row['id']}/versions/{row['latest_id']}/preview"
                assert req(preview, staff)
                req(preview, reader, status=403)

        public, internal, draft, empty = (examples[index][0] for index in (0, 3, 4, 5))
        keyword = "香樟" if branch == "north" else "白鹭"
        verify_answer(reader, public, rows[public]["active_version"], keyword=keyword)
        verify_answer(other_reader, public)
        verify_answer(staff, internal, rows[internal]["active_version"])
        verify_answer(reader, internal)
        verify_answer(reader, draft)
        verify_answer(reader, empty)

        _, lookup = direct_lookup(reader, f"有{public} 这本书么")
        assert lookup["citations"] == [f"book-version-{rows[public]['active_version']}-title"], lookup
        assert lookup["answer"].startswith("有。") and public in lookup["answer"]
        for account, hidden in ((other_reader, public), (reader, internal), (reader, draft)):
            _, lookup = direct_lookup(account, f"有《{hidden}》这本书吗？")
            assert not lookup["citations"] and "未找到" in lookup["answer"], lookup

        overview = req("/v1/agent/knowledge", staff)
        expected_fields = {field: rows[public][field] for field in ("author", "category", "isbn", "shelf")}
        listed = next(item for item in overview["books"] if item["id"] == rows[public]["id"])
        assert all(listed[field] == value for field, value in expected_fields.items()), listed
        index_path = f"/v1/agent/knowledge/books/{listed['id']}/versions/{listed['active_version']}/passages"
        indexed = req(index_path, staff)
        assert all(indexed[field] == value for field, value in expected_fields.items()), indexed
        if branch == "north":
            assert all(any(chapter in item["text"] for item in indexed["items"])
                       for chapter in ("内容简介", "固定路线", "自然笔记")), indexed
        for field, label in (("author", "作者"), ("category", "分类"), ("shelf", "馆藏位置")):
            search = req("/v1/agent/knowledge/search", staff, {"role": "reader", "query": expected_fields[field]})
            assert any(doc["id"] == f"book-version-{listed['active_version']}-title" and
                       f"{label} {expected_fields[field]}" in doc["text"] for doc in search["evidence"]), search
        internal_author = rows[internal]["author"]
        for role, visible in (("reader", False), ("staff", True)):
            search = req("/v1/agent/knowledge/search", staff, {"role": role, "query": internal_author})
            assert any(doc["title"] == internal for doc in search["evidence"]) == visible, search

        for account in (reader, staff):
            job_id, categories = direct_lookup(account, "都有哪些种类的书籍")
            expected = Counter(row["category"] or "未分类" for row in staff_catalog
                               if row["active_version"] and (account == staff or row["audience"] == "reader"))
            assert set(f"{name}：{count} 本" for name, count in expected.items()).issubset(set(categories["answer"].splitlines()))
            assert len(categories["citations"]) == len(expected)
            for citation in categories["citations"]:
                source = req(f"/v1/jobs/{job_id}/citations/{citation}", account)
                assert source["text"] == f"{source['title']}：{expected[source['title']]} 本"

    staff = "north-staff"
    sample = next(row for row in req("/v1/admin/books", staff) if row["title"] == BOOKS["north"][0][0])
    isbn = "9787302423287"
    edit = {field: sample[field] for field in ("title", "author", "category", "shelf", "audience", "revision")}
    req(f"/v1/admin/books/{sample['id']}", staff, {**edit, "isbn": isbn}, method="PUT")
    search = req("/v1/agent/knowledge/search", staff, {"role": "reader", "query": isbn})
    assert any(doc["id"] == f"book-version-{sample['active_version']}-title" and
               f"ISBN {isbn}" in doc["text"] for doc in search["evidence"]), search
    req(f"/v1/admin/books/{sample['id']}", staff,
        {**edit, "revision": edit["revision"] + 1, "isbn": sample["isbn"]}, method="PUT")

    reader = "north-reader"
    root, _ = direct_lookup(reader, "有街巷里的树这本书吗？")
    query = "这本书讲的是什么？"
    key = uuid4().hex
    payload = {"query": query, "conversation_id": root}
    next_turn = req("/v1/agent/jobs", reader, payload, key=key, status=202)
    assert req("/v1/agent/jobs", reader, payload, key=key, status=202) == next_turn
    req("/v1/agent/jobs", reader, {"query": query}, key=key, status=409)
    answer = terminal(next_turn["job_id"], reader)
    assert answer["status"] == "succeeded" and "自然观察手册" in answer["result"]["answer"], answer
    turns = req(f"/v1/conversations/{root}", reader)
    assert [turn["job_id"] for turn in turns] == [root, next_turn["job_id"]], turns
    assert all(turn["conversation_id"] == root for turn in turns), turns
    assert req(f"/v1/conversations/{next_turn['job_id']}", reader) == turns
    history = req("/v1/conversations?search=" + quote(query), reader)
    assert sum(entry["conversation_id"] == root for entry in history) == 1, history
    author_turn = req("/v1/agent/jobs", reader, {"query": "那作者呢？", "conversation_id": root},
                      key=uuid4().hex, status=202)["job_id"]
    assert "作者 林余" in terminal(author_turn, reader)["result"]["answer"]
    for outsider in ("north-staff", "south-reader"):
        req(f"/v1/conversations/{root}", outsider, status=404)
        req("/v1/agent/jobs", outsider, payload, key=uuid4().hex, status=404)
    fresh = req("/v1/agent/jobs", reader, {"query": query}, key=uuid4().hex, status=202)["job_id"]
    assert not terminal(fresh, reader)["result"]["citations"]
    assert len(req(f"/v1/conversations/{fresh}", reader)) == 1

    for branch, other_branch in (("north", "south"), ("south", "north")):
        staff, reader, other_staff = f"{branch}-staff", f"{branch}-reader", f"{other_branch}-staff"
        knowledge = "/v1/agent/knowledge"
        own, other = req(knowledge, staff), req(knowledge, other_staff)
        for kind in ("books", "materials"):
            assert own[kind] and other[kind]
            assert {item["id"] for item in own[kind]}.isdisjoint(item["id"] for item in other[kind])
        for material in own["materials"]:
            path = f"{knowledge}/materials/{material['id']}"
            req(path, staff)
            req(path, other_staff, status=404)
        for role, account in (("reader", reader), ("staff", staff)):
            root = req("/v1/jobs", account, {"query": "locker"}, key=uuid4().hex, status=202)["job_id"]
            followup = req("/v1/jobs", account, {"query": "那周末也能用吗？", "conversation_id": root},
                           key=uuid4().hex, status=202)["job_id"]
            expected = [f"{branch}-locker-guide"]
            if role == "staff":
                expected.append(f"{branch}-locker-staff")
            for turn in (root, followup):
                result = terminal(turn, account)["result"]
                assert sorted(result["citations"]) == expected, result
                for citation in expected:
                    path = f"/v1/jobs/{turn}/citations/{citation}"
                    req(path, account)
                    req(path, other_staff, status=404)
            req(f"/v1/conversations/{root}", other_staff, status=404)

    print("Sample books passed: metadata, repeat imports, publication states, tenant and role isolation, text citations")


if __name__ == "__main__":
    run()
