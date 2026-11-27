import json

from library.worker import HISTORY_BUDGET_BYTES, model_history


def turn(query, answer):
    return {"query": query, "result": {"answer": answer}}


def test_complete_history_under_budget():
    history, truncated = model_history([turn("第一问", "回答一"), turn("第二问", "回答二")])
    assert not truncated
    assert history == [
        {"question": "第一问", "answer": "回答一"},
        {"question": "第二问", "answer": "回答二"},
    ]


def test_budget_keeps_latest_complete_turns():
    history, truncated = model_history([
        turn("第一问", "旧" * 8_000), turn("第二问", "新" * 7_900), turn("第三问", "换行\n和双引号\""),
    ])
    assert truncated
    assert [entry["question"] for entry in history] == ["第二问", "第三问"]
    assert len(json.dumps(history, ensure_ascii=False).encode()) <= HISTORY_BUDGET_BYTES


def test_oversized_latest_reply_keeps_its_question():
    history, truncated = model_history([turn("最近一问", "长" * 9_000)])
    assert truncated
    assert history == [{"question": "最近一问", "answer": None}]
