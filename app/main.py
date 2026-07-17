from __future__ import annotations
import os
import json
import re
import time
from contextlib import asynccontextmanager
from typing import Any, Literal
from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from app.provider_safe_display_policy_v1 import (
    POLICY_VERSION as PROVIDER_DISPLAY_POLICY_VERSION,
    apply_provider_safe_display_policy,
    apply_provider_safe_display_policy_to_item,
)

DATABASE_URL = os.environ["DATABASE_URL"]
MOVIE_TABLES = {
    "current": "current_movie_api_v1",
    "historical": "historical_movie_serving_v5",
    "hollywood": "hollywood_movie_serving_v5",
    "webseries": "webseries_active_launch_serving_v5",
}
SEARCH_TABLES = {
    "current": "current_search_serving_v5",
    "historical": "historical_search_serving_v5",
    "hollywood": "hollywood_search_serving_v5",
    "webseries": "webseries_search_serving_v5",
}
PERSON_TABLES = {
    "current": "current_person_serving_v5",
    "historical": "historical_person_serving_v5",
    "hollywood": "hollywood_person_serving_v5",
    "webseries": "webseries_person_serving_v5",
}
DOMAIN_VALUES = {
    "current": ("current", "indian", "movie", "movies"),
    "historical": ("historical",),
    "hollywood": ("hollywood",),
    "webseries": ("webseries", "series"),
}
# FLIXYFY_FRESH_API_POOL_STALE_CONNECTION_RESILIENCE_V1
# Validate an idle pooled connection before handing it to an API request.
pool = ConnectionPool(
    conninfo=DATABASE_URL,
    min_size=1,
    max_size=10,
    timeout=15,
    max_lifetime=300,
    check=ConnectionPool.check_connection,
    kwargs={"row_factory": dict_row},
    open=False,
)

