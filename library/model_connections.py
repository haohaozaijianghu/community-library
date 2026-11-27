import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID

import httpx
from cryptography.fernet import Fernet
from fastapi import APIRouter, Depends, HTTPException, Request
from psycopg import errors
from pydantic import BaseModel, Field, SecretStr, StringConstraints

from library.dependencies import LibraryError
from library.model_client import PROVIDERS, Gateway, validate_url
from library.core import event

Label = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160)]
PUBLIC_COLUMNS = "id,provider,name,model_name,kind,dimension,verified_at,encrypted_key IS NOT NULL AS has_key"


@dataclass(frozen=True)
class GatewaySettings:
    model_timeout: float

    def cipher(self):
        configured = os.getenv("LIBRARY_MODEL_SECRET_KEY")
        if configured:
            return Fernet(configured.encode("ascii"))
        root = Path(os.environ["LIBRARY_FILE_ROOT"])
        root.mkdir(parents=True, exist_ok=True)
        target = root / ".model-encryption-key"
        try:
            return Fernet(target.read_bytes())
        except FileNotFoundError:
            pass
        with tempfile.NamedTemporaryFile(dir=root, prefix=".model-key-", delete=False) as temp:
            temp_path = Path(temp.name)
            try:
                temp.write(Fernet.generate_key())
                temp.flush()
                os.fsync(temp.fileno())
                try:
                    os.link(temp_path, target)
                except FileExistsError:
                    pass
            finally:
                temp_path.unlink(missing_ok=True)
        return Fernet(target.read_bytes())


class NewModel(BaseModel):
    provider: Label
    name: Label
    model_name: Label
    kind: Literal["chat", "embedding"] = "chat"
    api_key: SecretStr | None = Field(default=None, max_length=4096)


class Credentials(BaseModel):
    api_key: SecretStr = Field(max_length=4096)


class PickModel(BaseModel):
    model_name: Label
    kind: Literal["chat", "embedding"]


class ModelSelection(BaseModel):
    models: list[PickModel] = Field(min_length=1, max_length=30)


def provider_url(provider):
    spec = PROVIDERS.get(provider)
    value = spec.url if spec else os.getenv("LIBRARY_CUSTOM_MODEL_URL") if provider == "custom" else None
    if not value:
        raise HTTPException(400, "unknown_model_provider")
    try:
        return validate_url(value)
    except LibraryError as error:
        raise HTTPException(error.status, error.code) from error


def lock_provider(conn, tenant, provider):
    conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                 (f"library-model-provider:{tenant}:{provider}",))


def provider_credentials(conn, tenant, provider):
    return conn.execute(
        "SELECT encrypted_key,version FROM library_model_provider_credentials WHERE tenant=%s AND provider=%s",
        (tenant, provider),
    ).fetchone()


def builtin_choices(provider):
    spec = PROVIDERS.get(provider)
    if not spec:
        return {}
    return {(name, kind): "preset" for kind, names in (("chat", spec.text), ("embedding", spec.embedding)) for name in names}


def choices(conn, tenant, provider):
    options = builtin_choices(provider)
    for item in conn.execute(
        "SELECT model_name,kind FROM library_discovered_models WHERE tenant=%s AND provider=%s", (tenant, provider),
    ):
        options[(item["model_name"], item["kind"])] = "discovered"
    return options


def model_label(provider, model_name, default=None):
    if default and default != model_name:
        return default
    spec = PROVIDERS.get(provider)
    return spec.model_labels.get(model_name, model_name) if spec else model_name


def catalog():
    entries = [{"id": key, "name": spec.name, "models": spec.text, "embedding_models": spec.embedding, "requires_key": True}
               for key, spec in PROVIDERS.items()]
    if os.getenv("LIBRARY_CUSTOM_MODEL_URL"):
        entries.append({"id": "custom", "name": "OpenAI 兼容网关", "models": [], "embedding_models": [], "requires_key": False})
    return entries


def connection(conn, tenant, model_id, *, for_answer=False):
    return conn.execute(
        """SELECT m.*,COALESCE(m.encrypted_key,c.encrypted_key) AS encrypted_key,
                  CASE WHEN m.encrypted_key IS NULL THEN c.version END AS credential_version
           FROM library_model_connections m
           LEFT JOIN library_model_provider_credentials c ON c.tenant=m.tenant AND c.provider=m.provider
           WHERE m.tenant=%s AND m.id=%s AND (NOT %s OR (m.verified_at IS NOT NULL AND m.kind='chat'))""",
        (tenant, model_id, for_answer),
    ).fetchone()


