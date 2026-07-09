# FLIXYFY_DOMAIN_PROVIDER_FILTERS_V3_ADAPTIVE
# Adaptive provider filter middleware for Indian, Historical, Hollywood/Global, and Webseries.
# No DB mutation. No DDL. SELECT-only.

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
SCHEMA_TTL = 600
PLAN_TTL = 600
RESPONSE_TTL = 60

_SCHEMA_CACHE: Dict[str, Tuple[float, Any]] = {}
_PLAN_CACHE: Dict[str, Tuple[float, Any]] = {}
_RESPONSE_CACHE: Dict[str, Tuple[float, Dict[str, Any]]] = {}

PROVIDER_ALIASES = {
    "": "all", "all": "all", "all provider": "all", "all providers": "all", "all_provider": "all", "all_providers": "all",
    "youtube": "youtube", "yt": "youtube", "you tube": "youtube",
    "netflix": "netflix",
    "prime": "prime_video", "prime video": "prime_video", "primevideo": "prime_video", "prime_video": "prime_video", "amazon prime": "prime_video", "amazon prime video": "prime_video", "amazon_prime_video": "prime_video", "amazon_prime_video_with_ads": "prime_video",
    "jiohotstar": "jiohotstar", "jio hotstar": "jiohotstar", "hotstar": "jiohotstar", "disney hotstar": "jiohotstar", "disney+ hotstar": "jiohotstar",
    "zee5": "zee5", "zee 5": "zee5",
    "sonyliv": "sonyliv", "sony liv": "sonyliv",
    "aha": "aha",
    "sunnxt": "sun_nxt", "sun nxt": "sun_nxt", "sun_nxt": "sun_nxt",
    "etvwin": "etv_win", "etv win": "etv_win", "etv_win": "etv_win",
    "mxplayer": "mx_player", "mx player": "mx_player", "mx_player": "mx_player", "amazon mx player": "mx_player", "amazon_mx_player": "mx_player",
    "apple tv": "apple_tv_store", "apple tv store": "apple_tv_store", "apple_tv": "apple_tv_store", "apple_tv_store": "apple_tv_store", "itunes": "apple_tv_store",
    "amazon video": "amazon_video", "amazon_video": "amazon_video",
    "google tv": "google_tv", "google_tv": "google_tv", "google play": "google_tv",
    "disney+": "disney_plus", "disney plus": "disney_plus", "disney_plus": "disney_plus",
    "hulu": "hulu", "max": "max", "hbo max": "max", "hbo_max": "max", "plex": "plex", "viki": "viki", "rakuten viki": "viki", "kocowa": "kocowa", "tving": "tving", "wavve": "wavve", "watcha": "watcha", "coupang play": "coupang_play", "coupang_play": "coupang_play", "tubi": "tubi_tv", "tubi tv": "tubi_tv", "tubi_tv": "tubi_tv",
}

PROVIDER_LABELS = {
    "youtube": "YouTube", "netflix": "Netflix", "prime_video": "Prime Video", "jiohotstar": "JioHotstar", "zee5": "ZEE5", "sonyliv": "SonyLIV", "aha": "Aha", "sun_nxt": "Sun NXT", "etv_win": "ETV Win", "mx_player": "MX Player", "apple_tv_store": "Apple TV", "amazon_video": "Amazon Video", "google_tv": "Google TV", "disney_plus": "Disney+", "hulu": "Hulu", "max": "Max", "plex": "Plex", "viki": "Rakuten Viki", "kocowa": "Kocowa", "tving": "TVING", "wavve": "Wavve", "watcha": "Watcha", "coupang_play": "Coupang Play", "tubi_tv": "Tubi",
}

