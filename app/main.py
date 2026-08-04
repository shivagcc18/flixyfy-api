"""FLIXYFY clean lean PostgreSQL runtime.

The public route surface is intentionally kept stable while every runtime
query is restricted to the nine relations in the Phase B clean package.
Importing this module never opens a database connection; OpenAPI generation
is therefore safe in an offline verification process.
"""

from __future__ import annotations

import os
import re
from contextlib import asynccontextmanager
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool


PACKAGE_RELATIONS = frozenset(
    {
        "movie_identity_serving_v3",
        "movie_search_document_v3",
        "movie_alias_serving_v3",
        "movie_genre_serving_v3",
        "movie_language_serving_v3",
        "movie_people_serving_v3",
        "provider_ott_availability_v3",
        "youtube_video_master_v3",
        "movie_youtube_availability_v3",
    }
)

Domain = Literal["current", "historical", "hollywood", "webseries"]
SearchDomain = Literal["all", "current", "historical", "hollywood", "webseries"]

_pool: ConnectionPool | None = None


def _postgres_dsn() -> str | None:
    # The deployment contract may provide either the explicit FLIXYFY name or
    # the platform-standard name. The value is never logged or exposed.
    return os.environ.get("FLIXYFY_POSTGRES_DSN") or os.environ.get("DATABASE_URL")


def _get_pool() -> ConnectionPool:
    global _pool
    if _pool is not None:
        return _pool
    dsn = _postgres_dsn()
    if not dsn:
        raise HTTPException(status_code=503, detail="PostgreSQL runtime is not configured")
    _pool = ConnectionPool(
        conninfo=dsn,
        min_size=1,
        max_size=10,
        timeout=15,
        max_lifetime=300,
        check=ConnectionPool.check_connection,
        kwargs={"row_factory": dict_row},
        open=False,
    )
    _pool.open()
    return _pool


