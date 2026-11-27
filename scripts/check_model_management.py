"""Exercise model registration and Agent routing in a disposable Compose project."""
import json
from uuid import uuid4

from check_library import compose, req, terminal, upload_sample, wait_for

STUB = '''
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        assert self.path.endswith("/models")
        assert self.headers.get("Authorization") == "Bearer fixture-model-key"
        data = json.dumps({"data": [
            {"id":"chat-fixture-a","type":"chat"},
            {"id":"chat-fixture-b","type":"chat"},
            {"id":"vector-fixture","type":"embedding"},
        ]}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        assert self.headers.get("Authorization") == "Bearer fixture-model-key"
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        model = payload["model"]
        if self.path.endswith("/embeddings"):
            assert model == "vector-fixture"
            result = {"data": [{"index": 0, "embedding": [1, 2, 3]}]}
        else:
            request = json.loads(payload["messages"][-1]["content"])
            if request:
                assert len(request["documents"]) == 1 and "routerproof" in request["documents"][0]["text"]
                data = {"answer": "模型 " + model, "citations": [request["documents"][0]["id"]]}
                if request["question"].startswith("historyproof"):
                    data["answer"] = request["documents"][0]["text"]
                    with open("/tmp/library-history-requests.jsonl", "a") as log:
                        print(json.dumps(request), file=log)
            else:
                data = {"ok": True}
            result = {"choices": [{"message": {"content": json.dumps(data)}}]}
        data = json.dumps(result).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

ThreadingHTTPServer(("0.0.0.0", 8098), Handler).serve_forever()
'''


def check_archived_history(staff):
    def publish_sample(label, text):
        book_id = req("/v1/books", staff, {"title": label, "audience": "reader"}, status=201)["book_id"]
        version = upload_sample(book_id, staff, text)
        wait_for(lambda: req(f"/v1/ingestion-jobs/{version}", staff)["status"] == "ready")
        req(f"/v1/books/{book_id}/versions/{version}/publish", staff, {"rights_basis": "Test-owned text"}, form=True)
        return book_id, version

    old_marker = "archived-fixture-content"
    old_book, _ = publish_sample("旧版历史验收样例", "routerproof historyproofold " + old_marker)
    _, live_version = publish_sample("当前历史验收样例", "routerproof historyproofcurrent 当期内容")

    def ask(query, conversation=None):
        payload = {"query": query}
        if conversation:
            payload["conversation_id"] = conversation
        return req("/v1/agent/jobs", payload=payload, key=uuid4().hex, status=202)["job_id"]

    def latest_request():
        logs = compose("exec", "-T", "mock", "cat", "/tmp/library-history-requests.jsonl")
        return json.loads(logs.splitlines()[-1])

    root = ask("historyproofold")
    archived_answer = terminal(root)
    assert archived_answer["status"] == "succeeded" and old_marker in archived_answer["result"]["answer"]
    prior = terminal(ask("historyproofcurrent", root))
    assert prior["status"] == "succeeded"
    assert old_marker in latest_request()["conversation"][0]["answer"]

    req(f"/v1/admin/books/{old_book}?revision=1", staff, method="DELETE")
    followup = terminal(ask("historyproofcurrent", root))
    assert followup["status"] == "succeeded"
    assert followup["result"]["citations"] == [f"book-version-{live_version}-0"]
    sent = latest_request()
    assert sent["question"] == "historyproofcurrent"
    assert old_marker not in json.dumps(sent, ensure_ascii=False)
    assert sent["conversation"] == [
        {"question": "historyproofold", "answer": None},
        {"question": "historyproofcurrent", "answer": prior["result"]["answer"]},
    ]
    assert req(f"/v1/jobs/{root}") == archived_answer


def run():
    compose("exec", "-T", "-d", "mock", "python", "-c", STUB)
    base = "/v1/admin/models"
    settings_path = "/v1/agent/settings"
    staff = "north-staff"
    req(base, status=403)
    assert "custom" in {provider["id"] for provider in req(base + "/providers", staff)}
    deepseek = req(base + "/providers/deepseek/catalog", staff)["models"]
    assert {row["model_name"]: row["display_name"] for row in deepseek if row["source"] == "preset"} == {
        "deepseek-flash": "DeepSeek-V4.1-Flash-1017",
        "deepseek-v4-pro": "DeepSeek-V4-Pro-0813",
    }
    original = req(settings_path, staff)
    tokens = "routerproof" + uuid4().hex
    item = req("/v1/books", staff, {"title": "模型路由验收用电子书"}, status=201)["book_id"]
    version = upload_sample(item, staff, tokens)
    wait_for(lambda: req(f"/v1/ingestion-jobs/{version}", staff)["status"] == "ready")
    req(f"/v1/books/{item}/versions/{version}/publish", staff, {"rights_basis": "Test-owned text"}, form=True)
    catalog = base + "/providers/custom"
    assert req(catalog + "/catalog", staff) == {"has_key": False, "models": []}
    req(catalog + "/credentials", staff, {"api_key": "fixture-model-key"}, method="PUT")
    assert req(catalog + "/discover", staff, method="POST")["count"] == 3
    catalog_rows = req(catalog + "/catalog", staff)["models"]
    assert {row["model_name"] for row in catalog_rows} == {"chat-fixture-a", "chat-fixture-b", "vector-fixture"}
    req(catalog + "/models", staff, {"models": [{"model_name": "invented-id", "kind": "chat"}]}, status=400)
    picked = {"models": [{"model_name": row["model_name"], "kind": row["kind"]} for row in catalog_rows]}
    registered = req(catalog + "/models", staff, picked, status=201)
    assert len(registered) == 3 and all(row["name"] == row["model_name"] for row in registered)
    assert req(catalog + "/models", staff, picked, status=201) == []
    try:
        for row in registered:
            index, kind = row["model_name"], row["kind"]
            identifier = row["id"]
            assert row["has_key"] and row["verified_at"] is None and "encrypted_key" not in row and "api_key" not in row
            selection = {**original, "answer_model": identifier}
            req(settings_path, staff, selection, status=400, method="PUT")
            req(f"{base}/{identifier}/verify", "south-staff", status=404, method="POST")
            verified = req(f"{base}/{identifier}/verify", staff, method="POST")
            assert verified["verified_at"]
            other = req(settings_path, "south-staff")
            req(settings_path, "south-staff", {**other, "answer_model": identifier}, status=400, method="PUT")
            if kind == "embedding":
                assert verified["dimension"] == 3
                req(settings_path, staff, selection, status=400, method="PUT")
                continue
            assert req(settings_path, staff, selection, method="PUT")["answer_model"] == identifier
            task = req("/v1/agent/jobs", payload={"query": tokens}, key=uuid4().hex, status=202)["job_id"]
            result = terminal(task)
            assert result["status"] == "succeeded" and result["result"]["answer"] == "模型 " + index, result
        check_archived_history(staff)
        assert req(base, "south-staff") == []
    finally:
        req(settings_path, staff, original, method="PUT")
    print("Model management passed: provider credential reuse, discovery, bulk add, routing, tenant isolation and archived history")


if __name__ == "__main__":
    run()
