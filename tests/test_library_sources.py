import json
from zipfile import ZipFile

from library import epub, sources


def test_catalog_source_is_opt_in(monkeypatch):
    called = []

    async def fetch(url, params=None):
        called.append((url, params))
        return json.dumps({"docs": [{
            "key": "/works/OL1W", "title": "Example", "author_name": ["Author"],
            "first_publish_year": 1999,
        }]}).encode()

    monkeypatch.setattr(sources, "fetch", fetch)
    monkeypatch.delenv("LIBRARY_WEB_ENABLED", raising=False)
    job = {"id": "test", "query": "Example", "tenant": "north"}
    assert sources.public_evidence(job) == []
    assert called == []
    monkeypatch.setenv("LIBRARY_WEB_ENABLED", "1")
    evidence = sources.public_evidence(job)
    assert called[0][1]["q"] == "Example"
    assert evidence[0]["url"] == "https://openlibrary.org/works/OL1W"
    assert evidence[0]["fetched_at"]


def test_notice_feed_is_per_tenant_and_same_origin(monkeypatch):
    async def fetch(url, params=None):
        assert url == "https://north.example/notices"
        return b"""<rss><channel>
            <item><title>Closed Saturday</title><link>https://north.example/1</link></item>
            <item><title>Unrelated</title><link>https://other.example/2</link></item>
        </channel></rss>"""

    monkeypatch.setattr(sources, "fetch", fetch)
    monkeypatch.setenv("LIBRARY_WEB_ENABLED", "1")
    monkeypatch.setenv("LIBRARY_NOTICE_FEEDS", '{"north":"https://north.example/notices"}')
    job = {"id": "test", "query": "闭馆公告", "tenant": "north"}
    evidence = sources.public_evidence(job)
    assert [doc["url"] for doc in evidence] == ["https://north.example/1"]
    assert "Closed Saturday" in evidence[0]["text"]
    assert sources.public_evidence({**job, "tenant": "south"}) == []


def test_epub_keeps_spine_order_and_text_locations(tmp_path):
    path = tmp_path / "example.epub"
    with ZipFile(path, "w") as file:
        file.writestr("META-INF/container.xml", '<container><rootfiles><rootfile full-path="book/main.opf"/></rootfiles></container>')
        file.writestr("book/main.opf", '''<package><manifest>
            <item id="a" href="a.xhtml" media-type="application/xhtml+xml"/>
            <item id="b" href="b.xhtml" media-type="application/xhtml+xml"/>
        </manifest><spine><itemref idref="b"/><itemref idref="a"/></spine></package>''')
        file.writestr("book/a.xhtml", "<p>Alpha</p>")
        file.writestr("book/b.xhtml", "<script>skip</script><p>Beta</p>")
    assert [(block.text, block.locator) for block in epub.epub_blocks(path)] == [
        ("Beta", "第 1 章"), ("Alpha", "第 2 章"),
    ]


def test_notice_feeds_resolve_same_origin_relative_links(monkeypatch):
    async def fetch(url):
        if url.endswith("rss.xml"):
            return b"""<rss><channel>
              <item><title>New hours</title><link>hours</link></item>
              <item><title>External</title><link>//other.example/news</link></item>
            </channel></rss>"""
        return b"""<feed xmlns="http://www.w3.org/2005/Atom">
          <entry><title>Reading activity</title>
            <link rel="self" href="/index.atom"/><link rel="alternate" href="/reading"/>
          </entry>
        </feed>"""

    monkeypatch.setattr(sources, "fetch", fetch)
    monkeypatch.setenv("LIBRARY_WEB_ENABLED", "1")
    monkeypatch.setenv("LIBRARY_NOTICE_FEEDS", '{"north":"https://north.example/feeds/rss.xml"}')
    monkeypatch.setenv("LIBRARY_NOTICE_FEED_OPTIONS", '{"north":["https://north.example/atom.xml"]}')
    rss = sources.reader_notices("north")
    assert rss["state"] == "available"
    assert [item["url"] for item in rss["items"]] == ["https://north.example/feeds/hours"]
    atom = sources.reader_notices("north", "https://north.example/atom.xml")
    assert atom["state"] == "available"
    assert [item["url"] for item in atom["items"]] == ["https://north.example/reading"]


def test_valid_empty_feed_and_non_feed_response_have_different_status(monkeypatch):
    async def fetch(url):
        return b"<rss><channel/></rss>" if url.endswith("/empty") else b"<html><body>Error</body></html>"

    monkeypatch.setattr(sources, "fetch", fetch)
    monkeypatch.setenv("LIBRARY_WEB_ENABLED", "1")
    monkeypatch.setenv("LIBRARY_NOTICE_FEEDS", '{"north":"https://north.example/empty"}')
    monkeypatch.setenv("LIBRARY_NOTICE_FEED_OPTIONS", '{"north":["https://north.example/error"]}')
    assert sources.reader_notices("north") == {"state": "available", "items": []}
    assert sources.reader_notices("north", "https://north.example/error") == {"state": "unavailable", "items": []}
    job = {"id": "test", "query": "公告", "tenant": "north"}
    assert sources.public_evidence(job, notice_feed="https://north.example/error") == []
