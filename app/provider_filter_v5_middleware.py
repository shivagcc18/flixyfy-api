# FLIXYFY_UI_DOMAIN_FILTERS_FINAL_FIX_V2
from __future__ import annotations

import hashlib
import os
import re
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
SCHEMA_TTL_SECONDS = 300
RESPONSE_TTL_SECONDS = 60
_SCHEMA_CACHE: Dict[str, Tuple[float, Any]] = {}
_RESPONSE_CACHE: Dict[str, Tuple[float, Dict[str, Any]]] = {}

PROVIDER_ALIASES = {
    "": "all", "all": "all", "all_provider": "all", "all providers": "all", "all_providers": "all",
    "youtube": "youtube", "yt": "youtube", "you tube": "youtube",
    "netflix": "netflix", "netflix standard with ads": "netflix", "netflix basic with ads": "netflix",
    "prime": "prime_video", "prime video": "prime_video", "primevideo": "prime_video", "prime_video": "prime_video", "amazon prime": "prime_video", "amazon prime video": "prime_video", "amazon_prime_video": "prime_video", "amazon_prime_video_with_ads": "prime_video", "prime video with ads": "prime_video",
    "jiohotstar": "jiohotstar", "jio hotstar": "jiohotstar", "hotstar": "jiohotstar", "disney hotstar": "jiohotstar", "disney+ hotstar": "jiohotstar",
    "zee5": "zee5", "zee 5": "zee5",
    "sonyliv": "sonyliv", "sony liv": "sonyliv",
    "aha": "aha",
    "sunnxt": "sun_nxt", "sun nxt": "sun_nxt", "sun_nxt": "sun_nxt",
    "etvwin": "etv_win", "etv win": "etv_win", "etv_win": "etv_win",
    "mxplayer": "mx_player", "mx player": "mx_player", "mx_player": "mx_player", "amazon mx player": "mx_player", "amazon_mx_player": "mx_player",
    "shemaroome": "shemaroome", "shemaroo me": "shemaroome",
    "eros now": "eros_now", "eros_now": "eros_now",
    "apple tv": "apple_tv_store", "apple_tv": "apple_tv_store", "apple tv store": "apple_tv_store", "apple_tv_store": "apple_tv_store", "itunes": "apple_tv_store",
    "amazon video": "amazon_video", "amazon_video": "amazon_video",
    "google tv": "google_tv", "google_tv": "google_tv", "google play": "google_tv",
    "disney+": "disney_plus", "disney plus": "disney_plus", "disney_plus": "disney_plus",
    "hulu": "hulu", "max": "max", "hbo max": "max", "hbo_max": "max", "plex": "plex",
    "viki": "viki", "rakuten viki": "viki", "kocowa": "kocowa", "tving": "tving", "wavve": "wavve", "watcha": "watcha", "coupang play": "coupang_play", "coupang_play": "coupang_play", "tubi": "tubi_tv", "tubi tv": "tubi_tv", "tubi_tv": "tubi_tv",
}

PROVIDER_LABELS = {
    "youtube": "YouTube", "netflix": "Netflix", "prime_video": "Prime Video", "jiohotstar": "JioHotstar", "zee5": "ZEE5", "sonyliv": "SonyLIV", "aha": "Aha", "sun_nxt": "Sun NXT", "etv_win": "ETV Win", "mx_player": "MX Player", "shemaroome": "ShemarooMe", "eros_now": "Eros Now", "apple_tv_store": "Apple TV", "amazon_video": "Amazon Video", "google_tv": "Google TV", "disney_plus": "Disney+", "hulu": "Hulu", "max": "Max", "plex": "Plex", "viki": "Rakuten Viki", "kocowa": "Kocowa", "tving": "TVING", "wavve": "Wavve", "watcha": "Watcha", "coupang_play": "Coupang Play", "tubi_tv": "Tubi"
}

