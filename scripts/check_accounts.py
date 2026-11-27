"""Verify tenant-scoped account administration in a disposable Compose stack."""
import json
import os
from http.cookiejar import CookieJar
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, Request, build_opener
from uuid import uuid4

from check_library import BASE, req, wait_for

ACCOUNTS = "/v1/admin/accounts"


def browser():
    return build_opener(HTTPCookieProcessor(CookieJar()))


def call(client, path, body=None, *, method=None, expected=200):
    request = Request(
        BASE + path,
        None if body is None else json.dumps(body).encode(),
        {"Content-Type": "application/json", "X-Library-UI": "1", "Idempotency-Key": uuid4().hex},
        method=method,
    )
    try:
        response = client.open(request, timeout=5)
    except HTTPError as error:
        response = error
    with response:
        result = json.load(response)
        assert response.status == expected, (path, response.status, result)
        return result


def phone_login(client, phone, kind):
    payload = {"phone": phone, "login_type": kind}
    code = call(client, "/v1/auth/code", payload)["local_code"]
    return call(client, "/v1/auth/login", {**payload, "code": code})


def update(client, row, *, role, active):
    return call(client, f"{ACCOUNTS}/{row['user_id']}",
                {"role": role, "active": active, "version": row["version"]}, method="PATCH")


def check_profile(reader, phone, user_id):
    profile = "/v1/profile"
    account = {"tenant_id": "north", "user_id": user_id, "role": "reader"}
    other = browser()
    call(other, profile, expected=401)
    phone_login(other, phone, "user")
    result = call(reader, profile, {"display_name": "  北馆读者  "}, method="PATCH")
    assert result == {"display_name": "北馆读者", "phone_masked": phone[:3] + "****" + phone[-4:],
                      "has_password": False}
    assert call(other, profile) == result

    password = "Test first password 123"
    auth = "/v1/auth/password-login"
    credentials = {"phone": phone, "password": password, "login_type": "user"}
    call(browser(), auth, credentials, expected=401)
    code = call(reader, profile + "/code", {"purpose": "password"})["local_code"]
    assert call(reader, profile + "/password", {"code": code, "new_password": password})["has_password"]
    call(other, "/v1/me", expected=401)
    assert call(other, auth, credentials) == account

    new_phone = "139" + str(uuid4().int % 10**8).zfill(8)
    old = call(reader, profile + "/code", {"purpose": "phone-old"})["local_code"]
    call(reader, profile + "/code", {"purpose": "phone-new", "new_phone": "13800000003"}, expected=409)
    new = call(reader, profile + "/code", {"purpose": "phone-new", "new_phone": new_phone})["local_code"]
    edit = {"new_phone": new_phone, "old_code": old, "new_code": new}
    wrong = "000001" if new == "000000" else "000000"
    call(reader, profile + "/phone", {**edit, "new_code": wrong}, expected=401)
    assert call(reader, profile + "/phone", edit)["phone_masked"] == new_phone[:3] + "****" + new_phone[-4:]
    call(other, "/v1/me", expected=401)
    call(other, auth, credentials, expected=401)
    credentials["phone"] = new_phone
    assert call(other, auth, credentials) == account
    assert call(reader, "/v1/me") == account

    code = call(reader, profile + "/code", {"purpose": "password"})["local_code"]
    new_password = "Test second password 456"
    payload = {"code": code, "current_password": password, "new_password": new_password}
    rejected = call(reader, profile + "/password", {**payload, "current_password": "Incorrect password"}, expected=401)
    assert rejected["detail"] == "invalid_current_password"
    result = call(reader, profile + "/password", payload)
    assert result["has_password"] and result["display_name"] == "北馆读者"
    call(other, "/v1/me", expected=401)
    call(other, auth, credentials, expected=401)
    assert call(other, auth, {**credentials, "password": new_password}) == account
    call(reader, profile + "/password", payload, expected=401)
    assert phone_login(browser(), new_phone, "user") == account
    print("Profile passed: nickname, verified phone change, password setup and rotation, session revocation and OTP login")