CURRENT_CONTENT = ["current_movie_serving_v5_backend_compat", "current_movie_serving_v5", "media_serving_v8_expanded", "media_serving_v7_final"]
CURRENT_AVAIL = ["current_availability_serving_v5", "provider_availability_serving_v2", "provider_availability_serving_v1", "ott_availability_normalized_v2", "ott_availability_normalized_v1"]
GLOBAL_CONTENT = ["hollywood_movie_serving_v5", "hollywood_movie_serving_v5_backend_compat", "hollywood_card_serving_v3", "hollywood_serving_v3", "hollywood_movie_serving_v1"]
GLOBAL_AVAIL = ["hollywood_availability_serving_v5", "hollywood_availability_serving_v3", "hollywood_availability_serving_v2", "hollywood_availability_serving_v1", "provider_availability_serving_v2", "provider_availability_serving_v1"]
HIST_CONTENT = ["historical_movie_serving_v5", "historical_movie_serving_v5_backend_compat", "historical_card_serving_v1", "historical_serving_v2", "historical_serving_v1"]
HIST_AVAIL = ["historical_availability_serving_v5", "historical_availability_serving_v3", "historical_availability_serving_v2", "historical_availability_v2", "provider_availability_serving_v2", "provider_availability_serving_v1"]
WEB_CONTENT = ["webseries_series_serving_v5", "webseries_serving_v5", "webseries_card_serving_v1", "webseries_serving_v1"]
WEB_AVAIL = ["webseries_availability_serving_v5", "webseries_availability_serving_v3", "webseries_availability_serving_v2", "webseries_availability_serving_v1", "webseries_availability_v1", "provider_availability_serving_v2", "provider_availability_serving_v1"]

DOMAIN_CONFIG = {
    "/api/v3/movies": {"domain": "current", "label": "Indian Movies", "prefix": "/movie/", "content": CURRENT_CONTENT, "availability": CURRENT_AVAIL},
    "/api/v3/indian": {"domain": "current", "label": "Indian Movies", "prefix": "/movie/", "content": CURRENT_CONTENT, "availability": CURRENT_AVAIL},
    "/api/v3/hollywood": {"domain": "hollywood", "label": "Global Movies", "prefix": "/hollywood/", "content": GLOBAL_CONTENT, "availability": GLOBAL_AVAIL},
    "/api/v3/global": {"domain": "hollywood", "label": "Global Movies", "prefix": "/hollywood/", "content": GLOBAL_CONTENT, "availability": GLOBAL_AVAIL},
    "/api/v3/historical": {"domain": "historical", "label": "Historical Movies", "prefix": "/historical/", "content": HIST_CONTENT, "availability": HIST_AVAIL},
    "/api/v3/webseries": {"domain": "webseries", "label": "Webseries", "prefix": "/webseries/", "content": WEB_CONTENT, "availability": WEB_AVAIL},
    "/api/v3/web-series": {"domain": "webseries", "label": "Webseries", "prefix": "/webseries/", "content": WEB_CONTENT, "availability": WEB_AVAIL},
    "/api/v3/series": {"domain": "webseries", "label": "Webseries", "prefix": "/webseries/", "content": WEB_CONTENT, "availability": WEB_AVAIL},
}


def qident(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def norm_provider(value: Any) -> str:
    raw = str(value or "").strip().lower().replace("+", " plus ")
    raw = re.sub(r"[_\-]+", " ", raw)
    raw = re.sub(r"\s+", " ", raw).strip()
    if raw in PROVIDER_ALIASES:
        return PROVIDER_ALIASES[raw]
    key = raw.replace(" ", "_")
    return PROVIDER_ALIASES.get(key, key or "all")


def provider_needles(provider: str) -> List[str]:
    p = norm_provider(provider)
    values = {p, p.replace("_", " ")}
    for raw, mapped in PROVIDER_ALIASES.items():
        if mapped == p and raw:
            values.add(raw)
            values.add(raw.replace("_", " "))
            values.add(raw.replace(" ", "_"))
    broad = {
        "netflix": ["netflix"], "prime_video": ["prime", "amazon prime", "amazon_prime"], "jiohotstar": ["hotstar", "jiohotstar"], "zee5": ["zee5", "zee 5"], "sonyliv": ["sony", "sonyliv", "sony liv"], "youtube": ["youtube", "youtu.be"], "sun_nxt": ["sun nxt", "sunnxt", "sun_nxt"], "etv_win": ["etv win", "etvwin", "etv_win"], "mx_player": ["mx player", "mxplayer", "mx_player"], "apple_tv_store": ["apple", "itunes"], "amazon_video": ["amazon video"], "google_tv": ["google", "google tv"], "disney_plus": ["disney"], "max": ["max", "hbo"], "viki": ["viki"], "kocowa": ["kocowa"], "tving": ["tving"], "wavve": ["wavve"], "watcha": ["watcha"], "coupang_play": ["coupang"], "tubi_tv": ["tubi"],
    }
    values.update(broad.get(p, []))
    return sorted(v.lower() for v in values if v)


def slug_text_sql(alias: str, col: str) -> str:
    return f"LOWER(regexp_replace(COALESCE(CAST({alias}.{qident(col)} AS TEXT),''), '[^a-zA-Z0-9]+', '_', 'g'))"


def lower_text_sql(alias: str, col: str) -> str:
    return f"LOWER(COALESCE(CAST({alias}.{qident(col)} AS TEXT),''))"


def connect():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL missing")
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)