COMMON_AVAILABILITY = ["provider_availability_serving_v2", "provider_availability_serving_v1"]
DOMAIN_CONFIG_BY_PATH = {
    "/api/v3/movies": {"domain": "current", "label": "Indian Movies", "prefix": "/movie/", "content": ["current_movie_serving_v5_backend_compat", "current_movie_serving_v5", "media_serving_v8_expanded", "media_serving_v7_final"], "availability": ["current_availability_serving_v5", "ott_availability_normalized_v2", "ott_availability_normalized_v1"] + COMMON_AVAILABILITY},
    "/api/v3/indian": {"domain": "current", "label": "Indian Movies", "prefix": "/movie/", "content": ["current_movie_serving_v5_backend_compat", "current_movie_serving_v5", "media_serving_v8_expanded", "media_serving_v7_final"], "availability": ["current_availability_serving_v5", "ott_availability_normalized_v2", "ott_availability_normalized_v1"] + COMMON_AVAILABILITY},
    "/api/v3/hollywood": {"domain": "hollywood", "label": "Global Movies", "prefix": "/hollywood/", "content": ["hollywood_movie_serving_v5", "hollywood_card_serving_v3", "hollywood_serving_v3", "hollywood_movie_serving_v1"], "availability": ["hollywood_availability_serving_v5", "hollywood_availability_serving_v3", "hollywood_availability_serving_v1"] + COMMON_AVAILABILITY},
    "/api/v3/global": {"domain": "hollywood", "label": "Global Movies", "prefix": "/hollywood/", "content": ["hollywood_movie_serving_v5", "hollywood_card_serving_v3", "hollywood_serving_v3", "hollywood_movie_serving_v1"], "availability": ["hollywood_availability_serving_v5", "hollywood_availability_serving_v3", "hollywood_availability_serving_v1"] + COMMON_AVAILABILITY},
    "/api/v3/historical": {"domain": "historical", "label": "Historical Movies", "prefix": "/historical/", "content": ["historical_movie_serving_v5", "historical_card_serving_v1", "historical_serving_v2", "historical_serving_v1"], "availability": ["historical_availability_serving_v5", "historical_availability_serving_v2", "historical_availability_v2"] + COMMON_AVAILABILITY},
    "/api/v3/webseries": {"domain": "webseries", "label": "Webseries", "prefix": "/webseries/", "content": ["webseries_series_serving_v5", "webseries_serving_v5", "webseries_card_serving_v1", "webseries_serving_v1"], "availability": ["webseries_availability_serving_v5", "webseries_availability_serving_v2", "webseries_availability_serving_v1", "webseries_availability_v1"] + COMMON_AVAILABILITY},
    "/api/v3/web-series": {"domain": "webseries", "label": "Webseries", "prefix": "/webseries/", "content": ["webseries_series_serving_v5", "webseries_serving_v5", "webseries_card_serving_v1", "webseries_serving_v1"], "availability": ["webseries_availability_serving_v5", "webseries_availability_serving_v2", "webseries_availability_serving_v1", "webseries_availability_v1"] + COMMON_AVAILABILITY},
    "/api/v3/series": {"domain": "webseries", "label": "Webseries", "prefix": "/webseries/", "content": ["webseries_series_serving_v5", "webseries_serving_v5", "webseries_card_serving_v1", "webseries_serving_v1"], "availability": ["webseries_availability_serving_v5", "webseries_availability_serving_v2", "webseries_availability_serving_v1", "webseries_availability_v1"] + COMMON_AVAILABILITY},
}

def qident(value: str) -> str:
    return '"' + str(value).replace('"', '""') + '"'

def normalize_provider(value: Any) -> str:
    raw = str(value or "").strip().lower().replace("+", " plus ")
    raw = re.sub(r"[_\-]+", " ", raw)
    raw = re.sub(r"\s+", " ", raw).strip()
    if raw in PROVIDER_ALIASES:
        return PROVIDER_ALIASES[raw]
    underscored = raw.replace(" ", "_")
    return PROVIDER_ALIASES.get(underscored, underscored or "all")

def provider_aliases(provider: str) -> List[str]:
    canonical = normalize_provider(provider)
    aliases = {canonical, canonical.replace("_", " ")}
    for raw, mapped in PROVIDER_ALIASES.items():
        if mapped == canonical and raw:
            aliases.add(raw)
            aliases.add(raw.replace("_", " "))
            aliases.add(raw.replace(" ", "_"))
    return sorted(a.lower() for a in aliases if a)

def provider_needles(provider: str) -> List[str]:
    canonical = normalize_provider(provider)
    base = set(provider_aliases(canonical))
    broad = {
        "netflix": ["netflix"], "prime_video": ["prime", "amazon prime", "amazon_prime"], "jiohotstar": ["hotstar", "jiohotstar"], "zee5": ["zee5", "zee 5"], "sonyliv": ["sony", "sonyliv", "sony liv"], "youtube": ["youtube", "youtu.be"], "sun_nxt": ["sun nxt", "sunnxt"], "etv_win": ["etv win", "etvwin"], "mx_player": ["mx player", "mxplayer"], "apple_tv_store": ["apple", "itunes"], "amazon_video": ["amazon video"], "google_tv": ["google", "google tv"], "disney_plus": ["disney"], "max": ["max", "hbo"], "viki": ["viki"], "kocowa": ["kocowa"], "tving": ["tving"], "wavve": ["wavve"], "watcha": ["watcha"], "coupang_play": ["coupang"], "tubi_tv": ["tubi"],
    }
    for item in broad.get(canonical, []):
        base.add(item)
    return sorted(base)

