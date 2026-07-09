# FLIXYFY_UI_FILTERS_FAST_RESPONSE_FIX_V1
# Fast v5 provider-filter middleware. No DB mutation. No DDL. SELECT-only.
from __future__ import annotations

import hashlib
import os
import time
from typing import Any, Dict, Iterable, List, Optional, Tuple

import psycopg2
from psycopg2.extras import RealDictCursor
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

DATABASE_URL = os.getenv("DATABASE_URL")

PROVIDER_ALIASES = {
    "": "all", "all": "all", "all_provider": "all", "all providers": "all",
    "youtube": "youtube", "yt": "youtube", "you tube": "youtube",
    "netflix": "netflix",
    "prime": "prime_video", "prime video": "prime_video", "primevideo": "prime_video", "prime_video": "prime_video",
    "amazon prime": "prime_video", "amazon prime video": "prime_video", "amazon_prime_video": "prime_video",
    "amazon_prime_video_with_ads": "prime_video",
    "jiohotstar": "jiohotstar", "jio hotstar": "jiohotstar", "hotstar": "jiohotstar", "disney hotstar": "jiohotstar",
    "zee5": "zee5", "zee 5": "zee5", "sonyliv": "sonyliv", "sony liv": "sonyliv", "aha": "aha",
    "sunnxt": "sun_nxt", "sun nxt": "sun_nxt", "sun_nxt": "sun_nxt",
    "etvwin": "etv_win", "etv win": "etv_win", "etv_win": "etv_win",
    "mxplayer": "mx_player", "mx player": "mx_player", "mx_player": "mx_player",
    "shemaroome": "shemaroome", "shemaroo me": "shemaroome", "eros now": "eros_now", "eros_now": "eros_now",
    "apple tv": "apple_tv_store", "apple_tv": "apple_tv_store", "apple tv store": "apple_tv_store", "apple_tv_store": "apple_tv_store",
    "amazon video": "amazon_video", "amazon_video": "amazon_video", "google tv": "google_tv", "google_tv": "google_tv",
    "disney+": "disney_plus", "disney_plus": "disney_plus", "hulu": "hulu", "max": "max", "viki": "viki",
    "rakuten viki": "viki", "kocowa": "kocowa", "tving": "tving", "wavve": "wavve", "watcha": "watcha",
    "coupang play": "coupang_play", "coupang_play": "coupang_play", "tubi": "tubi_tv", "tubi tv": "tubi_tv", "tubi_tv": "tubi_tv",
}

PROVIDER_LABELS = {
    "youtube": "YouTube", "netflix": "Netflix", "prime_video": "Prime Video", "jiohotstar": "JioHotstar",
    "zee5": "ZEE5", "sonyliv": "SonyLIV", "aha": "Aha", "sun_nxt": "Sun NXT", "etv_win": "ETV Win",
    "mx_player": "MX Player", "apple_tv_store": "Apple TV", "amazon_video": "Amazon Video", "google_tv": "Google TV",
    "disney_plus": "Disney+", "hulu": "Hulu", "max": "Max", "viki": "Rakuten Viki", "kocowa": "Kocowa",
    "tving": "TVING", "wavve": "Wavve", "watcha": "Watcha", "coupang_play": "Coupang Play", "tubi_tv": "Tubi",
}

DOMAIN_CONFIG = {
    "/api/v3/movies": {
        "domain": "current",
        "content": ["current_movie_serving_v5", "media_serving_v8_expanded", "media_serving_v7_final"],
        "availability": ["current_availability_serving_v5", "ott_availability_normalized_v2", "ott_availability_provider_links_v2"],
        "prefix": "/movie/",
    },
    "/api/v3/hollywood": {
        "domain": "hollywood",
        "content": ["hollywood_movie_serving_v5", "hollywood_serving_v3", "hollywood_card_serving_v3"],
        "availability": ["hollywood_availability_serving_v5", "hollywood_availability_v3", "hollywood_availability_serving_v1"],
        "prefix": "/hollywood/",
    },
    "/api/v3/historical": {
        "domain": "historical",
        "content": ["historical_movie_serving_v5", "historical_serving_v1", "historical_serving_v2", "historical_card_serving_v1"],
        "availability": ["historical_availability_serving_v5", "historical_availability_v2"],
        "prefix": "/historical/",
    },
    "/api/v3/webseries": {
        "domain": "webseries",
        "content": ["webseries_series_serving_v5", "webseries_serving_v1", "webseries_card_serving_v1"],
        "availability": ["webseries_availability_serving_v5", "webseries_availability_serving_v1", "webseries_availability_v1"],
        "prefix": "/webseries/",
    },
}

