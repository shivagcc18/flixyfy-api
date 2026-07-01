import json
import os
from typing import Optional
from urllib.parse import quote_plus

import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
TABLE = os.getenv("SERVING_TABLE", "media_serving_v8_expanded")

app = FastAPI(
    title="Flixyfy API V3",
    version="3.0.0",
    description="Production API using Neon PostgreSQL",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "https://flixyfy-web.vercel.app",
        "https://flixyfy.com",
        "https://www.flixyfy.com",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

PROVIDER_HOME = {
    "netflix": "https://www.netflix.com/in/",
    "zee5": "https://www.zee5.com/",
    "jiohotstar": "https://www.hotstar.com/in/",
    "hotstar": "https://www.hotstar.com/in/",
    "prime_video": "https://www.primevideo.com/",
    "amazon_prime_video": "https://www.primevideo.com/",
    "sonyliv": "https://www.sonyliv.com/",
    "sun_nxt": "https://www.sunnxt.com/",
    "aha": "https://www.aha.video/",
    "vi_movies_and_tv": "https://www.myvi.in/vi-movies-and-tv",
    "mx_player": "https://www.mxplayer.in/",
    "youtube": "https://www.youtube.com/",
    "eros_now": "https://erosnow.com/",
    "hoichoi": "https://www.hoichoi.tv/",
    "etv_win": "https://www.etvwin.com/",
}

PROVIDER_SEARCH = {
    "netflix": "https://www.netflix.com/search?q={q}",
    "zee5": "https://www.zee5.com/search?q={q}",
    "jiohotstar": "https://www.hotstar.com/in/search?q={q}",
    "hotstar": "https://www.hotstar.com/in/search?q={q}",
    "prime_video": "https://www.primevideo.com/search/ref=atv_nb_sr?phrase={q}",
    "amazon_prime_video": "https://www.primevideo.com/search/ref=atv_nb_sr?phrase={q}",
    "sonyliv": "https://www.sonyliv.com/search?q={q}",
    "sun_nxt": "https://www.sunnxt.com/search?q={q}",
    "aha": "https://www.aha.video/search?q={q}",
    "vi_movies_and_tv": "https://www.myvi.in/vi-movies-and-tv/search?q={q}",
    "mx_player": "https://www.mxplayer.in/search/{q}",
    "youtube": "https://www.youtube.com/results?search_query={q}",
    "eros_now": "https://erosnow.com/search?q={q}",
    "hoichoi": "https://www.hoichoi.tv/search?q={q}",
    "etv_win": "https://www.etvwin.com/search?q={q}",
}


def get_conn():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL missing")
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)


def parse_json(value, fallback):
    if value is None or value == "":
        return fallback
    if isinstance(value, (list, dict)):
        return value
    try:
        return json.loads(value)
    except Exception:
        return fallback


def as_bool(value):
    return bool(value) if value is not None else False


def fix_image_url(value):
    if not value:
        return None
    value = str(value)
    if value.startswith("http"):
        return value
    if value.startswith("/"):
        return f"https://image.tmdb.org/t/p/w500{value}"
    return value


def normalize_provider_key(value):
    return (value or "").strip().lower().replace(" ", "_").replace("-", "_")


def movie_card(row):
    slug = row.get("slug")

    return {
        "tmdb_id": row.get("tmdb_id"),
        "title": row.get("title"),
        "original_title": row.get("original_title"),
        "slug": slug,
        "movie_url": row.get("movie_url") or (f"/movie/{slug}" if slug else None),
        "release_year": row.get("release_year"),
        "year_bucket": row.get("year_bucket"),
        "primary_language": row.get("primary_language"),
        "language_slug": row.get("language_slug"),
        "poster_url": fix_image_url(row.get("poster_url")),
        "backdrop_url": fix_image_url(row.get("backdrop_url")),
        "rating": row.get("rating"),
        "vote_count": row.get("vote_count"),
        "popularity": row.get("popularity"),
        "quality_score": row.get("quality_score"),
        "ott_primary": row.get("ott_primary"),
        "ott_primary_key": row.get("ott_primary_key"),
        "ott_count": row.get("ott_count"),
        "has_ott": as_bool(row.get("has_ott")),
        "has_free_ott": as_bool(row.get("has_free_ott")),
        "has_subscription_ott": as_bool(row.get("has_subscription_ott")),
        "has_rent_ott": as_bool(row.get("has_rent_ott")),
        "has_buy_ott": as_bool(row.get("has_buy_ott")),
        "is_free": as_bool(row.get("is_free")),
    }


def load_ott_links(tmdb_id):
    if not tmdb_id:
        return []

    v2_rows = load_ott_links_v2(tmdb_id)

    if v2_rows is not None:
        return v2_rows

    return load_ott_provider_links_fallback(tmdb_id)


def load_ott_links_v2(tmdb_id):
    conn = get_conn()
    cur = conn.cursor()

    try:
        cur.execute(
            """
            SELECT
                provider_key,
                provider_display_name,
                provider_category,
                provider_type,
                region,
                deep_link,
                final_url,
                final_url_source,
                button_label,
                source_layer,
                source_method,
                priority,
                confidence
            FROM ott_availability_normalized_v2
            WHERE CAST(tmdb_id AS TEXT) = CAST(%s AS TEXT)
            ORDER BY
                CASE WHEN UPPER(COALESCE(region, '')) = 'IN' THEN 0 ELSE 1 END,
                priority NULLS LAST,
                CASE LOWER(COALESCE(provider_category, provider_type, ''))
                    WHEN 'subscription' THEN 1
                    WHEN 'flatrate' THEN 1
                    WHEN 'free' THEN 2
                    WHEN 'free_with_ads' THEN 3
                    WHEN 'ads' THEN 3
                    WHEN 'rent' THEN 4
                    WHEN 'buy' THEN 5
                    ELSE 9
                END,
                provider_display_name
            """,
            (tmdb_id,),
        )

        rows = cur.fetchall()

        return [normalize_ott_v2_row(r) for r in rows]
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        return None
    finally:
        conn.close()


