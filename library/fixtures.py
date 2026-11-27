import json
from typing import Annotated, Literal

from pydantic import BaseModel, Field, SecretStr, StringConstraints, ValidationError

from library.core import ROOT, digest

NonBlank = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Audience = Literal["reader", "staff"]
Phone = Annotated[str, Field(pattern=r"^1[3-9][0-9]{9}$")]


class DemoIdentity(BaseModel):
    token: SecretStr
    tenant_id: NonBlank
    user_id: NonBlank
    role: Audience
    phone: Phone | None = None


class DemoDocument(BaseModel):
    id: NonBlank
    tenant_id: NonBlank
    audience: Audience
    keywords: list[NonBlank]
    text: NonBlank


class DemoData(BaseModel):
    identities: list[DemoIdentity]
    documents: list[DemoDocument]


def sync_demo_data(conn):
    directory = ROOT / "fixtures"
    try:
        data = DemoData.model_validate_json((directory / "demo.json").read_bytes())
        phones = json.loads((directory / "demo-phones.json").read_text(encoding="utf-8"))
    except (OSError, ValidationError, ValueError):
        raise RuntimeError("Demo fixture could not be loaded or validated") from None

    tokens = [item.token.get_secret_value() for item in data.identities]
    users = {(item.tenant_id, item.user_id) for item in data.identities}
    if (any(not token.strip() for token in tokens) or len(set(tokens)) != len(tokens)
            or len(users) != len(tokens) or len({doc.id for doc in data.documents}) != len(data.documents)):
        raise RuntimeError("Demo fixture contains an empty token or duplicate identity/document")

    conn.execute("DELETE FROM library_demo_tokens")
    conn.execute("DELETE FROM library_demo_documents")
    with conn.cursor() as cursor:
        cursor.executemany(
            """INSERT INTO library_accounts (tenant_id,user_id,phone,role) VALUES (%s,%s,%s,%s)
               ON CONFLICT (tenant_id,user_id) DO NOTHING""",
            [(item.tenant_id, item.user_id, item.phone or phones.get(item.tenant_id, {}).get(item.user_id),
              item.role) for item in data.identities],
        )
        cursor.executemany(
            "INSERT INTO library_demo_tokens (digest,tenant_id,user_id) VALUES (%s,%s,%s)",
            [(digest(token), item.tenant_id, item.user_id) for item, token in zip(data.identities, tokens, strict=True)],
        )
        cursor.executemany(
            """INSERT INTO library_demo_documents (id,tenant_id,audience,keywords,body,ordinal)
               VALUES (%s,%s,%s,%s,%s,%s)""",
            [(doc.id, doc.tenant_id, doc.audience, [word.casefold() for word in doc.keywords], doc.text, i)
             for i, doc in enumerate(data.documents)],
        )

    conn.execute(
        """INSERT INTO library_community_posts
             (tenant_id,id,kind,title,body,state,published_at,keywords,sort_order,source_id)
           SELECT tenant_id,md5(tenant_id || ':' || id)::uuid,
                  CASE WHEN audience='reader' THEN 'guide' ELSE 'handbook' END,
                  COALESCE(NULLIF(left(split_part(body,'。',1),80),''),id),
                  body,'published',now(),keywords,ordinal,id
           FROM library_demo_documents
           ON CONFLICT (tenant_id,source_id) DO UPDATE SET kind=excluded.kind,title=excluded.title,
               body=excluded.body,keywords=excluded.keywords,sort_order=excluded.sort_order
           WHERE library_community_posts.version=1"""
    )
    conn.execute(
        """DELETE FROM library_community_posts p WHERE p.source_id IS NOT NULL AND p.version=1
           AND NOT EXISTS (SELECT 1 FROM library_demo_documents d
                           WHERE d.tenant_id=p.tenant_id AND d.id=p.source_id)"""
    )
