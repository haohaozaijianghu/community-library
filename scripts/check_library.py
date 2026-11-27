"""Run against disposable Compose project initialized with the assignment fixtures."""
import concurrent.futures
import json
import os
import subprocess
import time
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from uuid import uuid4

BASE = os.environ.get("TEST_API_URL", "http://127.0.0.1:18080")
MOCK = os.environ.get("TEST_MOCK_URL", "http://127.0.0.1:18091")
PROJECT = os.environ.get("COMPOSE_PROJECT_NAME", "community-library-test")


def compose(*args, **env):
    return subprocess.check_output(
        ["docker", "compose", "-p", PROJECT, *args], env=os.environ | env, text=True
    ).strip()


def req(path, user="north-reader", payload=None, key=None, status=200, method=None, form=False, *, raw=None):
    headers = {"Authorization": f"Bearer demo-{user}"} if user else {}
    if key:
        headers["Idempotency-Key"] = key
    if raw is not None:
        headers["Content-Type"] = "application/json"
        payload = raw
    elif payload is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded" if form else "application/json"
        payload = (urlencode(payload) if form else json.dumps(payload)).encode()
    request = Request(BASE + path, data=payload, headers=headers, method=method)
    try:
        response = urlopen(request, timeout=5)
    except HTTPError as exc:
        response = exc
    with response:
        data = json.load(response)
        assert response.code == status, (request.full_url, response.code, data)
        return data


def submit(query="locker", user="north-reader", key=None):
    return req("/v1/jobs", user, {"query": query}, key or uuid4().hex, 202)["job_id"]


def job(job_id):
    return req(f"/v1/jobs/{job_id}")


def wait_for(probe, seconds=15):
    start = time.monotonic()
    while time.monotonic() - start < seconds:
        value = probe()
        if value:
            return value
        time.sleep(0.1)
    raise AssertionError("condition not reached")


def terminal(job_id, user="north-reader"):
    def check():
        row = req(f"/v1/jobs/{job_id}", user)
        return row if row["status"] in ("succeeded", "failed") else None
    return wait_for(check)


def trace(job_id):
    with urlopen(MOCK + "/__test/calls", timeout=5) as response:
        data = json.load(response)
    calls = [row for row in data["calls"] if row["job_id"] == job_id]
    attempt_ids = {row["attempt_id"] for row in calls if row["attempt_id"]}
    reads = [row for row in data["receipt_reads"]
             if row.get("job_id") == job_id or row["attempt_id"] in attempt_ids]
    return calls, reads


def mode(primary, backup="ok"):
    compose("up", "-d", "--force-recreate", "--no-deps", "mock",
            MOCK_PRIMARY_MODE=primary, MOCK_BACKUP_MODE=backup)
    wait_for(lambda: mock_up())


def mock_up():
    try:
        with urlopen(MOCK + "/__test/calls", timeout=1) as response:
            return response.status == 200
    except OSError:
        return False


def upload_sample(book_id, user, text):
    boundary = "library-test-boundary"
    body = (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"book.txt\"\r\n"
        f"Content-Type: text/plain\r\n\r\n{text} original page\r\n"
        f"--{boundary}--\r\n"
    ).encode()
    request = Request(BASE + f"/v1/books/{book_id}/uploads", data=body, headers={
        "Authorization": f"Bearer demo-{user}",
        "Content-Type": f"multipart/form-data; boundary={boundary}",
    })
    with urlopen(request, timeout=5) as response:
        assert response.status == 202
        return json.load(response)["job_id"]