def normalize_ott_v2_row(row):
    provider_name = row.get("provider_display_name")
    provider_key = row.get("provider_key") or normalize_provider_key(provider_name)
    provider_category = row.get("provider_category")
    provider_type = row.get("provider_type") or provider_category
    homepage_url = None

    if provider_key:
        homepage_url = PROVIDER_HOME.get(provider_key)

    final_url = row.get("final_url") or row.get("deep_link") or homepage_url

    return {
        "provider_key": provider_key,
        "provider_display_name": provider_name,
        "provider_category": provider_category,
        "provider_type": provider_type,
        "category": provider_category,
        "type": provider_type,
        "region": row.get("region"),
        "deep_link": row.get("deep_link"),
        "provider_deep_link": row.get("deep_link"),
        "fallback_search_url": None,
        "provider_search_url": None,
        "homepage_url": homepage_url,
        "provider_homepage_url": homepage_url,
        "final_url": final_url,
        "final_url_source": row.get("final_url_source"),
        "button_label": row.get("button_label") or (f"Watch on {provider_name}" if provider_name else "Watch"),
        "source_layer": row.get("source_layer"),
        "source_method": row.get("source_method"),
        "priority": row.get("priority"),
        "confidence": row.get("confidence"),
    }


def load_ott_provider_links_fallback(tmdb_id):
    conn = get_conn()
    cur = conn.cursor()

    try:
        cur.execute(
            """
            SELECT
                provider_key,
                provider_display_name,
                provider_category,
                provider_type,
                region,
                provider_deep_link,
                provider_search_url,
                provider_homepage_url,
                tmdb_watch_url,
                final_url,
                final_url_source,
                button_label,
                priority
            FROM ott_availability_provider_links_v2
            WHERE tmdb_id = %s
            ORDER BY priority NULLS LAST, provider_display_name
            """,
            (tmdb_id,),
        )

        rows = cur.fetchall()

        return [
            {
                "provider_key": r.get("provider_key"),
                "provider_display_name": r.get("provider_display_name"),
                "provider_category": r.get("provider_category"),
                "provider_type": r.get("provider_type"),
                "region": r.get("region"),
                "deep_link": r.get("provider_deep_link"),
                "provider_deep_link": r.get("provider_deep_link"),
                "fallback_search_url": r.get("provider_search_url"),
                "provider_search_url": r.get("provider_search_url"),
                "homepage_url": r.get("provider_homepage_url"),
                "provider_homepage_url": r.get("provider_homepage_url"),
                "tmdb_watch_url": r.get("tmdb_watch_url"),
                "final_url": r.get("final_url"),
                "final_url_source": r.get("final_url_source"),
                "button_label": r.get("button_label") or r.get("provider_display_name"),
                "priority": r.get("priority"),
            }
            for r in rows
        ]
    finally:
        conn.close()



def safe_int(value):
    try:
        if value is None or value == "":
            return None
        return int(value)
    except Exception:
        return None


def safe_float(value):
    try:
        if value is None or value == "":
            return None
        return float(value)
    except Exception:
        return None


def quote_ident(name):
    return '"' + str(name).replace('"', '""') + '"'


def table_exists(table_name):
    conn = None
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT 1
            FROM information_schema.tables
            WHERE table_schema = 'public'
              AND table_name = %s
            LIMIT 1
            """,
            (table_name,),
        )
        return cur.fetchone() is not None
    except Exception:
        if conn:
            try:
                conn.rollback()
            except Exception:
                pass
        return False
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass


def table_columns(table_name):
    conn = None
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = %s
            """,
            (table_name,),
        )
        return {r["column_name"] for r in cur.fetchall()}
    except Exception:
        if conn:
            try:
                conn.rollback()
            except Exception:
                pass
        return set()
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass


def normalize_detail_row(row, domain, source_table, slug_value):
    if not row:
        return None

    row = dict(row)
    row["_flixyfy_domain"] = domain
    row["_flixyfy_source_table"] = source_table

    if not row.get("slug"):
        row["slug"] = slug_value

    if not row.get("movie_url") and row.get("slug"):
        row["movie_url"] = f"/movie/{row.get('slug')}"

    if row.get("release_year") is None:
        row["release_year"] = row.get("year") or row.get("movie_year")

    if row.get("primary_language") is None:
        row["primary_language"] = row.get("language") or row.get("language_name")

    if row.get("language_slug") is None:
        lang = row.get("primary_language") or row.get("language")
        row["language_slug"] = str(lang).strip().lower().replace(" ", "-") if lang else None

    if row.get("rating") is None:
        row["rating"] = row.get("vote_average") or row.get("imdb_rating")

    if row.get("poster_url") is None:
        row["poster_url"] = row.get("poster") or row.get("poster_path")

    if row.get("backdrop_url") is None:
        row["backdrop_url"] = row.get("backdrop") or row.get("backdrop_path")

    return row


