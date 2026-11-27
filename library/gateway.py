import asyncio
import os
import time

import httpx

from library.core import event

RETRYABLE = {"timeout", "rate_limited"}
# The bundled mock may finish its two-second timeout path after our client disconnects.
MOCK_RECEIPT_GRACE_SECONDS = 2.2
RATES = {"primary": (1, 2), "backup": (2, 3)}


async def request(method, path, payload=None):
    async with asyncio.timeout(1):
        async with httpx.AsyncClient(trust_env=False) as client:
            return await client.request(
                method, os.environ["LIBRARY_MOCK_URL"].rstrip("/") + path, json=payload
            )


def validated(body, provider, ids):
    answer, citations, usage = body["answer"], body["citations"], body["usage"]
    if not isinstance(answer, str) or not answer.strip() or not isinstance(citations, list):
        raise ValueError("invalid answer")
    if any(not isinstance(item, str) or item not in ids for item in citations):
        raise ValueError("invalid citation")
    tokens = {key: usage[key] for key in ("input_tokens", "output_tokens")}
    if any(type(value) is not int or value < 0 for value in tokens.values()):
        raise ValueError("invalid usage")
    input_rate, output_rate = RATES[provider]
    tokens["cost_units"] = tokens["input_tokens"] * input_rate + tokens["output_tokens"] * output_rate
    return {"answer": answer, "citations": list(dict.fromkeys(citations)), "usage": tokens}


def call(job, attempt, *, recovery):
    provider, attempt_id = attempt["provider"], attempt["attempt_id"]
    path = f"/v1/{provider}"
    payload = {"job_id": job["id"], "query": job["query"], "evidence": [{key: doc[key] for key in ("id", "text")} for doc in job["evidence"]], "attempt_id": attempt_id}
    if job.get("history"):
        payload["conversation"] = job["history"]
        if job.get("history_truncated"):
            payload["conversation_truncated"] = True
    result = None
    try:
        if recovery:
            response = asyncio.run(request("GET", f"{path}/receipts/{attempt_id}"))
        else:
            response = asyncio.run(request("POST", f"{path}/generate", payload))
        if response.status_code == 200:
            body = response.json()
            if recovery and body["job_id"] != job["id"]:
                raise ValueError("receipt job mismatch")
            result = validated(body, provider, {doc["id"] for doc in job["evidence"]})
            outcome = "success"
        elif recovery:
            outcome = "receipt_missing" if response.status_code == 404 else "receipt_unavailable"
        else:
            outcome = {
                400: "bad_request",
                409: "attempt_already_completed",
                429: "rate_limited",
            }.get(response.status_code, "http_error")
    except (TimeoutError, httpx.TimeoutException):
        outcome = "receipt_unavailable" if recovery else "timeout"
    except httpx.RequestError:
        outcome = "receipt_unavailable" if recovery else "transport_error"
    except (ValueError, KeyError, TypeError):
        outcome = "invalid_response"
    event("model_attempt", job_id=job["id"], provider=provider,
          attempt_id=attempt_id, outcome=outcome, receipt=recovery)
    return outcome, result



def resume(job, attempt):
    outcome, result = call(job, attempt, recovery=True)
    if outcome != "receipt_missing":
        return outcome, result

    # A missing receipt can mean the old request is still running.
    time.sleep(MOCK_RECEIPT_GRACE_SECONDS)
    outcome, result = call(job, attempt, recovery=True)
    if outcome != "receipt_missing":
        return outcome, result

    outcome, result = call(job, attempt, recovery=False)
    if outcome == "attempt_already_completed":
        return call(job, attempt, recovery=True)
    return outcome, result
