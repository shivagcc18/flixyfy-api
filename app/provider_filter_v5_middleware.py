
# FLIXYFY_FILTER_RESTORE_WEBseries_SPEED_V5
# Adaptive provider filter middleware. No DB mutation. No DDL. SELECT-only.
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
    "jiohotstar": "jiohotstar", "jio hotstar": "jiohotstar", "hotstar": "jiohotstar", "disney hotstar": "jiohotstar",
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
PROVIDER_LABELS = {"youtube": "YouTube", "netflix": "Netflix", "prime_video": "Prime Video", "jiohotstar": "JioHotstar", "zee5": "ZEE5", "sonyliv": "SonyLIV", "aha": "Aha", "sun_nxt": "Sun NXT", "etv_win": "ETV Win", "mx_player": "MX Player", "apple_tv_store": "Apple TV", "amazon_video": "Amazon Video", "google_tv": "Google TV", "disney_plus": "Disney+", "hulu": "Hulu", "max": "Max", "plex": "Plex", "viki": "Rakuten Viki", "kocowa": "Kocowa", "tving": "TVING", "wavve": "Wavve", "watcha": "Watcha", "coupang_play": "Coupang Play", "tubi_tv": "Tubi"}

CONFIGS = {
    "/api/v3/movies": {"domain": "current", "label": "Indian Movies", "prefix": "/movie/", "content": ["current_movie_serving_v5_backend_compat", "current_movie_serving_v5", "media_serving_v8_expanded", "media_serving_v7_final"], "availability": ["current_availability_serving_v5", "provider_availability_serving_v2", "provider_availability_serving_v1", "ott_availability_normalized_v2", "ott_availability_normalized_v1"]},
    "/api/v3/indian": {"domain": "current", "label": "Indian Movies", "prefix": "/movie/", "content": ["current_movie_serving_v5_backend_compat", "current_movie_serving_v5", "media_serving_v8_expanded", "media_serving_v7_final"], "availability": ["current_availability_serving_v5", "provider_availability_serving_v2", "provider_availability_serving_v1", "ott_availability_normalized_v2", "ott_availability_normalized_v1"]},
    "/api/v3/hollywood": {"domain": "hollywood", "label": "Global Movies", "prefix": "/hollywood/", "content": ["hollywood_movie_serving_v5", "hollywood_movie_serving_v5_backend_compat", "hollywood_card_serving_v3", "hollywood_serving_v3"], "availability": ["hollywood_availability_serving_v5", "hollywood_availability_serving_v3", "hollywood_availability_serving_v2", "hollywood_availability_serving_v1", "provider_availability_serving_v2", "provider_availability_serving_v1"]},
    "/api/v3/global": {"domain": "hollywood", "label": "Global Movies", "prefix": "/hollywood/", "content": ["hollywood_movie_serving_v5", "hollywood_movie_serving_v5_backend_compat", "hollywood_card_serving_v3", "hollywood_serving_v3"], "availability": ["hollywood_availability_serving_v5", "hollywood_availability_serving_v3", "hollywood_availability_serving_v2", "hollywood_availability_serving_v1", "provider_availability_serving_v2", "provider_availability_serving_v1"]},
    "/api/v3/historical": {"domain": "historical", "label": "Historical Movies", "prefix": "/historical/", "content": ["historical_movie_serving_v5", "historical_movie_serving_v5_backend_compat", "historical_card_serving_v1", "historical_serving_v2", "historical_serving_v1"], "availability": ["historical_availability_serving_v5", "historical_availability_serving_v3", "historical_availability_serving_v2", "historical_availability_v2", "provider_availability_serving_v2", "provider_availability_serving_v1"]},
    "/api/v3/webseries": {"domain": "webseries", "label": "Webseries", "prefix": "/webseries/", "content": ["webseries_series_serving_v5", "webseries_serving_v5", "webseries_card_serving_v1", "webseries_serving_v1"], "availability": ["webseries_availability_serving_v5", "webseries_availability_serving_v3", "webseries_availability_serving_v2", "webseries_availability_serving_v1", "webseries_availability_v1", "provider_availability_serving_v2", "provider_availability_serving_v1"]},
    "/api/v3/web-series": {"domain": "webseries", "label": "Webseries", "prefix": "/webseries/", "content": ["webseries_series_serving_v5", "webseries_serving_v5", "webseries_card_serving_v1", "webseries_serving_v1"], "availability": ["webseries_availability_serving_v5", "webseries_availability_serving_v3", "webseries_availability_serving_v2", "webseries_availability_serving_v1", "webseries_availability_v1", "provider_availability_serving_v2", "provider_availability_serving_v1"]},
    "/api/v3/series": {"domain": "webseries", "label": "Webseries", "prefix": "/webseries/", "content": ["webseries_series_serving_v5", "webseries_serving_v5", "webseries_card_serving_v1", "webseries_serving_v1"], "availability": ["webseries_availability_serving_v5", "webseries_availability_serving_v3", "webseries_availability_serving_v2", "webseries_availability_serving_v1", "webseries_availability_v1", "provider_availability_serving_v2", "provider_availability_serving_v1"]},
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

def needles(provider: str) -> List[str]:
    p = norm_provider(provider)
    out = {p, p.replace("_", " ")}
    for raw, mapped in PROVIDER_ALIASES.items():
        if mapped == p and raw:
            out.add(raw); out.add(raw.replace("_", " ")); out.add(raw.replace(" ", "_"))
    out.update({"netflix": ["netflix"], "prime_video": ["prime", "amazon prime", "amazon_prime"], "youtube": ["youtube", "youtu.be"], "zee5": ["zee5", "zee 5"], "jiohotstar": ["hotstar", "jiohotstar"], "sonyliv": ["sony", "sonyliv"], "sun_nxt": ["sun nxt", "sunnxt"], "etv_win": ["etv win", "etvwin"], "mx_player": ["mx player", "mxplayer"], "apple_tv_store": ["apple", "itunes"], "amazon_video": ["amazon video"], "google_tv": ["google"], "disney_plus": ["disney"], "max": ["max", "hbo"]}.get(p, []))
    return sorted(x.lower() for x in out if x)

def lower_expr(alias: str, col: str) -> str:
    return "LOWER(COALESCE(CAST(" + alias + "." + qident(col) + " AS TEXT),''))"

def slug_expr(alias: str, col: str) -> str:
    return "LOWER(regexp_replace(COALESCE(CAST(" + alias + "." + qident(col) + " AS TEXT),''), '[^a-zA-Z0-9]+', '_', 'g'))"

def connect():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL missing")
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

def cache_get(cache, key):
    item = cache.get(key)
    if item and item[0] > time.time():
        return item[1]
    return None

def cache_set(cache, key, value, ttl):
    cache[key] = (time.time() + ttl, value)
    return value

def table_columns(cur, table: str) -> List[str]:
    key = "cols:" + table
    hit = cache_get(_SCHEMA_CACHE, key)
    if hit is not None:
        return hit
    cur.execute("SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name=%s ORDER BY ordinal_position", [table])
    return cache_set(_SCHEMA_CACHE, key, [r["column_name"] for r in cur.fetchall()], SCHEMA_TTL)

def table_exists(cur, table: str) -> bool:
    return bool(table_columns(cur, table))

def existing(cur, tables: Iterable[str]) -> List[Tuple[str, List[str]]]:
    rows = []
    for t in tables:
        cols = table_columns(cur, t)
        if cols:
            rows.append((t, cols))
    return rows

def pick(cols: Iterable[str], options: Iterable[str]) -> Optional[str]:
    s = set(cols)
    for x in options:
        if x in s:
            return x
    return None

def provider_sql(a_cols: List[str], provider: str, params: List[Any]) -> str:
    p = norm_provider(provider)
    if p in ("", "all"):
        return "1=1"
    cols = ["provider_key", "provider", "provider_name", "provider_display_name", "provider_label", "ott_primary_key", "ott_primary", "platform", "platform_name", "source", "source_name", "watch_provider", "watch_provider_name", "provider_slug", "provider_code"]
    parts = []
    for col in cols:
        if col not in a_cols:
            continue
        for n in needles(p):
            parts.append(lower_expr("a", col) + " LIKE %s"); params.append("%" + n.replace("_", " ") + "%")
            parts.append(slug_expr("a", col) + " LIKE %s"); params.append("%" + re.sub(r"[^a-z0-9]+", "_", n).strip("_") + "%")
    if p == "youtube":
        for col in ["final_url", "watch_url", "youtube_url", "video_url", "url", "deep_link"]:
            if col in a_cols:
                parts.append(lower_expr("a", col) + " LIKE %s"); params.append("%youtube%")
                parts.append(lower_expr("a", col) + " LIKE %s"); params.append("%youtu.be%")
    return "(" + " OR ".join(parts) + ")" if parts else "1=0"

def availability_sql(a_cols: List[str], availability: str, params: List[Any]) -> str:
    v = str(availability or "").strip().lower()
    if v in ("", "all", "all_titles", "all movies", "all_titles"):
        return "1=1"
    if v in ("free", "free_to_watch", "youtube"):
        return provider_sql(a_cols, "youtube", params)
    if v in ("ott", "streaming", "available"):
        usable = [c for c in ["provider_key", "provider", "provider_name", "provider_display_name", "platform"] if c in a_cols]
        if usable:
            return "(" + " OR ".join(["NULLIF(TRIM(CAST(a." + qident(c) + " AS TEXT)),'') IS NOT NULL" for c in usable]) + ")"
    return "1=1"

def domain_sql(a_cols: List[str], domain: str) -> str:
    vals = {"current": ["current", "indian", "movie", "movies"], "hollywood": ["hollywood", "global", "global_movie", "global_movies"], "historical": ["historical", "historical_movie", "historical_movies"], "webseries": ["webseries", "web_series", "series", "tv"]}.get(domain, [domain])
    for col in ["domain", "content_domain", "media_domain", "source_domain", "content_type", "media_type"]:
        if col in a_cols:
            quoted = ",".join(["'" + v.replace("'", "''") + "'" for v in vals])
            return "LOWER(CAST(a." + qident(col) + " AS TEXT)) IN (" + quoted + ")"
    return "1=1"

def join_candidates(m_cols: List[str], a_cols: List[str]) -> List[Tuple[str, str]]:
    out = []
    for m, a in [("slug", "slug"), ("slug", "content_slug"), ("slug", "movie_slug"), ("slug", "series_slug"), ("content_slug", "content_slug"), ("series_slug", "series_slug"), ("movie_slug", "movie_slug"), ("tmdb_id", "tmdb_id"), ("tmdb_id", "content_tmdb_id"), ("imdb_id", "imdb_id"), ("imdb_id", "content_imdb_id"), ("id", "content_id"), ("content_id", "content_id")]:
        if m in m_cols and a in a_cols:
            out.append((m + "=" + a, "NULLIF(CAST(m." + qident(m) + " AS TEXT),'') IS NOT NULL AND CAST(a." + qident(a) + " AS TEXT)=CAST(m." + qident(m) + " AS TEXT)"))
    mt = pick(m_cols, ["title", "name", "series_title", "original_title", "movie_title"])
    at = pick(a_cols, ["title", "name", "content_title", "movie_title", "series_title", "original_title"])
    my = pick(m_cols, ["release_year", "year", "start_year", "first_air_year"])
    ay = pick(a_cols, ["release_year", "year", "content_year", "movie_year", "start_year", "first_air_year"])
    if mt and at and my and ay:
        out.append(("title+year", "LOWER(TRIM(CAST(a." + qident(at) + " AS TEXT)))=LOWER(TRIM(CAST(m." + qident(mt) + " AS TEXT))) AND CAST(a." + qident(ay) + " AS TEXT)=CAST(m." + qident(my) + " AS TEXT)"))
        out.append(("slugtitle+year", slug_expr("a", at) + "=" + slug_expr("m", mt) + " AND CAST(a." + qident(ay) + " AS TEXT)=CAST(m." + qident(my) + " AS TEXT)"))
    if mt and at:
        out.append(("title", "LOWER(TRIM(CAST(a." + qident(at) + " AS TEXT)))=LOWER(TRIM(CAST(m." + qident(mt) + " AS TEXT)))"))
        out.append(("slugtitle", slug_expr("a", at) + "=" + slug_expr("m", mt)))
    return out

def content_sql(m_cols: List[str], qp, params: List[Any]) -> str:
    parts = []
    q = str(qp.get("q") or "").strip()
    if q:
        cols = [c for c in ["title", "name", "series_title", "original_title", "movie_title"] if c in m_cols]
        if cols:
            parts.append("(" + " OR ".join(["m." + qident(c) + " ILIKE %s" for c in cols]) + ")")
            params.extend(["%" + q + "%"] * len(cols))
    year = str(qp.get("year") or "").strip()
    yc = pick(m_cols, ["release_year", "year", "start_year", "first_air_year"])
    if year and yc:
        parts.append("CAST(m." + qident(yc) + " AS TEXT)=%s"); params.append(year)
    lang = str(qp.get("language") or qp.get("language_slug") or "").strip().lower().replace("_", "-")
    if lang:
        cols = [c for c in ["language_slug", "primary_language_slug", "primary_language", "language", "original_language"] if c in m_cols]
        if cols:
            parts.append("(" + " OR ".join(["LOWER(REPLACE(CAST(m." + qident(c) + " AS TEXT),'_','-'))=%s" for c in cols]) + ")")
            params.extend([lang] * len(cols))
    return " AND ".join(parts) if parts else "1=1"

def order_sql(m_cols: List[str], sort: str) -> str:
    s = str(sort or "popular").lower()
    candidates = ["release_date", "release_year", "year", "start_year", "created_at"] if s == "latest" else (["imdb_rating", "rating", "vote_average", "tmdb_rating", "flixyfy_score", "popularity"] if s in ("rating", "top", "imdb") else ["flixyfy_score", "popularity", "vote_count", "imdb_rating", "rating", "release_year", "year", "start_year"])
    cols = [c for c in candidates if c in m_cols]
    return ", ".join(["m." + qident(c) + " DESC NULLS LAST" for c in cols]) if cols else "1"

def choose_plan(cur, cfg: Dict[str, Any], provider: str) -> Dict[str, Any]:
    key = "plan:" + cfg["domain"] + ":" + norm_provider(provider)
    hit = cache_get(_PLAN_CACHE, key)
    if hit is not None:
        return hit
    contents = existing(cur, cfg["content"])
    avails = existing(cur, cfg["availability"])
    best = None
    for ct, mc in contents:
        for at, ac in avails:
            dsql = domain_sql(ac, cfg["domain"])
            for join_name, jsql in join_candidates(mc, ac):
                params: List[Any] = []
                psql = provider_sql(ac, provider, params)
                try:
                    cur.execute("SELECT 1 FROM public." + qident(ct) + " m WHERE EXISTS (SELECT 1 FROM public." + qident(at) + " a WHERE " + jsql + " AND " + dsql + " AND " + psql + ") LIMIT 1", params)
                    if cur.fetchone():
                        best = {"content_table": ct, "content_cols": mc, "availability_table": at, "availability_cols": ac, "join_name": join_name, "join_sql": jsql, "domain_sql": dsql}
                        return cache_set(_PLAN_CACHE, key, best, PLAN_TTL)
                except Exception:
                    cur.connection.rollback()
    if contents and avails:
        ct, mc = contents[0]; at, ac = avails[0]
        cands = join_candidates(mc, ac)
        jname, jsql = cands[0] if cands else ("none", "1=0")
        best = {"content_table": ct, "content_cols": mc, "availability_table": at, "availability_cols": ac, "join_name": jname, "join_sql": jsql, "domain_sql": domain_sql(ac, cfg["domain"])}
        return cache_set(_PLAN_CACHE, key, best, PLAN_TTL)
    raise RuntimeError("no provider filter plan")

def cors_headers(request: Request) -> Dict[str, str]:
    origin = request.headers.get("origin") or "https://flixyfy.com"
    allowed = {"https://flixyfy.com", "https://www.flixyfy.com", "https://flixyfy-web.vercel.app", "http://localhost:5173", "http://127.0.0.1:5173", "http://localhost:3000", "http://127.0.0.1:3000"}
    if origin not in allowed:
        origin = "https://flixyfy.com"
    return {"Access-Control-Allow-Origin": origin, "Access-Control-Allow-Credentials": "true", "Access-Control-Allow-Headers": "*", "Access-Control-Allow-Methods": "*", "Vary": "Origin", "X-Flixyfy-Provider-Filter": "restore-speed-v5"}

def enrich(rows: List[Dict[str, Any]], cfg: Dict[str, Any], provider: str) -> List[Dict[str, Any]]:
    p = norm_provider(provider); label = PROVIDER_LABELS.get(p)
    out = []
    for row in rows:
        item = dict(row)
        slug = item.get("slug") or item.get("content_slug") or item.get("movie_slug") or item.get("series_slug")
        if slug and not item.get("url_path"):
            item["url_path"] = cfg["prefix"] + str(slug)
        item.setdefault("domain", cfg["domain"])
        if p and p != "all":
            item["ott_primary_key"] = p; item["ott_primary"] = label or p.replace("_", " ").title(); item["has_ott"] = True
        out.append(item)
    return out

class ProviderFilterV5Middleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.method == "OPTIONS":
            return JSONResponse({"ok": True}, headers=cors_headers(request))
        if request.method != "GET":
            return await call_next(request)
        path = request.url.path.rstrip("/")
        cfg = CONFIGS.get(path)
        if not cfg:
            return await call_next(request)
        provider = norm_provider(request.query_params.get("provider") or request.query_params.get("provider_key") or "")
        availability = str(request.query_params.get("availability") or request.query_params.get("has_ott") or "").strip().lower()
        if availability in ("free", "free_to_watch", "youtube"):
            provider = "youtube"
        if provider in ("", "all") and availability in ("", "all", "all_titles"):
            return await call_next(request)
        key = hashlib.sha256((path + "?" + str(request.url.query) + "|" + provider + "|" + availability).encode("utf-8")).hexdigest()
        hit = cache_get(_RESPONSE_CACHE, key)
        if hit is not None:
            return JSONResponse(hit, headers={**cors_headers(request), "X-Flixyfy-Cache": "HIT"})
        try:
            payload = self.query(cfg, request.query_params, provider, availability)
            cache_set(_RESPONSE_CACHE, key, payload, RESPONSE_TTL)
            return JSONResponse(payload, headers={**cors_headers(request), "X-Flixyfy-Cache": "MISS"})
        except Exception as exc:
            response = await call_next(request)
            response.headers.setdefault("X-Flixyfy-Provider-Filter-Fallback", type(exc).__name__)
            return response

    def query(self, cfg: Dict[str, Any], qp, provider: str, availability: str) -> Dict[str, Any]:
        page = max(1, int(qp.get("page") or 1)); limit = max(1, min(100, int(qp.get("limit") or 24))); offset = (page - 1) * limit
        with connect() as db:
            with db.cursor(cursor_factory=RealDictCursor) as cur:
                plan = choose_plan(cur, cfg, provider)
                mcols = plan["content_cols"]; acols = plan["availability_cols"]
                cparams: List[Any] = []; csql = content_sql(mcols, qp, cparams)
                pparams: List[Any] = []; psql = provider_sql(acols, provider, pparams)
                aparams: List[Any] = []; asql = availability_sql(acols, availability, aparams)
                exists = "EXISTS (SELECT 1 FROM public." + qident(plan["availability_table"]) + " a WHERE " + plan["join_sql"] + " AND " + plan["domain_sql"] + " AND " + psql + " AND " + asql + ")"
                params = cparams + pparams + aparams
                cur.execute("SELECT COUNT(*) AS total FROM public." + qident(plan["content_table"]) + " m WHERE " + csql + " AND " + exists, params)
                total = int((cur.fetchone() or {}).get("total") or 0)
                cur.execute("SELECT m.* FROM public." + qident(plan["content_table"]) + " m WHERE " + csql + " AND " + exists + " ORDER BY " + order_sql(mcols, qp.get("sort") or "popular") + " LIMIT %s OFFSET %s", params + [limit, offset])
                rows = [dict(r) for r in cur.fetchall()]
        return {"page": page, "limit": limit, "total": total, "items": enrich(rows, cfg, provider), "domain": cfg["domain"], "label": cfg["label"], "provider": norm_provider(provider), "availability": availability, "source": "provider_filter_restore_speed_v5", "provider_plan": {"content_table": plan["content_table"], "availability_table": plan["availability_table"], "join": plan["join_name"]}}

def install_provider_filter_v5_middleware(app):
    return app
