#!/usr/bin/env python3
"""Shared primitives for firm-contacts.

Connects (read-only) to the contact_intelligence Postgres DB, embeds search
queries with OpenAI (text-embedding-3-small @ 1024 dims — MUST match the
contacts.embedding column), and resolves secrets via GCP metadata. Mirrors
the quiet-data-lookup core, trimmed to what this skill needs.

Env it reads (env var first, then /run/quiet/sandbox.env, then ~/.openclaw):
  CONTACT_INTEL_DSN        read-only DSN to the contact_intelligence DB
  OPENAI_API_KEY           (or fetched from the openai-api-key secret)
  GCP_PROJECT              (or read from metadata)
"""
from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.request
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

try:
    import psycopg2
    import psycopg2.extras
except ImportError:  # pragma: no cover
    psycopg2 = None  # type: ignore

SANDBOX_ENV_PATH = Path("/run/quiet/sandbox.env")
WORKSPACE_ENV_PATH = Path("/home/quiet/.openclaw/sandbox.env")
EMBED_URL = "https://api.openai.com/v1/embeddings"
EMBED_MODEL = "text-embedding-3-small"
EMBED_DIM = 1024  # MUST equal contacts.embedding's vector(1024)
METADATA_TOKEN_URL = "http://169.254.169.254/computeMetadata/v1/instance/service-accounts/default/token"
METADATA_PROJECT_URL = "http://169.254.169.254/computeMetadata/v1/project/project-id"
SECRET_URL = "https://secretmanager.googleapis.com/v1/projects/{project}/secrets/{secret}/versions/latest:access"

_secret_cache: dict[str, str] = {}


# ── env + secrets ──────────────────────────────────────────────────────────
def _read_env_file(path: Path, key: str) -> str:
    try:
        text = path.read_text()
    except OSError:
        return ""
    for line in text.splitlines():
        if line.startswith(f"{key}="):
            v = line[len(key) + 1:].strip()
            if len(v) >= 2 and v[0] == v[-1] in ("'", '"'):
                return v[1:-1]
            return v
    return ""


def env_value(key: str) -> str:
    return (os.environ.get(key, "").strip()
            or _read_env_file(SANDBOX_ENV_PATH, key)
            or _read_env_file(WORKSPACE_ENV_PATH, key))


def _metadata(url: str) -> str:
    req = urllib.request.Request(url, headers={"Metadata-Flavor": "Google"})
    with urllib.request.urlopen(req, timeout=3) as r:
        return r.read().decode("utf-8")


def _project() -> str:
    return env_value("GCP_PROJECT") or _metadata(METADATA_PROJECT_URL).strip()


def secret_value(name: str) -> str:
    if name in _secret_cache:
        return _secret_cache[name]
    token = json.loads(_metadata(METADATA_TOKEN_URL))["access_token"]
    url = SECRET_URL.format(project=_project(), secret=name)
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=5) as r:
        payload = json.loads(r.read().decode("utf-8"))
    val = base64.b64decode(payload["payload"]["data"]).decode("utf-8").strip()
    _secret_cache[name] = val
    return val


def dsn() -> str:
    d = env_value("CONTACT_INTEL_DSN")
    if d:
        return d
    return secret_value(env_value("CONTACT_INTEL_DSN_SECRET") or "contact-intelligence-readonly-dsn")


# ── serialization ────────────────────────────────────────────────────────--
def serialize(v: Any) -> Any:
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, list):
        return [serialize(x) for x in v]
    if isinstance(v, dict):
        return {k: serialize(x) for k, x in v.items()}
    return v


def vector_literal(values: list[float]) -> str:
    return "[" + ",".join(f"{float(v):.8g}" for v in values) + "]"


def like_wrap(value: str) -> str:
    """ILIKE wildcard wrap, with %/_ escaped so they're literal."""
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


# ── embedding (1024-d, matches the stored column) ───────────────────────────
def embed_query(query: str) -> tuple[list[float] | None, str | None]:
    api_key = env_value("OPENAI_API_KEY")
    if not api_key:
        try:
            api_key = secret_value(env_value("OPENAI_API_KEY_SECRET") or "openai-api-key")
        except Exception as exc:  # noqa: BLE001
            return None, f"OPENAI_API_KEY unavailable ({type(exc).__name__}); semantic search skipped"
    body = json.dumps({"model": EMBED_MODEL, "input": query, "dimensions": EMBED_DIM}).encode()
    req = urllib.request.Request(EMBED_URL, data=body, method="POST",
                                 headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            payload = json.loads(r.read().decode("utf-8"))
        vec = payload["data"][0]["embedding"]
    except (OSError, urllib.error.URLError, TimeoutError, KeyError, IndexError, json.JSONDecodeError) as exc:
        return None, f"embedding request failed: {type(exc).__name__}"
    if not isinstance(vec, list) or len(vec) != EMBED_DIM:
        return None, "embedding response malformed"
    return [float(v) for v in vec], None


# ── DB (read-only; SET app.role='service' so RLS exposes observer rows) ──────
def fetch(sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    if psycopg2 is None:
        raise RuntimeError("psycopg2 not installed")
    with psycopg2.connect(dsn()) as conn:
        conn.set_session(readonly=True, autocommit=True)
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            # contact_observers / raw_snapshots / audit are RLS-gated to the
            # 'service' role; without this they return zero rows.
            cur.execute("SET app.role = 'service'")
            cur.execute(sql, params)
            rows = cur.fetchall()
    return [serialize(dict(r)) for r in rows]