def resolve_movie_by_slug(slug, preferred_domain=None):
    """
    Unified resolver for MovieDetail.
    Searches current, historical, and Hollywood serving tables.
    """
    slug = (slug or "").strip()
    if not slug:
        return None

    candidates = []

    # Prefer configured serving table first for normal current catalog.
    if TABLE:
        candidates.append(("current", TABLE))

    candidates.extend(
        [
            ("current", "media_serving_v8_expanded"),
            ("current_v8", "media_serving_v8_expanded"),
            ("historical", "historical_detail_serving_v1"),
            ("historical", "historical_serving_v1"),
            ("hollywood", "hollywood_detail_serving_v3"),
            ("hollywood", "hollywood_serving_v3"),
        ]
    )

    seen = set()
    ordered = []

    for domain, table in candidates:
        key = (domain, table)
        if key in seen:
            continue
        seen.add(key)

        if preferred_domain and domain != preferred_domain:
            continue

        ordered.append((domain, table))

    if preferred_domain:
        # If preferred lookup fails, fall back to all domains.
        for domain, table in candidates:
            key = (domain, table)
            if key not in seen:
                ordered.append((domain, table))
                seen.add(key)

    conn = get_conn()
    cur = conn.cursor()

    try:
        checked_legacy_redirect = False

        def resolve_legacy_current_redirect():
            if preferred_domain and preferred_domain not in {"current", "current_v8"}:
                return None
            if not table_exists("media_legacy_slug_redirect_v1"):
                return None

            cur.execute(
                """
                SELECT new_slug
                FROM media_legacy_slug_redirect_v1
                WHERE old_slug = %s
                LIMIT 1
                """,
                (slug,),
            )
            redirect = cur.fetchone()
            new_slug = redirect.get("new_slug") if redirect else None
            if not new_slug or not table_exists("media_serving_v8_expanded"):
                return None

            cur.execute(
                """
                SELECT *
                FROM media_serving_v8_expanded
                WHERE slug = %s
                LIMIT 1
                """,
                (new_slug,),
            )
            row = cur.fetchone()
            if not row:
                return None

            data = normalize_detail_row(row, "current", "media_serving_v8_expanded", new_slug)
            data["legacy_slug"] = slug
            data["redirected_from_slug"] = slug
            return data

        for domain, table in ordered:
            if not checked_legacy_redirect and domain not in {"current", "current_v8"}:
                checked_legacy_redirect = True
                redirected = resolve_legacy_current_redirect()
                if redirected:
                    return redirected

            if not table_exists(table):
                continue

            cols = table_columns(table)
            slug_col = None
            for c in ["slug", "movie_slug", "media_slug", "canonical_slug"]:
                if c in cols:
                    slug_col = c
                    break

            if not slug_col:
                continue

            cur.execute(
                f"""
                SELECT *
                FROM {quote_ident(table)}
                WHERE {quote_ident(slug_col)} = %s
                LIMIT 1
                """,
                (slug,),
            )

            row = cur.fetchone()
            if row:
                return normalize_detail_row(row, domain, table, slug)

        if not checked_legacy_redirect:
            redirected = resolve_legacy_current_redirect()
            if redirected:
                return redirected

        return None

    finally:
        conn.close()