def cache_get(cache: Dict[str, Tuple[float, Any]], key: str):
    item = cache.get(key)
    if item and item[0] > time.time():
        return item[1]
    return None


def cache_set(cache: Dict[str, Tuple[float, Any]], key: str, value: Any, ttl: int):
    cache[key] = (time.time() + ttl, value)
    return value


def table_columns(cur, table: str) -> List[str]:
    key = f"cols:{table}"
    hit = cache_get(_SCHEMA_CACHE, key)
    if hit is not None:
        return hit
    cur.execute("SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name=%s ORDER BY ordinal_position", [table])
    return cache_set(_SCHEMA_CACHE, key, [r["column_name"] for r in cur.fetchall()], SCHEMA_TTL)


def table_count(cur, table: str) -> int:
    key = f"count:{table}"
    hit = cache_get(_SCHEMA_CACHE, key)
    if hit is not None:
        return int(hit)
    cur.execute(f"SELECT COUNT(*) AS c FROM public.{qident(table)}")
    return int(cache_set(_SCHEMA_CACHE, key, int((cur.fetchone() or {}).get("c") or 0), SCHEMA_TTL))


def existing_tables(cur, names: Iterable[str]) -> List[Tuple[str, List[str], int]]:
    out = []
    for name in names:
        cols = table_columns(cur, name)
        if cols:
            out.append((name, cols, table_count(cur, name)))
    return out


def pick(cols: Iterable[str], candidates: Iterable[str]) -> Optional[str]:
    present = set(cols)
    for c in candidates:
        if c in present:
            return c
    return None


def provider_where(a_cols: List[str], provider: str, params: List[Any]) -> str:
    p = norm_provider(provider)
    if p in ("", "all"):
        return "1=1"
    cols = ["provider_key", "provider", "provider_name", "provider_display_name", "provider_label", "ott_primary_key", "ott_primary", "platform", "platform_name", "source", "source_name", "watch_provider", "watch_provider_name", "provider_slug", "provider_code"]
    clauses = []
    needles = provider_needles(p)
    for col in cols:
        if col not in a_cols:
            continue
        for needle in needles:
            clauses.append(f"{lower_text_sql('a', col)} LIKE %s")
            params.append("%" + needle.replace("_", " ") + "%")
            clauses.append(f"{slug_text_sql('a', col)} LIKE %s")
            params.append("%" + re.sub(r"[^a-z0-9]+", "_", needle.lower()).strip("_") + "%")
    if p == "youtube":
        for col in ["final_url", "watch_url", "youtube_url", "video_url", "url", "deep_link"]:
            if col in a_cols:
                clauses.append(f"{lower_text_sql('a', col)} LIKE %s")
                params.append("%youtube%")
                clauses.append(f"{lower_text_sql('a', col)} LIKE %s")
                params.append("%youtu.be%")
    return "(" + " OR ".join(clauses) + ")" if clauses else "1=0"