def qi(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'

def all_rows(sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    with pool.connection() as conn:
        return list(conn.execute(sql, params).fetchall())

def one_row(sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
    with pool.connection() as conn:
        return conn.execute(sql, params).fetchone()

@asynccontextmanager
async def lifespan(app: FastAPI):
    pool.open()
    pool.wait()
    try:
        yield
    finally:
        pool.close()

app = FastAPI(title="FLIXYFY Fresh Lean API", version="1.0.0", lifespan=lifespan)

DEFAULT_CORS_ORIGINS = (
    "https://www.flixyfy.com",
    "https://flixyfy.com",
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://127.0.0.1:18081",
    "http://localhost:18081",
)


def _configured_cors_origins() -> list[str]:
    configured = [
        x.strip()
        for x in os.getenv("CORS_ORIGINS", ",".join(DEFAULT_CORS_ORIGINS)).split(",")
        if x.strip()
    ]
    return sorted(set(configured).union(DEFAULT_CORS_ORIGINS))


CORS_ORIGINS = _configured_cors_origins()

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

GET_CACHE_TTL_SECONDS = int(os.getenv("FLIXYFY_GET_CACHE_TTL_SECONDS", "45"))
_GET_CACHE: dict[str, tuple[float, int, dict[str, str], bytes]] = {}


def _cacheable_get_path(path: str) -> bool:
    return path.startswith("/api/v4/") and not any(
        blocked in path.lower() for blocked in ("/admin", "/login", "/auth")
    )


def _cors_headers_for_request(request: Request) -> dict[str, str]:
    origin = request.headers.get("origin")
    if origin in CORS_ORIGINS:
        return {"access-control-allow-origin": origin, "vary": "Origin"}
    return {}


@app.middleware("http")
async def _flixyfy_v4_get_cache(request: Request, call_next):
    if (
        request.method != "GET"
        or not _cacheable_get_path(request.url.path)
        or GET_CACHE_TTL_SECONDS <= 0
    ):
        return await call_next(request)

    key = f"{request.url.path}?{request.url.query}"
    now = time.monotonic()
    cached = _GET_CACHE.get(key)
    if cached and cached[0] > now:
        _, status_code, headers, body = cached
        merged_headers = dict(headers)
        merged_headers.update(_cors_headers_for_request(request))
        return Response(content=body, status_code=status_code, headers=merged_headers)

    response = await call_next(request)
    body = b""
    async for chunk in response.body_iterator:
        body += chunk

    headers = dict(response.headers)
    if 200 <= response.status_code < 300:
        headers.pop("access-control-allow-origin", None)
        headers.pop("access-control-allow-credentials", None)
        _GET_CACHE[key] = (now + GET_CACHE_TTL_SECONDS, response.status_code, headers, body)

    response_headers = dict(headers)
    response_headers.update(_cors_headers_for_request(request))
    return Response(content=body, status_code=response.status_code, headers=response_headers)

@app.get("/api/v1/health")
def health():
    row = one_row("SELECT current_database() AS database_name, now() AS checked_at")
    return {"status": "ok", "database_connected": True, **(row or {})}

@app.get("/api/v1/content")
def content(
    domain: Literal["current", "historical", "hollywood", "webseries"] = "current",
    provider: str | None = None,
    language: str | None = None,
    year: int | None = None,
    limit: int = Query(24, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    table = MOVIE_TABLES[domain]
    where = []
    params: list[Any] = []
    if language:
        where.append('"language_slug"=%s')
        params.append(language)
    if year:
        where.append('"release_year"=%s')
        params.append(year)
    if provider:
        if domain == "current" and provider.lower() == "youtube":
            where.append('COALESCE("has_youtube",false)=true')
        else:
            domains = DOMAIN_VALUES[domain]
            placeholders = ",".join(["%s"] * len(domains))
            where.append(
                'EXISTS (SELECT 1 FROM public."provider_availability_serving_v5" a '
                f'WHERE a."content_slug"={qi(table)}."slug" '
                f'AND LOWER(COALESCE(a."domain",\'\')) IN ({placeholders}) '
                'AND LOWER(COALESCE(a."provider_key",\'\'))=%s)'
            )
            params.extend([x.lower() for x in domains])
            params.append(provider.lower())
    clause = " WHERE " + " AND ".join(where) if where else ""
    total = one_row(
        f"SELECT COUNT(*)::bigint AS total FROM public.{qi(table)}{clause}",
        tuple(params),
    )
    items = all_rows(
        f"SELECT * FROM public.{qi(table)}{clause} "
        'ORDER BY "popularity" DESC NULLS LAST, "release_year" DESC NULLS LAST '
        "LIMIT %s OFFSET %s",
        tuple(params + [limit, offset]),
    )
    return {
        "domain": domain,
        "provider": provider,
        "total": int(total["total"]) if total else 0,
        "items": items,
    }

@app.get("/api/v1/content/{domain}/{slug}")
def detail(
    domain: Literal["current", "historical", "hollywood", "webseries"],
    slug: str,
):
    table = MOVIE_TABLES[domain]
    item = one_row(
        f'SELECT * FROM public.{qi(table)} WHERE "slug"=%s LIMIT 1',
        (slug,),
    )
    if not item:
        raise HTTPException(404, "Content not found")
    domains = DOMAIN_VALUES[domain]
    placeholders = ",".join(["%s"] * len(domains))
    availability = all_rows(
        'SELECT * FROM public."provider_availability_serving_v5" '
        f'WHERE LOWER(COALESCE("domain",\'\')) IN ({placeholders}) '
        'AND "content_slug"=%s ORDER BY "source_priority", "provider_key"',
        tuple([x.lower() for x in domains] + [slug]),
    )
    return {"domain": domain, "item": item, "availability": availability}

@app.get("/api/v1/search")
def search(
    q: str = Query(..., min_length=1, max_length=120),
    domain: Literal["all", "current", "historical", "hollywood", "webseries"] = "all",
    limit: int = Query(30, ge=1, le=100),
):
    domains = list(SEARCH_TABLES) if domain == "all" else [domain]
    results = []
    for key in domains:
        table = SEARCH_TABLES[key]
        results.extend(
            all_rows(
                f'SELECT *, %s::text AS "_domain" FROM public.{qi(table)} '
                'WHERE COALESCE("search_text",\'\') ILIKE %s '
                'OR COALESCE("title",\'\') ILIKE %s '
                'ORDER BY "rank_score" DESC NULLS LAST LIMIT %s',
                (key, f"%{q}%", f"%{q}%", max(limit, 5)),
            )
        )
    results.sort(key=lambda x: float(x.get("rank_score") or 0), reverse=True)
    return {"query": q, "domain": domain, "total": len(results[:limit]), "items": results[:limit]}

@app.get("/api/v1/person/{domain}/{person_slug}")
def person(
    domain: Literal["current", "historical", "hollywood", "webseries"],
    person_slug: str,
):
    table = PERSON_TABLES[domain]
    row = one_row(
        f'SELECT * FROM public.{qi(table)} WHERE "person_slug"=%s LIMIT 1',
        (person_slug,),
    )
    if not row:
        raise HTTPException(404, "Person not found")
    return {"domain": domain, "person": row}

@app.get("/api/v1/providers")
def providers(
    domain: Literal["current", "historical", "hollywood", "webseries"] = "current",
):
    domains = DOMAIN_VALUES[domain]
    placeholders = ",".join(["%s"] * len(domains))
    items = all_rows(
        'SELECT LOWER("provider_key") AS provider_key, '
        'MIN("provider_name") AS provider_name, COUNT(*)::bigint AS row_count, '
        'COUNT(DISTINCT "content_slug")::bigint AS content_count '
        'FROM public."provider_availability_serving_v5" '
        f'WHERE LOWER(COALESCE("domain",\'\')) IN ({placeholders}) '
        'GROUP BY LOWER("provider_key") ORDER BY content_count DESC, provider_key',
        tuple(x.lower() for x in domains),
    )
    return {"domain": domain, "items": items}

# FLIXYFY_FRESH_API_V4_BRIDGE_FOR_REAL_FRONTEND_V1
# Fresh-stack V4 bridge for the copied real frontend.
# Keeps /api/v1 intact. Adds /api/v4 endpoints so the fresh UI does not depend on legacy /api/v3 route names.

def _v4_norm_domain(value: str | None) -> str:
    v = (value or "current").strip().lower()
    aliases = {
        "movie": "current",
        "movies": "current",
        "indian": "current",
        "current_movies": "current",
        "historical_movies": "historical",
        "global": "hollywood",
        "global_movies": "hollywood",
        "series": "webseries",
        "web_series": "webseries",
    }
    if v in {"current", "historical", "hollywood", "webseries"}:
        return v
    return aliases.get(v, "current")

def _v4_norm_provider(value: str | None) -> str | None:
    v = (value or "").strip().lower().replace("-", "_")
    aliases = {
        "": "",
        "all": "",
        "all_provider": "",
        "all_providers": "",
        "all provider": "",
        "all providers": "",
        "prime": "prime_video",
        "prime video": "prime_video",
        "primevideo": "prime_video",
        "amazon prime": "prime_video",
        "amazon prime video": "prime_video",
        "youtube": "youtube",
        "netflix": "netflix",
    }
    return aliases.get(v, v.replace(" ", "_")) or None

def _v4_limit(limit: int | None, default: int = 24, cap: int = 100) -> int:
    try:
        value = int(limit or default)
    except Exception:
        value = default
    if value < 1:
        value = default
    return min(value, cap)

def _v4_offset(page: int | None, limit: int | None) -> int:
    p = int(page or 1)
    l = _v4_limit(limit)
    if p < 1:
        p = 1
    return (p - 1) * l

def _v4_qi(name: str) -> str:
    q = globals().get("qi")
    if callable(q):
        return q(name)
    return '"' + str(name).replace('"', '""') + '"'

def _v4_rows(sql: str, params: list | tuple = ()) -> list[dict]:
    many = globals().get("many_rows")
    if callable(many):
        return list(many(sql, list(params)) or [])
    pool_obj = globals().get("pool")
    if pool_obj is None:
        raise RuntimeError("database pool is unavailable")
    with pool_obj.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, list(params))
            data = cur.fetchall()
            return [dict(x) for x in (data or [])]

def _v4_one(sql: str, params: list | tuple = ()) -> dict | None:
    one = globals().get("one_row")
    if callable(one):
        row = one(sql, list(params))
        return dict(row) if row else None
    rows = _v4_rows(sql, params)
    return rows[0] if rows else None

def _v4_movie_table(domain: str) -> str:
    domain = _v4_norm_domain(domain)
    mapping = globals().get("MOVIE_TABLES") or {}
    if isinstance(mapping, dict) and domain in mapping:
        return mapping[domain]
    return {
        "current": "current_movie_serving_v5",
        "historical": "historical_movie_serving_v5",
        "hollywood": "hollywood_movie_serving_v5",
        "webseries": "webseries_active_launch_serving_v5",
    }[domain]

def _v4_person_table(domain: str) -> str:
    domain = _v4_norm_domain(domain)
    mapping = globals().get("PERSON_TABLES") or {}
    if isinstance(mapping, dict) and domain in mapping:
        return mapping[domain]
    return {
        "current": "current_person_serving_v5",
        "historical": "historical_person_serving_v5",
        "hollywood": "hollywood_person_serving_v5",
        "webseries": "webseries_person_serving_v5",
    }[domain]

def _v4_domain_values(domain: str) -> list[str]:
    domain = _v4_norm_domain(domain)
    mapping = globals().get("DOMAIN_VALUES") or {}
    if isinstance(mapping, dict) and domain in mapping:
        return list(mapping[domain])
    return {
        "current": ["current", "movie", "movies", "indian"],
        "historical": ["historical"],
        "hollywood": ["hollywood", "global"],
        "webseries": ["webseries", "series"],
    }[domain]

def _v4_items_payload(raw, page: int = 1, limit: int = 24, domain: str | None = None) -> dict:
    out = dict(raw or {}) if isinstance(raw, dict) else {"items": list(raw or [])}
    items = list(out.get("items") or out.get("results") or out.get("movies") or out.get("data") or [])
    out["items"] = items
    out.setdefault("results", items)
    out.setdefault("movies", items)
    out.setdefault("data", items)
    out.setdefault("total", len(items))
    out["page"] = int(page or 1)
    out["limit"] = int(limit or len(items) or 24)
    if domain is not None:
        out.setdefault("domain", domain)
    return out

CARD_FIELDS = (
    "id", "tmdb_id", "imdb_id", "domain", "source_domain", "slug", "movie_slug",
    "title", "name", "original_title", "release_year", "year", "first_air_year",
    "poster_url", "poster_path", "poster", "image_url", "image", "thumbnail",
    "backdrop_url", "backdrop_path", "primary_language", "language", "language_slug",
    "language_name", "primary_language_slug", "primary_language_name", "rating",
    "vote_average", "popularity", "quality_score", "ott_primary", "ott_primary_key",
    "provider", "provider_key", "provider_name", "has_youtube", "has_ott",
    "provider_trust_label", "provider_display_action", "provider_display_rank",
    "provider_is_public_safe", "provider_public_label", "provider_display_reason",
    "provider_display_primary_key", "provider_display_primary_name",
    "provider_display_primary_label", "provider_public_count", "provider_hidden_count",
    "provider_policy_version", "is_free", "type", "content_type", "entity_type",
)

PERSON_CARD_FIELDS = (
    "id", "person_slug", "slug", "person_name", "display_name", "name", "title",
    "primary_language_slug", "primary_language_name", "language_slug", "language_name",
    "primary_role", "known_for_department", "total_movie_count", "movie_count",
    "primary_language_movie_count", "career_attached_movie_count", "youtube_movie_count",
    "poster_url", "profile_url", "image_url", "image", "thumbnail",
)

_V4_TABLE_COLUMN_CACHE = {}

def _v4_table_columns(table: str) -> set[str]:
    cached = _V4_TABLE_COLUMN_CACHE.get(table)
    if cached is not None:
        return cached
    try:
        rows = _v4_rows(
            "SELECT column_name FROM information_schema.columns WHERE table_schema=%s AND table_name=%s",
            ["public", table],
        )
        columns = {str(row.get("column_name") or "") for row in rows}
    except Exception:
        columns = set()
    _V4_TABLE_COLUMN_CACHE[table] = columns
    return columns

def _v4_select_columns_sql(table: str, fields: tuple[str, ...]) -> str:
    columns = _v4_table_columns(table)
    selected = [field for field in fields if field in columns]
    if not selected:
        return "*"
    table_ref = _v4_qi(table)
    return ", ".join(f"{table_ref}.{_v4_qi(field)}" for field in selected)

def _v4_card_select_sql(table: str) -> str:
    return _v4_select_columns_sql(table, CARD_FIELDS)

def _v4_person_select_sql(table: str) -> str:
    return _v4_select_columns_sql(table, PERSON_CARD_FIELDS)


def _v4_slim_card_item(item: dict) -> dict:
    if not isinstance(item, dict):
        return item
    slim = {key: item.get(key) for key in CARD_FIELDS if key in item and item.get(key) is not None}
    if "domain" not in slim:
        source_domain = item.get("source_domain") or item.get("_domain")
        if source_domain:
            slim["domain"] = source_domain
    return slim or item


def _v4_slim_card_payload(payload: dict) -> dict:
    out = dict(payload or {})
    items = [_v4_slim_card_item(item) for item in list(out.get("items") or [])]
    out["items"] = items
    out["results"] = items
    out["movies"] = items
    out["data"] = items
    return out

# FLIXYFY_SINGLE_TITLE_PROVIDER_CORRECTION_THAI_KIZHAVI_JIOHOTSTAR_V1
# API-only trust correction. This remains intentionally outside provider_v5 and
# does not mutate Neon unless a separately approved data migration is added.
_V4_PROVIDER_CORRECTION_OVERLAYS = {
    "thaai-kizhavi-2026": {
        "title": "Thaai Kizhavi",
        "provider_key": "jiohotstar",
        "provider_name": "JioHotstar",
        "provider_display_name": "JioHotstar",
        "provider_type": "flatrate",
        "monetization_type": "subscription",
        "availability_type": "ott",
        "availability_status": "available",
        "homepage_url": "https://www.hotstar.com/in/",
        "search_url": "https://www.hotstar.com/in/search?q=Thaai%20Kizhavi",
        "source": "provider_correction_overlay_v1",
        "confidence": "HIGH_USER_REPORTED_PUBLIC_SOURCE_CONFIRMED",
    }
}


def _v4_provider_correction(slug: str | None) -> dict | None:
    return _V4_PROVIDER_CORRECTION_OVERLAYS.get(str(slug or "").strip().lower())


def _v4_provider_key(row: dict) -> str:
    return str(
        row.get("provider_key")
        or row.get("provider_name")
        or row.get("provider_display_name")
        or ""
    ).strip().lower().replace(" ", "_")


def _v4_overlay_provider_row(slug: str, correction: dict) -> dict:
    homepage_url = correction["homepage_url"]
    return {
        "domain": "current",
        "content_slug": slug,
        "content_type": "movie",
        "title": correction["title"],
        "provider_key": correction["provider_key"],
        "provider_name": correction["provider_name"],
        "provider_display_name": correction["provider_display_name"],
        "provider_type": correction["provider_type"],
        "monetization_type": correction["monetization_type"],
        "availability_type": correction["availability_type"],
        "availability_status": correction["availability_status"],
        "final_url": homepage_url,
        "deep_link": homepage_url,
        "search_url": correction["search_url"],
        "homepage_url": homepage_url,
        "is_youtube": False,
        "is_ott": True,
        "source": correction["source"],
        "confidence": correction["confidence"],
        "overlay_status": "primary",
    }


def _v4_apply_provider_correction_rows(slug: str, rows: list[dict]) -> list[dict]:
    correction = _v4_provider_correction(slug)
    if not correction:
        return rows

    normalized = [dict(row) for row in rows if isinstance(row, dict)]
    existing = []
    for row in normalized:
        if _v4_provider_key(row) == correction["provider_key"]:
            continue
        row["overlay_status"] = "demoted"
        row["overlay_reason"] = correction["source"]
        existing.append(row)
    return [_v4_overlay_provider_row(slug, correction)] + existing


def _v4_apply_provider_correction_card(item: dict) -> dict:
    if not isinstance(item, dict):
        return item
    correction = _v4_provider_correction(item.get("slug") or item.get("movie_slug"))
    if not correction:
        return item
    out = dict(item)
    out.update(
        {
            "provider": correction["provider_name"],
            "provider_key": correction["provider_key"],
            "provider_name": correction["provider_name"],
            "provider_display_name": correction["provider_display_name"],
            "ott_primary": correction["provider_name"],
            "ott_primary_key": correction["provider_key"],
            "has_ott": True,
            "has_subscription_ott": True,
            "availability_status": correction["availability_status"],
            "provider_correction_source": correction["source"],
            "provider_correction_confidence": correction["confidence"],
        }
    )
    return out


def _v4_apply_provider_correction_search_item(item: dict) -> dict:
    if not isinstance(item, dict):
        return item
    slug = item.get("slug") or item.get("movie_slug")
    correction = _v4_provider_correction(slug)
    if not correction:
        return item

    out = _v4_apply_provider_correction_card(item)
    overlay_row = _v4_overlay_provider_row(str(slug), correction)
    for field in ("provider_summary", "availability_json", "ott_all"):
        raw = out.get(field)
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except Exception:
                raw = []
        if not isinstance(raw, list):
            raw = []
        if field == "provider_summary":
            summary_row = {
                "provider_key": overlay_row["provider_key"],
                "provider_name": overlay_row["provider_name"],
                "provider_display_name": overlay_row["provider_display_name"],
                "provider_type": overlay_row["provider_type"],
                "monetization_type": overlay_row["monetization_type"],
                "availability_type": overlay_row["availability_type"],
                "availability_status": overlay_row["availability_status"],
                "ott_count": 1,
            }
            raw = [summary_row] + [
                x for x in raw
                if isinstance(x, dict) and _v4_provider_key(x) != correction["provider_key"]
            ]
        else:
            raw = _v4_apply_provider_correction_rows(str(slug), raw)
        out[field] = json.dumps(raw, ensure_ascii=False)
    provider_rows = json.loads(out.get("availability_json") or "[]")
    provider_row_count = len(provider_rows) if isinstance(provider_rows, list) else 1
    out["provider_count"] = max(int(out.get("provider_count") or 0), provider_row_count, 1)
    out["availability_count"] = max(int(out.get("availability_count") or 0), provider_row_count, 1)
    return out


def _v4_content_payload(domain: str = "current", page: int = 1, limit: int = 24, provider: str | None = None, language: str | None = None, year: int | None = None, sort: str | None = None) -> dict:
    domain = _v4_norm_domain(domain)
    provider = _v4_norm_provider(provider)
    limit = _v4_limit(limit)
    offset = _v4_offset(page, limit)


    table = _v4_movie_table(domain)
    where = []
    params = []

    if language:
        where.append('LOWER(COALESCE("language_slug", \'\')) = %s')
        params.append(str(language).lower())

    if year:
        where.append('"release_year" = %s')
        params.append(int(year))

    if provider:
        domains = _v4_domain_values(domain)
        placeholders = ",".join(["%s"] * len(domains))
        overlay_slugs = [
            slug
            for slug, correction in _V4_PROVIDER_CORRECTION_OVERLAYS.items()
            if domain == "current" and correction["provider_key"] == provider
        ]
        overlay_clause = ""
        if overlay_slugs:
            overlay_clause = f' OR {_v4_qi(table)}."slug" IN ({",".join(["%s"] * len(overlay_slugs))})'
        where.append(
            '(EXISTS (SELECT 1 FROM public."provider_availability_serving_v5" a '
            f'WHERE a."content_slug"={_v4_qi(table)}."slug" '
            f'AND LOWER(COALESCE(a."domain", \'\')) IN ({placeholders}) '
            'AND LOWER(COALESCE(a."provider_key", \'\'))=%s)' + overlay_clause + ')'
        )
        params.extend([x.lower() for x in domains])
        params.append(str(provider).lower())
        params.extend(overlay_slugs)

    clause = " WHERE " + " AND ".join(where) if where else ""
    total = _v4_one(f'SELECT COUNT(*) AS total FROM public.{_v4_qi(table)}{clause}', params) or {"total": 0}
    rows = _v4_rows(
        f'SELECT {_v4_card_select_sql(table)} FROM public.{_v4_qi(table)}{clause} '
        'ORDER BY COALESCE("release_year", 0) DESC, COALESCE("popularity", 0) DESC, COALESCE("title", \'\') '
        'LIMIT %s OFFSET %s',
        params + [limit, offset],
    )
    rows = [
        apply_provider_safe_display_policy_to_item(_v4_apply_provider_correction_card(row))
        for row in rows
    ]
    return _v4_slim_card_payload(_v4_items_payload({"total": int(total.get("total") or 0), "items": rows}, page=page, limit=limit, domain=domain))

def _v4_search_payload(q: str | None = None, page: int = 1, limit: int = 24, domain: str | None = None, type: str | None = None, region: str | None = None, provider: str | None = None, language: str | None = None, year: int | None = None, sort: str | None = None) -> dict:
    q = (q or "").strip()
    limit = _v4_limit(limit)
    provider = _v4_norm_provider(provider)

    search_fn = globals().get("search")
    if callable(search_fn) and q:
        mapped_domain = "all"
        if domain:
            mapped_domain = _v4_norm_domain(domain)
        elif region and str(region).lower() == "global":
            mapped_domain = "hollywood"

        raw = search_fn(q=q, domain=mapped_domain, limit=limit)
        out = _v4_items_payload(raw, page=page, limit=limit, domain=mapped_domain)
        out["items"] = [
            apply_provider_safe_display_policy_to_item(
                _v4_apply_provider_correction_search_item(item)
            )
            for item in list(out.get("items") or [])
        ]
        out["results"] = out["items"]
        out["movies"] = out["items"]
        out["data"] = out["items"]
        canonical = _v4_people_canonical_for_query_v2(q)
        if canonical and page == 1:
            try:
                person_payload = _v4_people_payload("historical", page=1, limit=3, q=q, tier="all")
                people_items = []
                for person in list(person_payload.get("items") or []):
                    person_item = dict(person)
                    person_item["entity_type"] = "person"
                    person_item["type"] = "person"
                    people_items.append(person_item)
                if people_items:
                    existing_keys = {_v4_people_merge_key_v2(item) for item in people_items}
                    movie_items = [item for item in list(out.get("items") or []) if _v4_people_merge_key_v2(item) not in existing_keys]
                    out["items"] = (people_items + movie_items)[:limit]
                    out["results"] = out["items"]
                    out["movies"] = out["items"]
                    out["data"] = out["items"]
                    out["total"] = max(int(out.get("total") or 0), len(out["items"]))
            except Exception:
                pass
        out["query"] = q
        out["type"] = type or "all"
        out["region"] = region or "all"
        return out

    desired = (type or "").strip().lower()
    if desired in {"webseries", "series"}:
        return _v4_content_payload("webseries", page=page, limit=limit, provider=provider, language=language, year=year, sort=sort)
    if region and str(region).lower() == "global":
        return _v4_content_payload("hollywood", page=page, limit=limit, provider=provider, language=language, year=year, sort=sort)
    return _v4_content_payload(domain or "current", page=page, limit=limit, provider=provider, language=language, year=year, sort=sort)

def _v4_provider_rows(slug: str, domain: str) -> list[dict]:
    domains = _v4_domain_values(domain)
    placeholders = ",".join(["%s"] * len(domains))
    try:
        return _v4_rows(
            f'SELECT * FROM public."provider_availability_serving_v5" '
            f'WHERE "content_slug"=%s AND LOWER(COALESCE("domain", \'\')) IN ({placeholders}) '
            f'ORDER BY COALESCE("provider_key", \'\'), COALESCE("provider_name", \'\') '
            f'LIMIT 100',
            [slug] + [x.lower() for x in domains],
        )
    except Exception:
        return []

def _v4_detail_payload(domain: str, slug: str) -> dict:
    domain = _v4_norm_domain(domain)
    table = _v4_movie_table(domain)
    row = _v4_one(f'SELECT * FROM public.{_v4_qi(table)} WHERE "slug"=%s LIMIT 1', [slug])
    if not row:
        raise HTTPException(status_code=404, detail="Not Found")
    availability = _v4_provider_rows(slug, domain)
    availability = _v4_apply_provider_correction_rows(slug, availability)
    availability, hidden_provider_rows = apply_provider_safe_display_policy(availability)
    row["availability"] = availability
    row["ott_all"] = availability
    row["watch_providers"] = availability
    row["providers"] = availability
    row["provider_hidden_rows"] = hidden_provider_rows
    row["provider_public_count"] = len(availability)
    row["provider_hidden_count"] = len(hidden_provider_rows)
    row["provider_policy_version"] = PROVIDER_DISPLAY_POLICY_VERSION
    if availability:
        display_primary = availability[0]
        row["provider_display_primary_key"] = display_primary.get("provider_key")
        row["provider_display_primary_name"] = display_primary.get("provider_display_name") or display_primary.get("provider_name")
        row["provider_display_primary_label"] = display_primary.get("provider_public_label")
    correction = _v4_provider_correction(slug)
    if correction:
        row.update(
            {
                "ott_primary": correction["provider_name"],
                "ott_primary_key": correction["provider_key"],
                "provider_count": len(availability),
                "availability_count": len(availability),
                "has_ott": True,
                "has_subscription_ott": True,
                "availability_status": correction["availability_status"],
                "provider_correction_source": correction["source"],
                "provider_correction_confidence": correction["confidence"],
            }
        )
    return row

def _v4_slugify(value: str) -> str:
    text = re.sub(r"[^a-z0-9]+", "-", str(value or "").strip().lower())
    return text.strip("-") or "person"


def _v4_clean_person_candidate(value: str) -> str:
    text = re.sub(r"\b(19|20)\d{2}\b", " ", str(value or ""))
    text = re.sub(r"[\[\]{}()\"']", " ", text)
    text = re.sub(r"\b(current|historical|hollywood|webseries|movie|movies|classic|indian)\b", " ", text, flags=re.I)
    text = re.sub(r"\s+", " ", text).strip(" ,-;/")
    if not text or any(ch.isdigit() for ch in text):
        return ""
    lower = text.lower()
    stop = {"hindi", "telugu", "tamil", "kannada", "malayalam", "bengali", "marathi", "punjabi", "gujarati", "odia", "assamese", "language", "unknown"}
    if lower in stop:
        return ""
    words = [w for w in text.split() if w]
    if len(words) < 1 or len(words) > 5:
        return ""
    if len(text) < 3 or len(text) > 64:
        return ""
    return " ".join(part[:1].upper() + part[1:] for part in words)


def _v4_people_candidates_from_search_row(row: dict) -> list[str]:
    text = str(row.get("search_text") or "")
    if not text:
        return []

    lower = text.lower()
    tail = text
    for marker in (row.get("language_name"), row.get("language_slug")):
        marker_text = str(marker or "").strip().lower()
        if not marker_text:
            continue
        idx = lower.rfind(marker_text)
        if idx >= 0:
            tail = text[idx + len(marker_text):]
            break

    title = str(row.get("title") or "").strip()
    if title and tail == text:
        tail = re.sub(re.escape(title), " ", tail, count=1, flags=re.I)

    parts = re.split(r",|;|\||\band\b|&", tail, flags=re.I)
    return [candidate for candidate in (_v4_clean_person_candidate(part) for part in parts) if candidate]


def _v4_language_aliases(language: str | None) -> list[str]:
    value = str(language or "").strip().lower()
    aliases = {
        "hi": ["hi", "hindi"],
        "hindi": ["hi", "hindi"],
        "te": ["te", "telugu"],
        "telugu": ["te", "telugu"],
        "ta": ["ta", "tamil"],
        "tamil": ["ta", "tamil"],
        "kn": ["kn", "kannada"],
        "kannada": ["kn", "kannada"],
        "ml": ["ml", "malayalam"],
        "malayalam": ["ml", "malayalam"],
    }
    return aliases.get(value, [value] if value else [])



# FLIXYFY_PEOPLE_RANKING_TOP_PERSON_FIX_V2
_V4_CANONICAL_PEOPLE = [
    {
        "display_name": "N. T. Rama Rao",
        "slug": "n-t-rama-rao",
        "score": 100000,
        "aliases": ["ntr", "n. t. rama rao", "n t rama rao", "nandamuri taraka rama rao", "n-t-rama-rao", "nandamuri-taraka-rama-rao"],
    },
    {
        "display_name": "Akkineni Nageswara Rao",
        "slug": "akkineni-nageswara-rao",
        "score": 99000,
        "aliases": ["anr", "a nageswara rao", "a. nageswara rao", "akkineni nageswara rao", "a-nageswara-rao", "akkineni-nageswara-rao"],
    },
    {"display_name": "Krishna", "slug": "krishna", "score": 98000, "aliases": ["krishna", "ghattamaneni krishna", "ghattamaneni-krishna"]},
    {"display_name": "Chiranjeevi", "slug": "chiranjeevi", "score": 97000, "aliases": ["chiranjeevi", "konidela chiranjeevi", "konidela-chiranjeevi"]},
    {"display_name": "Balakrishna", "slug": "balakrishna", "score": 96000, "aliases": ["balakrishna", "nandamuri balakrishna", "nandamuri-balakrishna"]},
    {"display_name": "Savitri", "slug": "savitri", "score": 95000, "aliases": ["savitri"]},
    {"display_name": "Sridevi", "slug": "sridevi", "score": 94000, "aliases": ["sridevi", "sri devi", "sri-devi"]},
    {"display_name": "Brahmanandam", "slug": "brahmanandam", "score": 93000, "aliases": ["brahmanandam"]},
    {"display_name": "Jayasudha", "slug": "jayasudha", "score": 92000, "aliases": ["jayasudha", "jaya sudha", "jaya-sudha"]},
    {"display_name": "Jamuna", "slug": "jamuna", "score": 91000, "aliases": ["jamuna"]},
]

_V4_CANONICAL_BY_COMPACT: dict[str, dict] = {}
for _person in _V4_CANONICAL_PEOPLE:
    for _alias in list(_person["aliases"]) + [_person["display_name"], _person["slug"]]:
        _V4_CANONICAL_BY_COMPACT[re.sub(r"[^a-z0-9]+", "", str(_alias).lower())] = _person

_V4_PEOPLE_QUERY_ALIASES = {
    "ntr": ["ntr", "n. t. rama rao", "n t rama rao", "nandamuri taraka rama rao"],
    "anr": ["anr", "a nageswara rao", "akkineni nageswara rao"],
    "balakrishna": ["balakrishna", "nandamuri balakrishna"],
    "chiranjeevi": ["chiranjeevi", "konidela chiranjeevi"],
    "krishna": ["krishna", "ghattamaneni krishna"],
    "savitri": ["savitri"],
    "sridevi": ["sridevi", "sri devi"],
    "brahmanandam": ["brahmanandam"],
    "jayasudha": ["jayasudha", "jaya sudha"],
    "jamuna": ["jamuna"],
}

def _v4_people_bad_name_v2(name: str | None) -> bool:
    value = str(name or "").strip().lower()
    return value in {"", "n/a", "n/a n/a", "unknown", "none", "null", "na"}

def _v4_people_compact_v2(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())

def _v4_people_canonical_for_value_v2(value: str | None) -> dict | None:
    return _V4_CANONICAL_BY_COMPACT.get(_v4_people_compact_v2(value))

def _v4_people_canonical_for_query_v2(q: str | None) -> dict | None:
    return _v4_people_canonical_for_value_v2(q)

def _v4_people_noisy_query_fragment_v2(name: str | None, q: str | None, domain: str) -> bool:
    query = _v4_people_compact_v2(q)
    if _v4_norm_domain(domain) != "historical" or query not in {"ntr", "anr"}:
        return False
    value = str(name or "").strip()
    compact = _v4_people_compact_v2(value)
    canonical = _v4_people_canonical_for_query_v2(query) or {}
    allowed = {_v4_people_compact_v2(alias) for alias in (canonical.get("aliases") or [])}
    allowed.add(_v4_people_compact_v2(canonical.get("display_name")))
    if compact in allowed:
        return False
    return bool(re.search(rf"(^|\s){re.escape(query)}$", value.lower()))

def _v4_people_count_expr_sql_v2(columns: set[str], preferred: tuple[str, ...]) -> str:
    present = [col for col in preferred if col in columns]
    if not present:
        return "0"
    if len(present) == 1:
        return f'COALESCE({_v4_qi(present[0])}, 0)'
    return "GREATEST(" + ", ".join(f'COALESCE({_v4_qi(col)}, 0)' for col in present) + ")"

def _v4_people_text_expr_sql_v2(columns: set[str], preferred: tuple[str, ...]) -> str:
    present = [col for col in preferred if col in columns]
    if not present:
        return "''"
    return "COALESCE(" + ", ".join(_v4_qi(col) for col in present) + ", '')"

def _v4_people_threshold_count_expr_sql_v2(columns: set[str]) -> str:
    return _v4_people_count_expr_sql_v2(columns, ("career_attached_movie_count", "total_movie_count", "movie_count", "primary_language_movie_count"))

def _v4_people_default_min_movies_v2(domain: str, tier: str | None, q: str | None, min_movies: int | None) -> int | None:
    if min_movies is not None:
        return max(0, int(min_movies or 0))
    if str(q or "").strip():
        return None
    domain = _v4_norm_domain(domain)
    tier_key = str(tier or "").strip().lower()
    if tier_key in {"all", "none", "0"}:
        return None
    if domain == "historical":
        if tier_key in {"eligible", "secondary"}:
            return 25
        return 50
    return 20

def _v4_people_query_terms_v2(q: str | None) -> list[str]:
    value = str(q or "").strip().lower()
    if not value:
        return []
    compact = re.sub(r"[^a-z0-9]+", " ", value).strip()
    terms = []
    for t in (value, compact):
        if t and t not in terms:
            terms.append(t)
    for key, aliases in _V4_PEOPLE_QUERY_ALIASES.items():
        if value == key or compact == key or value in aliases or compact in aliases:
            for alias in aliases:
                alias = alias.lower().strip()
                if alias and alias not in terms:
                    terms.append(alias)
    return terms

def _v4_people_name_blob_v2(item: dict) -> str:
    return " ".join(
        str(item.get(k) or "")
        for k in ("person_name", "display_name", "title", "name", "person_slug", "slug")
    ).lower()

def _v4_people_top_boost_v2(item: dict) -> int:
    slug = str(item.get("person_slug") or item.get("slug") or "")
    names = [item.get(k) for k in ("person_name", "display_name", "title", "name", "person_slug", "slug")]
    for value in [slug, *names]:
        canonical = _v4_people_canonical_for_value_v2(value)
        if canonical:
            return int(canonical.get("score") or 0)
    return 0

def _v4_people_top_boost_sql_v2(columns: set[str]) -> str:
    name_expr = _v4_people_text_expr_sql_v2(columns, ("person_name", "display_name", "name", "title"))
    slug_expr = _v4_people_text_expr_sql_v2(columns, ("person_slug", "slug"))
    name_compact = f"LOWER(REGEXP_REPLACE({name_expr}, '[^a-zA-Z0-9]+', '', 'g'))"
    slug_compact = f"LOWER(REGEXP_REPLACE({slug_expr}, '[^a-zA-Z0-9]+', '', 'g'))"
    parts = []
    for person in _V4_CANONICAL_PEOPLE:
        aliases = {_v4_people_compact_v2(alias) for alias in list(person["aliases"]) + [person["display_name"], person["slug"]]}
        aliases = sorted(alias for alias in aliases if alias)
        if not aliases:
            continue
        quoted = ",".join("'" + alias.replace("'", "''") + "'" for alias in aliases)
        parts.append(f"WHEN {name_compact} IN ({quoted}) OR {slug_compact} IN ({quoted}) THEN {int(person['score'])}")
    return "CASE " + " ".join(parts) + " ELSE 0 END"

def _v4_people_matches_query_v2(item: dict, terms: list[str]) -> bool:
    if not terms:
        return True
    blob = _v4_people_name_blob_v2(item)
    return any(term in blob for term in terms if term)

def _v4_people_sort_key_v2(item: dict):
    return (
        -int(_v4_people_top_boost_v2(item) or 0),
        -int(item.get("career_attached_movie_count") or item.get("total_movie_count") or item.get("movie_count") or 0),
        -int(item.get("total_movie_count") or item.get("movie_count") or 0),
        -int(item.get("movie_count") or 0),
        -int(item.get("youtube_movie_count") or 0),
        str(item.get("person_name") or item.get("display_name") or item.get("title") or ""),
    )

def _v4_people_merge_key_v2(item: dict) -> str:
    slug = str(item.get("person_slug") or item.get("slug") or "").strip().lower()
    name = str(item.get("person_name") or item.get("display_name") or item.get("title") or "").strip()
    if not slug and name:
        slug = _v4_slugify(name)
    return slug or name.lower()

def _v4_people_normalize_item_v2(row: dict) -> dict:
    item = dict(row or {})
    raw_name = item.get("person_name") or item.get("display_name") or item.get("title") or item.get("name") or ""
    raw_slug = item.get("person_slug") or item.get("slug") or _v4_slugify(raw_name)
    canonical = _v4_people_canonical_for_value_v2(raw_slug) or _v4_people_canonical_for_value_v2(raw_name)
    name = canonical.get("display_name") if canonical else raw_name
    slug = canonical.get("slug") if canonical else raw_slug
    item["domain"] = "person"
    item["source_domain"] = "person"
    item["person_name"] = name
    item["display_name"] = item.get("display_name") if not canonical else name
    item["display_name"] = item.get("display_name") or name
    item["title"] = item.get("title") if not canonical else name
    item["title"] = item.get("title") or name
    item["person_slug"] = slug
    item["slug"] = slug
    item["primary_role"] = item.get("primary_role") or item.get("known_for_department") or "film person"
    item["movie_count"] = int(item.get("movie_count") or item.get("primary_language_movie_count") or item.get("total_movie_count") or 0)
    item["primary_language_movie_count"] = int(item.get("primary_language_movie_count") or item.get("movie_count") or 0)
    item["career_attached_movie_count"] = int(item.get("career_attached_movie_count") or item.get("total_movie_count") or item.get("movie_count") or 0)
    item["total_movie_count"] = int(item.get("total_movie_count") or item.get("career_attached_movie_count") or item.get("movie_count") or 0)
    item["youtube_movie_count"] = int(item.get("youtube_movie_count") or 0)
    item["canonical_person_boost"] = _v4_people_top_boost_v2(item)
    item["ranking_boost"] = item["canonical_person_boost"]
    return item

def _v4_people_from_search_payload(domain: str = "historical", page: int = 1, limit: int = 24, q: str | None = None, language: str | None = None, min_movies: int | None = None) -> dict:
    domain = _v4_norm_domain(domain)
    limit = _v4_limit(limit, default=24, cap=100)
    page = max(int(page or 1), 1)
    table = (globals().get("SEARCH_TABLES") or {}).get(domain)
    if not table:
        return _v4_items_payload({"total": 0, "items": []}, page=page, limit=limit, domain=domain)

    where = ['COALESCE("search_text", \'\') <> \'\'']
    params = []
    aliases = _v4_language_aliases(language)
    if aliases:
        placeholders = ",".join(["%s"] * len(aliases))
        where.append(f'(LOWER(COALESCE("language_slug", \'\')) IN ({placeholders}) OR LOWER(COALESCE("language_name", \'\')) IN ({placeholders}))')
        params.extend(aliases)
        params.extend(aliases)

    q_terms = _v4_people_query_terms_v2(q)
    if q_terms:
        term_parts = []
        for term in q_terms:
            term_parts.append('LOWER(COALESCE("search_text", \'\')) LIKE %s')
            params.append(f"%{term}%")
        where.append("(" + " OR ".join(term_parts) + ")")

    clause = " WHERE " + " AND ".join(where)
    rows = _v4_rows(
        f'SELECT "title", "slug", "search_text", "language_slug", "language_name", "release_year" '
        f'FROM public.{_v4_qi(table)}{clause} '
        'ORDER BY COALESCE("rank_score", 0) DESC, COALESCE("release_year", 0) DESC LIMIT %s',
        params + [5000],
    )

    people: dict[str, dict] = {}
    for row in rows:
        row_language = str(row.get("language_slug") or row.get("language_name") or "").strip().lower()
        row_language_name = row.get("language_name") or row.get("language_slug") or ""
        for raw_name in _v4_people_candidates_from_search_row(row):
            if _v4_people_bad_name_v2(raw_name) or _v4_people_noisy_query_fragment_v2(raw_name, q, domain):
                continue
            canonical = _v4_people_canonical_for_value_v2(raw_name)
            name = canonical.get("display_name") if canonical else raw_name
            slug = canonical.get("slug") if canonical else _v4_slugify(name)
            item_probe = {"person_name": name, "person_slug": slug}
            if not _v4_people_matches_query_v2(item_probe, q_terms):
                continue
            item = people.setdefault(
                slug,
                {
                    "domain": "person",
                    "source_domain": "person",
                    "person_name": name,
                    "display_name": name,
                    "title": name,
                    "person_slug": slug,
                    "slug": slug,
                    "primary_language_slug": row_language,
                    "primary_language_name": row_language_name,
                    "language_slug": row_language,
                    "language_name": row_language_name,
                    "primary_role": "film person",
                    "movie_count": 0,
                    "primary_language_movie_count": 0,
                    "career_attached_movie_count": 0,
                    "total_movie_count": 0,
                    "youtube_movie_count": 0,
                    "poster_url": "",
                    "source": "search_derived_people_v2",
                },
            )
            item["movie_count"] += 1
            item["primary_language_movie_count"] += 1
            item["career_attached_movie_count"] += 1
            item["total_movie_count"] += 1

    min_count = int(min_movies or 1)
    items = [_v4_people_normalize_item_v2(item) for item in people.values() if int(item.get("movie_count") or 0) >= min_count]
    items.sort(key=_v4_people_sort_key_v2)
    total = len(items)
    start = (page - 1) * limit
    return _v4_items_payload({"total": total, "items": items[start:start + limit]}, page=page, limit=limit, domain=domain)

def _v4_people_payload(domain: str = "historical", page: int = 1, limit: int = 24, q: str | None = None, language: str | None = None, min_movies: int | None = None, tier: str | None = None) -> dict:
    domain = _v4_norm_domain(domain)
    limit = _v4_limit(limit)
    page = max(int(page or 1), 1)
    table = _v4_person_table(domain)
    columns = _v4_table_columns(table)
    name_sql = _v4_people_text_expr_sql_v2(columns, ("person_name", "display_name", "name", "title"))
    slug_sql = _v4_people_text_expr_sql_v2(columns, ("person_slug", "slug"))
    threshold_expr = _v4_people_threshold_count_expr_sql_v2(columns)
    career_expr = _v4_people_count_expr_sql_v2(columns, ("career_attached_movie_count", "total_movie_count", "movie_count"))
    total_expr = _v4_people_count_expr_sql_v2(columns, ("total_movie_count", "movie_count", "career_attached_movie_count"))
    movie_expr = _v4_people_count_expr_sql_v2(columns, ("movie_count", "primary_language_movie_count", "total_movie_count"))
    youtube_expr = _v4_people_count_expr_sql_v2(columns, ("youtube_movie_count",))

    where = [f"LOWER({name_sql}) NOT IN ('', 'n/a', 'n/a n/a', 'unknown', 'none', 'null', 'na')"]
    params = []

    q_terms = _v4_people_query_terms_v2(q)
    if q_terms:
        q_parts = []
        for term in q_terms:
            like = "%" + term.lower() + "%"
            compact = _v4_people_compact_v2(term)
            for col in ("person_name", "display_name", "name", "title", "person_slug", "slug"):
                if col in columns:
                    q_parts.append(f"LOWER(COALESCE(CAST({_v4_qi(col)} AS TEXT), '')) LIKE %s")
                    params.append(like)
                    q_parts.append(f"LOWER(REGEXP_REPLACE(COALESCE(CAST({_v4_qi(col)} AS TEXT), ''), '[^a-zA-Z0-9]+', '', 'g')) = %s")
                    params.append(compact)
        if q_parts:
            where.append("(" + " OR ".join(q_parts) + ")")

    aliases = _v4_language_aliases(language)
    if aliases:
        placeholders = ",".join(["%s"] * len(aliases))
        lang_parts = []
        for col in ("primary_language_slug", "language_slug", "primary_language_name", "language_name"):
            if col in columns:
                lang_parts.append(f"LOWER(COALESCE(CAST({_v4_qi(col)} AS TEXT), '')) IN ({placeholders})")
                params.extend(aliases)
        if lang_parts:
            where.append("(" + " OR ".join(lang_parts) + ")")

    effective_min_movies = _v4_people_default_min_movies_v2(domain, tier, q, min_movies)
    if effective_min_movies:
        where.append(f"{threshold_expr} >= %s")
        params.append(int(effective_min_movies))
    else:
        where.append(f"{threshold_expr} > 0")

    clause = " WHERE " + " AND ".join(where)

    table_rows = []
    table_total = 0
    try:
        total = _v4_one(f'SELECT COUNT(*) AS total FROM public.{_v4_qi(table)}{clause}', params) or {"total": 0}
        table_total = int(total.get("total") or 0)
        fetch_limit = max(250, min(1000, limit * 30))
        table_rows = _v4_rows(
            f'SELECT {_v4_person_select_sql(table)} FROM public.{_v4_qi(table)}{clause} '
            f'ORDER BY {_v4_people_top_boost_sql_v2(columns)} DESC, '
            f'{career_expr} DESC, {total_expr} DESC, {movie_expr} DESC, {youtube_expr} DESC, {name_sql} ASC '
            'LIMIT %s',
            params + [fetch_limit],
        )
    except Exception:
        table_rows = []
        table_total = 0

    search_rows = []
    if q_terms:
        search_payload = _v4_people_from_search_payload(domain=domain, page=1, limit=100, q=q, language=language, min_movies=min_movies)
        search_rows = list(search_payload.get("items") or [])

    merged: dict[str, dict] = {}
    for row in list(table_rows or []) + search_rows:
        item = _v4_people_normalize_item_v2(row)
        if _v4_people_bad_name_v2(item.get("person_name")) or _v4_people_noisy_query_fragment_v2(item.get("person_name"), q, domain):
            continue
        if q_terms and not _v4_people_matches_query_v2(item, q_terms):
            continue
        key = _v4_people_merge_key_v2(item)
        existing = merged.get(key)
        if not existing:
            merged[key] = item
            continue
        for count_key in ("movie_count", "primary_language_movie_count", "career_attached_movie_count", "total_movie_count", "youtube_movie_count"):
            existing[count_key] = max(int(existing.get(count_key) or 0), int(item.get(count_key) or 0))
        if not existing.get("poster_url") and item.get("poster_url"):
            existing["poster_url"] = item.get("poster_url")
        existing["canonical_person_boost"] = max(int(existing.get("canonical_person_boost") or 0), int(item.get("canonical_person_boost") or 0))
        existing["ranking_boost"] = existing["canonical_person_boost"]

    items = list(merged.values())
    items.sort(key=_v4_people_sort_key_v2)
    total_count = max(table_total, len(items))
    start = (page - 1) * limit
    payload_items = items[start:start + limit]
    return _v4_items_payload({"total": total_count, "items": payload_items}, page=page, limit=limit, domain=domain)


@app.get("/api/v4/health")
def _flixyfy_v4_health():
    return health()

@app.get("/api/v4/providers")
def _flixyfy_v4_providers():
    fn = globals().get("providers")
    if callable(fn):
        return fn()
    return {"items": []}

def _v4_home_section(payload: dict) -> dict:
    payload = dict(payload or {})
    items = list(payload.get("items") or payload.get("movies") or payload.get("results") or payload.get("data") or [])
    return {
        "domain": payload.get("domain"),
        "total": int(payload.get("total") or len(items) or 0),
        "page": int(payload.get("page") or 1),
        "limit": int(payload.get("limit") or len(items) or 0),
        "items": items,
    }

@app.get("/api/v4/home")
def _flixyfy_v4_home(limit: int = 12):
    l = _v4_limit(limit, default=12, cap=24)
    current = _v4_home_section(_v4_content_payload("current", page=1, limit=l))
    historical = _v4_home_section(_v4_content_payload("historical", page=1, limit=l))
    hollywood = _v4_home_section(_v4_content_payload("hollywood", page=1, limit=l))
    webseries = _v4_home_section(_v4_content_payload("webseries", page=1, limit=l))
    current_items = current.get("items", [])
    return {
        "status": "ok",
        "current": current,
        "movies": current_items,
        "popular_movies": current_items,
        "indian_movies": current,
        "historical": historical,
        "hollywood": hollywood,
        "webseries": webseries,
    }

@app.get("/api/v4/movies")
def _flixyfy_v4_movies(page: int = 1, limit: int = 24, provider: str | None = None, language: str | None = None, year: int | None = None, sort: str | None = None):
    return _v4_content_payload("current", page=page, limit=limit, provider=provider, language=language, year=year, sort=sort)

@app.get("/api/v4/current")
def _flixyfy_v4_current(page: int = 1, limit: int = 24, provider: str | None = None, language: str | None = None, year: int | None = None, sort: str | None = None):
    return _v4_content_payload("current", page=page, limit=limit, provider=provider, language=language, year=year, sort=sort)

@app.get("/api/v4/historical")
def _flixyfy_v4_historical(page: int = 1, limit: int = 24, provider: str | None = None, language: str | None = None, year: int | None = None, sort: str | None = None):
    return _v4_content_payload("historical", page=page, limit=limit, provider=provider, language=language, year=year, sort=sort)

@app.get("/api/v4/hollywood")
def _flixyfy_v4_hollywood(page: int = 1, limit: int = 24, provider: str | None = None, language: str | None = None, year: int | None = None, sort: str | None = None):
    return _v4_content_payload("hollywood", page=page, limit=limit, provider=provider, language=language, year=year, sort=sort)

@app.get("/api/v4/webseries")
def _flixyfy_v4_webseries(page: int = 1, limit: int = 24, provider: str | None = None, language: str | None = None, year: int | None = None, sort: str | None = None):
    return _v4_content_payload("webseries", page=page, limit=limit, provider=provider, language=language, year=year, sort=sort)

@app.get("/api/v4/search")
def _flixyfy_v4_search(q: str | None = None, page: int = 1, limit: int = 24, domain: str | None = None, type: str | None = None, region: str | None = None, provider: str | None = None, language: str | None = None, year: int | None = None, sort: str | None = None):
    return _v4_search_payload(q=q, page=page, limit=limit, domain=domain, type=type, region=region, provider=provider, language=language, year=year, sort=sort)

@app.get("/api/v4/global-search")
def _flixyfy_v4_global_search(q: str | None = None, page: int = 1, limit: int = 24, type: str | None = None, region: str | None = None, domain: str | None = None, provider: str | None = None, language: str | None = None, year: int | None = None, sort: str | None = None):
    return _v4_search_payload(q=q, page=page, limit=limit, domain=domain, type=type, region=region, provider=provider, language=language, year=year, sort=sort)

@app.get("/api/v4/search-suggestions")
def _flixyfy_v4_search_suggestions(q: str | None = None, limit: int = 10):
    payload = _v4_search_payload(q=q, page=1, limit=limit)
    items = payload.get("items", [])
    suggestions = []
    for item in items[: _v4_limit(limit, default=10)]:
        title = item.get("title") or item.get("name") or item.get("person_name")
        if title:
            clone = dict(item)
            clone["title"] = title
            suggestions.append(clone)
    return {"query": q or "", "suggestions": suggestions, "items": suggestions, "total": len(suggestions)}

@app.get("/api/v4/historical/people")
def _flixyfy_v4_historical_people(page: int = 1, limit: int = 24, q: str | None = None, language: str | None = None, min_movies: int | None = None, tier: str | None = None):
    return _v4_people_payload("historical", page=page, limit=limit, q=q, language=language, min_movies=min_movies, tier=tier)

@app.get("/api/v4/people")
def _flixyfy_v4_people(page: int = 1, limit: int = 24, q: str | None = None, domain: str | None = None, language: str | None = None, min_movies: int | None = None, tier: str | None = None):
    return _v4_people_payload(domain or "current", page=page, limit=limit, q=q, language=language, min_movies=min_movies, tier=tier)

@app.get("/api/v4/language/{language_slug}")
def _flixyfy_v4_language(language_slug: str, page: int = 1, limit: int = 24, sort: str | None = None):
    return _v4_content_payload("current", page=page, limit=limit, language=language_slug, sort=sort)

@app.get("/api/v4/{domain}/{slug}")
def _flixyfy_v4_detail(domain: str, slug: str):
    return _v4_detail_payload(domain, slug)

# FLIXYFY canonical provider and exact search middleware V1
from app.fresh_canonical_provider_search_middleware_v1 import install_fresh_canonical_provider_search_middleware_v1
install_fresh_canonical_provider_search_middleware_v1(app)