def check_account_isolation():
    expected = {
        "north-reader": ["north-locker-guide"],
        "north-staff": ["north-locker-guide", "north-locker-staff"],
        "south-reader": ["south-locker-guide"],
        "south-staff": ["south-locker-guide", "south-locker-staff"],
    }
    req("/v1/reader/guides", user=None, status=401)
    for owner in expected:
        tenant = owner.split("-")[0]
        guides = req("/v1/reader/guides", owner)
        assert [guide["id"] for guide in guides] == [f"{tenant}-locker-guide"], guides

    key = uuid4().hex
    with concurrent.futures.ThreadPoolExecutor(8) as executor:
        ids = set(executor.map(lambda _: submit(" locker ", key=key), range(12)))
    assert len(ids) == 1
    jobs = {"north-reader": ids.pop()}
    jobs.update({user: submit(user=user, key=key) for user in expected if user not in jobs})
    assert len(set(jobs.values())) == len(expected), jobs

    for owner, task in jobs.items():
        assert submit(user=owner, key=key) == task
        req("/v1/jobs", owner, {"query": "other"}, key, 409)
        row = terminal(task, owner)
        assert row["status"] == "succeeded" and sorted(row["result"]["citations"]) == expected[owner], row
        calls, _ = trace(task)
        assert len(calls) == 1 and calls[0]["provider"] == "primary", calls
        assert sorted(calls[0]["evidence_ids"]) == expected[owner], calls
        history_path = f"/v1/question-history?job_id={task}"
        (record,) = req(history_path, owner)
        assert (record["job_id"], record["source_mode"], record["status"]) == (task, "library", "succeeded")
        for viewer in expected.keys() - {owner}:
            req(f"/v1/jobs/{task}", viewer, status=404)
            assert req(history_path, viewer) == []
            req(f"/v1/jobs/{task}/citations/{expected[owner][0]}", viewer, status=404)
    print("Account isolation passed: four principals, four jobs, twelve denied cross-account reads", flush=True)


def check_recovery_before_send(primary, expected_providers, cost):
    mode(primary)
    compose("stop", "worker")
    task = submit()
    crash = subprocess.run(
        ["docker", "compose", "-p", PROJECT, "run", "--rm", "-T", "--no-deps", "worker",
         "python", "-c", "import os; from library import gateway, worker; "
         "gateway.call = lambda *args, **kwargs: os._exit(74); worker.main()"],
        check=False,
    )
    assert crash.returncode == 74, crash.returncode
    assert job(task) == {"job_id": task, "status": "running", "result": None}
    assert trace(task)[0] == []
    attempt_id = compose("exec", "-T", "db", "psql", "-U", "library", "-d", "library", "-tAc",
                         f"SELECT attempt_id FROM library_attempts WHERE job_id='{task}' AND outcome='pending'")
    assert attempt_id
    compose("up", "-d", "--wait", "--no-deps", "worker")
    final = terminal(task)
    assert final["status"] == "succeeded" and final["job_id"] == task, final
    assert final["result"]["citations"] == ["north-locker-guide"] and final["result"]["usage"]["cost_units"] == cost
    calls, reads = trace(task)
    assert [item["provider"] for item in calls] == expected_providers, calls
    assert calls[0]["attempt_id"] == attempt_id
    assert [item["status"] for item in reads if item["attempt_id"] == attempt_id] == [404, 404], reads
    print(f"crash before send: primary={primary}, exit=74, calls={len(calls)}, cost={cost}", flush=True)


