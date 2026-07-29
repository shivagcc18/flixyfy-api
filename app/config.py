from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(REPO_ROOT / ".env", override=False)

AUTHORITATIVE_SERVING_DB = Path(r"C:\Users\USER\Desktop\DB\flixyfy_launch_serving_v3.db")
SUPERSEDED_SERVING_ROOT = Path(r"C:\Users\USER\Desktop\flixyfy-clean-stack-v2\data_factory\serving")
DEFAULT_CORS_ORIGINS = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:5173",
    "http://127.0.0.1:5173",
]
CRITICAL_SERVING_OBJECTS = [
    "movie_identity_serving_v3",
    "movie_search_document_v3",
    "movie_search_fts_v3",
    "provider_ott_availability_v3",
    "movie_youtube_availability_v3",
    "movie_genre_serving_v3",
    "movie_language_serving_v3",
    "movie_people_serving_v3",
    "search_entity_v3",
    "app_meta",
]
SERVING_DB_PUBLIC_ID = "external_serving_v3"
API_VERSION = "2.1.0-local"


def resolve_serving_db_path() -> Path:
    candidate = Path(os.getenv("FLIXYFY_SERVING_DB", str(AUTHORITATIVE_SERVING_DB)))
    resolved = candidate.resolve(strict=False)
    superseded = SUPERSEDED_SERVING_ROOT.resolve(strict=False)
    if resolved == superseded or superseded in resolved.parents:
        raise RuntimeError("Superseded project serving database is not allowed")
    return resolved


def _split_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def resolve_cors_origins() -> list[str]:
    values = [
        *DEFAULT_CORS_ORIGINS,
        *_split_csv(os.getenv("FLIXYFY_CORS_ORIGINS")),
        *_split_csv(os.getenv("FLIXYFY_PRODUCTION_CORS_ORIGINS")),
    ]
    return list(dict.fromkeys(values))


def resolve_query_timeout_ms() -> int:
    raw = os.getenv("FLIXYFY_SQLITE_QUERY_TIMEOUT_MS", os.getenv("FLIXYFY_QUERY_TIMEOUT_MS", "30000"))
    try:
        value = int(raw)
    except ValueError:
        value = 30000
    return max(250, min(value, 30000))


SERVING_DB_PATH = resolve_serving_db_path()
CORS_ORIGINS = resolve_cors_origins()
SQLITE_QUERY_TIMEOUT_MS = resolve_query_timeout_ms()
QUERY_TIMEOUT_MS = SQLITE_QUERY_TIMEOUT_MS