def connect_db():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL missing")
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

def cached(key: str):
    item = _SCHEMA_CACHE.get(key)
    if item and item[0] > time.time():
        return item[1]
    return None

def set_cached(key: str, value: Any):
    _SCHEMA_CACHE[key] = (time.time() + SCHEMA_TTL_SECONDS, value)
    return value

def table_columns(cur, table: str) -> List[str]:
    key = f"cols:{table}"
    hit = cached(key)
    if hit is not None:
        return hit
    cur.execute("SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name=%s ORDER BY ordinal_position", [table])
    return set_cached(key, [r["column_name"] for r in cur.fetchall()])

def first_table(cur, names: Iterable[str]) -> Optional[Tuple[str, List[str]]]:
    for name in names:
        cols = table_columns(cur, name)
        if cols:
            return name, cols
    return None

def pick(cols: Iterable[str], candidates: Iterable[str]) -> Optional[str]:
    present = set(cols)
    for c in candidates:
        if c in present:
            return c
    return None

def expr(alias: str, col: str) -> str:
    return f"LOWER(COALESCE(CAST({alias}.{qident(col)} AS TEXT),''))"

def norm_expr(alias: str, col: str) -> str:
    return f"LOWER(regexp_replace(COALESCE(CAST({alias}.{qident(col)} AS TEXT),''), '[^a-zA-Z0-9]+', '_', 'g'))"

def domain_guard(a_cols: List[str], domain: str) -> str:
    for col in ["domain", "content_domain", "media_domain", "source_domain"]:
        if col in a_cols:
            values = {"current": ["current", "indian", "movie", "movies"], "hollywood": ["hollywood", "global", "global_movie", "global_movies"], "historical": ["historical", "historical_movie", "historical_movies"], "webseries": ["webseries", "web_series", "series", "tv"]}.get(domain, [domain])
            quoted = ",".join("'" + v.replace("'", "''") + "'" for v in values)
            return f" AND LOWER(CAST(a.{qident(col)} AS TEXT)) IN ({quoted})"
    return ""

def build_join(m_cols: List[str], a_cols: List[str], domain: str) -> str:
    for m_col, a_col in [("slug", "slug"), ("slug", "content_slug"), ("slug", "movie_slug"), ("slug", "series_slug"), ("content_slug", "content_slug"), ("movie_slug", "movie_slug"), ("series_slug", "series_slug"), ("tmdb_id", "tmdb_id"), ("imdb_id", "imdb_id"), ("id", "content_id"), ("content_id", "content_id"), ("tmdb_id", "content_tmdb_id"), ("imdb_id", "content_imdb_id")]:
        if m_col in m_cols and a_col in a_cols:
            return f"NULLIF(CAST(m.{qident(m_col)} AS TEXT),'') IS NOT NULL AND CAST(a.{qident(a_col)} AS TEXT)=CAST(m.{qident(m_col)} AS TEXT){domain_guard(a_cols, domain)}"
    m_title = pick(m_cols, ["title", "name", "series_title", "original_title", "movie_title"])
    a_title = pick(a_cols, ["title", "name", "content_title", "movie_title", "series_title", "original_title"])
    m_year = pick(m_cols, ["release_year", "year", "start_year", "first_air_year"])
    a_year = pick(a_cols, ["release_year", "year", "content_year", "movie_year", "start_year", "first_air_year"])
    if m_title and a_title and m_year and a_year:
        return f"LOWER(TRIM(CAST(a.{qident(a_title)} AS TEXT)))=LOWER(TRIM(CAST(m.{qident(m_title)} AS TEXT))) AND CAST(a.{qident(a_year)} AS TEXT)=CAST(m.{qident(m_year)} AS TEXT){domain_guard(a_cols, domain)}"
    if m_title and a_title:
        return f"LOWER(TRIM(CAST(a.{qident(a_title)} AS TEXT)))=LOWER(TRIM(CAST(m.{qident(m_title)} AS TEXT))){domain_guard(a_cols, domain)}"
    return "1=0"