def load_youtube_links(tmdb_id=None, slug=None, domain=None, title=None, year=None):
    """
    Production-safe YouTube links.

    Primary:
    - public.youtube_full_movie_links_v2 by movie_slug.
    - No narrow domain filter. New sync contains current/historical and legacy unknown-domain rows.

    Fallback:
    - youtube_variants_v2 by tmdb_id.
    """
    slug = (slug or "").strip()

    if slug:
        conn = None
        try:
            conn = get_conn()
            cur = conn.cursor()

            cur.execute(
                """
                SELECT
                    domain,
                    normalized_domain,
                    movie_id,
                    movie_slug,
                    movie_key,
                    title,
                    year,
                    youtube_video_id,
                    youtube_url,
                    youtube_title,
                    COALESCE(youtube_channel, channel_name) AS youtube_channel,
                    duration_seconds,
                    view_count,
                    match_score,
                    match_type,
                    source,
                    source_generation,
                    source_table,
                    source_run_id,
                    source_local_stage_id,
                    quality_score,
                    audio_language,
                    is_dubbed,
                    status
                FROM public.youtube_full_movie_links_v2
                WHERE COALESCE(status, 'active') = 'active'
                  AND movie_slug = %s
                ORDER BY
                    CASE
                        WHEN normalized_domain = %s THEN 1
                        WHEN domain = %s THEN 2
                        WHEN normalized_domain = 'current' THEN 3
                        WHEN domain IN ('current', 'current_v8', 'current_v7') THEN 4
                        WHEN normalized_domain = 'historical' THEN 5
                        WHEN domain = 'historical' THEN 6
                        WHEN domain IS NULL OR domain = '' OR domain = 'unknown' THEN 7
                        ELSE 9
                    END,
                    COALESCE(quality_score, final_score, match_score, 0) DESC NULLS LAST,
                    view_count DESC NULLS LAST,
                    youtube_video_id ASC
                LIMIT 5
                """,
                (slug, domain, domain),
            )

            rows = cur.fetchall()

            if rows:
                return [
                    {
                        "domain": r.get("domain"),
                        "normalized_domain": r.get("normalized_domain"),
                        "movie_id": r.get("movie_id"),
                        "movie_slug": r.get("movie_slug"),
                        "movie_key": r.get("movie_key"),
                        "video_id": r.get("youtube_video_id"),
                        "youtube_video_id": r.get("youtube_video_id"),
                        "video_url": r.get("youtube_url"),
                        "youtube_url": r.get("youtube_url"),
                        "youtube_title": r.get("youtube_title"),
                        "youtube_channel": r.get("youtube_channel"),
                        "duration_seconds": r.get("duration_seconds"),
                        "view_count": r.get("view_count"),
                        "match_score": r.get("match_score"),
                        "match_type": r.get("match_type"),
                        "quality_score": r.get("quality_score"),
                        "audio_language": r.get("audio_language"),
                        "is_dubbed": as_bool(r.get("is_dubbed")),
                        "trusted_brand": True,
                        "source": r.get("source") or r.get("source_table") or "youtube_full_movie_links_v2",
                        "source_generation": r.get("source_generation"),
                        "source_run_id": r.get("source_run_id"),
                        "variant_type": "FULL_MOVIE",
                        "provider_name": "YouTube",
                        "provider_type": "free",
                        "is_official": True,
                        "is_active": True,
                    }
                    for r in rows
                ]

        except Exception:
            if conn:
                try:
                    conn.rollback()
                except Exception:
                    pass
        finally:
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass

    if not tmdb_id:
        return []

    conn = None
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute(
            """
            WITH ranked AS (
                SELECT
                    video_id,
                    video_url,
                    youtube_title,
                    clean_title,
                    youtube_language,
                    original_language,
                    duration_seconds,
                    view_count,
                    release_year,
                    match_score,
                    match_source,
                    variant_type,
                    is_official,
                    is_active,
                    ROW_NUMBER() OVER (
                        PARTITION BY youtube_language
                        ORDER BY view_count DESC NULLS LAST, duration_seconds DESC NULLS LAST, video_id ASC
                    ) AS rn
                FROM youtube_variants_v2
                WHERE tmdb_id = %s
                  AND is_active = 1
                  AND variant_type = 'FULL_MOVIE'
            )
            SELECT * FROM ranked
            WHERE rn = 1
            ORDER BY view_count DESC NULLS LAST
            LIMIT 5
            """,
            (tmdb_id,),
        )
        rows = cur.fetchall()
        return [
            {
                "video_id": r.get("video_id"),
                "video_url": r.get("video_url"),
                "youtube_url": r.get("video_url"),
                "youtube_title": r.get("youtube_title"),
                "clean_title": r.get("clean_title"),
                "youtube_language": r.get("youtube_language"),
                "original_language": r.get("original_language"),
                "duration_seconds": r.get("duration_seconds"),
                "view_count": r.get("view_count"),
                "release_year": r.get("release_year"),
                "match_score": r.get("match_score"),
                "match_source": r.get("match_source"),
                "variant_type": r.get("variant_type"),
                "is_official": as_bool(r.get("is_official")),
                "is_active": as_bool(r.get("is_active")),
            }
            for r in rows
        ]
    except Exception:
        if conn:
            try:
                conn.rollback()
            except Exception:
                pass
        return []
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass



def movie_detail(row):
    data = movie_card(row)
    tmdb_id = row.get("tmdb_id")
    slug = row.get("slug")
    domain = row.get("_flixyfy_domain") or row.get("normalized_domain") or row.get("domain") or "current"
    ott_links = load_ott_links(tmdb_id)

    youtube_variants = load_youtube_links(
        tmdb_id=tmdb_id,
        slug=slug,
        domain=domain,
        title=row.get("title"),
        year=row.get("release_year") or row.get("year"),
    )

    data.update(
        {
            "domain": domain,
            "source_table": row.get("_flixyfy_source_table"),
            "overview": row.get("overview") or row.get("plot") or row.get("description"),
            "runtime": row.get("runtime"),
            "genres": parse_json(row.get("genres"), []),
            "imdb_id": row.get("imdb_id"),
            "imdb_rating": row.get("imdb_rating"),
            "imdb_votes": row.get("imdb_votes"),
            "omdb_runtime": row.get("omdb_runtime"),
            "omdb_genre": row.get("omdb_genre"),
            "director": row.get("director"),
            "writers": row.get("writers"),
            "actors": row.get("actors") or row.get("cast"),
            "awards": row.get("awards"),
            "certification": row.get("certification"),
            "trailer_url": row.get("trailer_url"),
            "production_companies": row.get("production_companies"),
            "availability": ott_links,
            "ott_all": ott_links,
            "watch_providers": ott_links,
            "youtube_variants": youtube_variants,
            "youtube_full_movies": youtube_variants,
            "youtube_count": len(youtube_variants),
            "created_at": str(row.get("created_at")) if row.get("created_at") else None,
            "updated_at": str(row.get("updated_at")) if row.get("updated_at") else None,
        }
    )

    if ott_links:
        primary_ott = ott_links[0]
        categories = {
            str(link.get("provider_category") or link.get("provider_type") or "").strip().lower()
            for link in ott_links
        }

        data["has_ott"] = True
        data["ott_count"] = len(ott_links)
        data["ott_primary"] = primary_ott.get("provider_display_name")
        data["ott_primary_key"] = primary_ott.get("provider_key")
        data["has_free_ott"] = bool(categories & {"free", "free_with_ads", "ads"})
        data["has_subscription_ott"] = bool(categories & {"subscription", "flatrate"})
        data["has_rent_ott"] = "rent" in categories
        data["has_buy_ott"] = "buy" in categories

        raw = data.get("raw")
        if isinstance(raw, dict):
            raw["has_ott"] = 1
            raw["ott_count"] = len(ott_links)
            raw["ott_primary"] = data["ott_primary"]
            raw["ott_primary_key"] = data["ott_primary_key"]

    data = enrich_historical_youtube_detail_patch_v2(data, row)
    return data


