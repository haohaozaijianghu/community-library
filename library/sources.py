import asyncio
import hashlib
import json
import os
from datetime import datetime, timezone
from urllib.parse import urljoin, urlsplit
from xml.etree import ElementTree

import httpx

from library.core import event

CATALOG_URL = "https://openlibrary.org/search.json"
NOTICE_WORDS = ("公告", "闭馆", "开放时间", "notice", "closure", "opening hours")
MAX_RESPONSE = 512 * 1024


async def fetch(url, params=None):
    async with asyncio.timeout(2):
        async with httpx.AsyncClient(trust_env=False) as client:
            async with client.stream("GET", url, params=params) as response:
                response.raise_for_status()
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(data) + len(chunk) > MAX_RESPONSE:
                        raise ValueError("Public source response too large")
                    data.extend(chunk)
                return bytes(data)


def reference(source, url, text):
    now = datetime.now(timezone.utc).isoformat()
    return {
        "id": source + "-" + hashlib.sha256(url.encode()).hexdigest()[:20],
        "text": f"{text}。来源 {url}，获取于 {now}。",
        "url": url,
        "fetched_at": now,
    }


def catalog(query):
    payload = fetch(CATALOG_URL, {
        "q": query, "fields": "key,title,author_name,first_publish_year", "limit": 3,
    })
    docs = json.loads(asyncio.run(payload))["docs"]
    return [
        reference("openlibrary", "https://openlibrary.org" + doc["key"],
                  f"Open Library 公开书目：{doc['title']}，"
                  f"作者 {'、'.join(doc.get('author_name', [])) or '未登记'}，"
                  f"首次出版 {doc.get('first_publish_year', '未登记')}")
        for doc in docs[:3] if doc["key"].startswith("/works/")
    ]


def trusted_url(url, origin=None):
    parsed = urlsplit(url)
    return (parsed.scheme == "https" and parsed.hostname
            and not (parsed.username or parsed.password)
            and (origin is None or parsed.netloc == origin))


def notice_feed_choices(tenant):
    legacy = json.loads(os.getenv("LIBRARY_NOTICE_FEEDS") or "{}").get(tenant)
    choices = json.loads(os.getenv("LIBRARY_NOTICE_FEED_OPTIONS") or "{}").get(tenant, [])
    if not isinstance(choices, list):
        raise ValueError("Notice feed options should be a list")
    return list(dict.fromkeys(url for url in (legacy, *choices) if isinstance(url, str) and trusted_url(url)))


def notice_entries(tenant, feed=None):
    if feed:
        if feed not in notice_feed_choices(tenant):
            raise ValueError("The selected feed is unavailable")
        url = feed
    else:
        url = json.loads(os.getenv("LIBRARY_NOTICE_FEEDS") or "{}").get(tenant)
    if not url:
        return []
    if not trusted_url(url):
        raise ValueError("Invalid configured feed")
    root = ElementTree.fromstring(asyncio.run(fetch(url)))
    root_kind = root.tag.rsplit("}", 1)[-1]
    if root_kind == "rss" and (channel := root.find("./{*}channel")) is not None:
        items = channel.findall("./{*}item")
    elif root_kind == "feed":
        items = root.findall("./{*}entry")
    else:
        raise ValueError("Unsupported announcement feed")

    origin = urlsplit(url).netloc
    entries = []
    for item in items:
        for element in item.findall("{*}link"):
            if element.get("rel", "alternate") != "alternate":
                continue
            target = (element.get("href") or element.text or "").strip()
            if not target:
                continue
            link = urljoin(url, target)
            if trusted_url(link, origin):
                break
        else:
            continue
        title = item.findtext("{*}title", default="")
        published = item.findtext("{*}pubDate") or item.findtext("{*}updated") or "未标明"
        body = item.findtext("{*}description") or item.findtext("{*}summary") or ""
        entries.append({"url": link, "title": title, "published": published, "summary": body[:1600]})
        if len(entries) == 3:
            break
    return entries


def notices(tenant, feed=None):
    return [
        reference("notice", item["url"],
                  f"馆方公告：{item['title']}。发布时间：{item['published']}。{item['summary']}")
        for item in notice_entries(tenant, feed)
    ]


def reader_notices(tenant, feed=None):
    if os.getenv("LIBRARY_WEB_ENABLED") != "1":
        return {"state": "disabled", "items": []}
    try:
        return {"state": "available", "items": notice_entries(tenant, feed)}
    except (TimeoutError, httpx.HTTPError, ValueError, TypeError, AttributeError, KeyError, ElementTree.ParseError):
        event("public_source_unavailable", tenant=tenant, source="official_notice")
        return {"state": "unavailable", "items": []}


def public_evidence(job, *, notices_enabled=True, notice_feed=None):
    if os.getenv("LIBRARY_WEB_ENABLED") != "1":
        return []
    is_notice = any(word in job["query"].casefold() for word in NOTICE_WORDS)
    if is_notice and not notices_enabled:
        return []
    source = "official_notice" if is_notice else "openlibrary"
    try:
        return notices(job["tenant"], notice_feed) if is_notice else catalog(job["query"])
    except (TimeoutError, httpx.HTTPError, ValueError, TypeError, AttributeError, KeyError, ElementTree.ParseError):
        event("public_source_unavailable", job_id=job["id"], source=source)
        return []