def provider_condition(a_cols: List[str], provider: str, params: List[Any]) -> str:
    canonical = normalize_provider(provider)
    if canonical in ("", "all"):
        return "1=1"
    aliases = provider_aliases(canonical)
    needles = provider_needles(canonical)
    cols = ["provider_key", "provider", "provider_name", "provider_display_name", "provider_label", "ott_primary_key", "ott_primary", "platform", "platform_name", "source", "source_name", "watch_provider", "watch_provider_name", "provider_slug", "provider_code"]
    clauses: List[str] = []
    for col in cols:
        if col not in a_cols:
            continue
        clauses.append(f"{expr('a', col)} = ANY(%s)")
        params.append(aliases)
        clauses.append(f"{norm_expr('a', col)} = ANY(%s)")
        params.append([a.replace(" ", "_") for a in aliases])
        for needle in needles:
            clauses.append(f"{expr('a', col)} LIKE %s")
            params.append("%" + needle.replace("_", " ") + "%")
            clauses.append(f"{norm_expr('a', col)} LIKE %s")
            params.append("%" + re.sub(r"[^a-z0-9]+", "_", needle.lower()).strip("_") + "%")
    if canonical == "youtube":
        for col in ["final_url", "watch_url", "youtube_url", "video_url", "url", "deep_link"]:
            if col in a_cols:
                clauses.append(f"{expr('a', col)} LIKE %s")
                params.append("%youtube%")
                clauses.append(f"{expr('a', col)} LIKE %s")
                params.append("%youtu.be%")
    return "(" + " OR ".join(clauses) + ")" if clauses else "1=0"

def availability_condition(a_cols: List[str], availability: str, params: List[Any]) -> str:
    value = str(availability or "").strip().lower()
    if value in ("", "all", "all_titles"):
        return "1=1"
    if value in ("free", "youtube"):
        return provider_condition(a_cols, "youtube", params)
    if value in ("ott", "streaming", "available", "true", "1"):
        cols = [c for c in ["provider_key", "provider", "provider_name", "provider_display_name", "platform"] if c in a_cols]
        if cols:
            return "(" + " OR ".join([f"NULLIF(TRIM(CAST(a.{qident(c)} AS TEXT)),'') IS NOT NULL" for c in cols]) + ")"
    return "1=1"

def content_filter(m_cols: List[str], qp: Any, params: List[Any]) -> str:
    clauses: List[str] = []
    q = str(qp.get("q") or "").strip()
    if q:
        cols = [c for c in ["title", "name", "series_title", "original_title", "movie_title"] if c in m_cols]
        if cols:
            clauses.append("(" + " OR ".join([f"m.{qident(c)} ILIKE %s" for c in cols]) + ")")
            params.extend(["%" + q + "%"] * len(cols))
    year = str(qp.get("year") or "").strip()
    if year:
        col = pick(m_cols, ["release_year", "year", "start_year", "first_air_year"])
        if col:
            clauses.append(f"CAST(m.{qident(col)} AS TEXT)=%s")
            params.append(year)
    lang = str(qp.get("language") or qp.get("language_slug") or "").strip().lower().replace("_", "-")
    if lang:
        cols = [c for c in ["language_slug", "primary_language_slug", "primary_language", "language", "original_language"] if c in m_cols]
        if cols:
            clauses.append("(" + " OR ".join([f"LOWER(REPLACE(CAST(m.{qident(c)} AS TEXT),'_','-'))=%s" for c in cols]) + ")")
            params.extend([lang] * len(cols))
    return " AND ".join(clauses) if clauses else "1=1"

def order_sql(m_cols: List[str], sort: str) -> str:
    value = str(sort or "popular").strip().lower()
    candidates = ["release_date", "release_year", "year", "start_year", "created_at"] if value == "latest" else ["imdb_rating", "rating", "vote_average", "tmdb_rating", "flixyfy_score", "popularity"] if value in ("rating", "top", "imdb") else ["flixyfy_score", "popularity", "vote_count", "imdb_rating", "rating", "release_year", "year", "start_year"]
    cols = [c for c in candidates if c in m_cols]
    return ", ".join([f"m.{qident(c)} DESC NULLS LAST" for c in cols]) if cols else "1"

def enrich(rows: List[Dict[str, Any]], config: Dict[str, Any], provider: str) -> List[Dict[str, Any]]:
    canonical = normalize_provider(provider)
    label = PROVIDER_LABELS.get(canonical)
    out = []
    for row in rows:
        item = dict(row)
        slug = item.get("slug") or item.get("content_slug") or item.get("movie_slug") or item.get("series_slug")
        if slug and not item.get("url_path"):
            item["url_path"] = f"{config['prefix']}{slug}"
        item.setdefault("domain", config["domain"])
        if canonical and canonical != "all":
            item["ott_primary_key"] = canonical
            item["ott_primary"] = label or canonical.replace("_", " ").title()
            item["has_ott"] = True
        out.append(item)
    return out