def build_filters(
    language: Optional[str],
    year: Optional[int],
    has_ott: Optional[int],
    is_free: Optional[int],
    provider: Optional[str],
):
    where = []
    params = []

    if language:
        where.append("language_slug = %s")
        params.append(language.strip().lower())

    if year:
        where.append("release_year = %s")
        params.append(year)

    if has_ott is not None:
        where.append("has_ott = %s")
        params.append(1 if has_ott else 0)

    if is_free is not None:
        where.append("is_free = %s")
        params.append(1 if is_free else 0)

    if provider:
        where.append("LOWER(COALESCE(ott_primary_key, ott_primary, '')) LIKE LOWER(%s)")
        params.append(f"%{provider.strip()}%")

    return ("WHERE " + " AND ".join(where)) if where else "", params


@app.get("/")
def root():
    return {
        "status": "ok",
        "name": "Flixyfy API",
        "version": "3.0.0",
        "source": TABLE,
        "docs": "/docs",
    }


@app.get("/api/v3/health")
def health():
    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute(f"SELECT COUNT(*) AS total FROM {TABLE}")
        total = cur.fetchone()["total"]
        cur.execute(f"SELECT COUNT(*) AS ott FROM {TABLE} WHERE has_ott = 1")
        ott = cur.fetchone()["ott"]
    finally:
        conn.close()

    return {
        "status": "ok",
        "table": TABLE,
        "movies": total,
        "ott": ott,
        "ott_coverage": ott,
    }


@app.get("/api/v3/movies")
def movies(
    page: int = Query(1, ge=1),
    limit: int = Query(24, ge=1, le=100),
    language: Optional[str] = None,
    year: Optional[int] = None,
    has_ott: Optional[int] = None,
    is_free: Optional[int] = None,
    provider: Optional[str] = None,
    availability: Optional[str] = None,
    sort: str = Query("popular"),
):
    offset = (page - 1) * limit

    where_sql, params = build_filters(
        language=language,
        year=year,
        has_ott=has_ott,
        is_free=is_free,
        provider=provider,
    )

    if availability == "ott":
        where_sql = f"{where_sql} AND has_ott = 1" if where_sql else "WHERE has_ott = 1"

    elif availability == "youtube":
        where_sql = f"{where_sql} AND is_free = 1" if where_sql else "WHERE is_free = 1"

    allowed_sorts = {
        "popular": "COALESCE(rating, 0) DESC NULLS LAST, release_year DESC NULLS LAST, title ASC",
        "latest": "release_year DESC NULLS LAST, title ASC",
        "rating": "COALESCE(rating, 0) DESC NULLS LAST, title ASC",
        "ott": "has_ott DESC NULLS LAST, COALESCE(rating, 0) DESC NULLS LAST, title ASC",
        "search": "COALESCE(rating, 0) DESC NULLS LAST, title ASC",
        "title": "title ASC",
    }

    order_sql = allowed_sorts.get(sort, allowed_sorts["popular"])

    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute(f"SELECT COUNT(*) AS total FROM {TABLE} {where_sql}", params)
        total = cur.fetchone()["total"]

        cur.execute(
            f"""
            SELECT *
            FROM {TABLE}
            {where_sql}
            ORDER BY {order_sql}
            LIMIT %s OFFSET %s
            """,
            params + [limit, offset],
        )
        rows = cur.fetchall()
    finally:
        conn.close()

    return {
        "page": page,
        "limit": limit,
        "total": total,
        "pages": (total + limit - 1) // limit,
        "items": [movie_card(r) for r in rows],
    }



@app.get("/api/v3/movie/{slug}")
def get_movie(slug: str):
    row = resolve_movie_by_slug(slug)

    if not row:
        raise HTTPException(status_code=404, detail="Movie not found")

    return movie_detail(row)


@app.get("/api/v3/movies/{slug}")
def get_movie_plural_alias(slug: str):
    return get_movie(slug)





# FLIXYFY_HISTORICAL_YOUTUBE_DETAIL_PATCH_V2
def _hist_yt_db_url():
    import os

    for key in ("DATABASE_URL", "POSTGRES_URL", "NEON_DATABASE_URL", "DATABASE_PUBLIC_URL"):
        value = os.getenv(key)
        if value:
            return value

    return None


def _hist_yt_rows(sql, params=None):
    params = params or []
    database_url = _hist_yt_db_url()

    if not database_url:
        return []

    try:
        import psycopg2
        from psycopg2.extras import RealDictCursor

        conn = psycopg2.connect(database_url)
        try:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(sql, params)
                return [dict(row) for row in cur.fetchall()]
        finally:
            conn.close()
    except Exception as exc:
        print("historical youtube patch query failed:", repr(exc))
        return []


def _hist_yt_table_exists(table_name):
    rows = _hist_yt_rows(
        "SELECT 1 FROM information_schema.tables WHERE table_schema='public' AND table_name=%s LIMIT 1",
        [table_name],
    )
    return bool(rows)


def _hist_yt_columns(table_name):
    rows = _hist_yt_rows(
        "SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name=%s ORDER BY ordinal_position",
        [table_name],
    )
    return [row.get("column_name") for row in rows if row.get("column_name")]


def _hist_yt_pick(row, *names):
    if not isinstance(row, dict):
        return None

    for name in names:
        value = row.get(name)
        if value is not None and str(value).strip() != "":
            return value

    return None


def _hist_yt_is_youtube_url(value):
    text = str(value or "").strip().lower()
    return bool(text and ("youtube.com" in text or "youtu.be" in text))


