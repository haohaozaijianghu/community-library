"""Evaluate the standalone system prompt with an OpenAI-compatible model."""
import argparse
import hashlib
import json
import os
import re
from pathlib import Path

import httpx


ROOT = Path(__file__).resolve().parents[1]


def examples(case):
    data = case["input"]
    expected = case["expected"]
    yield "original", data, expected
    reversed_docs = data["documents"][::-1]
    if len(reversed_docs) > 1:
        yield "reversed", {**data, "documents": reversed_docs}, expected
    ids = {
        doc["id"]: "ev-" + hashlib.sha256((case["id"] + doc["id"]).encode()).hexdigest()[:8]
        for doc in reversed_docs
    }
    yield (
        "reversed-and-renamed",
        {**data, "documents": [{**doc, "id": ids[doc["id"]]} for doc in reversed_docs]},
        {**expected, "citations": [ids[item] for item in expected["citations"]]},
    )


def matches(result, expected):
    if not isinstance(result, dict) or result.keys() != {"status", "answer", "citations"}:
        return False
    answer, citations = result["answer"], result["citations"]
    return (
        result["status"] == expected["status"]
        and isinstance(answer, str) and bool(re.search(r"[\u4e00-\u9fff]", answer))
        and isinstance(citations, list) and all(isinstance(item, str) for item in citations)
        and sorted(citations) == sorted(expected["citations"])
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="https://api.deepseek.com/v1")
    parser.add_argument("--model", default="deepseek-flash")
    args = parser.parse_args()
    key = os.getenv("PROMPT_EVAL_API_KEY")
    if not key:
        parser.error("set PROMPT_EVAL_API_KEY in the environment")
    prompt = (ROOT / "prompt.md").read_text(encoding="utf-8")
    cases = json.loads((ROOT / "prompt" / "public_cases.json").read_text(encoding="utf-8"))
    cases.append(json.loads((ROOT / "prompt" / "mixed_case.json").read_text(encoding="utf-8")))
    passed = total = 0
    with httpx.Client(timeout=15, follow_redirects=False) as client:
        for case in cases:
            for variant, data, expected in examples(case):
                total += 1
                row = {"case": case["id"], "variant": variant, "expected": expected}
                try:
                    response = client.post(
                        args.base_url.rstrip("/") + "/chat/completions",
                        headers={"Authorization": "Bearer " + key},
                        json={
                            "model": args.model,
                            "messages": [
                                {"role": "system", "content": prompt},
                                {"role": "user", "content": json.dumps(data, ensure_ascii=False)},
                            ],
                            "response_format": {"type": "json_object"},
                        },
                    )
                    response.raise_for_status()
                    row["result"] = json.loads(response.json()["choices"][0]["message"]["content"])
                    row["passed"] = matches(row["result"], expected)
                except httpx.HTTPStatusError as error:
                    row.update(passed=False, error=f"HTTP {error.response.status_code}")
                except (httpx.RequestError, ValueError, KeyError, IndexError, TypeError) as error:
                    row.update(passed=False, error=type(error).__name__)
                passed += row["passed"]
                print(json.dumps(row, ensure_ascii=False), flush=True)
    print(f"{passed}/{total} passed JSON, status and citation checks")
    return int(passed != total)


if __name__ == "__main__":
    raise SystemExit(main())