def routes(identity):
    router = APIRouter(prefix="/v1/admin/models")

    def manager(user=Depends(identity)):
        if user["role"] != "staff":
            event("access_denied", reason="models_staff_required")
            raise HTTPException(403, "staff_required")
        return user

    @router.get("/providers")
    def providers(user=Depends(manager)):
        return catalog()

    @router.get("/providers/{provider}/catalog")
    def model_catalog(request: Request, provider: str, user=Depends(manager)):
        provider_url(provider)
        tenant = user["tenant_id"]
        with request.app.state.db.connection() as conn:
            options = choices(conn, tenant, provider)
            saved = conn.execute(
                "SELECT model_name,kind FROM library_model_connections WHERE tenant=%s AND provider=%s",
                (tenant, provider),
            ).fetchall()
            connected = bool(provider_credentials(conn, tenant, provider))
        added = {(row["model_name"], row["kind"]) for row in saved}
        return {"has_key": connected, "models": [
            {"model_name": name, "display_name": model_label(provider, name), "kind": kind,
             "source": source, "added": (name, kind) in added}
            for (name, kind), source in options.items()
        ]}

    @router.put("/providers/{provider}/credentials")
    def configure_provider(request: Request, provider: str, body: Credentials, user=Depends(manager)):
        provider_url(provider)
        secret = body.api_key.get_secret_value().strip()
        if not secret or any(not 33 <= ord(ch) <= 126 for ch in secret):
            raise HTTPException(400, "model_key_required")
        tenant = user["tenant_id"]
        encrypted = GatewaySettings(5).cipher().encrypt(secret.encode())
        with request.app.state.db.connection() as conn:
            lock_provider(conn, tenant, provider)
            conn.execute(
                """INSERT INTO library_model_provider_credentials (tenant,provider,encrypted_key) VALUES (%s,%s,%s)
                   ON CONFLICT (tenant,provider) DO UPDATE SET encrypted_key=excluded.encrypted_key,
                   version=library_model_provider_credentials.version+1""",
                (tenant, provider, encrypted),
            )
            conn.execute("DELETE FROM library_discovered_models WHERE tenant=%s AND provider=%s", (tenant, provider))
            conn.execute(
                """UPDATE library_model_connections SET verified_at=NULL,dimension=NULL
                   WHERE tenant=%s AND provider=%s AND encrypted_key IS NULL""", (tenant, provider),
            )
        event("model_provider_credentials_saved", tenant_id=tenant, provider=provider)
        return {"has_key": True}

    @router.post("/providers/{provider}/discover")
    def discover(request: Request, provider: str, user=Depends(manager)):
        url = provider_url(provider)
        tenant = user["tenant_id"]
        with request.app.state.db.connection() as conn:
            stored = provider_credentials(conn, tenant, provider)
        if provider != "custom" and not stored:
            raise HTTPException(400, "model_key_required")
        headers = {}
        if stored:
            secret = GatewaySettings(5).cipher().decrypt(stored["encrypted_key"]).decode()
            headers["Authorization"] = "Bearer " + secret
        try:
            with httpx.Client(timeout=5, trust_env=False, follow_redirects=False) as client:
                response = client.get(url + "/models", headers=headers)
                response.raise_for_status()
                items = response.json()["data"]
        except (httpx.HTTPError, ValueError, TypeError, KeyError) as error:
            raise HTTPException(502, "model_catalog_unavailable") from error
        if not isinstance(items, list):
            raise HTTPException(502, "model_catalog_unavailable")
        preset = builtin_choices(provider)
        discovered = set()
        for item in items[:3000]:
            name = item.get("id") if isinstance(item, dict) else None
            if not isinstance(name, str) or not name.isprintable() or not 1 <= len(name) <= 160:
                continue
            kinds = {kind for model, kind in preset if model == name}
            tags = item.get("type")
            if tags == "embedding" or re.search(r"embed|(?:^|[/_-])(?:bge|e5)(?:[/_.-]|$)", name, re.I):
                kinds.add("embedding")
            if tags in ("chat", "llm"):
                kinds.add("chat")
            if not kinds and tags is None:
                kinds.add("chat")
            discovered.update((name, kind) for kind in kinds)
        with request.app.state.db.connection() as conn:
            lock_provider(conn, tenant, provider)
            current = provider_credentials(conn, tenant, provider)
            if (current and current["version"]) != (stored and stored["version"]):
                raise HTTPException(409, "model_credentials_changed")
            conn.execute("DELETE FROM library_discovered_models WHERE tenant=%s AND provider=%s", (tenant, provider))
            with conn.cursor() as cursor:
                cursor.executemany(
                    "INSERT INTO library_discovered_models (tenant,provider,model_name,kind) VALUES (%s,%s,%s,%s)",
                    [(tenant, provider, name, kind) for name, kind in sorted(discovered)],
                )
        return {"count": len(discovered)}

    @router.post("/providers/{provider}/models", status_code=201)
    def add_from_catalog(request: Request, provider: str, body: ModelSelection, user=Depends(manager)):
        url = provider_url(provider)
        tenant = user["tenant_id"]
        with request.app.state.db.connection() as conn:
            lock_provider(conn, tenant, provider)
            stored = provider_credentials(conn, tenant, provider)
            if provider != "custom" and not stored:
                raise HTTPException(400, "model_key_required")
            known = choices(conn, tenant, provider)
            rows = []
            for model in body.models:
                key = model.model_name, model.kind
                if key not in known:
                    raise HTTPException(400, "model_not_in_catalog")
                same = conn.execute(
                    """SELECT 1 FROM library_model_connections WHERE tenant=%s AND provider=%s
                       AND model_name=%s AND kind=%s""", (tenant, provider, *key),
                ).fetchone()
                if same:
                    continue
                row = conn.execute(
                    f"""INSERT INTO library_model_connections (tenant,provider,name,model_name,kind,base_url)
                        VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (tenant,provider,name) DO NOTHING
                        RETURNING {PUBLIC_COLUMNS}""", (tenant, provider, model.model_name, *key, url),
                ).fetchone()
                if row:
                    row["has_key"] = bool(stored)
                    rows.append(row)
        return rows

    @router.get("")
    def list_models(request: Request, user=Depends(manager)):
        with request.app.state.db.connection() as conn:
            tenant = user["tenant_id"]
            rows = conn.execute(
                f"SELECT {PUBLIC_COLUMNS} FROM library_model_connections WHERE tenant=%s ORDER BY provider,name,id",
                (tenant,),
            ).fetchall()
            configured = {item["provider"] for item in conn.execute(
                "SELECT provider FROM library_model_provider_credentials WHERE tenant=%s", (tenant,),
            )}
            return [
                {**row, "has_key": row["has_key"] or row["provider"] in configured,
                 "display_name": model_label(row["provider"], row["model_name"], row["name"])}
                for row in rows
            ]

    @router.post("", status_code=201)
    def create(request: Request, body: NewModel, user=Depends(manager)):
        spec = PROVIDERS.get(body.provider)
        url = spec.url if spec else os.getenv("LIBRARY_CUSTOM_MODEL_URL") if body.provider == "custom" else None
        if not url:
            raise HTTPException(400, "unknown_model_provider")
        try:
            url = validate_url(url)
        except LibraryError as error:
            raise HTTPException(error.status, error.code) from error
        secret = body.api_key.get_secret_value().strip() if body.api_key else ""
        if spec and not secret:
            raise HTTPException(400, "model_key_required")
        if "\x00" in body.name or "\x00" in body.model_name or any(not 33 <= ord(ch) <= 126 for ch in secret):
            raise HTTPException(400, "invalid_request")
        encrypted = GatewaySettings(5).cipher().encrypt(secret.encode()) if secret else None
        try:
            with request.app.state.db.connection() as conn:
                saved = conn.execute(
                    f"""INSERT INTO library_model_connections (tenant,provider,name,model_name,kind,base_url,encrypted_key)
                        VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING {PUBLIC_COLUMNS}""",
                    (user["tenant_id"], body.provider, body.name, body.model_name, body.kind, url, encrypted),
                ).fetchone()
        except errors.UniqueViolation as error:
            raise HTTPException(409, "model_name_conflict") from error
        event("model_connection_created", tenant_id=user["tenant_id"], model_id=str(saved["id"]))
        return saved

    @router.post("/{model_id}/verify")
    def verify(request: Request, model_id: UUID, user=Depends(manager)):
        tenant = user["tenant_id"]
        with request.app.state.db.connection() as conn:
            row = connection(conn, tenant, model_id)
        if not row:
            raise HTTPException(404, "model_not_found")
        gateway = Gateway(GatewaySettings(5))
        try:
            dimension = gateway.probe(row)
        except LibraryError as error:
            event("model_verification_failed", tenant_id=tenant, model_id=str(model_id), code=error.code)
            raise HTTPException(502, error.code) from error
        finally:
            gateway.close()
        with request.app.state.db.connection() as conn:
            lock_provider(conn, tenant, row["provider"])
            verified = conn.execute(
                f"""UPDATE library_model_connections m SET verified_at=now(),dimension=%s
                    WHERE tenant=%s AND id=%s AND (
                       m.encrypted_key IS NOT NULL OR
                       (SELECT version FROM library_model_provider_credentials c
                        WHERE c.tenant=m.tenant AND c.provider=m.provider) IS NOT DISTINCT FROM %s::int
                    ) RETURNING {PUBLIC_COLUMNS}""", (dimension, tenant, model_id, row["credential_version"]),
            ).fetchone()
        if not verified:
            raise HTTPException(409, "model_credentials_changed")
        verified["has_key"] = verified["has_key"] or row["credential_version"] is not None
        return verified

    return router