def cors_headers(request: Request) -> Dict[str, str]:
    origin = request.headers.get("origin") or "https://flixyfy.com"
    allowed = {"https://flixyfy.com", "https://www.flixyfy.com", "https://flixyfy-web.vercel.app", "http://localhost:5173", "http://127.0.0.1:5173", "http://localhost:3000", "http://127.0.0.1:3000"}
    if origin not in allowed:
        origin = "https://flixyfy.com"
    return {"Access-Control-Allow-Origin": origin, "Access-Control-Allow-Credentials": "true", "Access-Control-Allow-Headers": "*", "Access-Control-Allow-Methods": "*", "Vary": "Origin", "X-Flixyfy-Provider-Filter": "fast-v2"}

class ProviderFilterV5Middleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.method == "OPTIONS":
            return JSONResponse({"ok": True}, headers=cors_headers(request))
        if request.method != "GET":
            return await call_next(request)
        path = request.url.path.rstrip("/")
        config = DOMAIN_CONFIG_BY_PATH.get(path)
        if not config:
            return await call_next(request)
        provider = normalize_provider(request.query_params.get("provider") or request.query_params.get("provider_key") or "")
        availability = str(request.query_params.get("availability") or request.query_params.get("has_ott") or "").strip().lower()
        if provider in ("", "all") and availability in ("", "all", "all_titles"):
            return await call_next(request)
        key = hashlib.sha256((path + "?" + request.url.query).encode("utf-8")).hexdigest()
        item = _RESPONSE_CACHE.get(key)
        if item and item[0] > time.time():
            return JSONResponse(item[1], headers={**cors_headers(request), "X-Flixyfy-Cache": "HIT"})
        try:
            payload = self.query_payload(config, request.query_params, provider, availability)
            _RESPONSE_CACHE[key] = (time.time() + RESPONSE_TTL_SECONDS, payload)
            return JSONResponse(payload, headers={**cors_headers(request), "X-Flixyfy-Cache": "MISS"})
        except Exception as exc:
            response = await call_next(request)
            response.headers.setdefault("X-Flixyfy-Provider-Filter-Fallback", type(exc).__name__)
            return response

    def query_payload(self, config: Dict[str, Any], qp: Any, provider: str, availability: str) -> Dict[str, Any]:
        page = max(1, int(qp.get("page") or 1))
        limit = max(1, min(100, int(qp.get("limit") or 24)))
        offset = (page - 1) * limit
        with connect_db() as db:
            with db.cursor(cursor_factory=RealDictCursor) as cur:
                content_pick = first_table(cur, config["content"])
                availability_pick = first_table(cur, config["availability"])
                if not content_pick or not availability_pick:
                    raise RuntimeError("missing content/availability table")
                content_table, m_cols = content_pick
                availability_table, a_cols = availability_pick
                join_sql = build_join(m_cols, a_cols, config["domain"])
                content_params: List[Any] = []
                content_sql = content_filter(m_cols, qp, content_params)
                exists_params: List[Any] = []
                provider_sql = provider_condition(a_cols, provider, exists_params)
                avail_sql = availability_condition(a_cols, availability, exists_params)
                where_sql = f"WHERE {content_sql} AND EXISTS (SELECT 1 FROM public.{qident(availability_table)} a WHERE {join_sql} AND {provider_sql} AND {avail_sql})"
                full_params = content_params + exists_params
                cur.execute(f"SELECT COUNT(*) AS total FROM public.{qident(content_table)} m {where_sql}", full_params)
                total = int((cur.fetchone() or {}).get("total") or 0)
                cur.execute(f"SELECT m.* FROM public.{qident(content_table)} m {where_sql} ORDER BY {order_sql(m_cols, qp.get('sort') or 'popular')} LIMIT %s OFFSET %s", full_params + [limit, offset])
                rows = [dict(r) for r in cur.fetchall()]
        return {"page": page, "limit": limit, "total": total, "items": enrich(rows, config, provider), "domain": config["domain"], "label": config["label"], "provider": normalize_provider(provider), "availability": availability, "source": "provider_filter_v5_middleware_fast_v2"}

def install_provider_filter_v5_middleware(app):
    return app
