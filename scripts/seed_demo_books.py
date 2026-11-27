"""Add optional sample books to a running local library using its normal upload workflow."""

import argparse
import hashlib
import json
import os
import time
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from uuid import NAMESPACE_URL, uuid4, uuid5

# Paths used for shelf labels and all book text below are fictional.
BOOKS = {
    "north": [
        ("街巷里的树", "林余", "自然观察", "A-02", "reader", "published",
         "春末观察香樟时，先记录成熟叶子的形状，再比较新叶。香樟新叶常带浅红色，几天后会逐渐转绿。",
         "沿同一条街道每周记录一次，记下天气和观察日期。落叶可在原地拍照记录，无需摘取树叶。"),
        ("北城巷口的旧招牌", "周简", "地方文化", "B-04", "reader", "published",
         "在一条保留旧铺面的街道上，可以先从字体、用料和店铺名称辨认招牌的年代特征。木匾上的补漆痕迹也能反映后期修缮。",
         "整理走访记录时，把实地照片与口述回忆分开保存。遇到无法核对的年代，可标注为待考。"),
        ("周末亲子阅读笔记", "许芮", "亲子阅读", "C-01", "reader", "published",
         "共读一本绘本时，可以先请孩子观察封面并猜想故事，再读第一页。每读完一小段，可以停下来一起找图中的线索。",
         "读完后请孩子画出最想记住的情节，家长把孩子说的话记在图旁。记录中可写下书名和共读日期。"),
        ("北馆展阅交接手册", "北馆编辑组", "馆员资料", "内部 N-01", "staff", "published",
         "展阅材料交接时，先按展签核对件数和外观，再由交出人与接收人填写交接日期。临时发现的破损需在交接记录中单独注明。",
         "整理展阅清单时，将待修复材料放入独立的记录栏，避免与已归架材料混记。"),
        ("给雨后的花坛做笔记", "沈意", "自然观察", "A-05", "reader", "ready",
         "雨后观察花坛时，可记下积水消退的时间，并比较向阳和背阴处的变化。先从固定角度记录照片，便于之后对照。",
         "如需测量水深，可选边缘容易接近的位置，避免踩踏种植区。"),
        ("给社区读书会的一封信", "季行", "阅读与写作", "C-04", "reader", "empty", "", ""),
    ],
    "south": [
        ("沿河的观鸟课", "裴岸", "自然观察", "D-03", "reader", "published",
         "观察白鹭时，可在清晨到开阔河滩，从远处留意细长的颈和步态。白鹭觅食时常慢步寻找浅水里的小鱼。",
         "记录鸟类时注明地点、时间和数量。遇到辨认不清的鸟，可记录外形和行为，事后再查阅图鉴。"),
        ("河西老街的手艺人", "宋穗", "地方文化", "E-02", "reader", "published",
         "访问手艺人前，先说明记录用途和是否公开。可以从入行经过、工具用途和一件作品的制作步骤开始提问。",
         "整理采访时区分原话与编者说明，确认受访者同意后再分享涉及个人的内容。"),
        ("用一张纸做阅读地图", "顾言", "阅读与写作", "F-01", "reader", "published",
         "制作阅读地图时，把书中的主要地点画在纸上，再按故事顺序连接。每个地点下可以写一句话，解释人物为什么来到这里。",
         "故事发生年代不明确时，可以用颜色区分不同时间段。阅读地图适合在读完一章后逐步补全。"),
        ("南馆馆员巡架手册", "南馆编辑组", "馆员资料", "内部 S-01", "staff", "published",
         "巡架记录以书架编号为单位。检查错架书时，先记下当前架位和书脊标签，再按索书号核对目标架位。",
         "如发现标签缺失，可暂置待核区并标记发现位置，待同事复核后归架。"),
        ("孩子的第一本声音日记", "苏苇", "亲子阅读", "F-06", "reader", "ready",
         "在家门口听一分钟环境声音，并记录先后顺序。第二天换一个时间重听，把两次不同的声音画在纸上。",
         "分享日记时，可以只描述声音来自哪里，不记录路人的私人谈话。"),
        ("夏日河岸写生集", "叶弦", "艺术与手作", "E-05", "reader", "empty", "", ""),
    ],
}


TREE_BOOK_CHAPTERS = (
    "内容简介｜这是一册面向社区读者的自然观察手册。以常见行道树为例，介绍叶片辨认、季节变化、固定路线观察和笔记整理。",
    "第一节　识别叶片｜先观察叶子在枝条上的排列，再看叶缘、叶脉和叶柄。观察香樟时可比较成熟叶和新叶的形状与颜色。比较不同叶片时尽量在同一光照条件下拍照。",
    "第二节　季节变化｜为同一株树建立春、夏、秋、冬四栏记录。春季记录新芽与新叶，夏季补记树冠和遮阴，秋季观察落叶或果实，冬季记录枝条轮廓。每次沿用同一个拍摄位置。",
    "第三节　固定路线｜从熟悉的街角选择数株容易辨认的树，给每株树记一个观察编号。从人行道的安全位置观察。若遇道路施工或树木修剪，可顺手记录原因，便于下次比较。",
    "第四节　自然笔记｜一条记录至少包含日期、天气、观察编号和可描述的现象。可以补上照片或速写，对暂时无法判断的变化写明待复核。整理时将亲眼观察和从书中查来的资料分开。",
)


