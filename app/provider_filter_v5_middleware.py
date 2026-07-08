# FLIXYFY_BACKEND_PROVIDER_FILTERS_V5_AUDIT_APPLY_V2
# Provider-filtered list fallback for existing domain availability_serving_v5 tables.
# DB policy: SELECT-only. No DDL. No table creation. No unified provider serving dependency.

from __future__ import annotations

import math
import os
import re
import sqlite3
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse


DOMAIN_AVAILABILITY_TABLE = {
    "current": "current_availability_serving_v5",
    "hollywood": "hollywood_availability_serving_v5",
    "historical": "historical_availability_serving_v5",
    "webseries": "webseries_availability_serving_v5",
}

DOMAIN_CONTENT_HINTS = {
    "current": ("current", "movie", "media"),
    "hollywood": ("hollywood", "movie"),
    "historical": ("historical", "movie"),
    "webseries": ("webseries", "series", "show"),
}

BAD_CONTENT_NAME_PARTS = (
    "availability",
    "youtube",
    "search",
    "person",
    "people",
    "credit",
    "registry",
    "manifest",
    "backup",
    "__backup",
    "__stg",
    "quarantine",
    "audit",
    "run",
    "log",
    "source",
    "provider",
)

PROVIDER_ALIASES = {
    "all": "",
    "all_providers": "",
    "all providers": "",
    "": "",
    "youtube": "youtube",
    "yt": "youtube",
    "prime": "prime_video",
    "prime_video": "prime_video",
    "amazon_prime": "prime_video",
    "amazon_prime_video": "amazon_prime_video",
    "amazon video": "amazon_video",
    "amazon_video": "amazon_video",
    "netflix": "netflix",
    "jio": "jiohotstar",
    "hotstar": "jiohotstar",
    "jiohotstar": "jiohotstar",
    "disney_hotstar": "jiohotstar",
    "zee5": "zee5",
    "sony liv": "sonyliv",
    "sony_liv": "sonyliv",
    "sonyliv": "sonyliv",
    "aha": "aha",
    "sun nxt": "sun_nxt",
    "sun_nxt": "sun_nxt",
    "sunnxt": "sun_nxt",
    "apple tv": "apple_tv_store",
    "apple_tv": "apple_tv",
    "apple_tv_store": "apple_tv_store",
    "google tv": "google_tv",
    "google_tv": "google_tv",
    "google play": "google_play_movies",
    "google_play_movies": "google_play_movies",
    "mx player": "mx_player",
    "mx_player": "mx_player",
    "shemaroo": "shemaroome",
    "shemaroo me": "shemaroome",
    "shemaroome": "shemaroome",
}


def normalize_provider(value: Any) -> str:
    raw = str(value or "").strip().lower()
    raw = raw.replace("-", "_")
    raw = re.sub(r"\s+", " ", raw)
    compact = raw.replace(" ", "_")
    return PROVIDER_ALIASES.get(raw) or PROVIDER_ALIASES.get(compact) or compact


def normalize_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        n = int(value)
    except Exception:
        n = default
    return max(minimum, min(maximum, n))


def get_db_kind_and_connect():
    database_url = os.environ.get("DATABASE_URL") or os.environ.get("POSTGRES_URL") or ""
    sqlite_path = os.environ.get("FLIXYFY_SQLITE_DB") or os.environ.get("SQLITE_DB_PATH") or ""

    if database_url.startswith("postgres"):
        try:
            import psycopg2  # type: ignore
            return "postgres", psycopg2.connect(database_url)
        except Exception:
            try:
                import psycopg  # type: ignore
                return "psycopg3", psycopg.connect(database_url)
            except Exception as exc:
                raise RuntimeError(f"postgres_connect_failed: {exc}") from exc

    if sqlite_path:
        return "sqlite", sqlite3.connect(sqlite_path)

    local_default = os.path.join(os.getcwd(), "db", "flixyfy.db")
    if os.path.exists(local_default):
        return "sqlite", sqlite3.connect(local_default)

    raise RuntimeError("no_database_url")


def ph(kind: str) -> str:
    return "?" if kind == "sqlite" else "%s"


def qident(name: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_]", "", str(name or ""))
    if not safe:
        raise ValueError("unsafe_identifier")
    return safe