def _hist_yt_normalize_link(row, fallback_slug):
    if not isinstance(row, dict):
        return None

    youtube_url = _hist_yt_pick(row, "youtube_url", "final_url", "url")
    if not _hist_yt_is_youtube_url(youtube_url):
        return None

    slug = _hist_yt_pick(row, "slug") or fallback_slug
    youtube_title = _hist_yt_pick(row, "youtube_title", "title")
    youtube_video_id = _hist_yt_pick(row, "youtube_video_id", "video_id")

    return {
        "domain": "historical",
        "source": _hist_yt_pick(row, "youtube_source") or "historical_youtube_verified_links_v1",
        "provider": "YouTube",
        "provider_key": "youtube",
        "provider_display_name": "YouTube",
        "provider_type": "free",
        "button_label": "Watch on YouTube",
        "title": _hist_yt_pick(row, "title") or youtube_title,
        "slug": slug,
        "youtube_url": youtube_url,
        "youtube_title": youtube_title,
        "youtube_video_id": youtube_video_id,
        "youtube_language": _hist_yt_pick(row, "youtube_language", "language"),
        "youtube_duration_seconds": _hist_yt_pick(row, "youtube_duration_seconds"),
        "youtube_view_count": _hist_yt_pick(row, "youtube_view_count"),
        "youtube_match_score": _hist_yt_pick(row, "youtube_match_score"),
        "youtube_match_type": _hist_yt_pick(row, "youtube_match_type"),
        "youtube_confidence": _hist_yt_pick(row, "youtube_confidence"),
        "youtube_source": _hist_yt_pick(row, "youtube_source"),
        "final_url": youtube_url,
        "url": youtube_url,
        "active": True,
        "is_free": True,
        "has_youtube": True,
        "has_ott": True,
        "ott_primary": "YouTube",
        "ott_primary_key": "youtube",
    }


def _hist_yt_fetch_verified_links(slug):
    slug = (slug or "").strip()
    if not slug:
        return []

    links = []

    if _hist_yt_table_exists("historical_youtube_verified_links_v1"):
        rows = _hist_yt_rows(
            "SELECT * FROM historical_youtube_verified_links_v1 "
            "WHERE slug = %s AND COALESCE(active, TRUE) = TRUE "
            "ORDER BY COALESCE(is_primary, FALSE) DESC, COALESCE(link_rank, 999999) ASC, id ASC",
            [slug],
        )

        for row in rows:
            link = _hist_yt_normalize_link(row, slug)
            if link:
                links.append(link)

    if links:
        return links

    fallback_tables = [
        "historical_detail_serving_v1",
        "historical_serving_v1",
        "historical_availability_v2",
        "historical_card_serving_v1",
        "historical_search_serving_v1",
    ]

    for table in fallback_tables:
        if not _hist_yt_table_exists(table):
            continue

        cols = set(_hist_yt_columns(table))
        if "slug" not in cols or "youtube_url" not in cols:
            continue

        sql = (
            'SELECT * FROM "' + table + '" '
            "WHERE slug = %s "
            "AND youtube_url IS NOT NULL "
            "AND TRIM(CAST(youtube_url AS TEXT)) <> '' "
            "AND (LOWER(CAST(youtube_url AS TEXT)) LIKE %s OR LOWER(CAST(youtube_url AS TEXT)) LIKE %s) "
            "LIMIT 20"
        )

        rows = _hist_yt_rows(sql, [slug, "%youtube.com%", "%youtu.be%"])

        for row in rows:
            link = _hist_yt_normalize_link(row, slug)
            if link:
                links.append(link)

        if links:
            return links

    return []


def _hist_yt_is_historical(data, row=None):
    if not isinstance(data, dict):
        return False

    domain = str(data.get("domain") or data.get("source_domain") or "").lower()
    movie_url = str(data.get("movie_url") or "")
    source_label = str(data.get("source_label") or "").lower()

    if domain == "historical":
        return True

    if movie_url.startswith("/historical/"):
        return True

    if source_label.startswith("historical"):
        return True

    if isinstance(row, dict):
        row_url = str(row.get("movie_url") or "")
        if row_url.startswith("/historical/"):
            return True

    return False


def _hist_yt_unique_links(links):
    out = []
    seen = set()

    for link in links:
        if not isinstance(link, dict):
            continue

        url = str(link.get("youtube_url") or link.get("final_url") or link.get("url") or "").strip()
        if not _hist_yt_is_youtube_url(url):
            continue

        if url in seen:
            continue

        seen.add(url)
        out.append(link)

    return out


