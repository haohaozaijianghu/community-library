from library.dependencies import LibraryError
from library.model_client import Gateway
from library.core import ROOT, event
from library.model_connections import GatewaySettings, connection


def generate(conn, job, attempt, recovery):
    result = None
    if recovery:
        outcome = "result_unconfirmed"
    else:
        model = connection(conn, job["tenant"], attempt["model_id"], for_answer=True)
        if not model:
            outcome = "model_unavailable"
        else:
            setup = GatewaySettings(15)
            gateway = Gateway(setup)
            try:
                instruction = (ROOT / "prompt.md").read_text(encoding="utf-8")
                if job["source_mode"] == "books" and job["agent_prompt"]:
                    instruction += (
                        "\n本馆对回答方式的附加要求如下。沿用上述资料权限、事实依据、引用与 JSON 格式约束。\n"
                        + job["agent_prompt"]
                    )
                documents = [{"id": doc["id"], "text": doc["text"]} for doc in job["evidence"]]
                payload = {"question": job["query"], "documents": documents}
                if job.get("history"):
                    instruction += "\nconversation 是按时间排列的历史问答，仅用于理解上下文。事实与引用必须依据本轮 documents。"
                    payload["conversation"] = job["history"]
                    if job.get("history_truncated"):
                        payload["conversation_truncated"] = True
                body, usage = gateway.json_with_usage(model, instruction, payload)
                ids = {item["id"] for item in job["evidence"]}
                answer, citations = body["answer"], body["citations"]
                if (not isinstance(answer, str) or not answer.strip()
                        or not isinstance(citations, list)
                        or any(not isinstance(item, str) or item not in ids for item in citations)):
                    raise ValueError("invalid model result")
                reported = usage or {}
                counts = (reported.get("prompt_tokens"), reported.get("completion_tokens"))
                if any(value is not None and (type(value) is not int or value < 0) for value in counts):
                    raise ValueError("invalid token counts")
                result = {
                    "answer": answer, "citations": list(dict.fromkeys(citations)),
                    "usage": {"input_tokens": counts[0], "output_tokens": counts[1], "cost_units": None},
                }
                outcome = "success"
            except LibraryError as error:
                outcome = error.code
            except (KeyError, ValueError, TypeError, AttributeError):
                outcome = "invalid_response"
            finally:
                gateway.close()
    event("model_attempt", job_id=job["id"], provider="configured",
          attempt_id=attempt["attempt_id"], outcome=outcome, model_id=str(attempt["model_id"]))
    return outcome, result