def fetch_all_objects(conn, kind: str) -> List[Dict[str, str]]:
    cur = conn.cursor()
    if kind == "sqlite":
        rows = cur.execute(
            "SELECT name, type FROM sqlite_master WHERE type IN ('table','view') ORDER BY name"
        ).fetchall()
        return [{"name": r[0], "type": r[1]} for r in rows]

    cur.execute(
        """
        SELECT table_name, table_type
        FROM information_schema.tables
        WHERE table_schema = 'public'
        ORDER BY table_name
        """
    )
    return [{"name": r[0], "type": r[1]} for r in cur.fetchall()]


def fetch_columns(conn, kind: str, table: str) -> List[str]:
    cur = conn.cursor()
    table = qident(table)
    if kind == "sqlite":
        rows = cur.execute(f"PRAGMA table_info({table})").fetchall()
        return [r[1] for r in rows]

    cur.execute(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = %s
        ORDER BY ordinal_position
        """,
        (table,),
    )
    return [r[0] for r in cur.fetchall()]


def table_exists(objects: Sequence[Dict[str, str]], table: str) -> bool:
    return any(obj["name"] == table for obj in objects)


def first_col(cols: Iterable[str], candidates: Sequence[str]) -> Optional[str]:
    s = set(cols)
    for c in candidates:
        if c in s:
            return c
    return None


def choose_content_table(objects: Sequence[Dict[str, str]], conn, kind: str, domain: str) -> Optional[str]:
    explicit = {
        "current": [
            "current_movie_serving_v5",
            "current_movies_serving_v5",
            "current_serving_v5",
            "current_movie_serving_v5_backend_compat",
            "media_serving_v5",
        ],
        "hollywood": [
            "hollywood_movie_serving_v5",
            "hollywood_movies_serving_v5",
            "hollywood_serving_v5",
            "hollywood_card_serving_v5",
            "hollywood_detail_serving_v5",
        ],
        "historical": [
            "historical_movie_serving_v5",
            "historical_movies_serving_v5",
            "historical_serving_v5",
            "historical_card_serving_v5",
            "historical_detail_serving_v5",
        ],
        "webseries": [
            "webseries_series_serving_v5",
            "webseries_serving_v5",
            "webseries_show_serving_v5",
            "webseries_card_serving_v5",
        ],
    }.get(domain, [])

    names = [obj["name"] for obj in objects]
    for name in explicit:
        if name in names:
            cols = set(fetch_columns(conn, kind, name))
            if cols.intersection({"slug", "content_slug", "movie_slug", "series_slug"}):
                return name

    scored: List[Tuple[int, str]] = []
    hints = DOMAIN_CONTENT_HINTS.get(domain, (domain,))
    for name in names:
        low = name.lower()
        if "serving_v5" not in low:
            continue
        if any(part in low for part in BAD_CONTENT_NAME_PARTS):
            continue
        if domain != "current" and domain not in low:
            continue
        cols = set(fetch_columns(conn, kind, name))
        if not cols.intersection({"slug", "content_slug", "movie_slug", "series_slug"}):
            continue
        if not cols.intersection({"title", "name", "original_title"}):
            continue
        score = 0
        for hint in hints:
            if hint in low:
                score += 5
        if "movie" in low:
            score += 3
        if "card" in low:
            score += 1
        if "detail" in low:
            score -= 1
        scored.append((score, name))

    scored.sort(key=lambda x: (-x[0], x[1]))
    return scored[0][1] if scored else None


def select_expr(alias: str, cols: Sequence[str], candidates: Sequence[str], out: str, default_sql: str = "NULL") -> str:
    col = first_col(cols, candidates)
    if col:
        return f"{alias}.{qident(col)} AS {qident(out)}"
    return f"{default_sql} AS {qident(out)}"


def order_expr(alias: str, cols: Sequence[str]) -> str:
    parts = []
    if "popularity" in cols:
        parts.append(f"COALESCE({alias}.popularity, 0) DESC")
    if "quality_score" in cols:
        parts.append(f"COALESCE({alias}.quality_score, 0) DESC")
    if "vote_count" in cols:
        parts.append(f"COALESCE({alias}.vote_count, 0) DESC")
    if "release_year" in cols:
        parts.append(f"COALESCE({alias}.release_year, 0) DESC")
    elif "year" in cols:
        parts.append(f"COALESCE({alias}.year, 0) DESC")
    return ", ".join(parts) if parts else "1"


def build_items_from_content(conn, kind: str, domain: str, provider: str, page: int, limit: int, q: str = "") -> Dict[str, Any]:
    objects = fetch_all_objects(conn, kind)
    availability = DOMAIN_AVAILABILITY_TABLE[domain]
    if not table_exists(objects, availability):
        return {"items": [], "total": 0, "source": "availability_missing", "content_table": None}

    content = choose_content_table(objects, conn, kind, domain)
    if not content:
        return build_items_from_availability(conn, kind, domain, provider, page, limit, q)

    mcols = fetch_columns(conn, kind, content)
    acols = fetch_columns(conn, kind, availability)
    m_slug = first_col(mcols, ("slug", "content_slug", "movie_slug", "series_slug"))
    a_slug = first_col(acols, ("content_slug", "slug", "movie_slug", "series_slug"))
    a_provider = first_col(acols, ("provider_key", "normalized_provider_name", "provider_name", "provider_display_name"))
    if not (m_slug and a_slug and a_provider):
        return build_items_from_availability(conn, kind, domain, provider, page, limit, q)

    p = ph(kind)
    exists_parts = [
        f"a.{qident(a_slug)} = m.{qident(m_slug)}",
        f"LOWER(COALESCE(a.{qident(a_provider)}, '')) = {p}",
    ]
    params: List[Any] = [provider]
    if "domain" in acols:
        exists_parts.append(f"LOWER(COALESCE(a.domain,'')) = {p}")
        params.append(domain)

    where = [
        f"EXISTS (SELECT 1 FROM {qident(availability)} a WHERE {' AND '.join(exists_parts)})"
    ]
    if q:
        text_cols = [c for c in ("title", "original_title", "name", "slug") if c in mcols]
        if text_cols:
            where.append("(" + " OR ".join([f"LOWER(COALESCE(m.{qident(c)}, '')) LIKE {p}" for c in text_cols]) + ")")
            params.extend([f"%{q.lower()}%"] * len(text_cols))

    select_cols = [
        select_expr("m", mcols, ("id",), "id", "NULL"),
        select_expr("m", mcols, (m_slug,), "slug", "NULL"),
        select_expr("m", mcols, ("title", "name", "original_title"), "title", "NULL"),
        select_expr("m", mcols, ("original_title", "title", "name"), "original_title", "NULL"),
        select_expr("m", mcols, ("release_year", "year"), "release_year", "NULL"),
        select_expr("m", mcols, ("year", "release_year"), "year", "NULL"),
        select_expr("m", mcols, ("poster_url", "poster_path", "poster"), "poster_url", "NULL"),
        select_expr("m", mcols, ("backdrop_url", "backdrop_path"), "backdrop_url", "NULL"),
        select_expr("m", mcols, ("primary_language", "language_name", "language"), "primary_language", "NULL"),
        select_expr("m", mcols, ("language_slug",), "language_slug", "NULL"),
        select_expr("m", mcols, ("rating", "vote_average"), "rating", "NULL"),
        select_expr("m", mcols, ("popularity",), "popularity", "NULL"),
        select_expr("m", mcols, ("vote_count",), "vote_count", "NULL"),
        select_expr("m", mcols, ("tmdb_id",), "tmdb_id", "NULL"),
        select_expr("m", mcols, ("imdb_id",), "imdb_id", "NULL"),
        f"'{domain}' AS domain",
        f"'{provider}' AS ott_primary_key",
        f"'{provider_to_label(provider)}' AS ott_primary",
        "1 AS ott_count",
        "1 AS has_ott",
        "0 AS has_free_ott",
        "0 AS has_subscription_ott",
        "0 AS has_buy_ott",
        "0 AS has_rent_ott",
        "0 AS is_free",
    ]

    where_sql = " AND ".join(where)
    offset = (page - 1) * limit
    cur = conn.cursor()
    cur.execute(f"SELECT COUNT(*) FROM {qident(content)} m WHERE {where_sql}", tuple(params))
    total = int(cur.fetchone()[0] or 0)

    sql = (
        f"SELECT {', '.join(select_cols)} "
        f"FROM {qident(content)} m "
        f"WHERE {where_sql} "
        f"ORDER BY {order_expr('m', mcols)} "
        f"LIMIT {p} OFFSET {p}"
    )
    cur.execute(sql, tuple(params + [limit, offset]))
    cols = [d[0] for d in cur.description]
    rows = [dict(zip(cols, row)) for row in cur.fetchall()]
    return {
        "items": [normalize_item(row, domain, provider) for row in rows],
        "total": total,
        "source": "content_exists_v5",
        "content_table": content,
        "availability_table": availability,
    }


def build_items_from_availability(conn, kind: str, domain: str, provider: str, page: int, limit: int, q: str = "") -> Dict[str, Any]:
    objects = fetch_all_objects(conn, kind)
    availability = DOMAIN_AVAILABILITY_TABLE[domain]
    if not table_exists(objects, availability):
        return {"items": [], "total": 0, "source": "availability_missing", "content_table": None}

    acols = fetch_columns(conn, kind, availability)
    a_slug = first_col(acols, ("content_slug", "slug", "movie_slug", "series_slug"))
    a_provider = first_col(acols, ("provider_key", "normalized_provider_name", "provider_name", "provider_display_name"))
    if not (a_slug and a_provider):
        return {"items": [], "total": 0, "source": "availability_missing_key_columns", "content_table": None}

    p = ph(kind)
    where = [f"LOWER(COALESCE({qident(a_provider)}, '')) = {p}"]
    params: List[Any] = [provider]
    if "domain" in acols:
        where.append(f"LOWER(COALESCE(domain,'')) = {p}")
        params.append(domain)
    if q:
        text_cols = [c for c in ("title", "content_title", "movie_title", "provider_name", "provider_display_name", a_slug) if c in acols]
        if text_cols:
            where.append("(" + " OR ".join([f"LOWER(COALESCE({qident(c)}, '')) LIKE {p}" for c in text_cols]) + ")")
            params.extend([f"%{q.lower()}%"] * len(text_cols))

    title_col = first_col(acols, ("title", "content_title", "movie_title", "name", "provider_title"))
    year_col = first_col(acols, ("release_year", "year"))
    poster_col = first_col(acols, ("poster_url", "poster_path", "poster"))
    backdrop_col = first_col(acols, ("backdrop_url", "backdrop_path"))
    language_col = first_col(acols, ("primary_language", "language_name", "language"))
    lang_slug_col = first_col(acols, ("language_slug",))

    def min_expr(col: Optional[str], alias: str) -> str:
        if col:
            return f"MIN({qident(col)}) AS {qident(alias)}"
        return f"NULL AS {qident(alias)}"

    select_cols = [
        f"{qident(a_slug)} AS slug",
        min_expr(title_col, "title"),
        min_expr(title_col, "original_title"),
        min_expr(year_col, "release_year"),
        min_expr(year_col, "year"),
        min_expr(poster_col, "poster_url"),
        min_expr(backdrop_col, "backdrop_url"),
        min_expr(language_col, "primary_language"),
        min_expr(lang_slug_col, "language_slug"),
        f"'{domain}' AS domain",
        f"'{provider}' AS ott_primary_key",
        f"'{provider_to_label(provider)}' AS ott_primary",
        "COUNT(*) AS ott_count",
        "1 AS has_ott",
        "0 AS has_free_ott",
        "0 AS has_subscription_ott",
        "0 AS has_buy_ott",
        "0 AS has_rent_ott",
        "0 AS is_free",
    ]
    where_sql = " AND ".join(where)
    offset = (page - 1) * limit
    order_col = year_col or a_slug
    cur = conn.cursor()
    cur.execute(f"SELECT COUNT(DISTINCT {qident(a_slug)}) FROM {qident(availability)} WHERE {where_sql}", tuple(params))
    total = int(cur.fetchone()[0] or 0)

    sql = (
        f"SELECT {', '.join(select_cols)} "
        f"FROM {qident(availability)} "
        f"WHERE {where_sql} "
        f"GROUP BY {qident(a_slug)} "
        f"ORDER BY MAX(COALESCE({qident(order_col)}, 0)) DESC "
        f"LIMIT {p} OFFSET {p}"
    )
    cur.execute(sql, tuple(params + [limit, offset]))
    cols = [d[0] for d in cur.description]
    rows = [dict(zip(cols, row)) for row in cur.fetchall()]
    return {
        "items": [normalize_item(row, domain, provider) for row in rows],
        "total": total,
        "source": "availability_only_v5",
        "content_table": None,
        "availability_table": availability,
    }


def normalize_item(row: Dict[str, Any], domain: str, provider: str) -> Dict[str, Any]:
    slug = row.get("slug") or row.get("content_slug") or ""
    title = row.get("title") or row.get("original_title") or str(slug).replace("-", " ").title()
    year = row.get("release_year") or row.get("year")
    item = dict(row)
    item["slug"] = slug
    item["title"] = title
    item["original_title"] = item.get("original_title") or title
    item["release_year"] = year
    item["year"] = year
    item["domain"] = domain
    item["source_domain"] = domain
    item["ott_primary_key"] = provider
    item["ott_primary"] = provider_to_label(provider)
    item["ott_count"] = int(item.get("ott_count") or 1)
    item["has_ott"] = 1
    item["availability"] = [
        {
            "provider_key": provider,
            "provider_name": provider_to_label(provider),
            "provider_display_name": provider_to_label(provider),
        }
    ]
    item["watch_providers"] = item["availability"]
    if domain == "hollywood":
        item["movie_url"] = f"/hollywood/{slug}"
    elif domain == "historical":
        item["movie_url"] = f"/historical/{slug}"
    elif domain == "webseries":
        item["movie_url"] = f"/webseries/{slug}"
    else:
        item["movie_url"] = f"/movie/{slug}"
    return item


def provider_to_label(provider: str) -> str:
    labels = {
        "youtube": "YouTube",
        "prime_video": "Prime Video",
        "amazon_prime_video": "Prime Video",
        "amazon_video": "Amazon Video",
        "netflix": "Netflix",
        "jiohotstar": "JioHotstar",
        "zee5": "ZEE5",
        "sonyliv": "SonyLIV",
        "aha": "Aha",
        "sun_nxt": "Sun NXT",
        "apple_tv": "Apple TV",
        "apple_tv_store": "Apple TV",
        "google_tv": "Google TV",
        "mx_player": "MX Player",
        "shemaroome": "ShemarooMe",
    }
    return labels.get(provider, provider.replace("_", " ").title())


def domain_from_path(path: str) -> Optional[str]:
    if path == "/api/v3/movies":
        return "current"
    if path == "/api/v3/hollywood":
        return "hollywood"
    if path == "/api/v3/historical":
        return "historical"
    if path == "/api/v3/webseries":
        return "webseries"
    if path == "/api/v3/home":
        return "current"
    if path == "/api/v3/search":
        return "current"
    return None


def home_payload(items: List[Dict[str, Any]], provider: str, total: int) -> Dict[str, Any]:
    latest = sorted(items, key=lambda x: int(x.get("release_year") or 0), reverse=True)
    return {
        "trending": items[:12],
        "latest": latest[:12],
        "free": items[:12] if provider == "youtube" else [],
        "hindi": [x for x in items if str(x.get("language_slug") or "").lower() in ("hi", "hindi")][:12],
        "telugu": [x for x in items if str(x.get("language_slug") or "").lower() in ("te", "telugu")][:12],
        "tamil": [x for x in items if str(x.get("language_slug") or "").lower() in ("ta", "tamil")][:12],
        "sections": [
            {
                "key": "provider",
                "title": f"{provider_to_label(provider)} - Popular Movies",
                "items": items[:24],
                "total": total,
            }
        ],
    }


class ProviderFilterV5Middleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        provider = normalize_provider(
            request.query_params.get("provider") or request.query_params.get("provider_key") or ""
        )
        if not provider:
            return await call_next(request)

        domain = domain_from_path(request.url.path)
        if not domain:
            return await call_next(request)

        page = normalize_int(request.query_params.get("page"), 1, 1, 10000)
        limit = normalize_int(request.query_params.get("limit"), 24, 1, 100)
        q = str(request.query_params.get("q") or request.query_params.get("query") or "").strip()

        try:
            kind, conn = get_db_kind_and_connect()
            try:
                result = build_items_from_content(conn, kind, domain, provider, page, limit, q)
            finally:
                try:
                    conn.close()
                except Exception:
                    pass

            items = result["items"]
            total = int(result["total"] or 0)
            pages = max(1, int(math.ceil(total / float(limit)))) if limit else 1

            if request.url.path == "/api/v3/home":
                return JSONResponse(home_payload(items, provider, total))

            if request.url.path == "/api/v3/search":
                return JSONResponse(
                    {
                        "q": q,
                        "query": q,
                        "items": items,
                        "count": len(items),
                        "total": total,
                        "page": page,
                        "limit": limit,
                        "pages": pages,
                        "provider": provider,
                        "source": result.get("source"),
                    }
                )

            return JSONResponse(
                {
                    "domain": domain,
                    "source_domain": domain,
                    "items": items,
                    "total": total,
                    "page": page,
                    "limit": limit,
                    "pages": pages,
                    "provider": provider,
                    "source": result.get("source"),
                }
            )
        except Exception:
            return await call_next(request)


def install_provider_filter_v5_middleware(app) -> None:
    if getattr(app.state, "flixyfy_provider_filter_v5_installed", False):
        return
    app.add_middleware(ProviderFilterV5Middleware)
    app.state.flixyfy_provider_filter_v5_installed = True