def check_registration(north, south):
    reader = browser()
    phone = "139" + str(uuid4().int % 10**8).zfill(8)
    username = "reader-" + uuid4().hex[:10]
    password = "Local test password 123"
    fields = {"tenant_id": "south", "phone": phone}
    register = "/v1/auth/register"
    credentials = {**fields, "username": username.upper(), "password": password}
    call(reader, register + "/code", {**fields, "tenant_id": "other"}, expected=400)
    code = call(reader, register + "/code", fields)["local_code"]
    call(reader, register + "/code", {**fields, "tenant_id": "north"}, expected=429)
    wrong = "000001" if code == "000000" else "000000"
    call(reader, register, {**credentials, "code": wrong}, expected=401)
    call(reader, register, {**credentials, "tenant_id": "north", "code": code}, expected=401)
    call(reader, register, {**credentials, "role": "staff", "code": code}, expected=400)
    created = call(reader, register, {**credentials, "code": code}, expected=201)
    assert created["tenant_id"] == "south" and created["role"] == "reader"
    assert call(reader, "/v1/me") == created
    assert all(row["user_id"] != created["user_id"] for row in call(north, ACCOUNTS))
    assert any(row["user_id"] == created["user_id"] for row in call(south, ACCOUNTS))
    call(reader, register, {**credentials, "code": code}, expected=401)
    call(reader, register + "/code", fields, expected=409)

    other_phone = "139" + str((int(phone[3:]) + 1) % 10**8).zfill(8)
    other_fields = {"tenant_id": "north", "phone": other_phone}
    other_code = call(reader, register + "/code", other_fields)["local_code"]
    result = call(reader, register, {**credentials, **other_fields, "username": username, "code": other_code},
                  expected=409)
    assert result["detail"] == "username_unavailable"
    corrected = call(reader, register,
                     {**credentials, **other_fields, "username": username + "-north", "code": other_code},
                     expected=201)
    assert corrected["tenant_id"] == "north"

    auth = "/v1/auth/password-login"
    login = {"username": username.upper(), "password": password, "login_type": "user"}
    assert call(browser(), auth, login) == created
    assert call(browser(), auth, {"phone": phone, "password": password, "login_type": "user"}) == created
    call(browser(), auth, {**login, "login_type": "admin"}, expected=401)
    assert phone_login(browser(), phone, "user") == created
    print("Registration passed: verified branch, reader-only role, uniqueness, single-use code and all login methods")


def run():
    north, south, new_user = browser(), browser(), browser()
    phone_login(north, "13800000002", "admin")
    phone_login(south, "13800000004", "admin")
    phone = os.environ.get("TEST_NEW_ACCOUNT_PHONE", "13900008081")
    new = call(north, ACCOUNTS, {"phone": phone, "role": "reader", "tenant_id": "south"}, expected=201)
    assert any(row["user_id"] == new["user_id"] for row in call(north, ACCOUNTS))
    assert all(row["user_id"] != new["user_id"] for row in call(south, ACCOUNTS))
    payload = {"role": "staff", "active": True, "version": new["version"]}
    call(south, f"{ACCOUNTS}/{new['user_id']}", payload, method="PATCH", expected=404)
    call(south, ACCOUNTS, {"phone": phone, "role": "staff"}, expected=409)

    args = {"phone": phone, "login_type": "admin"}
    call(new_user, "/v1/auth/code", args, expected=401)
    assert phone_login(new_user, phone, "user") == {
        "tenant_id": "north", "user_id": new["user_id"], "role": "reader",
    }
    call(new_user, ACCOUNTS, expected=403)
    promoted = update(north, new, role="staff", active=True)
    call(new_user, "/v1/me", expected=401)
    phone_login(new_user, phone, "admin")
    assert call(new_user, "/v1/me")["role"] == "staff"
    call(north, f"{ACCOUNTS}/{new['user_id']}", payload, method="PATCH", expected=409)

    task = call(new_user, "/v1/jobs", {"query": "locker"}, expected=202)["job_id"]
    def finished():
        job = call(new_user, f"/v1/jobs/{task}")
        return job if job["status"] in {"succeeded", "failed"} else None

    assert "north-locker-staff" in wait_for(finished)["result"]["citations"]
    demoted = update(north, promoted, role="reader", active=True)
    call(new_user, "/v1/me", expected=401)
    phone_login(new_user, phone, "user")
    call(new_user, f"/v1/jobs/{task}", expected=404)
    call(new_user, f"/v1/jobs/{task}/citations/north-locker-staff", expected=404)

    disabled = update(north, demoted, role="reader", active=False)
    call(new_user, "/v1/me", expected=401)
    call(new_user, "/v1/auth/code", {"phone": phone, "login_type": "user"}, expected=401)
    update(north, disabled, role="reader", active=True)
    phone_login(new_user, phone, "user")

    current = next(row for row in call(north, ACCOUNTS) if row["phone"] == "13800000002")
    call(north, f"{ACCOUNTS}/{current['user_id']}",
         {"role": "reader", "active": False, "version": current["version"]}, method="PATCH", expected=409)
    reader = next(row for row in call(north, ACCOUNTS) if row["phone"] == "13800000001")
    inactive = update(north, reader, role="reader", active=False)
    try:
        req("/v1/me", "north-reader", status=401)
    finally:
        update(north, inactive, role="reader", active=True)
    assert req("/v1/me", "north-reader")["tenant_id"] == "north"
    check_profile(new_user, phone, new["user_id"])
    check_registration(north, south)
    print("Accounts passed: tenant boundary, phone login, role changes, revocation, reactivation and admin lockout protection")


if __name__ == "__main__":
    run()
