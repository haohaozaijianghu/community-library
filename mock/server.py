"""In-memory deterministic test double for the library's model calls."""
import json
import os
import re
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Lock
from urllib.parse import urlsplit

providers = {
    key: (os.getenv(f"MOCK_{key.upper()}_MODE", "ok"), usage)
    for key, usage in (("primary", (120, 40)), ("backup", (150, 50)))
}
history = deque(maxlen=200)
lookups = deque(maxlen=200)
receipts = {}
mutex = Lock()
attempt_format = re.compile(r"[A-Za-z0-9_-]{1,80}")


class ModelAPI(BaseHTTPRequestHandler):
    def send_json(self, code, body):
        encoded = json.dumps(body, ensure_ascii=False).encode()
        try:
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)
        except (BrokenPipeError, ConnectionError):
            return

    def fail(self, code, error):
        self.send_json(code, {"error": error})

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/healthz":
            return self.send_json(200, {"status": "ok"})
        if path == "/__test/calls":
            with mutex:
                snapshot = {"calls": list(history), "receipt_reads": list(lookups)}
            return self.send_json(200, snapshot)
        parts = path.split("/")
        if len(parts) != 5 or parts[1] != "v1" or parts[2] not in providers or parts[3] != "receipts":
            return self.fail(404, "not_found")
        provider, identifier = parts[2], parts[4]
        if not attempt_format.fullmatch(identifier):
            return self.fail(404, "not_found")
        with mutex:
            receipt = receipts.get((provider, identifier))
            row = {"provider": provider, "attempt_id": identifier, "status": 200 if receipt else 404}
            if receipt:
                row["job_id"] = receipt["job_id"]
            lookups.append(row)
        return self.send_json(200, receipt) if receipt else self.fail(404, "not_found")

    def do_POST(self):
        parts = urlsplit(self.path).path.split("/")
        if len(parts) != 4 or parts[1] != "v1" or parts[2] not in providers or parts[3] != "generate":
            return self.fail(404, "not_found")
        provider = parts[2]
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 1 <= length <= 1024 * 1024:
                raise ValueError
            body = json.loads(self.rfile.read(length))
            job, question, docs = (body[key] for key in ("job_id", "query", "evidence"))
            attempt = body.get("attempt_id")
            if not all(isinstance(item, str) and item.strip() for item in (job, question)):
                raise ValueError
            if "attempt_id" in body and (not isinstance(attempt, str) or not attempt_format.fullmatch(attempt)):
                raise ValueError
            if not isinstance(docs, list) or not docs or not all(
                isinstance(doc[key], str) and doc[key] for doc in docs for key in ("id", "text")
            ):
                raise ValueError
        except (ValueError, TypeError, KeyError):
            return self.fail(400, "invalid_request")
        mode, (input_tokens, output_tokens) = providers[provider]
        ids = [doc["id"] for doc in docs]
        key = provider, attempt
        with mutex:
            history.append({"provider": provider, "job_id": job, "attempt_id": attempt, "mode": mode, "evidence_ids": ids})
            found = key in receipts
        if found:
            return self.fail(409, "attempt_already_completed")
        if mode == "rate_limit":
            return self.fail(429, "rate_limited")
        if mode == "bad_request":
            return self.fail(400, "invalid_model_request")
        if mode == "timeout":
            time.sleep(2)
        response = {
            "answer": "\n".join(doc["text"] for doc in docs),
            "citations": ids,
            "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
        }
        if attempt is not None:
            receipt = {**response, "job_id": job}
            with mutex:
                found = receipts.setdefault(key, receipt) is not receipt
            if found:
                return self.fail(409, "attempt_already_completed")
        return self.send_json(200, response)


if __name__ == "__main__":
    if not all(mode in {"ok", "bad_request", "rate_limit", "timeout"} for mode, _ in providers.values()):
        raise SystemExit("Invalid mock mode")
    ThreadingHTTPServer(("0.0.0.0", 8091), ModelAPI).serve_forever()
