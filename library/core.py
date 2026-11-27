import hashlib
import json
import logging
import os
import re
from pathlib import Path

from psycopg.types.json import Jsonb

from library.dependencies import database

ROOT = Path(__file__).resolve().parent.parent
log = logging.getLogger("library")

def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def pool():
    return database(os.environ["DATABASE_URL"])


def event(name, **fields):
    log.info(json.dumps({"event": name, **fields}))


def fixture_evidence(conn, job):
    query = job["query"].strip().casefold()
    words = re.findall(r"[\w-]+", query)
    return conn.execute(
        """SELECT source_id AS id,body AS text FROM library_community_posts
           WHERE tenant_id=%s AND state='published' AND source_id IS NOT NULL
               AND (kind='guide' OR %s='staff') AND (keywords && %s::text[] OR lower(source_id)=%s)
           ORDER BY sort_order,id""",
        (job["tenant"], job["role"], words, query),
    ).fetchall()


def portal_evidence(conn, job):
    query = job["query"].strip().casefold()
    words = re.findall(r"[\w-]+", query)
    return conn.execute(
        """SELECT 'community-' || p.id::text AS id,p.title || '：' || p.body AS text
           FROM library_community_posts p WHERE p.tenant_id=%s AND p.state='published' AND p.source_id IS NULL
             AND (p.kind IN ('notice','activity','guide','faq') OR (p.kind='handbook' AND %s='staff'))
             AND (p.keywords && %s::text[]
                  OR EXISTS (SELECT 1 FROM unnest(p.keywords) k
                             WHERE octet_length(k)>char_length(k) AND strpos(%s,k)>0)
                  OR strpos(lower(p.title),%s)>0)
           ORDER BY p.sort_order,p.published_at DESC,p.id LIMIT 8""",
        (job["tenant"], job["role"], words, query, query),
    ).fetchall()


def finish(conn, job_id, *, result=None, error=None):
    conn.execute(
        """UPDATE library_jobs SET status=%s, result=%s, error_code=%s WHERE id=%s""",
        ("failed" if error else "succeeded", Jsonb(result) if result is not None else None,
         error, job_id),
    )
