import os
from urllib.parse import urlsplit

import httpx
from fastapi import HTTPException
from fastapi.responses import JSONResponse


def local_enabled(request):
    return os.getenv("LIBRARY_LOCAL_PHONE_LOGIN") == "1" and request.url.hostname in {"127.0.0.1", "localhost", "::1"}


def gateway():
    url = os.getenv("LIBRARY_SMS_GATEWAY_URL", "")
    token = os.getenv("LIBRARY_SMS_GATEWAY_TOKEN", "")
    try:
        parsed = urlsplit(url)
        url_valid = parsed.scheme == "https" and parsed.hostname and not (
            parsed.username or parsed.password or parsed.fragment
        )
    except ValueError:
        return None
    return (url, token) if url_valid and token else None


def browser_auth_enabled(request):
    return local_enabled(request) or (request.url.scheme == "https" and gateway() is not None)


def deliver_code(conn, request, phone, code, purpose):
    result = {"expires_in": 300}
    if local_enabled(request):
        result["local_code"] = code
    else:
        if request.url.scheme != "https" or not (config := gateway()):
            raise HTTPException(503, "phone_login_unavailable")
        allowed = conn.execute(
            """INSERT INTO library_sms_usage (minute,sends) VALUES (date_trunc('minute',now()),1)
               ON CONFLICT (minute) DO UPDATE SET sends=library_sms_usage.sends+1
               WHERE library_sms_usage.sends<30 RETURNING sends"""
        ).fetchone()
        if not allowed:
            raise HTTPException(429, "sms_delivery_limited")
        conn.execute("DELETE FROM library_sms_usage WHERE minute<now()-interval '1 hour'")
        url, token = config
        try:
            with httpx.Client(timeout=4, trust_env=False, follow_redirects=False) as client:
                response = client.post(
                    url, headers={"Authorization": "Bearer " + token},
                    json={"phone": phone, "code": code, "purpose": purpose, "expires_in": 300},
                )
                response.raise_for_status()
        except (httpx.HTTPError, ValueError):
            raise HTTPException(503, "sms_delivery_failed") from None
    return JSONResponse(result, headers={"Cache-Control": "no-store"})
