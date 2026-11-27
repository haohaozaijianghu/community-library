"""Verify branch-scoped community publishing in a disposable Compose stack."""
from check_accounts import browser, call, phone_login

ADMIN = "/v1/admin/community-posts"
READER = "/v1/reader/community-posts"


def run():
    north, south, north_reader, south_reader = (browser() for _ in range(4))
    for client, phone, role in (
        (north, "13800000002", "admin"), (south, "13800000004", "admin"),
        (north_reader, "13800000001", "user"), (south_reader, "13800000003", "user"),
    ):
        phone_login(client, phone, role)

    details = {"kind": "activity", "title": "  北馆共读  ", "body": "周六\n带一本喜欢的书。"}
    call(browser(), READER, expected=401)
    call(north_reader, ADMIN, expected=403)
    call(north_reader, ADMIN, details, expected=403)
    call(north, ADMIN, {**details, "tenant_id": "south"}, expected=400)
    draft = call(north, ADMIN, details, expected=201)
    assert draft["state"] == "draft" and draft["title"] == "北馆共读"
    assert not any(post["id"] == draft["id"] for post in call(north_reader, READER))
    assert not any(post["id"] == draft["id"] for post in call(south, ADMIN))

    path = f"{ADMIN}/{draft['id']}"
    edited = call(north, path, {**details, "title": "共读活动", "version": draft["version"]}, method="PATCH")
    publish = {"state": "published", "version": edited["version"]}
    call(south, path, {**details, "version": edited["version"]}, method="PATCH", expected=404)
    call(south, path + "/state", publish, expected=404)
    call(north, path + "/state", {**publish, "version": draft["version"]}, expected=409)
    call(north, path + "/state", {**publish, "state": "withdrawn"}, expected=409)
    published = call(north, path + "/state", publish)
    reader_post = next(post for post in call(north_reader, READER) if post["id"] == draft["id"])
    assert reader_post == {
        **{key: published[key] for key in ("id", "kind", "title", "body", "published_at")},
        "starts_at": None, "location": None, "capacity": None, "enrolled": False, "enrolled_count": 0,
    }
    assert not any(post["id"] == draft["id"] for post in call(south_reader, READER))

    state = {"state": "withdrawn", "version": published["version"]}
    withdrawn = call(north, path + "/state", state)
    assert withdrawn["state"] == "withdrawn"
    assert not any(post["id"] == draft["id"] for post in call(north_reader, READER))
    call(north, path + "/state", {"state": "published", "version": withdrawn["version"]})
    assert any(post["id"] == draft["id"] for post in call(north_reader, READER))
    print("Community publishing passed: drafts, editing, publishing, withdrawal, stale versions and tenant isolation")


if __name__ == "__main__":
    run()