def availability_where(a_cols: List[str], availability: str, params: List[Any]) -> str:
    v = str(availability or "").strip().lower()
    if v in ("", "all", "all_titles", "all movies"):
        return "1=1"
    if v in ("free", "free_to_watch", "youtube"):
        return provider_where(a_cols, "youtube", params)
    if v in ("ott", "streaming", "available"):
        usable = [c for c in ["provider_key", "provider", "provider_name", "provider_display_name", "platform"] if c in a_cols]
        if usable:
            return "(" + " OR ".join([f"NULLIF(TRIM(CAST(a.{qident(c)} AS TEXT)),'') IS NOT NULL" for c in usable]) + ")"
    return "1=1"


def domain_guard(a_cols: List[str], domain: str) -> str:
    values = {
        "current": ["current", "indian", "movie", "movies"],
        "hollywood": ["hollywood", "global", "global_movie", "global_movies"],
        "historical": ["historical", "historical_movie", "historical_movies"],
        "webseries": ["webseries", "web_series", "series", "tv"],
    }.get(domain, [domain])
    for c in ["domain", "content_domain", "media_domain", "source_domain", "content_type", "media_type"]:
        if c in a_cols:
            quoted = ",".join("'" + x.replace("'", "''") + "'" for x in values)
            return f"LOWER(CAST(a.{qident(c)} AS TEXT)) IN ({quoted})"
    return "1=1"


def join_candidates(m_cols: List[str], a_cols: List[str], domain: str) -> List[Tuple[str, str]]:
    cands = []
    pairs = [("slug", "slug"), ("slug", "content_slug"), ("slug", "movie_slug"), ("slug", "series_slug"), ("content_slug", "content_slug"), ("movie_slug", "movie_slug"), ("series_slug", "series_slug"), ("tmdb_id", "tmdb_id"), ("tmdb_id", "content_tmdb_id"), ("imdb_id", "imdb_id"), ("imdb_id", "content_imdb_id"), ("id", "content_id"), ("content_id", "content_id")]
    for m, a in pairs:
        if m in m_cols and a in a_cols:
            cands.append((f"{m}={a}", f"NULLIF(CAST(m.{qident(m)} AS TEXT),'') IS NOT NULL AND CAST(a.{qident(a)} AS TEXT)=CAST(m.{qident(m)} AS TEXT)"))
    m_title = pick(m_cols, ["title", "name", "series_title", "original_title", "movie_title"])
    a_title = pick(a_cols, ["title", "name", "content_title", "movie_title", "series_title", "original_title"])
    m_year = pick(m_cols, ["release_year", "year", "start_year", "first_air_year"])
    a_year = pick(a_cols, ["release_year", "year", "content_year", "movie_year", "start_year", "first_air_year"])
    if m_title and a_title and m_year and a_year:
        cands.append(("title+year", f"LOWER(TRIM(CAST(a.{qident(a_title)} AS TEXT)))=LOWER(TRIM(CAST(m.{qident(m_title)} AS TEXT))) AND CAST(a.{qident(a_year)} AS TEXT)=CAST(m.{qident(m_year)} AS TEXT)"))
        cands.append(("slugtitle+year", f"{slug_text_sql('a', a_title)}={slug_text_sql('m', m_title)} AND CAST(a.{qident(a_year)} AS TEXT)=CAST(m.{qident(m_year)} AS TEXT)"))
    if m_title and a_title:
        cands.append(("title", f"LOWER(TRIM(CAST(a.{qident(a_title)} AS TEXT)))=LOWER(TRIM(CAST(m.{qident(m_title)} AS TEXT)))"))
        cands.append(("slugtitle", f"{slug_text_sql('a', a_title)}={slug_text_sql('m', m_title)}"))
    return cands