def enrich_historical_youtube_detail_patch_v2(data, row=None):
    try:
        if not isinstance(data, dict):
            return data

        if not _hist_yt_is_historical(data, row):
            return data

        slug = str(data.get("slug") or "").strip()
        if not slug and isinstance(row, dict):
            slug = str(row.get("slug") or "").strip()

        if not slug:
            return data

        verified_links = _hist_yt_fetch_verified_links(slug)
        verified_links = _hist_yt_unique_links(verified_links)

        # Critical fix:
        # Historical detail route was showing wrong availability rows because old logic joined by numeric id.
        # For historical detail, keep only same-slug verified YouTube links here.
        data["availability"] = verified_links
        data["ott_all"] = verified_links
        data["watch_providers"] = verified_links
        data["youtube_full_movies"] = verified_links
        data["youtube_variants"] = verified_links
        data["youtube_count"] = len(verified_links)

        if verified_links:
            primary = verified_links[0]

            data["youtube_url"] = primary.get("youtube_url")
            data["youtube_title"] = primary.get("youtube_title")
            data["youtube_video_id"] = primary.get("youtube_video_id")
            data["youtube_language"] = primary.get("youtube_language")
            data["has_youtube"] = True
            data["has_ott"] = True
            data["ott_primary"] = "YouTube"
            data["ott_primary_key"] = "youtube"
            data["ott_count"] = max(int(data.get("ott_count") or 0), len(verified_links))
            data["is_free"] = True

            raw = data.get("raw")
            if isinstance(raw, dict):
                raw["youtube_url"] = primary.get("youtube_url")
                raw["youtube_title"] = primary.get("youtube_title")
                raw["youtube_video_id"] = primary.get("youtube_video_id")
                raw["youtube_language"] = primary.get("youtube_language")
                raw["has_youtube"] = 1
                raw["has_ott"] = 1
                raw["ott_primary"] = "YouTube"
                raw["ott_primary_key"] = "youtube"
        else:
            data["youtube_url"] = data.get("youtube_url")
            data["youtube_title"] = data.get("youtube_title")
            data["youtube_video_id"] = data.get("youtube_video_id")
            data["youtube_count"] = 0

        return data

    except Exception as exc:
        print("enrich_historical_youtube_detail_patch_v2 failed:", repr(exc))
        return data
# /FLIXYFY_HISTORICAL_YOUTUBE_DETAIL_PATCH_V2


@app.get("/api/v3/historical/movie/{slug}")
def get_historical_movie(slug: str):
    row = resolve_movie_by_slug(slug, preferred_domain="historical")

    if not row:
        raise HTTPException(status_code=404, detail="Historical movie not found")

    return movie_detail(row)


@app.get("/api/v3/historical/movies/{slug}")
def get_historical_movie_plural_alias(slug: str):
    return get_historical_movie(slug)


@app.get("/api/v3/hollywood/movie/{slug}")
def get_hollywood_movie(slug: str):
    row = resolve_movie_by_slug(slug, preferred_domain="hollywood")

    if not row:
        raise HTTPException(status_code=404, detail="Hollywood movie not found")

    return movie_detail(row)


@app.get("/api/v3/hollywood/movies/{slug}")
def get_hollywood_movie_plural_alias(slug: str):
    return get_hollywood_movie(slug)



@app.get("/api/v3/search")
def search(
    q: str = Query("", min_length=0),
    page: int = Query(1, ge=1),
    limit: int = Query(24, ge=1, le=100),
    language: Optional[str] = None,
    year: Optional[int] = None,
    has_ott: Optional[int] = None,
):
    offset = (page - 1) * limit
    query = q.strip()

    where = []
    params = []

    if query:
        where.append(
            "(LOWER(title) LIKE LOWER(%s) "
            "OR LOWER(COALESCE(original_title, '')) LIKE LOWER(%s))"
        )
        params.extend([f"%{query}%", f"%{query}%"])

    if language:
        where.append("language_slug = %s")
        params.append(language.strip().lower())

    if year:
        where.append("release_year = %s")
        params.append(year)

    if has_ott is not None:
        where.append("has_ott = %s")
        params.append(1 if has_ott else 0)

    where_sql = "WHERE " + " AND ".join(where) if where else ""

    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute(f"SELECT COUNT(*) AS total FROM {TABLE} {where_sql}", params)
        total = cur.fetchone()["total"]

        if query:
            order_sql = """
                CASE
                    WHEN LOWER(title) = LOWER(%s) THEN 1
                    WHEN LOWER(title) LIKE LOWER(%s) THEN 2
                    WHEN LOWER(COALESCE(original_title, '')) = LOWER(%s) THEN 3
                    WHEN LOWER(COALESCE(original_title, '')) LIKE LOWER(%s) THEN 4
                    ELSE 5
                END,
            """
            order_params = [query, f"{query}%", query, f"{query}%"]
        else:
            order_sql = ""
            order_params = []

        cur.execute(
            f"""
            SELECT *
            FROM {TABLE}
            {where_sql}
            ORDER BY
                {order_sql}
                has_ott DESC NULLS LAST,
                release_year DESC NULLS LAST,
                COALESCE(rating, 0) DESC NULLS LAST,
                title ASC
            LIMIT %s OFFSET %s
            """,
            params + order_params + [limit, offset],
        )
        rows = cur.fetchall()
    finally:
        conn.close()

    return {
        "query": query,
        "q": query,
        "page": page,
        "limit": limit,
        "total": total,
        "count": len(rows),
        "pages": (total + limit - 1) // limit,
        "items": [movie_card(r) for r in rows],
    }


@app.get("/api/v3/languages")
def languages():
    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            f"""
            SELECT
                language_slug,
                primary_language,
                COUNT(*) AS movie_count,
                SUM(CASE WHEN has_ott = 1 THEN 1 ELSE 0 END) AS ott_count
            FROM {TABLE}
            WHERE language_slug IS NOT NULL
            GROUP BY language_slug, primary_language
            ORDER BY movie_count DESC
            """
        )
        rows = cur.fetchall()
    finally:
        conn.close()

    return {
        "items": [
            {
                "language_slug": r["language_slug"],
                "primary_language": r["primary_language"],
                "movie_count": r["movie_count"],
                "ott_count": r["ott_count"],
                "url": f"/language/{r['language_slug']}",
            }
            for r in rows
        ]
    }


@app.get("/api/v3/language/{language_slug}")
def language_page(
    language_slug: str,
    page: int = Query(1, ge=1),
    limit: int = Query(24, ge=1, le=100),
    sort: str = Query("popular"),
    year: Optional[int] = None,
    availability: Optional[str] = None,
    provider: Optional[str] = None,
    has_ott: Optional[int] = None,
):
    return movies(
        page=page,
        limit=limit,
        language=language_slug,
        year=year,
        has_ott=has_ott,
        is_free=None,
        provider=provider,
        availability=availability,
        sort=sort,
    )


