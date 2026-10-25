"""Configuration shared by the pipeline and the evaluation.

Settings are read from environment variables first, then ``.streamlit/secrets.toml``:

    from pipeline.config import ROOT, setting, get_pg_connection, get_neo4j_driver
"""

from __future__ import annotations

import os
import sys
import urllib.parse
from pathlib import Path
from typing import Any

# ── Project root ────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent

# ── Data locations ──────────────────────────────────────────────────────────
DATA_DIR = ROOT / "data"                       # source CSV/XLSX files (not in git)
OUTPUT_DIR = DATA_DIR / "output"               # files the pipeline writes for review
ENTITIES_CSV = OUTPUT_DIR / "Extracted_Historical_Entities.csv"
ENTITIES_XLSX = OUTPUT_DIR / "Extracted_Historical_Entities.xlsx"
CIDOC_TTL = OUTPUT_DIR / "CIDOC_Timeline_Mappings.ttl"
GRAPH_REPORT_DIR = ROOT / "evaluation" / "results" / "graph"   # graph size/sparsity tables

# Identifier column of each record table in the March First Movement Database. Records are
# cited internally as "<table>-<rowid>"; these identifiers are the ones a reader can look up
# in the database, and rowids are assigned in their order so that a rebuild keeps them.
SOURCE_ID_COLUMNS = {
    "raw_event_info": "아이디",
    "raw_bibliography": "문서아이디",
    "raw_source_info": "출처정보아이디",
    "raw_detail_place": "세부장소아이디",
}

# ── TOML loader (Python 3.11+ or tomli fallback) ───────────────────────────
try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib  # type: ignore[no-redef]


def load_secrets(secret_file: str | Path | None = None) -> dict[str, Any]:
    """Read ``.streamlit/secrets.toml`` and return the parsed dict.

    Falls back to an empty dict if the file is missing or unreadable.
    """
    if secret_file is None:
        secret_file = ROOT / ".streamlit" / "secrets.toml"
    secret_path = Path(secret_file)
    if not secret_path.exists():
        return {}
    try:
        with secret_path.open("rb") as fh:
            values = tomllib.load(fh)
        return values if isinstance(values, dict) else {}
    except Exception as exc:
        print(f"⚠️ secrets.toml 읽기 실패 ({type(exc).__name__})", file=sys.stderr)
        return {}


# Module-level singleton so callers can ``from pipeline.config import SECRETS``.
SECRETS: dict[str, Any] = load_secrets()


def setting(key: str, default: str = "", secrets: dict[str, Any] | None = None) -> str:
    """Return *env-var → secrets.toml → default*, coerced to ``str``."""
    env_val = os.getenv(key)
    if env_val:
        return str(env_val)
    src = secrets if secrets is not None else SECRETS
    toml_val = src.get(key)
    if toml_val is not None and str(toml_val):
        return str(toml_val)
    return str(default) if default else ""


# ── PostgreSQL helpers ──────────────────────────────────────────────────────

def get_pg_config(secrets: dict[str, Any] | None = None) -> dict[str, str]:
    s = secrets if secrets is not None else SECRETS
    db = setting("PG_DATABASE", "postgres", s)
    return {
        "host": setting("PG_HOST", "localhost", s),
        "port": setting("PG_PORT", "5432", s),
        "database": db,
        "dbname": db,
        "user": setting("PG_USER", "postgres", s),
        "password": setting("PG_PASSWORD", "", s),
    }


def get_pg_engine(secrets: dict[str, Any] | None = None):
    """Return a SQLAlchemy engine for the configured PostgreSQL database."""
    from sqlalchemy import create_engine

    cfg = get_pg_config(secrets)
    encoded_pass = urllib.parse.quote_plus(cfg["password"])
    url = (
        f"postgresql://{cfg['user']}:{encoded_pass}"
        f"@{cfg['host']}:{cfg['port']}/{cfg['database']}"
    )
    return create_engine(url)


def get_pg_connection(secrets: dict[str, Any] | None = None):
    """Return a raw ``psycopg2`` connection."""
    import psycopg2

    cfg = get_pg_config(secrets)
    return psycopg2.connect(
        host=cfg["host"],
        port=int(cfg["port"]),
        user=cfg["user"],
        password=cfg["password"],
        dbname=cfg["database"],
    )


# ── Neo4j helpers ───────────────────────────────────────────────────────────

def get_neo4j_config(secrets: dict[str, Any] | None = None) -> dict[str, str]:
    s = secrets if secrets is not None else SECRETS
    return {
        "uri": setting("NEO4J_URI", "bolt://localhost:7687", s),
        "user": setting("NEO4J_USER", "neo4j", s),
        "password": setting("NEO4J_PASSWORD", "", s),
    }


def get_neo4j_driver(secrets: dict[str, Any] | None = None, **kwargs):
    """Return a Neo4j driver for the configured server."""
    from neo4j import GraphDatabase

    cfg = get_neo4j_config(secrets)
    return GraphDatabase.driver(
        cfg["uri"],
        auth=kwargs.pop("auth", (cfg["user"], cfg["password"])),
        **kwargs,
    )