def content_where(m_cols: List[str], qp: Any, params: List[Any]) -> str:
    clauses = []
    q = str(qp.get("q") or "").strip()
    if q:
        cols = [c for c in ["title", "name", "series_title", "original_title", "movie_title"] if c in m_cols]
        if cols:
            clauses.append("(" + " OR ".join([f"m.{qident(c)} ILIKE %s" for c in cols]) + ")")
            params.extend(["%" + q + "%"] * len(cols))
    year = str(qp.get("year") or "").strip()
    if year:
        c = pick(m_cols, ["release_year", "year", "start_year", "first_air_year"])
        if c:
            clauses.append(f"CAST(m.{qident(c)} AS TEXT)=%s")
            params.append(year)
    lang = str(qp.get("language") or qp.get("language_slug") or "").strip().lower().replace("_", "-")
    if lang:
        cols = [c for c in ["language_slug", "primary_language_slug", "primary_language", "language", "original_language"] if c in m_cols]
        if cols:
            clauses.append("(" + " OR ".join([f"LOWER(REPLACE(CAST(m.{qident(c)} AS TEXT),'_','-'))=%s" for c in cols]) + ")")
            params.extend([lang] * len(cols))
    return " AND ".join(clauses) if clauses else "1=1"


def order_by(m_cols: List[str], sort: str) -> str:
    s = str(sort or "popular").lower()
    if s == "latest":
        candidates = ["release_date", "release_year", "year", "start_year", "created_at", "updated_at"]
    elif s in ("rating", "top", "imdb"):
        candidates = ["imdb_rating", "rating", "vote_average", "tmdb_rating", "flixyfy_score", "popularity"]
    else:
        candidates = ["flixyfy_score", "popularity", "vote_count", "imdb_rating", "rating", "release_year", "year", "start_year"]
    cols = [c for c in candidates if c in m_cols]
    return ", ".join([f"m.{qident(c)} DESC NULLS LAST" for c in cols]) if cols else "1"


def plan_for(cur, config: Dict[str, Any], provider: str) -> Dict[str, Any]:
    p = norm_provider(provider)
    key = f"plan:{config['domain']}:{p}"
    hit = cache_get(_PLAN_CACHE, key)
    if hit is not None:
        return hit
    content_tables = existing_tables(cur, config["content"])
    availability_tables = existing_tables(cur, config["availability"])
    best = None
    best_count = -1
    for ct, m_cols, m_rows in content_tables:
        for at, a_cols, a_rows in availability_tables:
            p_params: List[Any] = []
            p_sql = provider_where(a_cols, p, p_params)
            d_sql = domain_guard(a_cols, config["domain"])
            for join_name, join_sql in join_candidates(m_cols, a_cols, config["domain"]):
                try:
                    cur.execute(
                        f"SELECT COUNT(*) AS c FROM public.{qident(ct)} m WHERE EXISTS (SELECT 1 FROM public.{qident(at)} a WHERE {join_sql} AND {d_sql} AND {p_sql})",
                        p_params,
                    )
                    count = int((cur.fetchone() or {}).get("c") or 0)
                except Exception:
                    cur.connection.rollback()
                    count = -1
                if count > best_count:
                    best_count = count
                    best = {"content_table": ct, "content_cols": m_cols, "availability_table": at, "availability_cols": a_cols, "join_name": join_name, "join_sql": join_sql, "domain_guard_sql": d_sql, "probe_count": count}
    if not best:
        raise RuntimeError("no provider join plan found")
    return cache_set(_PLAN_CACHE, key, best, PLAN_TTL)


def cors_headers(request: Request) -> Dict[str, str]:
    origin = request.headers.get("origin") or "https://flixyfy.com"
    allowed = {"https://flixyfy.com", "https://www.flixyfy.com", "https://flixyfy-web.vercel.app", "http://localhost:5173", "http://127.0.0.1:5173", "http://localhost:3000", "http://127.0.0.1:3000"}
    if origin not in allowed:
        origin = "https://flixyfy.com"
    return {"Access-Control-Allow-Origin": origin, "Access-Control-Allow-Credentials": "true", "Access-Control-Allow-Headers": "*", "Access-Control-Allow-Methods": "*", "Vary": "Origin", "X-Flixyfy-Provider-Filter": "adaptive-v3"}


