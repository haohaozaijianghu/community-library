import json
import logging
import os
import re
import time
from uuid import uuid4

from psycopg.types.json import Jsonb

from library import agent, configured, gateway
from library.books import catalog_evidence, catalog_requested, ingest, published_evidence
from library.core import event, finish, fixture_evidence, pool, portal_evidence
from library.portal import settings as portal_settings
from library.sources import public_evidence

NO_EVIDENCE = {
    "answer": "可用资料不足，无法确认。",
    "citations": [],
    "usage": {"input_tokens": 0, "output_tokens": 0, "cost_units": 0},
}
WORKER_LOCK = 2026112601
PROVIDERS = ("primary", "primary", "backup")
HISTORY_BUDGET_BYTES = 24_000


def conversation_context(conn, job):
    if not job["conversation_id"]:
        return []
    return conn.execute(
        """SELECT query,source_mode,result,evidence FROM library_jobs
           WHERE (id=%(conversation_id)s OR conversation_id=%(conversation_id)s)
             AND tenant=%(tenant)s AND owner_id=%(owner_id)s AND role=%(role)s
             AND (created_at,id)<(%(created_at)s,%(id)s)
           ORDER BY created_at,id""", job,
    ).fetchall()


def model_history(context):
    # Budget the JSON array itself, including separators and brackets.
    size = 2
    history = []
    for turn in reversed(context):
        item = {"question": turn["query"], "answer": turn["result"]["answer"] if turn["result"] else None}
        item_size = len(json.dumps(item, ensure_ascii=False).encode("utf-8"))
        if size + item_size + (2 if history else 0) > HISTORY_BUDGET_BYTES:
            if not history:
                history.append({"question": item["question"], "answer": None})
            return list(reversed(history)), True
        size += item_size + (2 if history else 0)
        history.append(item)
    return list(reversed(history)), False


def step(conn):
    with conn.transaction():
        job = conn.execute(
            """SELECT * FROM library_jobs WHERE status IN ('queued', 'running')
               ORDER BY (status='running') DESC, created_at, id LIMIT 1"""
        ).fetchone()
        if not job:
            return False
        conn.execute("UPDATE library_jobs SET status='running' WHERE id=%s", (job["id"],))

    context = conversation_context(conn, job)
    job["history"], job["history_truncated"] = model_history(context)
    if job["evidence"] is None:
        result = None
        if job["source_mode"] == "books":
            config = agent.settings(conn, job["tenant"])
            job["agent_prompt"] = config["prompt"]
            job["answer_model"] = config["answer_model"]
            job["evidence"], result = agent.retrieve(conn, job, config, context)
            if not job["evidence"] and result is None:
                result = {**NO_EVIDENCE, "answer": "本馆已发布电子书中没有找到可支持这个问题的资料。"}
        elif not job["web_search"] and catalog_requested(job["query"], job["tenant"]):
            job["evidence"], result = catalog_evidence(conn, job)
        else:
            job["evidence"] = portal_evidence(conn, job) + fixture_evidence(conn, job) + published_evidence(conn, job)
            if (not job["evidence"] and not job["web_search"] and
                    re.search(r"^(?:那|那么|那么说|接着|再说|还有|关于这)|(?:这个|那项|它)", job["query"])):
                for turn in reversed(context):
                    if turn["source_mode"] != "library" or not (turn["result"] or {}).get("citations"):
                        continue
                    earlier = {**job, "query": turn["query"]}
                    job["evidence"] = portal_evidence(conn, earlier) + fixture_evidence(conn, earlier) + published_evidence(conn, earlier)
                    if job["evidence"]:
                        break
            if job["web_search"]:
                portal = portal_settings(conn, job["tenant"])
                job["evidence"].extend(public_evidence(
                    job, notices_enabled=portal["external_notices"], notice_feed=portal["notice_feed"],
                ))
        with conn.transaction():
            if job["evidence"]:
                conn.execute("UPDATE library_jobs SET evidence=%s,agent_prompt=%s,answer_model=%s WHERE id=%s",
                             (Jsonb(job["evidence"]), job["agent_prompt"], job["answer_model"], job["id"]))
            if result is not None or not job["evidence"]:
                finish(conn, job["id"], result=result if result is not None else NO_EVIDENCE)
                return True

    with conn.transaction():
        latest = conn.execute(
            "SELECT * FROM library_attempts WHERE job_id=%s ORDER BY ordinal DESC LIMIT 1",
            (job["id"],),
        ).fetchone()
        recovery = bool(latest and latest["outcome"] == "pending")
        if recovery:
            attempt = latest
        else:
            ordinal = latest["ordinal"] + 1 if latest else 0
            model = None
            selection = job["answer_model"] or "default"
            if latest is None and selection not in {"default", "mock"}:
                model = {"id": selection}
            attempt = conn.execute(
                """INSERT INTO library_attempts (job_id, ordinal, attempt_id, provider, model_id)
                   VALUES (%s,%s,%s,%s,%s) RETURNING *""",
                (job["id"], ordinal, uuid4().hex, "configured" if model else PROVIDERS[ordinal],
                 model["id"] if model else None),
            ).fetchone()

    if attempt["provider"] == "configured":
        outcome, result = configured.generate(conn, job, attempt, recovery)
    elif recovery:
        outcome, result = gateway.resume(job, attempt)
    else:
        outcome, result = gateway.call(job, attempt, recovery=False)
    if result and not recovery and os.getenv("LIBRARY_CRASH_AFTER_SUCCESS") == "1":
        event("crash_after_success", job_id=job["id"], attempt_id=attempt["attempt_id"])
        raise SystemExit(73)

    with conn.transaction():
        conn.execute("UPDATE library_attempts SET outcome=%s WHERE attempt_id=%s",
                     (outcome, attempt["attempt_id"]))
        if result is not None:
            finish(conn, job["id"], result=result)
        elif attempt["ordinal"] < 2 and outcome in gateway.RETRYABLE:
            if attempt["ordinal"] == 1:
                event("model_switch", job_id=job["id"], provider="backup")
        else:
            finish(conn, job["id"], error=f"{attempt['provider']}_{outcome}")
    return True


def main():
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    with pool() as connections, connections.connection() as conn:
        conn.autocommit = True
        if not conn.execute("SELECT pg_try_advisory_lock(%s) AS acquired",
                            (WORKER_LOCK,)).fetchone()["acquired"]:
            raise SystemExit("A question worker is already running")
        while True:
            if not step(conn) and not ingest(conn):
                time.sleep(0.15)


if __name__ == "__main__":
    main()