def run():
    mode("ok")
    req("/healthz", None)
    req("/v1/jobs", None, {"query": "locker"}, "denied", 401)
    req("/v1/jobs", "north-reader", {"query": ""}, "bad", 400)

    for path in ("/v1/jobs", "/v1/agent/jobs"):
        for body in (b"{", b'{"query":"\xff"}'):
            assert req(path, None, key="denied", status=401, raw=body) == {
                "detail": "invalid_credentials",
            }
            assert req(path, "unknown-user", key="denied", status=401, raw=body) == {
                "detail": "invalid_credentials",
            }
            req(path, key="invalid-json", status=400, raw=body)

    for unknown in (uuid4().hex, "%00"):
        assert req(f"/v1/jobs/{unknown}", status=404) == {"detail": "job_not_found"}
        assert req(f"/v1/jobs/{unknown}/citations/north-locker-guide", status=404) == {
            "detail": "citation_not_found",
        }

    check_account_isolation()
    empty = submit("zzz-no-match")
    no_match = terminal(empty)
    assert no_match["status"] == "succeeded" and no_match["result"]["citations"] == []
    assert no_match["result"]["usage"] == {"input_tokens": 0, "output_tokens": 0, "cost_units": 0}
    assert trace(empty)[0] == []

    book = req("/v1/books", "north-staff", {"title": "Test book", "audience": "staff"}, status=201)["book_id"]
    book_keyword = "wintercodex" + uuid4().hex
    upload_id = upload_sample(book, "north-staff", book_keyword)
    assert upload_sample(book, "north-staff", book_keyword) == upload_id
    upload_path = f"/v1/ingestion-jobs/{upload_id}"
    req(upload_path, "south-staff", status=404)
    wait_for(lambda: req(upload_path, "north-staff")["status"] == "ready")
    before = submit(book_keyword, "north-staff")
    assert terminal(before, "north-staff")["result"]["citations"] == []
    req(f"/v1/books/{book}/versions/{upload_id}/publish", "north-staff",
        {"rights_basis": "Test-owned text"}, form=True)
    for user, expected in (("north-staff", [f"book-version-{upload_id}-0"]), ("north-reader", []), ("south-staff", [])):
        assert terminal(submit(book_keyword, user), user)["result"]["citations"] == expected

    compose("stop", "worker")
    queued = submit()
    assert job(queued)["status"] == "queued" and trace(queued)[0] == []
    compose("restart", "api")
    compose("up", "-d", "--wait", "--no-deps", "api", "worker")
    assert terminal(queued)["status"] == "succeeded"

    for primary, backup, providers, cost, error in (
        ("timeout", "ok", ["primary", "primary", "backup"], 450, None),
        ("bad_request", "ok", ["primary"], None, "primary_bad_request"),
        ("rate_limit", "bad_request", ["primary", "primary", "backup"], None, "backup_bad_request"),
    ):
        mode(primary, backup)
        task = submit()
        row = terminal(task)
        assert [call["provider"] for call in trace(task)[0]] == providers
        if error:
            assert row["status"] == "failed" and row["result"] is None and row["error_code"] == error
        else:
            assert row["status"] == "succeeded"
            assert row["result"]["usage"] == {"input_tokens": 150, "output_tokens": 50, "cost_units": cost}

    check_recovery_before_send("ok", ["primary"], 200)
    check_recovery_before_send("rate_limit", ["primary", "primary", "backup"], 450)

    for primary, providers, cost in (("ok", ["primary"], 200), ("rate_limit", ["primary", "primary", "backup"], 450)):
        mode(primary)
        compose("up", "-d", "--no-deps", "--force-recreate", "worker", LIBRARY_CRASH_AFTER_SUCCESS="1")
        task = submit()
        container = compose("ps", "-aq", "worker")
        def exited():
            return subprocess.check_output(
                ["docker", "inspect", "--format", "{{.State.Status}}/{{.State.ExitCode}}", container],
                text=True,
            ).strip() == "exited/73"
        wait_for(exited)
        assert job(task)["status"] == "running" and job(task)["result"] is None
        calls, reads = trace(task)
        assert [call["provider"] for call in calls] == providers and not reads
        compose("up", "-d", "--no-deps", "--force-recreate", "worker", LIBRARY_CRASH_AFTER_SUCCESS="0")
        recovered = terminal(task)
        assert recovered["result"]["usage"]["cost_units"] == cost
        assert recovered["result"]["citations"] == ["north-locker-guide"]
        assert recovered["result"]["answer"] == "北馆储物柜在一层服务台旁。读者可以免费使用至当天闭馆。"
        after, reads = trace(task)
        assert after == calls and len(reads) == 1 and reads[0]["attempt_id"] == calls[-1]["attempt_id"]
        compose("restart", "api", "worker")
        compose("up", "-d", "--wait", "--no-deps", "api", "worker")
        assert job(task) == recovered and job(task) == recovered and trace(task) == (after, reads)
        print(f"crash/recovery: primary={primary}, exit=73, calls={len(calls)}, cost={cost}", flush=True)
    mode("ok")
    print("Library integration checks passed", flush=True)


if __name__ == "__main__":
    run()