def enrich(rows: List[Dict[str, Any]], config: Dict[str, Any], provider: str) -> List[Dict[str, Any]]:
    p = norm_provider(provider)
    label = PROVIDER_LABELS.get(p)
    out = []
    for r in rows:
        item = dict(r)
        slug = item.get("slug") or item.get("content_slug") or item.get("movie_slug") or item.get("series_slug")
        if slug and not item.get("url_path"):
            item["url_path"] = config["prefix"] + str(slug)
        item.setdefault("domain", config["domain"])
        if p and p != "all":
            item["ott_primary_key"] = p
            item["ott_primary"] = label or p.replace("_", " ").title()
            item["has_ott"] = True
        out.append(item)
    return out


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
        provider = norm_provider(request.query_params.get("provider") or request.query_params.get("provider_key") or "")
        availability = str(request.query_params.get("availability") or request.query_params.get("has_ott") or "").strip().lower()
        if availability in ("free", "free_to_watch", "youtube"):
            provider = "youtube"
        if provider in ("", "all") and availability in ("", "all", "all_titles"):
            return await call_next(request)
        response_key = hashlib.sha256((path + "?" + str(request.url.query) + "|p=" + provider + "|a=" + availability).encode("utf-8")).hexdigest()
        hit = cache_get(_RESPONSE_CACHE, response_key)
        if hit is not None:
            return JSONResponse(hit, headers={**cors_headers(request), "X-Flixyfy-Cache": "HIT"})
        try:
            payload = self.query(config, request.query_params, provider, availability)
            cache_set(_RESPONSE_CACHE, response_key, payload, RESPONSE_TTL)
            return JSONResponse(payload, headers={**cors_headers(request), "X-Flixyfy-Cache": "MISS"})
        except Exception as exc:
            response = await call_next(request)
            response.headers.setdefault("X-Flixyfy-Provider-Filter-Fallback", type(exc).__name__)
            return response

    def query(self, config: Dict[str, Any], qp: Any, provider: str, availability: str) -> Dict[str, Any]:
        page = max(1, int(qp.get("page") or 1))
        limit = max(1, min(100, int(qp.get("limit") or 24)))
        offset = (page - 1) * limit
        with connect() as db:
            with db.cursor(cursor_factory=RealDictCursor) as cur:
                plan = plan_for(cur, config, provider)
                m_cols = plan["content_cols"]
                a_cols = plan["availability_cols"]
                c_params: List[Any] = []
                c_sql = content_where(m_cols, qp, c_params)
                p_params: List[Any] = []
                p_sql = provider_where(a_cols, provider, p_params)
                a_params: List[Any] = []
                a_sql = availability_where(a_cols, availability, a_params)
                exists_sql = f"EXISTS (SELECT 1 FROM public.{qident(plan['availability_table'])} a WHERE {plan['join_sql']} AND {plan['domain_guard_sql']} AND {p_sql} AND {a_sql})"
                params = c_params + p_params + a_params
                cur.execute(f"SELECT COUNT(*) AS total FROM public.{qident(plan['content_table'])} m WHERE {c_sql} AND {exists_sql}", params)
                total = int((cur.fetchone() or {}).get("total") or 0)
                cur.execute(
                    f"SELECT m.* FROM public.{qident(plan['content_table'])} m WHERE {c_sql} AND {exists_sql} ORDER BY {order_by(m_cols, qp.get('sort') or 'popular')} LIMIT %s OFFSET %s",
                    params + [limit, offset],
                )
                rows = [dict(r) for r in cur.fetchall()]
        return {"page": page, "limit": limit, "total": total, "items": enrich(rows, config, provider), "domain": config["domain"], "label": config["label"], "provider": norm_provider(provider), "availability": availability, "source": "provider_filter_adaptive_v3", "provider_plan": {"content_table": plan["content_table"], "availability_table": plan["availability_table"], "join": plan["join_name"], "probe_count": plan["probe_count"]}}


def install_provider_filter_v5_middleware(app):
    return app