@app.get("/api/v3/ott-providers")
def ott_providers():
    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            f"""
            SELECT
                ott_primary_key,
                ott_primary,
                COUNT(*) AS movie_count
            FROM {TABLE}
            WHERE ott_primary IS NOT NULL
              AND TRIM(ott_primary) != ''
            GROUP BY ott_primary_key, ott_primary
            ORDER BY movie_count DESC
            """
        )
        rows = cur.fetchall()
    finally:
        conn.close()

    return {
        "items": [
            {
                "provider_key": r["ott_primary_key"],
                "provider": r["ott_primary"],
                "movie_count": r["movie_count"],
            }
            for r in rows
        ]
    }


@app.get("/api/v3/home")
def home():
    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            f"""
            SELECT *
            FROM {TABLE}
            WHERE poster_url IS NOT NULL
              AND poster_url != ''
              AND COALESCE(vote_count, 0) >= 20
              AND has_ott = 1
            ORDER BY COALESCE(rating, 0) DESC NULLS LAST, release_year DESC NULLS LAST, title ASC
            LIMIT 24
            """
        )
        trending = cur.fetchall()

        cur.execute(
            f"""
            SELECT *
            FROM {TABLE}
            WHERE poster_url IS NOT NULL
              AND poster_url != ''
            ORDER BY release_year DESC NULLS LAST, title ASC
            LIMIT 24
            """
        )
        latest = cur.fetchall()

        cur.execute(
            f"""
            SELECT *
            FROM {TABLE}
            WHERE poster_url IS NOT NULL
              AND poster_url != ''
              AND is_free = 1
            ORDER BY COALESCE(rating, 0) DESC NULLS LAST, release_year DESC NULLS LAST, title ASC
            LIMIT 24
            """
        )
        free = cur.fetchall()

        cur.execute(
            f"""
            SELECT *
            FROM {TABLE}
            WHERE language_slug = 'hindi'
              AND poster_url IS NOT NULL
              AND poster_url != ''
            ORDER BY COALESCE(rating, 0) DESC NULLS LAST, release_year DESC NULLS LAST, title ASC
            LIMIT 24
            """
        )
        hindi = cur.fetchall()

        cur.execute(
            f"""
            SELECT *
            FROM {TABLE}
            WHERE language_slug = 'telugu'
              AND poster_url IS NOT NULL
              AND poster_url != ''
            ORDER BY COALESCE(rating, 0) DESC NULLS LAST, release_year DESC NULLS LAST, title ASC
            LIMIT 24
            """
        )
        telugu = cur.fetchall()

        cur.execute(
            f"""
            SELECT *
            FROM {TABLE}
            WHERE language_slug = 'tamil'
              AND poster_url IS NOT NULL
              AND poster_url != ''
            ORDER BY COALESCE(rating, 0) DESC NULLS LAST, release_year DESC NULLS LAST, title ASC
            LIMIT 24
            """
        )
        tamil = cur.fetchall()
    finally:
        conn.close()

    return {
        "trending": [movie_card(r) for r in trending],
        "latest": [movie_card(r) for r in latest],
        "free": [movie_card(r) for r in free],
        "hindi": [movie_card(r) for r in hindi],
        "telugu": [movie_card(r) for r in telugu],
        "tamil": [movie_card(r) for r in tamil],
        "sections": [
            {"title": "Popular Movies", "items": [movie_card(r) for r in trending]},
            {"title": "Latest Movies", "items": [movie_card(r) for r in latest]},
            {"title": "Free to Watch", "items": [movie_card(r) for r in free]},
            {"title": "Hindi Movies", "items": [movie_card(r) for r in hindi]},
            {"title": "Telugu Movies", "items": [movie_card(r) for r in telugu]},
            {"title": "Tamil Movies", "items": [movie_card(r) for r in tamil]},
        ],
    }


@app.get("/api/v3/stats")
def stats():
    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute(f"SELECT COUNT(*) AS total FROM {TABLE}")
        total = cur.fetchone()["total"]

        cur.execute(f"SELECT COUNT(*) AS ott FROM {TABLE} WHERE has_ott = 1")
        ott = cur.fetchone()["ott"]

        cur.execute(f"SELECT COUNT(*) AS free FROM {TABLE} WHERE is_free = 1")
        free = cur.fetchone()["free"]

        cur.execute(f"SELECT COUNT(*) AS sub FROM {TABLE} WHERE has_subscription_ott = 1")
        sub = cur.fetchone()["sub"]

        cur.execute(f"SELECT COUNT(*) AS poster FROM {TABLE} WHERE poster_url IS NOT NULL AND poster_url != ''")
        poster = cur.fetchone()["poster"]

        cur.execute(f"SELECT COUNT(*) AS overview FROM {TABLE} WHERE overview IS NOT NULL AND overview != ''")
        overview = cur.fetchone()["overview"]
    finally:
        conn.close()

    return {
        "movies": total,
        "ott_coverage": ott,
        "free_ott": free,
        "subscription_ott": sub,
        "poster_coverage": poster,
        "overview_coverage": overview,
        "poster_coverage_percent": round((poster / total) * 100, 2) if total else 0,
        "overview_coverage_percent": round((overview / total) * 100, 2) if total else 0,
    }
from app.domain_routes_v1 import router as domain_router
app.include_router(domain_router)

