"""Verify content, account policy, activity enrollment and help across the two branches."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier
from urllib.error import HTTPError
from urllib.request import Request
from uuid import uuid4

from check_accounts import ACCOUNTS, browser, call, phone_login
from check_library import BASE, wait_for

COMMUNITY = "/v1/admin/community-posts"


def publish(client, kind, title, body, **fields):
    draft = call(client, COMMUNITY, {"kind": kind, "title": title, "body": body, **fields}, expected=201)
    return call(client, f"{COMMUNITY}/{draft['id']}/state", {"version": draft["version"], "state": "published"})


def submit(client, query):
    job = call(client, "/v1/jobs", {"query": query}, expected=202)["job_id"]

    def finished():
        status = call(client, f"/v1/jobs/{job}")
        return status if status["status"] in {"succeeded", "failed"} else None

    result = wait_for(finished)
    assert result["status"] == "succeeded", result
    return job, result["result"]


def run():
    north, south, reader, other = (browser() for _ in range(4))
    phone_login(north, "13800000002", "admin")
    phone_login(south, "13800000004", "admin")
    phone_login(other, "13800000003", "user")
    phone = "137" + str(uuid4().int % 10**8).zfill(8)
    call(north, ACCOUNTS, {"role": "reader", "phone": phone}, expected=201)
    phone_login(reader, phone, "user")
    guide = publish(north, "guide", "预约规则", "到阅览室登记。", keywords=["studyroom"])
    faq = publish(north, "faq", "怎么预约阅览室？", "请到前台登记。", featured=True)
    handbook = publish(north, "handbook", "值班规程", "仅供本馆馆员核对。", keywords=["deskduty"])
    guide_id = "community-" + guide["id"]
    handbook_id = "community-" + handbook["id"]
    assert any(row["id"] == guide["id"] for row in call(reader, "/v1/reader/guides"))
    assert all(row["id"] != guide["id"] for row in call(other, "/v1/reader/guides"))
    assert all(row["id"] != handbook["id"] for row in call(reader, "/v1/reader/guides"))
    seeded = next(row for row in call(north, COMMUNITY) if row["source_id"] == "north-locker-guide")
    seeded_path = f"{COMMUNITY}/{seeded['id']}"
    edit_fields = {key: seeded[key] for key in ("kind", "title", "body", "keywords", "sort_order", "version")}
    call(north, seeded_path, {**edit_fields, "kind": "handbook"}, method="PATCH", expected=409)
    tuned = call(north, seeded_path, {**edit_fields, "body": "开放期间可使用储物柜。"}, method="PATCH")
    assert any(row["id"] == seeded["source_id"] and row["text"] == tuned["body"] for row in call(reader, "/v1/reader/guides"))
    call(north, seeded_path, {**edit_fields, "version": tuned["version"]}, method="PATCH")
    assert any(row["id"] == faq["id"] and row["featured"] for row in call(reader, "/v1/reader/portal")["faqs"])
    assert not any(row["id"] == faq["id"] for row in call(other, "/v1/reader/portal")["faqs"])
    job, answer = submit(reader, "studyroom")
    assert guide_id in answer["citations"]
    _, answer = submit(reader, "deskduty")
    assert handbook_id not in answer["citations"]
    _, answer = submit(north, "deskduty")
    assert handbook_id in answer["citations"]
    _, answer = submit(other, "studyroom")
    assert guide_id not in answer["citations"]
    call(north, f"{COMMUNITY}/{guide['id']}/state", {"version": guide["version"], "state": "withdrawn"})
    _, answer = submit(reader, "studyroom")
    assert guide_id not in answer["citations"]

    help_path = f"/v1/reader/help/{job}"
    assert call(reader, help_path) is None
    assert all(ticket["job_id"] != job for ticket in call(north, "/v1/admin/help"))
    call(other, help_path, method="PUT", expected=404)
    call(reader, help_path, method="PUT")
    call(reader, help_path, method="PUT")
    assert len([ticket for ticket in call(north, "/v1/admin/help") if ticket["job_id"] == job]) == 1
    assert all(ticket["job_id"] != job for ticket in call(south, "/v1/admin/help"))
    assert call(other, help_path) is None
    reply_path = f"/v1/admin/help/{job}/reply"
    call(south, reply_path, {"reply": "跨馆回复"}, expected=404)
    call(north, reply_path, {"reply": "馆员已核实。"})
    call(north, reply_path, {"reply": "重复回复"}, expected=409)
    assert call(reader, help_path)["reply"] == "馆员已核实。"

    activity = publish(north, "activity", "共读活动", "带上图书。",
                       starts_at=(datetime.now(timezone.utc) + timedelta(days=3)).isoformat(),
                       location="北馆阅览室", capacity=1)
    enrollment = f"/v1/reader/activities/{activity['id']}/enrollment"
    assert call(reader, enrollment, method="PUT")["enrolled"]
    assert call(reader, enrollment, method="PUT")["enrolled"]
    call(other, enrollment, method="PUT", expected=404)
    second = browser()
    phone2 = "136" + phone[3:]
    call(north, ACCOUNTS, {"role": "reader", "phone": phone2}, expected=201)
    phone_login(second, phone2, "user")
    call(second, enrollment, method="PUT", expected=409)
    roster = f"{COMMUNITY}/{activity['id']}/enrollments"
    assert [item["phone"] for item in call(north, roster)] == [phone]
    call(south, roster, expected=404)
    call(reader, enrollment, method="DELETE")
    assert call(second, enrollment, method="PUT")["enrolled"]
    assert [item["phone"] for item in call(north, roster)] == [phone2]
    call(second, enrollment, method="DELETE")
    barrier = Barrier(2)

    def race_for_seat(item):
        client, number = item
        barrier.wait(timeout=5)
        request = Request(BASE + enrollment, headers={"X-Library-UI": "1"}, method="PUT")
        try:
            response = client.open(request, timeout=5)
        except HTTPError as error:
            response = error
        with response:
            return response.status, number

    with ThreadPoolExecutor(max_workers=2) as threads:
        outcomes = sorted(threads.map(race_for_seat, [(reader, phone), (second, phone2)]))
    assert [status for status, _ in outcomes] == [200, 409], outcomes
    assert [item["phone"] for item in call(north, roster)] == [outcomes[0][1]]


    book = call(north, "/v1/books", {"title": "城市阅读", "category": "城市生活"}, expected=201)
    assert any(item["id"] == book["book_id"] and item["category"] == "城市生活" for item in call(reader, "/v1/books"))
    assert not any(item["id"] == book["book_id"] for item in call(other, "/v1/books"))

    settings = call(north, "/v1/admin/portal")
    choices = settings.pop("notice_feed_options")
    assert isinstance(choices, list)
    call(north, "/v1/admin/portal", {**settings, "notice_feed": "https://unapproved.example.org/rss"},
         method="PUT", expected=400)
    if choices:
        selected = call(north, "/v1/admin/portal", {**settings, "notice_feed": choices[0]}, method="PUT")
        assert selected["notice_feed"] == choices[0]
        settings = selected
    edited = {**settings, "title": "北馆读者门户", "shortcuts": ["borrow-guide"], "external_notices": False,
              "otp_login_enabled": False, "registration_enabled": False}
    call(north, "/v1/admin/portal", edited, method="PUT")
    call(north, "/v1/admin/portal", edited, method="PUT", expected=409)
    assert call(reader, "/v1/reader/portal")["title"] == edited["title"]
    assert call(reader, "/v1/reader/portal")["shortcuts"] == edited["shortcuts"]
    assert call(reader, "/v1/reader/notices") == {"items": [], "state": "disabled"}
    assert call(south, "/v1/admin/portal")["registration_enabled"]
    call(browser(), "/v1/auth/register/code", {"tenant_id": "north", "phone": "135" + phone[3:]}, expected=403)
    fields = {"phone": phone, "login_type": "user"}
    call(browser(), "/v1/auth/code", fields, expected=403)
    reset = browser()
    code = call(reset, "/v1/auth/password-reset/code", fields)["local_code"]
    password = "Example password 123"
    payload = {**fields, "code": code, "password": password}
    call(reset, "/v1/auth/password-reset", payload)
    call(reset, "/v1/auth/password-reset", payload, expected=401)
    call(reader, "/v1/me", expected=401)
    assert call(reset, "/v1/auth/password-login", {**fields, "password": password})["tenant_id"] == "north"
    print("Portal workflows passed: content and citations, help, enrollment, categories, policy and reset")


if __name__ == "__main__":
    run()