def _rows(sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    with _get_pool().connection() as connection:
        return list(connection.execute(sql, params).fetchall())


def _one(sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
    with _get_pool().connection() as connection:
        return connection.execute(sql, params).fetchone()


def _qi(identifier: str) -> str:
    if identifier not in PACKAGE_RELATIONS:
        raise ValueError(f"relation outside clean package: {identifier}")
    return f'public."{identifier}"'


def _slug(value: Any) -> str:
    result = re.sub(r"[^a-z0-9]+", "-", str(value or "").strip().lower())
    return result.strip("-") or "movie"


def _person_slug(value: Any) -> str:
    result = re.sub(r"[^a-z0-9]+", "-", str(value or "").strip().lower())
    return result.strip("-") or "person"


def _normalise_domain(value: str | None) -> str:
    aliases = {
        "movie": "current",
        "movies": "current",
        "indian": "current",
        "series": "webseries",
        "web_series": "webseries",
        "global": "hollywood",
        "global_movies": "hollywood",
    }
    value = str(value or "current").strip().lower()
    return aliases.get(value, value if value in {"current", "historical", "hollywood", "webseries"} else "current")


def _domain_values(domain: str) -> tuple[str, ...]:
    domain = _normalise_domain(domain)
    if domain == "current":
        return ("current", "indian", "movie", "movies")
    if domain == "historical":
        return ("historical",)
    if domain == "webseries":
        return ("webseries", "series")
    return ("hollywood", "global")


def _provider_key(value: str | None) -> str:
    return str(value or "").strip().lower().replace("-", "_")



def _person_role(value: Any) -> str:
    role = str(value or "").strip().lower().replace("-", "_")
    return "actor" if role in {"cast", "actor", "actress"} else role


def _person_roles(values: Any) -> list[str]:
    if values is None:
        return []
    if isinstance(values, str):
        values = [values]
    return sorted({role for role in (_person_role(value) for value in values) if role})


def _person_query_forms(value: str) -> tuple[str, str]:
    text = re.sub(r"\s+", " ", str(value or "").strip().lower())
    normalized = re.sub(r"[^a-z0-9]+", " ", text).strip()
    compact = re.sub(r"[^a-z0-9]+", "", text)
    return normalized, compact


def _limit(value: int, default: int = 24, cap: int = 100) -> int:
    try:
        value = int(value)
    except (TypeError, ValueError):
        value = default
    return max(1, min(cap, value))


def _offset(page: int, limit: int) -> int:
    return max(0, int(page or 1) - 1) * limit


MOVIE_SELECT = f"""
    SELECT
        i.*,
        i.canonical_movie_id AS id,
        i.canonical_movie_id AS movie_id,
        i.title AS name,
        i.poster AS poster_url,
        i.poster AS poster_path,
        i.backdrop AS backdrop_url,
        i.original_language AS language,
        i.original_language AS language_name,
        i.release_year AS year,
        i.domain AS source_domain,
        EXISTS (
            SELECT 1 FROM {_qi('provider_ott_availability_v3')} p
            WHERE p.canonical_movie_id = i.canonical_movie_id
        ) AS has_ott,
        EXISTS (
            SELECT 1 FROM {_qi('movie_youtube_availability_v3')} y
            WHERE y.canonical_movie_id = i.canonical_movie_id
        ) AS has_youtube,
        COALESCE(i.ott_provider_count, 0) AS provider_count,
        COALESCE(i.youtube_video_count, 0) AS youtube_count
    FROM {_qi('movie_identity_serving_v3')} i
"""


def _movie_predicates(
    domain: str,
    provider: str | None = None,
    language: str | None = None,
    year: int | None = None,
    genre: str | None = None,
    alias: str = "i",
) -> tuple[list[str], list[Any]]:
    predicates: list[str] = []
    params: list[Any] = []
    domains = _domain_values(domain)
    domain_marks = ",".join(["%s"] * len(domains))
    predicates.append(f"LOWER(COALESCE({alias}.domain, '')) IN ({domain_marks})")
    params.extend(x.lower() for x in domains)
    if language:
        predicates.append(
            f"EXISTS (SELECT 1 FROM {_qi('movie_language_serving_v3')} l "
            f"WHERE l.canonical_movie_id = {alias}.canonical_movie_id "
            "AND (LOWER(COALESCE(l.language_code, '')) = %s "
            "OR LOWER(COALESCE(l.language_name, '')) = %s "
            "OR LOWER(COALESCE(l.normalized_name, '')) = %s))"
        )
        language_value = str(language).strip().lower()
        params.extend([language_value] * 3)
    if genre:
        predicates.append(
            f"EXISTS (SELECT 1 FROM {_qi('movie_genre_serving_v3')} g "
            f"WHERE g.canonical_movie_id = {alias}.canonical_movie_id "
            "AND (LOWER(COALESCE(g.genre_name, '')) = %s "
            "OR LOWER(COALESCE(g.normalized_name, '')) = %s))"
        )
        genre_value = str(genre).strip().lower()
        params.extend([genre_value] * 2)
    if year:
        predicates.append(f"{alias}.release_year = %s")
        params.append(int(year))
    if provider:
        provider = _provider_key(provider)
        if provider == "youtube":
            predicates.append(
                f"EXISTS (SELECT 1 FROM {_qi('movie_youtube_availability_v3')} y "
                f"WHERE y.canonical_movie_id = {alias}.canonical_movie_id)"
            )
        else:
            predicates.append(
                f"EXISTS (SELECT 1 FROM {_qi('provider_ott_availability_v3')} p "
                f"WHERE p.canonical_movie_id = {alias}.canonical_movie_id "
                "AND LOWER(COALESCE(p.provider_key, '')) = %s)"
            )
            params.append(provider)
    return predicates, params


def _movie_rows(
    domain: str,
    page: int = 1,
    limit: int = 24,
    provider: str | None = None,
    language: str | None = None,
    year: int | None = None,
) -> tuple[int, list[dict[str, Any]]]:
    limit = _limit(limit)
    predicates, params = _movie_predicates(domain, provider, language, year)
    clause = " WHERE " + " AND ".join(predicates)
    total = _one(
        f"SELECT COUNT(*)::bigint AS total FROM {_qi('movie_identity_serving_v3')} i{clause}",
        tuple(params),
    )
    rows = _rows(
        MOVIE_SELECT + clause
        + " ORDER BY i.release_year DESC NULLS LAST, i.rating DESC NULLS LAST, i.title ASC NULLS LAST LIMIT %s OFFSET %s",
        tuple(params + [limit, _offset(page, limit)]),
    )
    return int((total or {}).get("total") or 0), [_normalise_movie(row) for row in rows]


def _normalise_movie(row: dict[str, Any]) -> dict[str, Any]:
    item = dict(row)
    item.setdefault("slug", _slug(item.get("normalized_title") or item.get("title")))
    item.setdefault("content_slug", item["slug"])
    item.setdefault("movie_slug", item["slug"])
    item.setdefault("type", "movie")
    item.setdefault("content_type", "movie")
    item.setdefault("entity_type", "movie")
    item.setdefault("provider_count", int(item.get("ott_provider_count") or 0))
    item.setdefault("availability_count", int(item.get("provider_count") or 0))
    item.setdefault("youtube_count", int(item.get("youtube_video_count") or 0))
    item.setdefault("has_ott", bool(item.get("has_ott")))
    item.setdefault("has_youtube", bool(item.get("has_youtube")))
    return item


def _search_rows(
    q: str,
    domain: str = "all",
    limit: int = 30,
    provider: str | None = None,
    language: str | None = None,
    year: int | None = None,
) -> list[dict[str, Any]]:
    domains = ("current", "historical", "hollywood", "webseries") if domain == "all" else (_normalise_domain(domain),)
    output: list[dict[str, Any]] = []
    for current_domain in domains:
        predicates, params = _movie_predicates(current_domain, provider, language, year)
        like = f"%{q.strip()}%"
        predicates.append(
            "(LOWER(COALESCE(s.search_text, '')) LIKE LOWER(%s) "
            "OR LOWER(COALESCE(s.title, '')) LIKE LOWER(%s) "
            "OR LOWER(COALESCE(s.aliases, '')) LIKE LOWER(%s) "
            "OR LOWER(COALESCE(s.people, '')) LIKE LOWER(%s))"
        )
        params.extend([like] * 4)
        clause = " WHERE " + " AND ".join(predicates)
        output.extend(
            _rows(
                "SELECT s.*, i.poster, i.backdrop, i.original_language, i.release_year, "
                "i.domain, i.tmdb_id, i.imdb_id, i.rating, i.canonical_movie_id "
                f"FROM {_qi('movie_search_document_v3')} s "
                f"JOIN {_qi('movie_identity_serving_v3')} i ON i.canonical_movie_id = s.canonical_movie_id"
                + clause
                + " ORDER BY i.rating DESC NULLS LAST, s.title ASC NULLS LAST LIMIT %s",
                tuple(params + [_limit(limit, cap=100)]),
            )
        )
    output = [_normalise_movie(row) for row in output]
    output.sort(key=lambda row: (float(row.get("rating") or 0), str(row.get("title") or "").lower()), reverse=True)
    return output[: _limit(limit, cap=100)]



def _person_entity_rows(q: str, limit: int) -> list[dict[str, Any]]:
    normalized, compact = _person_query_forms(q)
    if not normalized and not compact:
        return []
    relation = _qi("movie_people_serving_v3")
    bounded_limit = _limit(limit, default=8, cap=20)
    exact_text = normalized
    exact_compact = compact
    prefix_text = f"{normalized}%" if normalized else "%"
    prefix_compact = f"{compact}%" if compact else "%"

    def fetch(where_sql: str, where_params: tuple[Any, ...]) -> list[dict[str, Any]]:
        sql = f"""
            WITH person_candidates AS (
                SELECT
                    p.person_id,
                    MIN(NULLIF(BTRIM(p.name), '')) AS display_name,
                    ARRAY_AGG(DISTINCT NULLIF(BTRIM(p.name), '') ORDER BY NULLIF(BTRIM(p.name), ''))
                        FILTER (WHERE NULLIF(BTRIM(p.name), '') IS NOT NULL) AS aliases,
                    ARRAY_AGG(DISTINCT LOWER(NULLIF(BTRIM(p.role), '')) ORDER BY LOWER(NULLIF(BTRIM(p.role), '')))
                        FILTER (WHERE NULLIF(BTRIM(p.role), '') IS NOT NULL) AS roles,
                    COUNT(DISTINCT p.canonical_movie_id)::bigint AS movie_count,
                    MIN(CASE
                        WHEN p.normalized_name = %s OR p.compact_name = %s THEN 0
                        WHEN p.normalized_name LIKE %s OR p.compact_name LIKE %s THEN 1
                        ELSE 2
                    END) AS match_rank
                FROM {relation} p
                WHERE {where_sql}
                GROUP BY p.person_id
            )
            SELECT person_id, display_name, aliases, roles, movie_count, match_rank,
                   COUNT(*) OVER()::bigint AS total_matches
            FROM person_candidates
            ORDER BY match_rank ASC, movie_count DESC, display_name ASC NULLS LAST, person_id ASC
            LIMIT %s
        """
        return _rows(
            sql,
            (exact_text, exact_compact, prefix_text, prefix_compact, *where_params, bounded_limit),
        )

    prefix_rows = fetch(
        "(p.normalized_name = %s OR p.compact_name = %s "
        "OR p.normalized_name LIKE %s OR p.compact_name LIKE %s)",
        (exact_text, exact_compact, prefix_text, prefix_compact),
    )
    if prefix_rows:
        return prefix_rows

    fuzzy = f"%{normalized}%" if normalized else "%"
    fuzzy_compact = f"%{compact}%" if compact else "%"
    return fetch(
        "(LOWER(COALESCE(p.name, '')) LIKE %s "
        "OR LOWER(COALESCE(p.normalized_name, '')) LIKE %s "
        "OR LOWER(COALESCE(p.compact_name, '')) LIKE %s)",
        (fuzzy, fuzzy, fuzzy_compact),
    )


def _person_entity_payload(row: dict[str, Any], query: str) -> dict[str, Any]:
    aliases = sorted({str(value).strip() for value in (row.get("aliases") or []) if str(value).strip()})
    normalized, compact = _person_query_forms(query)
    matched_alias = next(
        (
            alias for alias in aliases
            if _person_query_forms(alias)[0] == normalized
            or _person_query_forms(alias)[1] == compact
            or _person_query_forms(alias)[0].startswith(normalized)
            or _person_query_forms(alias)[1].startswith(compact)
        ),
        None,
    )
    display_name = str(row.get("display_name") or matched_alias or "").strip()
    roles = _person_roles(row.get("roles"))
    movie_count = int(row.get("movie_count") or 0)
    role_label = ", ".join(role.replace("_", " ") for role in roles[:3]) or "credited person"
    payload: dict[str, Any] = {
        "entity_type": "person",
        "person_id": str(row.get("person_id")),
        "display_name": display_name,
        "aliases": aliases[:12],
        "disambiguation": f"{role_label}; {movie_count} serving movies",
        "roles": roles,
    }
    if matched_alias and matched_alias.casefold() != display_name.casefold():
        payload["matched_alias"] = matched_alias
    return payload


def _search_intelligence_rows(
    q: str | None,
    domain: str,
    limit: int,
    provider: str | None,
    person_id: int | None,
    language: str | None,
    genre: str | None,
    year: int | None,
) -> list[dict[str, Any]]:
    domains = ("current", "historical", "hollywood", "webseries") if domain == "all" else (_normalise_domain(domain),)
    output: list[dict[str, Any]] = []
    for current_domain in domains:
        predicates, params = _movie_predicates(current_domain, provider, language, year, genre)
        person_roles_select = ""
        select_params: list[Any] = []
        if person_id is not None:
            predicates.append(
                f"EXISTS (SELECT 1 FROM {_qi('movie_people_serving_v3')} mp "
                f"WHERE mp.canonical_movie_id = i.canonical_movie_id AND mp.person_id = %s)"
            )
            params.append(person_id)
            person_roles_select = f""",
                ARRAY(
                    SELECT DISTINCT mp_roles.role
                    FROM {_qi('movie_people_serving_v3')} mp_roles
                    WHERE mp_roles.canonical_movie_id = i.canonical_movie_id
                      AND mp_roles.person_id = %s
                      AND mp_roles.role IS NOT NULL
                ) AS matched_person_roles"""
            select_params.append(person_id)
        elif q and q.strip():
            like = f"%{q.strip()}%"
            predicates.append(
                "(LOWER(COALESCE(s.search_text, '')) LIKE LOWER(%s) "
                "OR LOWER(COALESCE(s.title, '')) LIKE LOWER(%s) "
                "OR LOWER(COALESCE(s.aliases, '')) LIKE LOWER(%s) "
                "OR LOWER(COALESCE(s.people, '')) LIKE LOWER(%s))"
            )
            params.extend([like] * 4)
        clause = " WHERE " + " AND ".join(predicates)
        output.extend(
            _rows(
                "SELECT s.*, i.poster, i.backdrop, i.original_language, i.release_year, "
                "i.domain, i.tmdb_id, i.imdb_id, i.rating, i.canonical_movie_id"
                + person_roles_select
                + f" FROM {_qi('movie_search_document_v3')} s "
                f"JOIN {_qi('movie_identity_serving_v3')} i ON i.canonical_movie_id = s.canonical_movie_id"
                + clause
                + " ORDER BY i.rating DESC NULLS LAST, s.title ASC NULLS LAST LIMIT %s",
                tuple(select_params + params + [_limit(limit, cap=100)]),
            )
        )
    output = [_normalise_movie(row) for row in output]
    output.sort(key=lambda row: (float(row.get("rating") or 0), str(row.get("title") or "").lower()), reverse=True)
    return output[: _limit(limit, cap=100)]


def _parse_person_id(value: str | None) -> int | None:
    if value is None or not str(value).strip():
        return None
    raw = str(value).strip()
    if not re.fullmatch(r"[0-9]+", raw):
        raise HTTPException(status_code=400, detail="person_id must be a canonical person identifier")
    return int(raw)


def _provider_rows(canonical_movie_id: str) -> list[dict[str, Any]]:
    return _rows(
        f"SELECT * FROM {_qi('provider_ott_availability_v3')} "
        "WHERE canonical_movie_id = %s ORDER BY provider_name ASC NULLS LAST, provider_key ASC",
        (canonical_movie_id,),
    )


def _youtube_rows(canonical_movie_id: str) -> list[dict[str, Any]]:
    return _rows(
        f"SELECT y.*, v.representative_title, v.representative_language_code, "
        "v.representative_language_name, v.duration_seconds AS master_duration_seconds "
        f"FROM {_qi('movie_youtube_availability_v3')} y "
        f"LEFT JOIN {_qi('youtube_video_master_v3')} v ON v.video_id = y.video_id "
        "WHERE y.canonical_movie_id = %s ORDER BY y.audio_language_name ASC NULLS LAST, y.video_id ASC",
        (canonical_movie_id,),
    )


def _resolve_movie(domain: str, slug: str) -> dict[str, Any] | None:
    domain_values = _domain_values(domain)
    marks = ",".join(["%s"] * len(domain_values))
    value = str(slug).strip().lower()
    title_value = value.replace("-", " ")
    return _one(
        MOVIE_SELECT
        + " WHERE LOWER(COALESCE(i.domain, '')) IN ("
        + marks
        + ") AND (LOWER(i.canonical_movie_id) = %s OR CAST(i.tmdb_id AS TEXT) = %s "
        + "OR LOWER(COALESCE(i.normalized_title, '')) = %s "
        + "OR LOWER(REGEXP_REPLACE(COALESCE(i.title, ''), '[^a-zA-Z0-9]+', '-', 'g')) = %s) LIMIT 1",
        tuple([x.lower() for x in domain_values] + [value, str(slug), title_value, value]),
    )


def _detail_payload(domain: str, slug: str) -> dict[str, Any]:
    row = _resolve_movie(domain, slug)
    if not row:
        raise HTTPException(status_code=404, detail="Not Found")
    item = _normalise_movie(row)
    canonical_movie_id = str(item.get("canonical_movie_id") or "")
    providers = _provider_rows(canonical_movie_id)
    youtube = _youtube_rows(canonical_movie_id)
    item["availability"] = providers
    item["ott_all"] = providers
    item["watch_providers"] = providers
    item["providers"] = providers
    item["youtube"] = youtube
    item["youtube_versions"] = youtube
    item["youtube_full_movies"] = youtube
    item["provider_public_count"] = len(providers)
    item["provider_hidden_count"] = 0
    item["provider_policy_version"] = "CLEAN_PACKAGE_V1"
    return item


def _items_payload(items: list[dict[str, Any]], total: int, page: int, limit: int, domain: str | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "items": items,
        "results": items,
        "movies": items,
        "data": items,
        "total": int(total),
        "page": int(page),
        "limit": int(limit),
    }
    if domain is not None:
        payload["domain"] = domain
    return payload


def _people_rows(
    domain: str = "current",
    page: int = 1,
    limit: int = 24,
    q: str | None = None,
    language: str | None = None,
    min_movies: int | None = None,
) -> tuple[int, list[dict[str, Any]]]:
    domains = _domain_values(domain)
    marks = ",".join(["%s"] * len(domains))
    params: list[Any] = [x.lower() for x in domains]
    where = [f"LOWER(COALESCE(i.domain, '')) IN ({marks})"]
    if q:
        where.append("(LOWER(COALESCE(p.name, '')) LIKE LOWER(%s) OR LOWER(COALESCE(p.normalized_name, '')) LIKE LOWER(%s))")
        params.extend([f"%{q.strip()}%"] * 2)
    if language:
        where.append(
            f"EXISTS (SELECT 1 FROM {_qi('movie_language_serving_v3')} l "
            "WHERE l.canonical_movie_id = p.canonical_movie_id "
            "AND (LOWER(l.language_code) = %s OR LOWER(l.language_name) = %s OR LOWER(l.normalized_name) = %s))"
        )
        params.extend([str(language).lower()] * 3)
    clause = " WHERE " + " AND ".join(where)
    having = ""
    if min_movies is not None:
        having = " HAVING COUNT(DISTINCT p.canonical_movie_id) >= %s"
        params.append(int(min_movies))
    total_row = _one(
        "SELECT COUNT(*)::bigint AS total FROM (SELECT p.person_id "
        f"FROM {_qi('movie_people_serving_v3')} p JOIN {_qi('movie_identity_serving_v3')} i "
        "ON i.canonical_movie_id = p.canonical_movie_id"
        + clause
        + " GROUP BY p.person_id, p.name, p.normalized_name, p.compact_name"
        + having
        + ") people",
        tuple(params),
    )
    rows = _rows(
        "SELECT p.person_id, p.name, p.normalized_name, p.compact_name, "
        "COUNT(DISTINCT p.canonical_movie_id)::bigint AS movie_count, "
        "COUNT(DISTINCT CASE WHEN y.canonical_movie_id IS NOT NULL THEN p.canonical_movie_id END)::bigint AS youtube_movie_count "
        f"FROM {_qi('movie_people_serving_v3')} p JOIN {_qi('movie_identity_serving_v3')} i "
        "ON i.canonical_movie_id = p.canonical_movie_id "
        f"LEFT JOIN {_qi('movie_youtube_availability_v3')} y ON y.canonical_movie_id = p.canonical_movie_id"
        + clause
        + " GROUP BY p.person_id, p.name, p.normalized_name, p.compact_name"
        + having
        + " ORDER BY movie_count DESC, p.name ASC LIMIT %s OFFSET %s",
        tuple(params + [_limit(limit), _offset(page, _limit(limit))]),
    )
    normalised: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["person_slug"] = _person_slug(item.get("compact_name") or item.get("normalized_name") or item.get("name"))
        item["slug"] = item["person_slug"]
        item["display_name"] = item.get("name")
        item["total_movie_count"] = int(item.get("movie_count") or 0)
        item["career_attached_movie_count"] = int(item.get("movie_count") or 0)
        normalised.append(item)
    return int((total_row or {}).get("total") or 0), normalised


@asynccontextmanager
async def lifespan(_: FastAPI):
    try:
        yield
    finally:
        global _pool
        if _pool is not None:
            _pool.close()
            _pool = None


app = FastAPI(title="FLIXYFY Clean Lean PostgreSQL API", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "OPTIONS"],
    allow_headers=["*"],
)


@app.get("/api/v1/health")
def health() -> dict[str, Any]:
    row = _one("SELECT current_database() AS database_name, now() AS checked_at")
    return {"status": "ok", "database_connected": True, **(row or {})}


@app.get("/api/v1/content")
def content(
    domain: Domain = "current",
    provider: str | None = None,
    language: str | None = None,
    year: int | None = None,
    limit: int = Query(24, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    page = (offset // _limit(limit)) + 1
    total, items = _movie_rows(domain, page, limit, provider, language, year)
    return {"domain": domain, "provider": provider, "total": total, "items": items}


@app.get("/api/v1/content/{domain}/{slug}")
def detail(domain: Domain, slug: str) -> dict[str, Any]:
    item = _detail_payload(domain, slug)
    return {"domain": domain, "item": item, "availability": item.get("availability", [])}


@app.get("/api/v1/search/entities")
def search_entities(
    q: str = Query(..., min_length=1, max_length=120),
    entity_type: Literal["person"] = "person",
    limit: int = Query(8, ge=1, le=20),
) -> dict[str, Any]:
    rows = _person_entity_rows(q, limit)
    items = [_person_entity_payload(row, q) for row in rows]
    total = int((rows[0] or {}).get("total_matches") or 0) if rows else 0
    return {"query": q, "entity_type": entity_type, "total": total, "limit": _limit(limit, default=8, cap=20), "items": items, "entities": items}


@app.get("/api/v1/search/intelligence")
def search_intelligence(
    q: str | None = Query(None, max_length=120),
    person_id: str | None = Query(None, max_length=32),
    provider: str | None = Query(None, max_length=80),
    domain: SearchDomain = "all",
    language: str | None = Query(None, max_length=40),
    genre: str | None = Query(None, max_length=80),
    year: int | None = None,
    page: int = Query(1, ge=1),
    limit: int = Query(24, ge=1, le=100),
) -> dict[str, Any]:
    canonical_person_id = _parse_person_id(person_id)
    size = _limit(limit, cap=100)
    items = _search_intelligence_rows(q, domain, size, provider, canonical_person_id, language, genre, year)
    start = _offset(page, size)
    visible = items[start : start + size]
    for item in visible:
        matched_roles = _person_roles(item.pop("matched_person_roles", None))
        if canonical_person_id is None:
            continue
        if not matched_roles:
            raise RuntimeError("canonical person result missing person edge evidence")
        evidence: dict[str, Any] = {
            "person_id": str(canonical_person_id),
            "person_roles": matched_roles,
        }
        if provider:
            evidence["provider_key"] = _provider_key(provider)
            evidence["provider_confirmed"] = True
        item["match_evidence"] = evidence
    return _items_payload(visible, len(items), page, size, domain if domain != "all" else None)


@app.get("/api/v1/search")
def search(
    q: str = Query(..., min_length=1, max_length=120),
    domain: SearchDomain = "all",
    limit: int = Query(30, ge=1, le=100),
) -> dict[str, Any]:
    items = _search_rows(q, domain, limit)
    return {"query": q, "domain": domain, "total": len(items), "items": items}


@app.get("/api/v1/person/{domain}/{person_slug}")
def person(domain: Domain, person_slug: str) -> dict[str, Any]:
    _, people = _people_rows(domain, 1, 100, person_slug)
    match = next((item for item in people if item["person_slug"] == _person_slug(person_slug)), None)
    if not match:
        raise HTTPException(status_code=404, detail="Person not found")
    return {"domain": domain, "person": match}


@app.get("/api/v1/providers")
def providers(domain: Domain = "current") -> dict[str, Any]:
    domains = _domain_values(domain)
    marks = ",".join(["%s"] * len(domains))
    return {
        "domain": domain,
        "items": _rows(
            f"SELECT LOWER(p.provider_key) AS provider_key, MIN(p.provider_name) AS provider_name, "
            "COUNT(*)::bigint AS row_count, COUNT(DISTINCT p.canonical_movie_id)::bigint AS content_count "
            f"FROM {_qi('provider_ott_availability_v3')} p JOIN {_qi('movie_identity_serving_v3')} i "
            "ON i.canonical_movie_id = p.canonical_movie_id "
            f"WHERE LOWER(COALESCE(i.domain, '')) IN ({marks}) "
            "GROUP BY LOWER(p.provider_key) ORDER BY content_count DESC, provider_key",
            tuple(x.lower() for x in domains),
        ),
    }


def _v4_content(domain: str, page: int, limit: int, provider: str | None, language: str | None, year: int | None) -> dict[str, Any]:
    domain = _normalise_domain(domain)
    total, items = _movie_rows(domain, page, limit, provider, language, year)
    return _items_payload(items, total, page, _limit(limit), domain)


def _v4_search(q: str | None, page: int, limit: int, domain: str | None, provider: str | None, language: str | None, year: int | None) -> dict[str, Any]:
    items = _search_rows(q or "", _normalise_domain(domain) if domain else "all", _limit(limit), provider, language, year) if q else []
    return _items_payload(items[_offset(page, _limit(limit)) : _offset(page, _limit(limit)) + _limit(limit)], len(items), page, _limit(limit), _normalise_domain(domain) if domain else None)


@app.get("/api/v4/health")
def _flixyfy_v4_health() -> dict[str, Any]:
    return health()


@app.get("/api/v4/providers")
def _flixyfy_v4_providers() -> dict[str, Any]:
    return providers()


@app.get("/api/v4/home")
def _flixyfy_v4_home(limit: int = 12) -> dict[str, Any]:
    size = _limit(limit, default=12, cap=24)
    current = _v4_content("current", 1, size, None, None, None)
    historical = _v4_content("historical", 1, size, None, None, None)
    webseries = _v4_content("webseries", 1, size, None, None, None)
    hollywood = _v4_content("hollywood", 1, size, None, None, None)
    return {
        "status": "ok",
        "current": current,
        "movies": current["items"],
        "popular_movies": current["items"],
        "indian_movies": current,
        "historical": historical,
        "hollywood": hollywood,
        "webseries": webseries,
    }


@app.get("/api/v4/movies")
def _flixyfy_v4_movies(page: int = 1, limit: int = 24, provider: str | None = None, language: str | None = None, year: int | None = None, sort: str | None = None) -> dict[str, Any]:
    return _v4_content("current", page, limit, provider, language, year)


@app.get("/api/v4/current")
def _flixyfy_v4_current(page: int = 1, limit: int = 24, provider: str | None = None, language: str | None = None, year: int | None = None, sort: str | None = None) -> dict[str, Any]:
    return _v4_content("current", page, limit, provider, language, year)


@app.get("/api/v4/historical")
def _flixyfy_v4_historical(page: int = 1, limit: int = 24, provider: str | None = None, language: str | None = None, year: int | None = None, sort: str | None = None) -> dict[str, Any]:
    return _v4_content("historical", page, limit, provider, language, year)


@app.get("/api/v4/hollywood")
def _flixyfy_v4_hollywood(page: int = 1, limit: int = 24, provider: str | None = None, language: str | None = None, year: int | None = None, sort: str | None = None) -> dict[str, Any]:
    return _v4_content("hollywood", page, limit, provider, language, year)


@app.get("/api/v4/webseries")
def _flixyfy_v4_webseries(page: int = 1, limit: int = 24, provider: str | None = None, language: str | None = None, year: int | None = None, sort: str | None = None) -> dict[str, Any]:
    return _v4_content("webseries", page, limit, provider, language, year)


@app.get("/api/v4/search")
def _flixyfy_v4_search(q: str | None = None, page: int = 1, limit: int = 24, domain: str | None = None, type: str | None = None, region: str | None = None, provider: str | None = None, language: str | None = None, year: int | None = None, sort: str | None = None) -> dict[str, Any]:
    return _v4_search(q, page, limit, domain, provider, language, year)


@app.get("/api/v4/global-search")
def _flixyfy_v4_global_search(q: str | None = None, page: int = 1, limit: int = 24, type: str | None = None, region: str | None = None, domain: str | None = None, provider: str | None = None, language: str | None = None, year: int | None = None, sort: str | None = None) -> dict[str, Any]:
    return _v4_search(q, page, limit, domain, provider, language, year)


@app.get("/api/v4/search-suggestions")
def _flixyfy_v4_search_suggestions(q: str | None = None, limit: int = 10) -> dict[str, Any]:
    payload = _v4_search(q, 1, limit, None, None, None, None)
    suggestions = []
    for item in payload["items"][: _limit(limit, default=10, cap=100)]:
        title = item.get("title") or item.get("name")
        if title:
            clone = dict(item)
            clone["title"] = title
            suggestions.append(clone)
    return {"query": q or "", "suggestions": suggestions, "items": suggestions, "total": len(suggestions)}


@app.get("/api/v4/historical/people")
def _flixyfy_v4_historical_people(page: int = 1, limit: int = 24, q: str | None = None, language: str | None = None, min_movies: int | None = None, tier: str | None = None) -> dict[str, Any]:
    total, items = _people_rows("historical", page, limit, q, language, min_movies)
    return _items_payload(items, total, page, _limit(limit), "historical")


@app.get("/api/v4/people")
def _flixyfy_v4_people(page: int = 1, limit: int = 24, q: str | None = None, domain: str | None = None, language: str | None = None, min_movies: int | None = None, tier: str | None = None) -> dict[str, Any]:
    domain = _normalise_domain(domain)
    total, items = _people_rows(domain, page, limit, q, language, min_movies)
    return _items_payload(items, total, page, _limit(limit), domain)


@app.get("/api/v4/language/{language_slug}")
def _flixyfy_v4_language(language_slug: str, page: int = 1, limit: int = 24, sort: str | None = None) -> dict[str, Any]:
    return _v4_content("current", page, limit, None, language_slug, None)


@app.get("/api/v4/{domain}/{slug}")
def _flixyfy_v4_detail(domain: str, slug: str) -> dict[str, Any]:
    return _detail_payload(domain, slug)