def sample_text(title, first, second):
    paragraphs = (title, first, second)
    if title == "街巷里的树":
        paragraphs = (title, TREE_BOOK_CHAPTERS[0], first, *TREE_BOOK_CHAPTERS[1:], second)
    return "\n\n".join(paragraphs)


def request(base, tenant, path, body=None, *, content_type="application/json"):
    if body is not None and content_type == "application/json":
        body = json.dumps(body, ensure_ascii=False).encode("utf-8")
    headers = {"Authorization": f"Bearer demo-{tenant}-staff", "Content-Type": content_type}
    with urlopen(Request(base + path, data=body, headers=headers), timeout=5) as response:
        return json.load(response)


def upload(base, tenant, book_id, text):
    boundary = uuid4().hex
    payload = (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"demo-catalog.txt\"\r\n"
        f"Content-Type: text/plain\r\n\r\n{text}\r\n--{boundary}--\r\n"
    ).encode("utf-8")
    return request(base, tenant, f"/v1/books/{book_id}/uploads", payload,
                   content_type=f"multipart/form-data; boundary={boundary}")["job_id"]


def wait_until_ready(base, tenant, version):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        upload = request(base, tenant, f"/v1/ingestion-jobs/{version}")
        if upload["status"] == "ready":
            return
        if upload["status"] == "failed":
            raise RuntimeError(f"{version}: {upload.get('error_code', '解析失败')}")
        time.sleep(0.2)
    raise TimeoutError(f"{version}: 等待电子版解析超时")


def register_seed_book(conn, book_id, tenant, metadata):
    with conn.transaction():
        conn.execute(
            """INSERT INTO library_books (id,tenant,title,author,category,shelf,audience)
               VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (id) DO NOTHING""",
            (book_id, tenant, metadata["title"], metadata["author"], metadata["category"],
             metadata["shelf"], metadata["audience"]),
        )


def seed(base, tenant, conn=None):
    rows = request(base, tenant, "/v1/admin/books")
    existing = {row["title"]: row for row in rows}
    by_id = {row["id"]: row for row in rows}
    for title, author, category, shelf, audience, state, first, second in BOOKS[tenant]:
        metadata = {"title": title, "author": author, "category": category, "shelf": shelf, "audience": audience}
        text = sample_text(title, first, second)
        book_id = uuid5(NAMESPACE_URL, f"community-library/demo-books/v1/{tenant}/{title}").hex if conn else None
        own = by_id.get(book_id)
        version = None
        if own:
            if (own["revision"] != 1 or own["archived_at"] or own["active_version"] or state == "empty"
                    or any(own[key] != value for key, value in metadata.items())):
                print(f"{tenant} 保留已有图书 · {title}")
                continue
            if own["latest_id"]:
                checksum = hashlib.sha256(text.encode("utf-8")).hexdigest()
                with conn.transaction():
                    resume = conn.execute(
                        "SELECT id FROM library_uploads WHERE id=%s AND book_id=%s AND checksum=%s",
                        (own["latest_id"], book_id, checksum),
                    ).fetchone()
                if not resume:
                    print(f"{tenant} 保留已有图书版本 · {title}")
                    continue
                version = own["latest_id"]
        elif title in existing:
            print(f"{tenant} 跳过已有图书 · {title}")
            continue
        elif conn:
            register_seed_book(conn, book_id, tenant, metadata)
        else:
            book_id = request(base, tenant, "/v1/books", metadata)["book_id"]

        if state != "empty":
            version = version or upload(base, tenant, book_id, text)
            wait_until_ready(base, tenant, version)
            if state == "published":
                body = urlencode({"rights_basis": "本项目原创演示内容"}).encode()
                request(base, tenant, f"/v1/books/{book_id}/versions/{version}/publish", body,
                        content_type="application/x-www-form-urlencoded")
        print(f"{tenant} 已完成 · {title} · {state}")

def run(base, *, compose=False):
    marker = None
    if compose:
        if os.getenv("LIBRARY_SEED_DEMO_BOOKS", "1") != "1" or os.getenv("LIBRARY_DEMO_TOKENS", "1") != "1":
            print("按 Compose 配置跳过示例馆藏。")
            return
        marker = Path(os.environ["LIBRARY_FILE_ROOT"]) / ".demo-books-v1"
        if marker.exists():
            print("示例馆藏已初始化。")
            return
    if marker is None:
        for branch in BOOKS:
            seed(base, branch)
        return
    from psycopg import connect

    with connect(os.environ["DATABASE_URL"]) as conn:
        for branch in BOOKS:
            seed(base, branch, conn)
    marker.touch()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    address = parser.add_mutually_exclusive_group()
    address.add_argument("--port", type=int, default=8080)
    address.add_argument("--base-url")
    parser.add_argument("--compose", action="store_true", help="Apply the Compose demo settings")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("--port 必须在 1 到 65535 之间")
    run((args.base_url or f"http://127.0.0.1:{args.port}").rstrip("/"), compose=args.compose)