_SCHEMA_CACHE: Dict[str, Tuple[float, Dict[str, Any]]] = {}
_RESPONSE_CACHE: Dict[str, Tuple[float, Dict[str, Any]]] = {}
SCHEMA_TTL = 300
RESPONSE_TTL = 60


def qident(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def normalize_provider(value: Any) -> str:
    raw = str(value or "").strip().lower().replace("+", " plus ").replace("_", " ").replace("-", " ")
    raw = " ".join(raw.split())
    if raw in PROVIDER_ALIASES:
        return PROVIDER_ALIASES[raw]
    underscored = raw.replace(" ", "_")
    return PROVIDER_ALIASES.get(underscored, underscored or "all")


def provider_aliases(provider: str) -> List[str]:
    provider = normalize_provider(provider)
    aliases = {provider}
    for raw, canonical in PROVIDER_ALIASES.items():
        if canonical == provider and raw:
            aliases.add(raw)
            aliases.add(raw.replace(" ", "_"))
            aliases.add(raw.replace("_", " "))
    return sorted(aliases)


def conn():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL missing")
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)


def table_columns(cur, table: str) -> List[str]:
    now = time.time()
    key = f"cols:{table}"
    cached = _SCHEMA_CACHE.get(key)
    if cached and cached[0] > now:
        return list(cached[1].get("columns") or [])
    cur.execute(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema='public' AND table_name=%s
        ORDER BY ordinal_position
        """,
        [table],
    )
    cols = [r["column_name"] for r in cur.fetchall()]
    _SCHEMA_CACHE[key] = (now + SCHEMA_TTL, {"columns": cols})
    return cols


def first_existing_table(cur, names: Iterable[str]) -> Optional[Tuple[str, List[str]]]:
    for table in names:
        cols = table_columns(cur, table)
        if cols:
            return table, cols
    return None


def pick(cols: Iterable[str], candidates: Iterable[str]) -> Optional[str]:
    s = set(cols)
    for c in candidates:
        if c in s:
            return c
    return None


def build_join_condition(m_cols: List[str], a_cols: List[str]) -> str:
    pairs = [
        ("slug", "slug"), ("content_slug", "content_slug"), ("movie_slug", "movie_slug"), ("series_slug", "series_slug"),
        ("slug", "content_slug"), ("slug", "movie_slug"), ("slug", "series_slug"),
        ("tmdb_id", "tmdb_id"), ("imdb_id", "imdb_id"), ("id", "content_id"), ("content_id", "content_id"),
    ]
    for mc, ac in pairs:
        if mc in m_cols and ac in a_cols:
            return f"CAST(a.{qident(ac)} AS TEXT)=CAST(m.{qident(mc)} AS TEXT)"
    m_title = pick(m_cols, ["title", "name", "series_title", "original_title"])
    a_title = pick(a_cols, ["title", "name", "content_title", "movie_title", "series_title"])
    m_year = pick(m_cols, ["release_year", "year", "start_year"])
    a_year = pick(a_cols, ["release_year", "year", "content_year", "movie_year", "start_year"])
    if m_title and a_title and m_year and a_year:
        return (
            f"LOWER(TRIM(CAST(a.{qident(a_title)} AS TEXT)))=LOWER(TRIM(CAST(m.{qident(m_title)} AS TEXT))) "
            f"AND CAST(a.{qident(a_year)} AS TEXT)=CAST(m.{qident(m_year)} AS TEXT)"
        )
    if m_title and a_title:
        return f"LOWER(TRIM(CAST(a.{qident(a_title)} AS TEXT)))=LOWER(TRIM(CAST(m.{qident(m_title)} AS TEXT)))"
    return "1=0"


def provider_condition(a_cols: List[str], provider: str, params: List[Any]) -> str:
    provider = normalize_provider(provider)
    if provider in ("", "all"):
        return "1=1"
    aliases = provider_aliases(provider)
    candidate_cols = ["provider_key", "provider", "provider_name", "provider_display_name", "ott_primary_key", "ott_primary", "source", "source_name"]
    clauses = []
    for col in candidate_cols:
        if col in a_cols:
            clauses.append(f"LOWER(COALESCE(CAST(a.{qident(col)} AS TEXT),'')) = ANY(%s)")
            params.append(aliases)
    if provider == "youtube":
        for col in ["final_url", "watch_url", "youtube_url", "video_url", "url"]:
            if col in a_cols:
                clauses.append(f"LOWER(COALESCE(CAST(a.{qident(col)} AS TEXT),'')) LIKE %s")
                params.append("%youtube%")
    return "(" + " OR ".join(clauses) + ")" if clauses else "1=0"


def availability_condition(a_cols: List[str], availability: str, params: List[Any]) -> str:
    av = str(availability or "").strip().lower()
    if av in ("", "all"):
        return "1=1"
    if av in ("youtube", "free"):
        return provider_condition(a_cols, "youtube", params)
    if av in ("ott", "available", "streaming"):
        provider_cols = [c for c in ["provider_key", "provider", "provider_name", "provider_display_name"] if c in a_cols]
        if provider_cols:
            return "(" + " OR ".join([f"NULLIF(TRIM(CAST(a.{qident(c)} AS TEXT)),'') IS NOT NULL" for c in provider_cols]) + ")"
    return "1=1"


def content_filters(m_cols: List[str], qp: Any, params: List[Any]) -> str:
    clauses = []
    query = str(qp.get("q", "") or "").strip()
    if query:
        title_cols = [c for c in ["title", "original_title", "name", "series_title"] if c in m_cols]
        if title_cols:
            clauses.append("(" + " OR ".join([f"m.{qident(c)} ILIKE %s" for c in title_cols]) + ")")
            params.extend([f"%{query}%"] * len(title_cols))
    year = str(qp.get("year", "") or "").strip()
    if year:
        col = pick(m_cols, ["release_year", "year", "start_year"])
        if col:
            clauses.append(f"CAST(m.{qident(col)} AS TEXT)=%s")
            params.append(year)
    lang = str(qp.get("language", "") or "").strip()
    if lang:
        lang_norm = lang.lower().replace("_", "-")
        lang_cols = [c for c in ["language_slug", "primary_language_slug", "primary_language", "language"] if c in m_cols]
        if lang_cols:
            clauses.append("(" + " OR ".join([f"LOWER(REPLACE(CAST(m.{qident(c)} AS TEXT),'_','-'))=%s" for c in lang_cols]) + ")")
            params.extend([lang_norm] * len(lang_cols))
    return " AND ".join(clauses) if clauses else "1=1"


def order_sql(m_cols: List[str], sort: str) -> str:
    sort = str(sort or "popular").lower()
    if sort == "latest":
        cols = [c for c in ["release_date", "release_year", "year", "created_at"] if c in m_cols]
    elif sort in ("rating", "top", "imdb"):
        cols = [c for c in ["imdb_rating", "rating", "vote_average", "tmdb_rating", "flixyfy_score", "popularity"] if c in m_cols]
    else:
        cols = [c for c in ["flixyfy_score", "popularity", "vote_count", "imdb_rating", "rating", "release_year", "year"] if c in m_cols]
    return ", ".join([f"m.{qident(c)} DESC NULLS LAST" for c in cols]) if cols else "1"


def enrich_items(rows: List[Dict[str, Any]], domain: str, prefix: str, provider: str) -> List[Dict[str, Any]]:
    key = normalize_provider(provider)
    label = PROVIDER_LABELS.get(key)
    out = []
    for row in rows:
        item = dict(row)
        slug = item.get("slug") or item.get("movie_slug") or item.get("series_slug") or item.get("content_slug")
        if slug and not item.get("url_path"):
            item["url_path"] = f"{prefix}{slug}"
        if domain and not item.get("domain"):
            item["domain"] = domain
        if key and key != "all":
            item.setdefault("ott_primary_key", key)
            item.setdefault("ott_primary", label or key.replace("_", " ").title())
            item.setdefault("has_ott", True)
        out.append(item)
    return out


def cors_headers(request: Request) -> Dict[str, str]:
    origin = request.headers.get("origin") or "https://flixyfy.com"
    allowed = {
        "https://flixyfy.com", "https://www.flixyfy.com", "https://flixyfy-web.vercel.app",
        "http://localhost:5173", "http://127.0.0.1:5173", "http://localhost:3000", "http://127.0.0.1:3000",
    }
    if origin not in allowed:
        origin = "https://flixyfy.com"
    return {
        "Access-Control-Allow-Origin": origin,
        "Access-Control-Allow-Credentials": "true",
        "Access-Control-Allow-Headers": "*",
        "Access-Control-Allow-Methods": "*",
        "Vary": "Origin",
        "X-Flixyfy-Provider-Filter": "fast-v1",
    }


class ProviderFilterV5Middleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.method == "OPTIONS":
            return JSONResponse({"ok": True}, headers=cors_headers(request))
        if request.method != "GET":
            return await call_next(request)
        path = request.url.path.rstrip("/")
        config = DOMAIN_CONFIG.get(path)
        if not config:
            return await call_next(request)
        qp = request.query_params
        provider = normalize_provider(qp.get("provider") or qp.get("provider_key") or "")
        availability = str(qp.get("availability") or "").strip().lower()
        if provider in ("", "all") and availability in ("", "all"):
            return await call_next(request)
        cache_key = hashlib.sha256(f"{path}?{request.url.query}".encode("utf-8")).hexdigest()
        now = time.time()
        cached = _RESPONSE_CACHE.get(cache_key)
        if cached and cached[0] > now:
            return JSONResponse(cached[1], headers={**cors_headers(request), "X-Flixyfy-Cache": "HIT"})
        try:
            data = self._query(config, qp, provider, availability)
            _RESPONSE_CACHE[cache_key] = (now + RESPONSE_TTL, data)
            return JSONResponse(data, headers={**cors_headers(request), "X-Flixyfy-Cache": "MISS"})
        except Exception as exc:
            response = await call_next(request)
            response.headers.setdefault("X-Flixyfy-Provider-Filter-Fallback", type(exc).__name__)
            return response

    def _query(self, config: Dict[str, Any], qp: Any, provider: str, availability: str) -> Dict[str, Any]:
        page = max(1, int(qp.get("page") or 1))
        limit = max(1, min(100, int(qp.get("limit") or 24)))
        offset = (page - 1) * limit
        with conn() as c:
            with c.cursor(cursor_factory=RealDictCursor) as cur:
                content_pick = first_existing_table(cur, config["content"])
                availability_pick = first_existing_table(cur, config["availability"])
                if not content_pick or not availability_pick:
                    raise RuntimeError("v5 content/availability table missing")
                content_table, m_cols = content_pick
                availability_table, a_cols = availability_pick
                join = build_join_condition(m_cols, a_cols)
                exists_params: List[Any] = []
                provider_sql = provider_condition(a_cols, provider, exists_params)
                availability_sql = availability_condition(a_cols, availability, exists_params)
                exists_sql = (
                    f"EXISTS (SELECT 1 FROM public.{qident(availability_table)} a "
                    f"WHERE {join} AND {provider_sql} AND {availability_sql})"
                )
                where_params: List[Any] = []
                content_sql = content_filters(m_cols, qp, where_params)
                where_sql = f"WHERE {content_sql} AND {exists_sql}"
                final_params = where_params + exists_params
                cur.execute(f"SELECT COUNT(*) AS total FROM public.{qident(content_table)} m {where_sql}", final_params)
                total = int((cur.fetchone() or {}).get("total") or 0)
                cur.execute(
                    f"""
                    SELECT m.*
                    FROM public.{qident(content_table)} m
                    {where_sql}
                    ORDER BY {order_sql(m_cols, qp.get("sort") or "popular")}
                    LIMIT %s OFFSET %s
                    """,
                    final_params + [limit, offset],
                )
                rows = [dict(r) for r in cur.fetchall()]
        return {
            "page": page,
            "limit": limit,
            "total": total,
            "items": enrich_items(rows, config["domain"], config["prefix"], provider),
            "source": "provider_filter_v5_middleware_fast_v1",
            "domain": config["domain"],
            "provider": provider,
            "availability": availability or "",
        }

# Backward compatibility for older main_v3.py import/call path.
# main_v3.py may import install_provider_filter_v5_middleware from older builds.
# The middleware is already installed directly before CORS by the V1 fix.
def install_provider_filter_v5_middleware(app):
    return app
