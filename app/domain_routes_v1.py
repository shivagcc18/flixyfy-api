import json
import os
import re
from functools import lru_cache
from typing import Optional, Dict, Any, List, Tuple
from urllib.parse import quote_plus

import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

from fastapi import APIRouter, Query, HTTPException

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")

def env_nonempty(name: str, default: str) -> str:
    value = os.getenv(name)
    if value is None or str(value).strip() == "":
        return default
    return str(value).strip()


MODERN_TABLE = env_nonempty("SERVING_TABLE", "current_movie_serving_v5_backend_compat")
MODERN_SEARCH_TABLE = env_nonempty("MODERN_SEARCH_TABLE", "current_movie_serving_v5_backend_compat")

WEBSERIES_SEARCH_TABLE = env_nonempty("WEBSERIES_SEARCH_TABLE", "webseries_series_serving_v5")
WEBSERIES_CARD_TABLE = env_nonempty("WEBSERIES_CARD_TABLE", WEBSERIES_SEARCH_TABLE)
WEBSERIES_DETAIL_TABLE = env_nonempty("WEBSERIES_DETAIL_TABLE", "webseries_series_serving_v5")
WEBSERIES_AVAILABILITY_TABLE = env_nonempty("WEBSERIES_AVAILABILITY_TABLE", "webseries_availability_serving_v5")

HOLLYWOOD_TABLE = env_nonempty("HOLLYWOOD_SERVING_TABLE", "hollywood_movie_serving_v5")
HOLLYWOOD_CARD_TABLE = env_nonempty("HOLLYWOOD_CARD_TABLE", HOLLYWOOD_TABLE)
HOLLYWOOD_DETAIL_TABLE = env_nonempty("HOLLYWOOD_DETAIL_TABLE", HOLLYWOOD_TABLE)
HOLLYWOOD_SEARCH_TABLE = env_nonempty("HOLLYWOOD_SEARCH_TABLE", "hollywood_search_serving_v5")
HOLLYWOOD_AVAILABILITY_TABLE = env_nonempty("HOLLYWOOD_AVAILABILITY_TABLE", "hollywood_availability_serving_v5")

HISTORICAL_TABLE = env_nonempty("HISTORICAL_SERVING_TABLE", "historical_movie_serving_v5")
HISTORICAL_CARD_TABLE = env_nonempty("HISTORICAL_CARD_TABLE", HISTORICAL_TABLE)
HISTORICAL_DETAIL_TABLE = env_nonempty("HISTORICAL_DETAIL_TABLE", HISTORICAL_TABLE)
HISTORICAL_SEARCH_TABLE = env_nonempty("HISTORICAL_SEARCH_TABLE", "historical_search_serving_v5")
HISTORICAL_AVAILABILITY_TABLE = env_nonempty("HISTORICAL_AVAILABILITY_TABLE", "historical_availability_serving_v5")
PEOPLE_SEARCH_CACHE_TABLE = env_nonempty("PEOPLE_SEARCH_CACHE_TABLE", "current_person_serving_v5")
PERSON_SLUG_REDIRECT_TABLE = env_nonempty("PERSON_SLUG_REDIRECT_TABLE", "person_slug_redirect_v1")
YOUTUBE_LINK_TABLE = env_nonempty("YOUTUBE_LINK_TABLE", "youtube_link_from_provider_v2")

router = APIRouter()


# FLIXYFY_HISTORICAL_PERSON_DETAIL_FIRST_MATCH_V2
@router.get("/api/v3/historical/person/{person_slug}")
def historical_person_detail_first_match_v2(person_slug: str, page: int = 1, limit: int = 96):
    alias_map = {
        "ntr": "n-t-rama-rao",
        "anr": "akkineni-nageshwara-rao",
        "mgr": "m-g-ramachandran",
    }

    requested_slug = str(person_slug or "").strip().lower()
    slug = alias_map.get(requested_slug, requested_slug)

    people_table = "historical_people_seo_preprod_fixed_v1"
    index_table = "historical_movie_people_seo_preprod_v1"
    serving_table = HISTORICAL_CARD_TABLE

    person_rows = _fhp_rows(
        f"""
        SELECT *
        FROM public.{qident(people_table)}
        WHERE person_slug = %s
        LIMIT 1
        """,
        [slug],
    )

    if not person_rows:
        raise HTTPException(status_code=404, detail="Historical person not found")

    person_row = person_rows[0]
    person = _fhp_person_payload(person_row)

    primary_language_slug = str(_fhp_pick(person_row, "primary_language_slug") or "").strip().lower()
    primary_count = _fhp_int(
        _fhp_pick(person_row, "primary_language_movie_count", "movie_count", "career_attached_movie_count"),
        0,
    )

    person["person_slug"] = _fhp_pick(person_row, "person_slug") or slug
    person["person_name"] = _fhp_pick(person_row, "person_name", "display_name") or person.get("person_name")
    person["display_name"] = _fhp_pick(person_row, "display_name", "person_name") or person.get("display_name")
    person["primary_language_slug"] = primary_language_slug
    person["primary_language_name"] = _fhp_pick(person_row, "primary_language_name")
    person["movie_count"] = primary_count
    person["primary_language_movie_count"] = primary_count
    person["total_movie_count"] = _fhp_int(
        _fhp_pick(person_row, "career_attached_movie_count", "total_movie_count", "movie_count"),
        primary_count,
    )
    person["profile_path"] = _fhp_pick(person_row, "profile_path") or f"/historical/person/{slug}"
    person["source_table"] = people_table

    page = max(1, int(page or 1))
    limit = max(1, min(int(limit or 96), 200))
    offset = (page - 1) * limit

    where = ["mp.person_slug = %s"]
    params = [slug]

    if primary_language_slug:
        where.append("LOWER(COALESCE(mp.language_slug, '')) = %s")
        params.append(primary_language_slug)

    where_sql = "WHERE " + " AND ".join(where)

    total_rows = _fhp_rows(
        f"""
        SELECT COUNT(DISTINCT mp.movie_slug) AS total
        FROM public.{qident(index_table)} mp
        {where_sql}
        """,
        params,
    )
    total = _fhp_int(total_rows[0].get("total") if total_rows else 0, 0)

    has_card_table = _fhp_table_exists(serving_table)

    if has_card_table:
        movie_rows = _fhp_rows(
            f"""
            SELECT
                h.*,
                mp.movie_slug AS mp_movie_slug,
                mp.title AS mp_title,
                mp.release_year AS mp_release_year,
                mp.language_slug AS mp_language_slug,
                mp.role_type AS role_type,
                mp.has_youtube AS mp_has_youtube
            FROM public.{qident(index_table)} mp
            LEFT JOIN public.{qident(serving_table)} h
              ON h.slug = mp.movie_slug
            {where_sql}
            ORDER BY COALESCE(mp.has_youtube, 0) DESC,
                     mp.release_year DESC NULLS LAST,
                     mp.title ASC
            LIMIT %s OFFSET %s
            """,
            params + [limit, offset],
        )
    else:
        movie_rows = _fhp_rows(
            f"""
            SELECT
                mp.movie_slug AS mp_movie_slug,
                mp.title AS mp_title,
                mp.release_year AS mp_release_year,
                mp.language_slug AS mp_language_slug,
                mp.role_type AS role_type,
                mp.has_youtube AS mp_has_youtube
            FROM public.{qident(index_table)} mp
            {where_sql}
            ORDER BY COALESCE(mp.has_youtube, 0) DESC,
                     mp.release_year DESC NULLS LAST,
                     mp.title ASC
            LIMIT %s OFFSET %s
            """,
            params + [limit, offset],
        )

    def detail_movie_card(row):
        slug_value = _fhp_pick(row, "slug", "mp_movie_slug", "movie_slug")
        title_value = _fhp_pick(row, "title", "mp_title")
        year_value = _fhp_int(_fhp_pick(row, "release_year", "mp_release_year"), None)
        language_value = _fhp_pick(row, "language_slug", "mp_language_slug") or primary_language_slug

        has_youtube = _fhp_bool(_fhp_pick(row, "has_youtube", "mp_has_youtube"))
        youtube_count = _fhp_int(_fhp_pick(row, "youtube_count"), 0)
        if has_youtube and youtube_count <= 0:
            youtube_count = 1

        return {
            "domain": "historical",
            "media_type": "movie",
            "type": "movie",
            "title": title_value,
            "slug": slug_value,
            "release_year": year_value,
            "year": year_value,
            "language_slug": language_value,
            "language": language_value,
            "poster_url": _fhp_pick(row, "poster_url", "poster", "image_url"),
            "overview": _fhp_pick(row, "overview", "description"),
            "rating": _fhp_pick(row, "rating", "vote_average"),
            "vote_count": _fhp_pick(row, "vote_count"),
            "has_ott": _fhp_bool(_fhp_pick(row, "has_ott", "ott_available")),
            "ott_count": _fhp_int(_fhp_pick(row, "ott_count"), 0),
            "has_youtube": has_youtube,
            "youtube_count": youtube_count,
            "role_type": _fhp_pick(row, "role_type"),
            "source_table": index_table,
        }

    items = [
        detail_movie_card(row)
        for row in movie_rows
        if _fhp_pick(row, "slug", "mp_movie_slug", "movie_slug")
    ]

    return {
        "domain": "historical",
        "type": "person",
        "source_table": people_table,
        "movie_index_table": index_table,
        "person": person,
        "page": page,
        "limit": limit,
        "total": total,
        "count": len(items),
        "pages": (total + limit - 1) // limit if total else 0,
        "items": items,
        "redirected_from": requested_slug if requested_slug != slug else None,
    }



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
    "youtube": "https://www.youtube.com/",
    "etv_win": "https://www.etvwin.com/",
    "apple_tv_store": "https://tv.apple.com/",
    "amazon_video": "https://www.amazon.com/video",
    "google_play_movies": "https://play.google.com/store/movies",
    "fandango_at_home": "https://www.fandangoathome.com/",
    "tubi_tv": "https://tubitv.com/",
    "plex": "https://watch.plex.tv/",
    "pluto_tv": "https://pluto.tv/",
    "disney_plus": "https://www.disneyplus.com/",
    "paramount_plus": "https://www.paramountplus.com/",
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
    "youtube": "https://www.youtube.com/results?search_query={q}",
    "etv_win": "https://www.etvwin.com/search?q={q}",
    "apple_tv_store": "https://tv.apple.com/search?term={q}",
    "amazon_video": "https://www.amazon.com/s?k={q}&i=instant-video",
    "google_play_movies": "https://play.google.com/store/search?q={q}&c=movies",
    "fandango_at_home": "https://www.fandangoathome.com/search?q={q}",
    "tubi_tv": "https://tubitv.com/search/{q}",
    "plex": "https://watch.plex.tv/search/?q={q}",
    "pluto_tv": "https://pluto.tv/search/details/{q}",
    "disney_plus": "https://www.disneyplus.com/search?q={q}",
    "paramount_plus": "https://www.paramountplus.com/search/?q={q}",
}


def get_conn():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL missing")
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)


def qident(value: str) -> str:
    return '"' + str(value).replace('"', '""') + '"'


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
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)

    return str(value).strip().lower() in {
        "1",
        "true",
        "yes",
        "y",
        "available",
        "youtube_available",
    }


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


WEB_LANGUAGE_ALIASES = {
    "hindi": ["hi", "hindi"],
    "telugu": ["te", "telugu"],
    "tamil": ["ta", "tamil"],
    "malayalam": ["ml", "malayalam"],
    "kannada": ["kn", "kannada"],
    "bengali": ["bn", "bengali"],
    "marathi": ["mr", "marathi"],
    "punjabi": ["pa", "punjabi"],
    "gujarati": ["gu", "gujarati"],
    "odia": ["or", "odia"],
    "assamese": ["as", "assamese"],
    "english": ["en", "english"],
    "korean": ["ko", "korean"],
    "japanese": ["ja", "japanese"],
    "chinese": ["zh", "chinese"],
}


WEB_PROVIDER_ALIASES = {
    "prime": ["amazon prime video", "prime video", "amazon prime", "prime"],
    "prime_video": ["amazon prime video", "prime video", "amazon prime", "prime"],
    "amazon_prime_video": ["amazon prime video", "prime video", "amazon prime", "prime"],
    "jiohotstar": ["jiohotstar", "hotstar", "disney hotstar"],
    "hotstar": ["jiohotstar", "hotstar", "disney hotstar"],
    "zee5": ["zee5", "zee 5"],
    "sonyliv": ["sony liv", "sonyliv", "sony-liv"],
    "sony_liv": ["sony liv", "sonyliv", "sony-liv"],
    "sunnxt": ["sun nxt", "sunnxt", "sun-nxt"],
    "sun_nxt": ["sun nxt", "sunnxt", "sun-nxt"],
    "etvwin": ["etv win", "etvwin", "etv-win"],
    "etv_win": ["etv win", "etvwin", "etv-win"],
    "vi_movies_and_tv": ["vi movies and tv", "vi movies", "vi"],
    "mx_player": ["mx player", "amazon mx player", "mxplayer"],
    "amazon_mx_player": ["mx player", "amazon mx player", "mxplayer"],
    "apple_tv": ["apple tv", "apple tv plus", "apple tv+"],
    "apple_tv_plus": ["apple tv", "apple tv plus", "apple tv+"],
    "disney_plus": ["disney plus", "disney+", "disney"],
    "hbo_max": ["max", "hbo max"],
    "max": ["max", "hbo max"],
    "rakuten_viki": ["rakuten viki", "viki"],
    "viki": ["rakuten viki", "viki"],
    "kocowa": ["kocowa", "kocowa+"],
    "tving": ["tving", "tvn"],
    "wavve": ["wavve"],
    "watcha": ["watcha"],
    "coupang_play": ["coupang play", "coupang"],
    "hoichoi": ["hoichoi"],
    "discovery": ["discovery", "discovery+"],
    "tubi": ["tubi", "tubi tv"],
    "tubi_tv": ["tubi", "tubi tv"],
}


def language_match_values(value):
    raw = str(value or "").strip().lower()
    if not raw:
        return []

    normalized = raw.replace("-", "_").replace(" ", "_")
    values = {raw, normalized}

    for item in WEB_LANGUAGE_ALIASES.get(normalized, []):
        values.add(item)

    return sorted(v for v in values if v)


def provider_match_terms(value):
    raw = str(value or "").strip().lower()
    if not raw:
        return []

    normalized = normalize_provider_key(raw)
    spaced = normalized.replace("_", " ")
    dashed = normalized.replace("_", "-")
    terms = {raw, normalized, spaced, dashed}

    for key in [normalized, raw.replace("-", "_").replace(" ", "_")]:
        for item in WEB_PROVIDER_ALIASES.get(key, []):
            terms.add(item)
            terms.add(item.replace("-", " "))
            terms.add(item.replace("_", " "))

    return sorted(t for t in terms if t)


def append_modern_provider_filters(where, params, provider=None, availability=None):
    if not table_exists("ott_availability_normalized_v2"):
        return where, params, []

    def add_where(condition: str):
        nonlocal where
        where = f"{where} AND {condition}" if where else f"WHERE {condition}"

    availability_value = str(availability or "").strip().lower()
    if availability_value in {"ott", "available", "true", "1"}:
        add_where(
            """
            CAST(tmdb_id AS TEXT) IN (
                SELECT CAST(a.tmdb_id AS TEXT)
                FROM public.ott_availability_normalized_v2 a
            )
            """
        )
    elif availability_value in {"youtube", "free"}:
        add_where(
            """
            CAST(tmdb_id AS TEXT) IN (
                SELECT CAST(a.tmdb_id AS TEXT)
                FROM public.ott_availability_normalized_v2 a
                WHERE LOWER(COALESCE(a.provider_category, a.provider_type, '')) IN ('free', 'free_with_ads', 'ads')
                   OR LOWER(COALESCE(a.provider_key, '')) LIKE '%%youtube%%'
                   OR LOWER(COALESCE(a.provider_display_name, '')) LIKE '%%youtube%%'
            )
            """
        )

    provider_terms = provider_match_terms(provider)
    if provider_terms:
        provider_conditions = []
        provider_params = []
        for term in provider_terms:
            provider_conditions.append("LOWER(COALESCE(a.provider_key, '')) LIKE %s")
            provider_params.append(f"%{term.replace(' ', '_').replace('-', '_')}%")
            provider_conditions.append("LOWER(COALESCE(a.provider_display_name, '')) LIKE %s")
            provider_params.append(f"%{term}%")

        add_where(
            f"""
            CAST(tmdb_id AS TEXT) IN (
                SELECT CAST(a.tmdb_id AS TEXT)
                FROM public.ott_availability_normalized_v2 a
                WHERE {" OR ".join(provider_conditions)}
            )
            """
        )
        params.extend(provider_params)

    return where, params, provider_terms


def provider_item_matches(item, provider_terms):
    if not provider_terms:
        return False

    provider_key = str(item.get("provider_key") or "").lower()
    provider_name = str(item.get("provider_display_name") or item.get("provider_name") or "").lower()
    provider_text = f"{provider_key} {provider_key.replace('_', ' ')} {provider_name}"

    for term in provider_terms:
        normalized = term.lower().replace("-", " ").replace("_", " ")
        key_term = term.lower().replace("-", "_").replace(" ", "_")
        if normalized and normalized in provider_text:
            return True
        if key_term and key_term in provider_key:
            return True

    return False


def prioritize_selected_provider(providers, provider):
    provider_terms = provider_match_terms(provider)
    if not provider_terms or not providers:
        return providers

    matched = [item for item in providers if provider_item_matches(item, provider_terms)]
    rest = [item for item in providers if not provider_item_matches(item, provider_terms)]
    return matched + rest


def first(row: Dict[str, Any], keys: List[str], default=None):
    for key in keys:
        value = row.get(key)

        if value is not None and str(value).strip() != "":
            return value

    return default


def list_value(value):
    if isinstance(value, list):
        return value

    parsed = parse_json(value, [])
    return parsed if isinstance(parsed, list) else []


def route_for(domain: str, slug: Optional[str]):
    if not slug:
        return None

    if domain == "modern":
        return f"/movie/{slug}"

    return f"/{domain}/{slug}"


def domain_label(domain: str) -> str:
    if domain == "hollywood":
        return "Hollywood"

    if domain == "historical":
        return "Historical Indian"

    return "Indian Movies"


@lru_cache(maxsize=256)
def table_exists(table_name: str) -> bool:
    conn = get_conn()
    cur = conn.cursor()

    try:
        cur.execute(
            f"""
            SELECT 1
            FROM information_schema.tables
            WHERE table_schema = 'public'
              AND table_name = %s
            UNION ALL
            SELECT 1
            FROM information_schema.views
            WHERE table_schema = 'public'
              AND table_name = %s
            LIMIT 1
            """,
            (table_name, table_name),
        )

        return cur.fetchone() is not None
    finally:
        conn.close()


@lru_cache(maxsize=256)
def table_columns_cached(table_name: str) -> Tuple[str, ...]:
    conn = get_conn()
    cur = conn.cursor()

    try:
        cur.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = %s
            ORDER BY ordinal_position
            """,
            (table_name,),
        )

        return tuple(r["column_name"] for r in cur.fetchall())
    finally:
        conn.close()


def table_columns(table_name: str) -> set:
    return set(table_columns_cached(table_name))


def count_table(cur, table_name: str) -> int:
    if not table_exists(table_name):
        return 0

    cur.execute(f"SELECT COUNT(*) AS total FROM public.{qident(table_name)}")
    return cur.fetchone()["total"]


def numeric_expr(col: str) -> str:
    return (
        f"COALESCE("
        f"NULLIF(regexp_replace(CAST({qident(col)} AS TEXT), '[^0-9\\\\.]+', '', 'g'), '')::DOUBLE PRECISION,"
        f"0)"
    )


def order_sql(table_name: str, sort: str):
    cols = table_columns(table_name)

    title_expr = "title ASC" if "title" in cols else "1 ASC"

    if "release_year" in cols:
        year_expr = "release_year DESC NULLS LAST"
    elif "year" in cols:
        year_expr = "year DESC NULLS LAST"
    else:
        year_expr = title_expr

    score_col = None
    for candidate in ["flixyfy_score", "quality_score", "popularity", "rating", "vote_average"]:
        if candidate in cols:
            score_col = candidate
            break

    score_expr = numeric_expr(score_col) if score_col else "(0)::DOUBLE PRECISION"

    if sort == "latest":
        return f"{year_expr}, {title_expr}"

    if sort == "rating":
        return f"{score_expr} DESC NULLS LAST, {year_expr}, {title_expr}"

    if sort == "title":
        return title_expr

    return f"{score_expr} DESC NULLS LAST, {year_expr}, {title_expr}"


def where_sql(
    table_name: str,
    search_query: str = "",
    language: Optional[str] = None,
    year: Optional[int] = None,
):
    cols = table_columns(table_name)
    where = []
    params = []

    if search_query:
        title_clauses = []
        normalized_query = "".join(ch.lower() for ch in search_query if ch.isalnum())

        for col in ["title", "original_title", "wiki_title"]:
            if col in cols:
                title_clauses.append(
                    "("
                    f"LOWER(CAST({qident(col)} AS TEXT)) LIKE LOWER(%s) "
                    "OR "
                    f"regexp_replace(LOWER(CAST({qident(col)} AS TEXT)), '[^a-z0-9]+', '', 'g') LIKE %s"
                    ")"
                )
                params.append(f"{search_query}%")
                params.append(f"{normalized_query}%")

        if title_clauses:
            where.append("(" + " OR ".join(title_clauses) + ")")

    if language:
        lang = language.strip().lower()

        if "language_slug" in cols:
            where.append("LOWER(CAST(language_slug AS TEXT)) = LOWER(%s)")
            params.append(lang)
        elif "primary_language" in cols:
            where.append("LOWER(CAST(primary_language AS TEXT)) = LOWER(%s)")
            params.append(lang)
        elif "language" in cols:
            where.append("LOWER(CAST(language AS TEXT)) = LOWER(%s)")
            params.append(lang)

    if year:
        if "release_year" in cols:
            where.append("release_year = %s")
            params.append(year)
        elif "year" in cols:
            where.append("year = %s")
            params.append(year)

    return ("WHERE " + " AND ".join(where)) if where else "", params


def domain_card(row: Dict[str, Any], domain: str):
    slug = row.get("slug")
    title = first(row, ["title", "movie_title", "name"])
    release_year = first(row, ["release_year", "year"])

    language_slug = first(row, ["language_slug", "primary_language_slug", "language"])
    language_name = first(row, ["language_name", "primary_language", "language"])

    poster = first(row, ["poster_url", "poster_path", "image_url", "poster"])
    backdrop = first(row, ["backdrop_url", "backdrop_path"])

    provider = first(row, ["ott_primary", "primary_provider", "top_provider"])
    provider_key = first(row, ["ott_primary_key", "provider_key"])

    has_ott_value = first(row, ["has_ott", "ott_available", "is_available"])
    is_free_value = first(row, ["is_free", "has_free_ott", "youtube_available"])

    return {
        "domain": domain,
        "source_domain": domain,
        "source_label": domain_label(domain),
        "id": first(row, ["id", "movie_id"]),
        "tmdb_id": row.get("tmdb_id"),
        "imdb_id": row.get("imdb_id"),
        "title": title,
        "original_title": first(row, ["original_title", "wiki_title"]),
        "slug": slug,
        "movie_url": route_for(domain, slug),
        "release_year": release_year,
        "year": release_year,
        "primary_language": language_name,
        "language_name": language_name,
        "language_slug": language_slug,
        "poster_url": fix_image_url(poster),
        "backdrop_url": fix_image_url(backdrop),
        "rating": first(row, ["rating", "vote_average", "imdb_rating"]),
        "vote_count": first(row, ["vote_count", "votes", "imdb_votes"]),
        "popularity": first(row, ["popularity", "quality_score", "flixyfy_score"]),
        "quality_score": first(row, ["quality_score", "flixyfy_score"]),
        "ott_primary": provider,
        "ott_primary_key": provider_key or normalize_provider_key(provider),
        "ott_count": first(row, ["ott_count", "provider_count", "confirmed_provider_count"]),
        "has_ott": as_bool(has_ott_value) if has_ott_value is not None else None,
        "has_free_ott": as_bool(row.get("has_free_ott")) if row.get("has_free_ott") is not None else None,
        "has_subscription_ott": as_bool(row.get("has_subscription_ott")) if row.get("has_subscription_ott") is not None else None,
        "has_rent_ott": as_bool(row.get("has_rent_ott")) if row.get("has_rent_ott") is not None else None,
        "has_buy_ott": as_bool(row.get("has_buy_ott")) if row.get("has_buy_ott") is not None else None,
        "is_free": as_bool(is_free_value) if is_free_value is not None else False,
    }


def fetch_rows(
    table_name: str,
    domain: str,
    page: int,
    limit: int,
    search_query: str = "",
    language: Optional[str] = None,
    year: Optional[int] = None,
    sort: str = "popular",
    provider: Optional[str] = None,
    availability: Optional[str] = None,
):
    if not table_exists(table_name):
        raise HTTPException(status_code=500, detail=f"Missing table: {table_name}")

    offset = (page - 1) * limit

    where, params = where_sql(
        table_name=table_name,
        search_query=search_query,
        language=language,
        year=year,
    )

    def add_where(condition: str):
        nonlocal where
        where = f"{where} AND {condition}" if where else f"WHERE {condition}"

    provider_text = str(provider or "").strip().lower()
    availability_text = str(availability or "").strip().lower()

    if domain == "modern":
        where, params, _ = append_modern_provider_filters(where, params, provider, availability)

    if domain == "hollywood" and table_exists(HOLLYWOOD_AVAILABILITY_TABLE):
        if availability_text == "ott":
            add_where(
                "tmdb_id IN ("
                f"SELECT tmdb_id FROM public.{qident(HOLLYWOOD_AVAILABILITY_TABLE)} "
                "WHERE COALESCE(has_confirmed_ott, 0) = 1 OR COALESCE(provider_count, 0) > 0"
                ")"
            )

        if availability_text == "free":
            add_where(
                "tmdb_id IN ("
                f"SELECT tmdb_id FROM public.{qident(HOLLYWOOD_AVAILABILITY_TABLE)} "
                "WHERE LOWER(COALESCE(provider_links_json, '')) LIKE %s"
                ")"
            )
            params.append("%youtube%")

        if provider_text:
            provider_like = provider_text.replace("_", " ")
            add_where(
                "tmdb_id IN ("
                f"SELECT tmdb_id FROM public.{qident(HOLLYWOOD_AVAILABILITY_TABLE)} "
                "WHERE LOWER(COALESCE(provider_links_json, '')) LIKE %s "
                "OR LOWER(COALESCE(provider_links_json, '')) LIKE %s"
                ")"
            )
            params.extend([f"%{provider_text}%", f"%{provider_like}%"])

    order = order_sql(table_name, sort)

    conn = get_conn()
    cur = conn.cursor()

    try:
        cur.execute(
            f"""
            SELECT COUNT(*) AS total
            FROM public.{qident(table_name)}
            {where}
            """,
            params,
        )
        true_total = int(cur.fetchone()["total"] or 0)

        cur.execute(
            f"""
            SELECT *
            FROM public.{qident(table_name)}
            {where}
            ORDER BY {order}
            LIMIT %s OFFSET %s
            """,
            params + [limit + 1, offset],
        )

        rows = cur.fetchall()
    finally:
        conn.close()

    has_more = len(rows) > limit
    rows = rows[:limit]
    total = true_total
    pages = (total + limit - 1) // limit if total else 0
    item_rows = [dict(r) for r in rows]

    if domain == "modern":
        ott_summaries = modern_ott_v2_summaries([r.get("tmdb_id") for r in item_rows])
        for row in item_rows:
            summary = ott_summaries.get(str(row.get("tmdb_id")))
            if not summary:
                continue

            watch_providers = prioritize_selected_provider(summary["watch_providers"], provider)
            row.update(summary)
            row["watch_providers"] = watch_providers
            row["availability"] = watch_providers
            row["ott_all"] = watch_providers
            if watch_providers:
                selected_primary = watch_providers[0]
                row["ott_primary"] = selected_primary.get("provider_display_name")
                row["ott_primary_key"] = selected_primary.get("provider_key") or normalize_provider_key(
                    selected_primary.get("provider_display_name")
                )

    return {
        "domain": domain,
        "source_domain": domain,
        "page": page,
        "limit": limit,
        "total": total,
        "pages": pages,
        "items": [domain_card(r, domain) for r in item_rows],
    }


def row_by_slug(table_name: str, slug: str):
    if not table_exists(table_name):
        return None

    if "slug" not in table_columns(table_name):
        return None

    conn = get_conn()
    cur = conn.cursor()

    try:
        cur.execute(
            f"""
            SELECT *
            FROM public.{qident(table_name)}
            WHERE slug = %s
            LIMIT 1
            """,
            (slug,),
        )

        row = cur.fetchone()

        return dict(row) if row else None
    finally:
        conn.close()


def availability_rows(domain: str, movie_id=None, tmdb_id=None, imdb_id=None, slug=None, title=None):
    table_name = HOLLYWOOD_AVAILABILITY_TABLE if domain == "hollywood" else HISTORICAL_AVAILABILITY_TABLE

    if not table_exists(table_name):
        return []

    cols = table_columns(table_name)
    where = []
    params = []

    # Hollywood availability table has its own row id. Do NOT join Hollywood by id.
    # Use tmdb_id / imdb_id / slug / title so Avatar does not pull another movie with the same local row id.
    if domain == "hollywood":
        if tmdb_id is not None and "tmdb_id" in cols:
            where.append("CAST(tmdb_id AS TEXT) = CAST(%s AS TEXT)")
            params.append(str(tmdb_id))

        if imdb_id and "imdb_id" in cols:
            where.append("CAST(imdb_id AS TEXT) = CAST(%s AS TEXT)")
            params.append(str(imdb_id))

        if slug and "slug" in cols:
            where.append("slug = %s")
            params.append(slug)

        if title and "title" in cols:
            where.append("LOWER(title) = LOWER(%s)")
            params.append(title)

    else:
        # Historical has stable local ids and some rows only have historical id/slug/title.
        if movie_id is not None and "id" in cols:
            where.append("CAST(id AS TEXT) = CAST(%s AS TEXT)")
            params.append(str(movie_id))

        if movie_id is not None and "movie_id" in cols:
            where.append("CAST(movie_id AS TEXT) = CAST(%s AS TEXT)")
            params.append(str(movie_id))

        if slug and "slug" in cols:
            where.append("slug = %s")
            params.append(slug)

        if title and "title" in cols:
            where.append("LOWER(title) = LOWER(%s)")
            params.append(title)

    if not where:
        return []

    where_clause = " OR ".join(f"({w})" for w in where)

    conn = get_conn()
    cur = conn.cursor()

    try:
        cur.execute(
            f"""
            SELECT *
            FROM public.{qident(table_name)}
            WHERE {where_clause}
            LIMIT 100
            """,
            params,
        )

        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def normalize_availability(rows: List[Dict[str, Any]], domain: str):
    expanded = []

    for row in rows:
        item = dict(row)

        provider_links_json = item.get("provider_links_json")

        if provider_links_json:
            try:
                links = json.loads(provider_links_json)

                if isinstance(links, list):
                    for link in links:
                        if not isinstance(link, dict):
                            continue

                        merged = dict(link)

                        merged.update(
                            {
                                "movie_id": item.get("id") or item.get("movie_id"),
                                "tmdb_id": item.get("tmdb_id"),
                                "imdb_id": item.get("imdb_id"),
                                "title": item.get("title"),
                                "slug": item.get("slug"),
                                "movie_url": item.get("movie_url"),
                                "release_year": item.get("release_year"),
                                "availability_status": item.get("availability_status"),
                                "source_layer": item.get("source_layer"),
                            }
                        )

                        expanded.append(merged)

                    continue
            except Exception:
                pass

        expanded.append(item)

    out = []
    seen = set()

    for row in expanded:
        item = dict(row)

        provider = first(
            item,
            [
                "provider_display_name",
                "provider_name",
                "provider",
                "ott_primary",
                "primary_provider",
                "top_provider",
            ],
        )

        provider_key = first(item, ["provider_key", "ott_primary_key"]) or normalize_provider_key(provider)

        provider_type = first(item, ["provider_type", "type", "category"])
        region = first(item, ["region", "country", "locale"])

        youtube_url = first(item, ["youtube_url", "video_url", "youtube_video_url"])

        youtube_title = first(item, ["youtube_title", "video_title"])

        final_url = first(
            item,
            [
                "final_url",
                "deep_link",
                "provider_deep_link",
                "url",
                "watch_url",
                "tmdb_watch_url",
                "youtube_url",
                "video_url",
            ],
        )

        if is_bad_watch_url(final_url):
            item.setdefault("tmdb_watch_url", final_url)
            final_url = None

        if not final_url and provider_key:
            title = item.get("title") or item.get("movie_title") or ""
            search_template = PROVIDER_SEARCH.get(provider_key)

            if search_template:
                final_url = search_template.format(q=quote_plus(title))

        if not final_url and provider_key:
            final_url = PROVIDER_HOME.get(provider_key)

        button_label = (
            item.get("button_label")
            or (f"Watch on {provider}" if provider else None)
            or ("Watch on YouTube" if youtube_url else "Watch")
        )

        dedupe_key = (
            str(provider_key or "").lower(),
            str(provider_type or "").lower(),
            str(region or "").upper(),
            str(final_url or "").lower(),
        )

        if dedupe_key in seen:
            continue

        seen.add(dedupe_key)

        item.update(
            {
                "domain": domain,
                "provider_key": provider_key,
                "provider_display_name": provider,
                "provider_type": provider_type,
                "region": region,
                "button_label": button_label,
                "final_url": final_url,
                "youtube_url": youtube_url,
                "youtube_title": youtube_title,
            }
        )

        out.append(item)

    def sort_key(item):
        region_score = 0 if str(item.get("region") or "").upper() == "IN" else 1
        type_order = {
            "subscription": 1,
            "free": 2,
            "free_with_ads": 3,
            "rent": 4,
            "buy": 5,
        }.get(str(item.get("provider_type") or "").lower(), 9)

        try:
            priority = int(item.get("display_priority") or item.get("priority") or 999)
        except Exception:
            priority = 999

        return (region_score, type_order, priority, str(item.get("provider_display_name") or ""))

    out.sort(key=sort_key)

    return out


def is_bad_watch_url(url) -> bool:
    value = str(url or "").strip().lower()
    return not value or "themoviedb.org/" in value or "justwatch.com/" in value


def has_direct_provider_url(rows: List[Dict[str, Any]]) -> bool:
    for row in rows or []:
        for key in ("deep_link", "provider_deep_link", "final_url", "watch_url", "url"):
            if not is_bad_watch_url(row.get(key)):
                return True
    return False


PROVIDER_LINK_TABLES = ("ott_availability_provider_links_v5", "ott_availability_provider_links_v2")
DOMAIN_PROVIDER_TABLES = {
    "hollywood": (
        "hollywood_availability_serving_v5",
        "hollywood_availability_serving_v3",
        "hollywood_availability_serving_v2",
        "hollywood_availability_serving_v1",
        "provider_availability_serving_v2",
        "provider_availability_serving_v1",
    ),
    "historical": (
        "historical_availability_serving_v5",
        "historical_availability_serving_v3",
        "historical_availability_serving_v2",
        "historical_availability_v2",
        "provider_availability_serving_v2",
        "provider_availability_serving_v1",
    ),
    "current": (
        "current_availability_serving_v5",
        "provider_availability_serving_v2",
        "provider_availability_serving_v1",
        "ott_availability_normalized_v2",
        "ott_availability_normalized_v1",
    ),
}


def provider_links_availability(tmdb_id, domain: str) -> List[Dict[str, Any]]:
    if tmdb_id is None:
        return []

    conn = get_conn()
    cur = conn.cursor()

    try:
        for table_name in PROVIDER_LINK_TABLES:
            if not table_exists(table_name):
                continue

            cur.execute(
                f"""
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
                FROM public.{qident(table_name)}
                WHERE CAST(tmdb_id AS TEXT) = CAST(%s AS TEXT)
                ORDER BY priority NULLS LAST, provider_display_name
                LIMIT 80
                """,
                (tmdb_id,),
            )

            rows = []
            for row in cur.fetchall():
                direct_url = (
                    row.get("provider_deep_link")
                    or (None if is_bad_watch_url(row.get("final_url")) else row.get("final_url"))
                    or row.get("provider_search_url")
                    or row.get("provider_homepage_url")
                )
                provider_name = row.get("provider_display_name")
                rows.append(
                    {
                        "domain": domain,
                        "provider_key": row.get("provider_key") or normalize_provider_key(provider_name),
                        "provider_display_name": provider_name,
                        "provider_category": row.get("provider_category"),
                        "provider_type": row.get("provider_type") or row.get("provider_category"),
                        "region": row.get("region"),
                        "deep_link": row.get("provider_deep_link"),
                        "provider_deep_link": row.get("provider_deep_link"),
                        "provider_search_url": row.get("provider_search_url"),
                        "provider_homepage_url": row.get("provider_homepage_url"),
                        "tmdb_watch_url": row.get("tmdb_watch_url"),
                        "final_url": direct_url,
                        "final_url_source": row.get("final_url_source"),
                        "button_label": row.get("button_label") or (f"Watch on {provider_name}" if provider_name else "Watch"),
                        "priority": row.get("priority"),
                        "source_table": table_name,
                    }
                )

            rows = [row for row in rows if row.get("provider_key") or row.get("provider_display_name")]
            if rows:
                return rows

        return []
    finally:
        conn.close()


def domain_values(domain: str) -> List[str]:
    return {
        "current": ["current", "indian", "movie", "movies"],
        "hollywood": ["hollywood", "global", "global_movie", "global_movies"],
        "historical": ["historical", "historical_movie", "historical_movies"],
        "webseries": ["webseries", "web_series", "series", "tv"],
    }.get(domain, [domain])


def generic_provider_availability(row: Dict[str, Any], domain: str) -> List[Dict[str, Any]]:
    tables = DOMAIN_PROVIDER_TABLES.get(domain, ())
    if not tables:
        return []

    conn = get_conn()
    cur = conn.cursor()

    try:
        for table_name in tables:
            if not table_exists(table_name):
                continue

            cols = table_columns(table_name)
            where = []
            params = []

            for col in ("tmdb_id", "content_tmdb_id", "movie_tmdb_id"):
                if row.get("tmdb_id") is not None and col in cols:
                    where.append(f"CAST({qident(col)} AS TEXT) = CAST(%s AS TEXT)")
                    params.append(str(row.get("tmdb_id")))

            for col in ("imdb_id", "content_imdb_id", "movie_imdb_id"):
                if row.get("imdb_id") and col in cols:
                    where.append(f"CAST({qident(col)} AS TEXT) = CAST(%s AS TEXT)")
                    params.append(str(row.get("imdb_id")))

            for col in ("slug", "content_slug", "movie_slug"):
                if row.get("slug") and col in cols:
                    where.append(f"{qident(col)} = %s")
                    params.append(row.get("slug"))

            title_col = next((col for col in ("title", "name", "content_title", "movie_title", "original_title") if col in cols), None)
            year_col = next((col for col in ("release_year", "year", "content_year", "movie_year") if col in cols), None)
            if title_col and row.get("title"):
                if year_col and row.get("release_year"):
                    where.append(f"(LOWER(TRIM(CAST({qident(title_col)} AS TEXT))) = LOWER(TRIM(%s)) AND CAST({qident(year_col)} AS TEXT) = CAST(%s AS TEXT))")
                    params.extend([row.get("title"), str(row.get("release_year"))])
                else:
                    where.append(f"LOWER(TRIM(CAST({qident(title_col)} AS TEXT))) = LOWER(TRIM(%s))")
                    params.append(row.get("title"))

            if not where:
                continue

            domain_col = next((col for col in ("domain", "content_domain", "media_domain", "source_domain", "content_type", "media_type") if col in cols), None)
            domain_sql = ""
            if domain_col:
                values = domain_values(domain)
                placeholders = ",".join(["%s"] * len(values))
                domain_sql = f" AND LOWER(CAST({qident(domain_col)} AS TEXT)) IN ({placeholders})"
                params.extend(values)

            cur.execute(
                f"""
                SELECT *
                FROM public.{qident(table_name)}
                WHERE ({" OR ".join(f"({w})" for w in where)}){domain_sql}
                LIMIT 100
                """,
                params,
            )

            rows = normalize_availability([dict(r) for r in cur.fetchall()], domain)
            rows = [
                item
                for item in rows
                if item.get("provider_key")
                or item.get("provider_display_name")
                or item.get("final_url")
                or item.get("provider_deep_link")
            ]
            if rows:
                for item in rows:
                    item.setdefault("source_table", table_name)
                return rows

        return []
    finally:
        conn.close()


def domain_detail(row: Dict[str, Any], domain: str):
    data = domain_card(row, domain)
    ott_summary = None

    raw_availability = availability_rows(
        domain=domain,
        movie_id=first(row, ["id", "movie_id"]),
        tmdb_id=row.get("tmdb_id"),
        imdb_id=row.get("imdb_id"),
        slug=row.get("slug"),
        title=row.get("title"),
    )

    availability = normalize_availability(raw_availability, domain)
    provider_link_rows = provider_links_availability(row.get("tmdb_id"), domain)

    if provider_link_rows and not has_direct_provider_url(availability):
        availability = provider_link_rows

    if not availability:
        availability = generic_provider_availability(row, domain)

    # Domain detail pages can miss provider rows when the domain-specific
    # availability table lags the normalized v2 OTT serving table.
    if not availability and row.get("tmdb_id"):
        ott_summary = modern_ott_v2_summaries([row.get("tmdb_id")]).get(str(row.get("tmdb_id")))
        if ott_summary:
            availability = ott_summary.get("watch_providers") or []

    if provider_link_rows and not has_direct_provider_url(availability):
        availability = provider_link_rows

    youtube_full_movies = []

    for item in availability:
        youtube_url = first(item, ["youtube_url", "video_url", "youtube_video_url", "final_url"])
        youtube_title = first(item, ["youtube_title", "video_title", "title"])

        status = str(item.get("availability_status") or "").lower()
        provider_key = str(item.get("provider_key") or "").lower()

        # Only historical YouTube matches are real free full-movie links.
        # Hollywood TMDB YouTube providers are rent/buy availability, not free full movies.
        is_real_youtube_full_movie = (
            domain == "historical"
            and youtube_url
            and (
                status == "youtube_available"
                or first(item, ["youtube_video_id", "video_id"])
                or first(item, ["youtube_match_type", "match_type"])
            )
        )

        if is_real_youtube_full_movie:
            youtube_full_movies.append(
                {
                    "video_id": first(item, ["youtube_video_id", "video_id"]),
                    "video_url": youtube_url,
                    "youtube_title": youtube_title,
                    "view_count": first(item, ["view_count", "youtube_view_count"]),
                    "match_type": first(item, ["youtube_match_type", "match_type"]),
                    "confidence": first(item, ["youtube_confidence", "confidence"]),
                    "people_hits": first(item, ["youtube_people_hits", "people_hits"]),
                }
            )

    data.update(
        {
            "overview": first(row, ["overview", "plot", "description", "wiki_overview"]),
            "runtime": first(row, ["runtime", "runtime_minutes"]),
            "genres": parse_json(first(row, ["genres", "genre"], "[]"), []),
            "director": first(row, ["director", "directors"]),
            "writers": first(row, ["writers", "writer"]),
            "actors": first(row, ["actors", "cast", "top_cast"]),
            "cast": first(row, ["cast", "actors", "top_cast"]),
            "certification": first(row, ["certification", "certificate"]),
            "trailer_url": row.get("trailer_url"),
            "production_companies": row.get("production_companies"),
            "availability": availability,
            "ott_all": availability,
            "watch_providers": availability,
            "youtube_full_movies": youtube_full_movies,
            "youtube_variants": youtube_full_movies,
            "youtube_count": len(youtube_full_movies),
            "raw": row,
        }
    )

    if ott_summary:
        data.update(
            {
                "ott_count": ott_summary.get("ott_count"),
                "ott_primary": ott_summary.get("ott_primary"),
                "ott_primary_key": ott_summary.get("ott_primary_key"),
                "has_ott": ott_summary.get("has_ott"),
                "has_free_ott": ott_summary.get("has_free_ott"),
                "has_subscription_ott": ott_summary.get("has_subscription_ott"),
                "has_rent_ott": ott_summary.get("has_rent_ott"),
                "has_buy_ott": ott_summary.get("has_buy_ott"),
            }
        )

    return data


def modern_card(row: Dict[str, Any]):
    slug = row.get("slug")
    providers = (
        list_value(row.get("watch_providers"))
        or list_value(row.get("ott_all"))
        or list_value(row.get("availability"))
    )

    return {
        "domain": "modern",
        "source_domain": "modern",
        "source_label": "Indian Movies",
        "id": row.get("id"),
        "tmdb_id": row.get("tmdb_id"),
        "title": row.get("title"),
        "original_title": row.get("original_title"),
        "slug": slug,
        "movie_url": route_for("modern", slug),
        "release_year": row.get("release_year"),
        "year": row.get("release_year"),
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
        "availability": providers,
        "ott_all": providers,
        "watch_providers": providers,
        "is_free": as_bool(row.get("is_free")),
    }


def modern_ott_v2_summaries(tmdb_ids: List[Any]) -> Dict[str, Dict[str, Any]]:
    clean_ids = []
    seen = set()

    for value in tmdb_ids:
        if value is None or str(value).strip() == "":
            continue

        key = str(value).strip()
        if key in seen:
            continue

        seen.add(key)
        clean_ids.append(key)

    if not clean_ids or not table_exists("ott_availability_normalized_v2"):
        return {}

    conn = get_conn()
    cur = conn.cursor()

    try:
        cur.execute(
            """
            SELECT tmdb_id, provider_key, provider_display_name, provider_category, provider_type,
                   region, final_url, deep_link, button_label, priority
            FROM public.ott_availability_normalized_v2
            WHERE CAST(tmdb_id AS TEXT) = ANY(%s)
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
            [clean_ids],
        )

        summaries: Dict[str, Dict[str, Any]] = {}

        for row in cur.fetchall():
            key = str(row.get("tmdb_id"))
            summary = summaries.setdefault(
                key,
                {
                    "ott_count": 0,
                    "ott_primary": None,
                    "ott_primary_key": None,
                    "has_ott": False,
                    "has_free_ott": False,
                    "has_subscription_ott": False,
                    "has_rent_ott": False,
                    "has_buy_ott": False,
                    "watch_providers": [],
                },
            )

            category = str(row.get("provider_category") or row.get("provider_type") or "").strip().lower()
            provider_name = row.get("provider_display_name")
            provider_key = row.get("provider_key") or normalize_provider_key(provider_name)
            final_url = row.get("final_url") or row.get("deep_link")

            summary["ott_count"] += 1
            summary["has_ott"] = True

            if not summary["ott_primary"]:
                summary["ott_primary"] = provider_name
                summary["ott_primary_key"] = provider_key

            if category in {"free", "free_with_ads", "ads"}:
                summary["has_free_ott"] = True
            if category in {"subscription", "flatrate"}:
                summary["has_subscription_ott"] = True
            if category == "rent":
                summary["has_rent_ott"] = True
            if category == "buy":
                summary["has_buy_ott"] = True

            summary["watch_providers"].append(
                {
                    "provider_key": provider_key,
                    "provider_display_name": provider_name,
                    "provider_category": row.get("provider_category"),
                    "provider_type": row.get("provider_type") or row.get("provider_category"),
                    "region": row.get("region"),
                    "final_url": final_url,
                    "deep_link": row.get("deep_link"),
                    "button_label": row.get("button_label") or (f"Watch on {provider_name}" if provider_name else "Watch"),
                    "priority": row.get("priority"),
                }
            )

        return summaries
    finally:
        conn.close()


def search_modern(
    query: str,
    limit: int,
    language: Optional[str],
    year: Optional[int],
    provider: Optional[str] = None,
    availability: Optional[str] = None,
    sort: str = "popular",
    _table_name: Optional[str] = None,
    _allow_fallback: bool = True,
):
    table_name = _table_name or (MODERN_SEARCH_TABLE if table_exists(MODERN_SEARCH_TABLE) else MODERN_TABLE)

    if not table_exists(table_name):
        return 0, []

    cols = table_columns(table_name)
    where = []
    params = []

    if query:
        compact_query = re.sub(r"[^a-z0-9]+", "", query.lower())
        search_clauses = []

        for col in ("title", "original_title", "wiki_title", "slug"):
            if col not in cols:
                continue
            expr = f"CAST({qident(col)} AS TEXT)"
            if compact_query:
                search_clauses.append(
                    "("
                    f"LOWER({expr}) LIKE LOWER(%s) "
                    "OR "
                    f"regexp_replace(LOWER({expr}), '[^a-z0-9]+', '', 'g') LIKE %s"
                    ")"
                )
                params.extend([f"%{query}%", f"%{compact_query}%"])
            else:
                search_clauses.append(f"LOWER({expr}) LIKE LOWER(%s)")
                params.append(f"%{query}%")

        for col in ("aliases_json", "aliases", "alternate_titles", "aka", "search_text"):
            if col not in cols:
                continue
            expr = f"CAST({qident(col)} AS TEXT)"
            if compact_query:
                search_clauses.append(
                    "("
                    f"LOWER({expr}) LIKE LOWER(%s) "
                    "OR "
                    f"regexp_replace(LOWER({expr}), '[^a-z0-9]+', '', 'g') LIKE %s"
                    ")"
                )
                params.extend([f"%{query}%", f"%{compact_query}%"])
            else:
                search_clauses.append(f"LOWER({expr}) LIKE LOWER(%s)")
                params.append(f"%{query}%")

        if search_clauses:
            where.append("(" + " OR ".join(search_clauses) + ")")

    if language:
        where.append("language_slug = %s")
        params.append(language.strip().lower())

    if year:
        where.append("release_year = %s")
        params.append(year)

    where_clause = "WHERE " + " AND ".join(where) if where else ""
    where_clause, params, provider_terms = append_modern_provider_filters(
        where_clause,
        params,
        provider,
        availability,
    )

    conn = get_conn()
    cur = conn.cursor()

    try:
        cur.execute(
            f"SELECT COUNT(*) AS total FROM public.{qident(table_name)} {where_clause}",
            params,
        )
        total = cur.fetchone()["total"]
        order_params = []

        if query:
            order_sql = """
                CASE
                    WHEN LOWER(title) = LOWER(%s) THEN 1
                    WHEN regexp_replace(LOWER(CAST(title AS TEXT)), '[^a-z0-9]+', '', 'g') = %s THEN 2
                    WHEN LOWER(COALESCE(original_title, '')) = LOWER(%s) THEN 3
                    WHEN regexp_replace(LOWER(COALESCE(CAST(original_title AS TEXT), '')), '[^a-z0-9]+', '', 'g') = %s THEN 4
                    WHEN LOWER(title) LIKE LOWER(%s) THEN 5
                    WHEN LOWER(COALESCE(original_title, '')) LIKE LOWER(%s) THEN 6
                    WHEN regexp_replace(LOWER(CAST(title AS TEXT)), '[^a-z0-9]+', '', 'g') LIKE %s THEN 7
                    WHEN regexp_replace(LOWER(COALESCE(CAST(original_title AS TEXT), '')), '[^a-z0-9]+', '', 'g') LIKE %s THEN 8
                    ELSE 9
                END,
            """
            compact_query = re.sub(r"[^a-z0-9]+", "", query.lower())
            order_params = [
                query,
                compact_query,
                query,
                compact_query,
                f"{query}%",
                f"{query}%",
                f"{compact_query}%",
                f"{compact_query}%",
            ]
        else:
            order_sql = ""

        sort_value = str(sort or "popular").strip().lower()
        rating_expr = "COALESCE(rating, 0)"
        vote_expr = "COALESCE(vote_count, 0)"
        if sort_value == "latest":
            ranking_sql = (
                f"release_year DESC NULLS LAST, {rating_expr} DESC NULLS LAST, "
                f"{vote_expr} DESC NULLS LAST, title ASC"
            )
        elif sort_value in {"rating", "imdb", "top_imdb", "top-imdb"}:
            ranking_sql = (
                f"{rating_expr} DESC NULLS LAST, release_year DESC NULLS LAST, "
                f"{vote_expr} DESC NULLS LAST, title ASC"
            )
        elif provider_terms:
            ranking_sql = (
                f"{rating_expr} DESC NULLS LAST, COALESCE(popularity, quality_score, 0) DESC, "
                f"release_year DESC NULLS LAST, title ASC"
            )
        else:
            ranking_sql = (
                "release_year DESC NULLS LAST, "
                "has_ott DESC NULLS LAST, "
                "(poster_url IS NOT NULL AND TRIM(CAST(poster_url AS TEXT)) <> '') DESC, "
                f"{rating_expr} DESC NULLS LAST, "
                "title ASC"
            )

        cur.execute(
            f"""
            SELECT *
            FROM public.{qident(table_name)}
            {where_clause}
            ORDER BY
                {order_sql}
                {ranking_sql}
            LIMIT %s
            """,
            params + order_params + [limit],
        )

        rows = [dict(r) for r in cur.fetchall()]
        ott_summaries = modern_ott_v2_summaries([r.get("tmdb_id") for r in rows])
        items = []

        for row in rows:
            summary = ott_summaries.get(str(row.get("tmdb_id")))
            if summary:
                watch_providers = prioritize_selected_provider(summary["watch_providers"], provider)
                row.update(summary)
                row["watch_providers"] = watch_providers
                row["ott_all"] = watch_providers
                row["availability"] = watch_providers
                if watch_providers:
                    selected_primary = watch_providers[0]
                    row["ott_primary"] = selected_primary.get("provider_display_name")
                    row["ott_primary_key"] = selected_primary.get("provider_key") or normalize_provider_key(
                        selected_primary.get("provider_display_name")
                    )

            items.append(modern_card(row))
    finally:
        conn.close()

    if query and _allow_fallback and total == 0:
        for fallback_table in (MODERN_TABLE, "current_movie_serving_v5_backend_compat", "current_movie_serving_v5"):
            if fallback_table == table_name or not table_exists(fallback_table):
                continue

            fallback_total, fallback_items = search_modern(
                query=query,
                limit=limit,
                language=language,
                year=year,
                provider=provider,
                availability=availability,
                sort=sort,
                _table_name=fallback_table,
                _allow_fallback=False,
            )

            if fallback_total > 0 or fallback_items:
                return fallback_total, fallback_items

    return total, items


def search_domain(table_name: str, domain: str, query: str, limit: int, language: Optional[str], year: Optional[int]):
    if not table_exists(table_name):
        return 0, []

    where, params = where_sql(
        table_name=table_name,
        search_query=query,
        language=language,
        year=year,
    )

    cols = table_columns(table_name)
    order_params = []

    if query:
        title_col = "title" if "title" in cols else None
        original_col = "original_title" if "original_title" in cols else None
        match_parts = []

        if title_col:
            match_parts.extend(
                [
                    f"WHEN LOWER({qident(title_col)}) = LOWER(%s) THEN 1",
                    f"WHEN LOWER({qident(title_col)}) LIKE LOWER(%s) THEN 2",
                ]
            )
            order_params.extend([query, f"{query}%"])

        if original_col:
            match_parts.extend(
                [
                    f"WHEN LOWER(COALESCE({qident(original_col)}, '')) = LOWER(%s) THEN 3",
                    f"WHEN LOWER(COALESCE({qident(original_col)}, '')) LIKE LOWER(%s) THEN 4",
                ]
            )
            order_params.extend([query, f"{query}%"])

        match_sql = "CASE " + " ".join(match_parts) + " ELSE 5 END," if match_parts else ""
        youtube_sql = "COALESCE(has_youtube, 0) DESC," if "has_youtube" in cols else ""
        ott_sql = "COALESCE(has_ott, 0) DESC," if "has_ott" in cols else ""
        poster_sql = "(poster_url IS NOT NULL AND TRIM(CAST(poster_url AS TEXT)) <> '') DESC," if "poster_url" in cols else ""
        language_expr = None
        for language_col in ["language_slug", "primary_language", "language"]:
            if language_col in cols:
                language_expr = f"LOWER(COALESCE(CAST({qident(language_col)} AS TEXT), ''))"
                break
        tier1_language_sql = (
            f"CASE WHEN {language_expr} IN ('telugu', 'tamil', 'hindi', 'kannada', 'malayalam') THEN 0 "
            f"WHEN {language_expr} IN ('bengali', 'marathi', 'gujarati', 'punjabi', 'odia', 'assamese', 'bhojpuri', 'urdu', 'sanskrit') THEN 1 "
            "ELSE 2 END,"
            if language_expr
            else ""
        )
        year_sql = "release_year DESC NULLS LAST," if "release_year" in cols else ("year DESC NULLS LAST," if "year" in cols else "")
        score_col = next((col for col in ["quality_score", "popularity", "rating", "vote_average"] if col in cols), None)
        score_sql = f"COALESCE({qident(score_col)}, 0) DESC NULLS LAST," if score_col else ""
        if domain == "historical":
            order = f"{match_sql} {youtube_sql} {ott_sql} {poster_sql} {tier1_language_sql} {year_sql} {score_sql} title ASC"
        else:
            order = f"{match_sql} {year_sql} {youtube_sql} {ott_sql} {poster_sql} {score_sql} title ASC"
    else:
        order = order_sql(table_name, "popular")

    conn = get_conn()
    cur = conn.cursor()

    try:
        total = None
        if not query:
            cur.execute(
                f"SELECT COUNT(*) AS total FROM public.{qident(table_name)} {where}",
                params,
            )
            total = cur.fetchone()["total"]

        cur.execute(
            f"""
            SELECT *
            FROM public.{qident(table_name)}
            {where}
            ORDER BY {order}
            LIMIT %s
            """,
            params + order_params + [limit],
        )

        items = [domain_card(dict(r), domain) for r in cur.fetchall()]
        if total is None:
            total = len(items) + (1 if len(items) == limit else 0)
    finally:
        conn.close()

    return total, items


def webseries_card(row: Dict[str, Any], domain: str = "webseries", source_label: str = "Webseries"):
    providers = parse_json(row.get("card_provider_names") or row.get("provider_names") or row.get("provider_summary"), [])
    major_providers = parse_json(row.get("major_provider_names"), [])
    provider_list = major_providers if major_providers else providers
    primary_provider = provider_list[0] if provider_list else None
    imdb_rating = row.get("omdb_imdb_rating")
    try:
        imdb_rating_value = float(imdb_rating) if imdb_rating not in {None, "", "N/A"} else None
    except Exception:
        imdb_rating_value = None
    rating = row.get("vote_average") or row.get("rating") or imdb_rating_value
    latest_air_year = row.get("latest_air_year") or row.get("card_latest_air_year")
    latest_air_date = row.get("latest_air_date") or row.get("card_latest_air_date")
    display_year = latest_air_year or row.get("first_air_year") or row.get("release_year")

    return {
        "domain": domain,
        "content_type": domain,
        "source_label": source_label,
        "tmdb_id": row.get("tmdb_id"),
        "imdb_id": row.get("imdb_id"),
        "title": row.get("title"),
        "slug": row.get("slug"),
        "movie_url": f"/webseries/{row.get('slug')}" if row.get("slug") else None,
        "release_year": display_year,
        "year": display_year,
        "first_air_year": row.get("first_air_year") or row.get("release_year"),
        "latest_air_year": latest_air_year,
        "latest_air_date": latest_air_date,
        "primary_language": row.get("original_language") or row.get("language_name") or row.get("language_slug"),
        "language_slug": row.get("original_language") or row.get("language_slug"),
        "poster_url": fix_image_url(row.get("poster_path") or row.get("poster_url")),
        "backdrop_url": fix_image_url(row.get("backdrop_path") or row.get("backdrop_url")),
        "rating": rating,
        "imdb_rating": imdb_rating_value,
        "vote_count": row.get("card_vote_count") or row.get("vote_count"),
        "popularity": row.get("popularity_score") or row.get("popularity"),
        "quality_score": row.get("popularity_score") or row.get("confidence"),
        "ott_primary": primary_provider,
        "ott_primary_key": normalize_provider_key(primary_provider),
        "ott_count": row.get("availability_count"),
        "has_ott": as_bool(row.get("has_major_provider")) or bool(primary_provider),
        "raw": dict(row),
    }


def webseries_catalog_filters(cols=None):
    cols = set(cols or [])
    if not cols:
        return []

    poster_col = "poster_path" if "poster_path" in cols else ("poster_url" if "poster_url" in cols else None)
    filters = []
    if poster_col:
        filters.extend([
            f"s.{qident(poster_col)} IS NOT NULL",
            f"TRIM(CAST(s.{qident(poster_col)} AS TEXT)) <> ''",
        ])

    quality_parts = []
    if "vote_count" in cols:
        quality_parts.append("COALESCE(s.vote_count, 0) > 0")
    if "omdb_imdb_rating" in cols:
        quality_parts.append("CAST(s.omdb_imdb_rating AS TEXT) ~ '^[0-9]+(\\.[0-9]+)?$'")
    if quality_parts:
        filters.append("(" + " OR ".join(quality_parts) + ")")

    genre_program_sql = """
        (
            LOWER(COALESCE(s.genres, '')) LIKE '%%reality%%'
            OR LOWER(COALESCE(s.genres, '')) LIKE '%%talk%%'
            OR LOWER(COALESCE(s.genres, '')) LIKE '%%news%%'
            OR LOWER(COALESCE(s.genres, '')) LIKE '%%game show%%'
        )
    """
    title_program_sql = """
        (
            LOWER(COALESCE(s.title, '')) LIKE '%%bigg boss%%'
            OR LOWER(COALESCE(s.title, '')) LIKE '%%celebrity%%'
            OR LOWER(COALESCE(s.title, '')) LIKE '%%challenge%%'
            OR LOWER(COALESCE(s.title, '')) LIKE '%%juniors%%'
            OR LOWER(COALESCE(s.title, '')) LIKE '%%superstar%%'
            OR LOWER(COALESCE(s.title, '')) LIKE '%%super family%%'
            OR LOWER(COALESCE(s.title, '')) LIKE '%%sa re ga ma%%'
            OR LOWER(COALESCE(s.title, '')) LIKE '%%dance%%'
            OR LOWER(COALESCE(s.title, '')) LIKE '%%singing%%'
            OR LOWER(COALESCE(s.title, '')) LIKE '%%awards%%'
            OR LOWER(COALESCE(s.title, '')) LIKE '%%award%%'
        )
    """
    daily_program_sql = """
        (
            {vote_zero}
            AND {rating_missing}
            AND (
                {daily_inner}
            )
        )
    """.format(
        vote_zero="COALESCE(s.vote_count, 0) = 0" if "vote_count" in cols else "TRUE",
        rating_missing="NOT (CAST(s.omdb_imdb_rating AS TEXT) ~ '^[0-9]+(\\.[0-9]+)?$')" if "omdb_imdb_rating" in cols else "TRUE",
        daily_inner=" OR ".join(
            part for part in [
                "LOWER(COALESCE(s.genres, '')) LIKE '%%family%%'" if "genres" in cols else None,
                "LOWER(COALESCE(s.genres, '')) LIKE '%%soap%%'" if "genres" in cols else None,
                f"s.{qident(poster_col)} IS NULL" if poster_col else None,
                f"TRIM(CAST(s.{qident(poster_col)} AS TEXT)) = ''" if poster_col else None,
            ]
            if part
        ) or "FALSE",
    )

    if "genres" in cols:
        filters.append(f"NOT {genre_program_sql}")
    filters.append(f"NOT {title_program_sql}")
    filters.append(f"NOT {daily_program_sql}")
    return filters


def search_webseries(
    query: str,
    limit: int,
    scope: str,
    year: Optional[int],
    language: Optional[str] = None,
    provider: Optional[str] = None,
    availability: Optional[str] = None,
    sort: str = "popular",
):
    if not table_exists(WEBSERIES_SEARCH_TABLE):
        return 0, []

    cols = set(table_columns(WEBSERIES_SEARCH_TABLE))
    where = []
    params = []

    if query:
        query_parts = []
        for col in ("title", "original_title", "normalized_title", "search_text"):
            if col in cols:
                query_parts.append(f"LOWER(COALESCE(CAST(s.{qident(col)} AS TEXT), '')) LIKE LOWER(%s)")
                params.append(f"{query}%")
        if query_parts:
            where.append("(" + " OR ".join(query_parts) + ")")

    scope_value = str(scope or "").strip().lower()
    indian_languages = ['hi', 'te', 'ta', 'ml', 'kn', 'bn', 'mr', 'pa', 'gu', 'or', 'as']
    domain_col = "region" if "region" in cols else ("domain" if "domain" in cols else None)
    language_col = "original_language" if "original_language" in cols else (
        "language_slug" if "language_slug" in cols else ("language" if "language" in cols else None)
    )

    if scope_value in {'indian', 'india', 'in'}:
        scope_parts = []
        if domain_col:
            scope_parts.append(f"LOWER(COALESCE(CAST(s.{qident(domain_col)} AS TEXT), '')) IN ('indian', 'india', 'in', 'current')")
        if language_col:
            scope_parts.append(f"LOWER(COALESCE(CAST(s.{qident(language_col)} AS TEXT), '')) = ANY(%s)")
            params.append(indian_languages)
        if "country" in cols:
            scope_parts.append("LOWER(COALESCE(CAST(s.country AS TEXT), '')) IN ('in', 'india')")
        if scope_parts:
            where.append("(" + " OR ".join(scope_parts) + ")")
    elif scope_value in {'korean', 'korea', 'kr'}:
        scope_parts = []
        if domain_col:
            scope_parts.append(f"LOWER(COALESCE(CAST(s.{qident(domain_col)} AS TEXT), '')) = 'korean'")
        if language_col:
            scope_parts.append(f"LOWER(COALESCE(CAST(s.{qident(language_col)} AS TEXT), '')) = 'ko'")
        if "country" in cols:
            scope_parts.append("LOWER(COALESCE(CAST(s.country AS TEXT), '')) IN ('kr', 'korea', 'south korea')")
        if scope_parts:
            where.append("(" + " OR ".join(scope_parts) + ")")
    elif scope_value in {'global', 'world', 'international'}:
        scope_parts = []
        if domain_col:
            scope_parts.append(f"LOWER(COALESCE(CAST(s.{qident(domain_col)} AS TEXT), '')) NOT IN ('indian', 'india', 'in', 'current')")
        if language_col:
            scope_parts.append(f"LOWER(COALESCE(CAST(s.{qident(language_col)} AS TEXT), '')) <> ALL(%s)")
            params.append(indian_languages)
        if scope_parts:
            where.append("(" + " AND ".join(scope_parts) + ")")

    if year:
        year_col = "first_air_year" if "first_air_year" in cols else ("release_year" if "release_year" in cols else None)
        if year_col:
            where.append(f"s.{qident(year_col)} = %s")
            params.append(year)

    language_values = language_match_values(language)
    if language_values and language_col:
        where.append(f"LOWER(COALESCE(CAST(s.{qident(language_col)} AS TEXT), '')) = ANY(%s)")
        params.append(language_values)

    availability_value = str(availability or "").strip().lower()
    if availability_value in {"ott", "available", "true", "1"}:
        availability_parts = []
        if "has_major_provider" in cols:
            availability_parts.append("COALESCE(s.has_major_provider, 0) = 1")
        if "availability_row_count" in cols:
            availability_parts.append("COALESCE(s.availability_row_count, 0) > 0")
        if "availability_count" in cols:
            availability_parts.append("COALESCE(s.availability_count, 0) > 0")
        if availability_parts:
            where.append("(" + " OR ".join(availability_parts) + ")")
    elif availability_value in {"youtube", "free"}:
        free_parts = []
        for col in ("provider_search_text", "provider_names", "provider_summary", "availability_json"):
            if col in cols:
                free_parts.append(f"LOWER(COALESCE(CAST(s.{qident(col)} AS TEXT), '')) LIKE '%%youtube%%'")
        if free_parts:
            where.append("(" + " OR ".join(free_parts) + ")")

    provider_terms = provider_match_terms(provider)
    if provider_terms:
        provider_conditions = []
        provider_params = []
        for term in provider_terms:
            for col in ("provider_names", "provider_search_text", "provider_summary", "availability_json"):
                if col in cols:
                    provider_conditions.append(f"LOWER(COALESCE(CAST(s.{qident(col)} AS TEXT), '')) LIKE %s")
                    provider_params.append(f"%{term}%")

        if provider_conditions:
            where.append(f"({' OR '.join(provider_conditions)})")
            params.extend(provider_params)

    where.extend(webseries_catalog_filters(cols))

    where_clause = "WHERE " + " AND ".join(where) if where else ""

    conn = get_conn()
    cur = conn.cursor()

    try:
        cur.execute(
            f"""
            SELECT COUNT(*) AS total
            FROM public.{qident(WEBSERIES_SEARCH_TABLE)} s
            {where_clause}
            """,
            params,
        )
        total = cur.fetchone()["total"]

        order_params = []
        if query:
            order_parts = [
                "WHEN LOWER(s.title) = LOWER(%s) THEN 1",
                "WHEN LOWER(s.title) LIKE LOWER(%s) THEN 2",
            ]
            order_params = [query, f"{query}%"]
            if "normalized_title" in cols:
                order_parts.extend([
                    "WHEN LOWER(COALESCE(s.normalized_title, '')) = LOWER(%s) THEN 3",
                    "WHEN LOWER(COALESCE(s.normalized_title, '')) LIKE LOWER(%s) THEN 4",
                ])
                order_params.extend([query, f"{query}%"])
            order_sql = "CASE " + " ".join(order_parts) + " ELSE 5 END,"
        else:
            order_sql = ""

        rating_sources = []
        if "omdb_imdb_rating" in cols:
            rating_sources.append(
                "CASE WHEN CAST(s.omdb_imdb_rating AS TEXT) ~ '^[0-9]+(\\.[0-9]+)?$' "
                "THEN s.omdb_imdb_rating::DOUBLE PRECISION ELSE NULL END"
            )
        rating_sources.extend(f"s.{qident(col)}" for col in ("vote_average", "rating") if col in cols)
        rating_expr = "COALESCE(" + ", ".join(rating_sources + ["0"]) + ")"
        vote_expr = "COALESCE(s.vote_count, 0)" if "vote_count" in cols else "CAST(0 AS numeric)"
        year_sources = [f"s.{qident(col)}" for col in ("latest_air_year", "first_air_year", "release_year") if col in cols]
        year_expr = "COALESCE(" + ", ".join(year_sources + ["0"]) + ")"
        popularity_expr = "COALESCE(s.popularity_score, 0)" if "popularity_score" in cols else (
            "COALESCE(s.popularity, 0)" if "popularity" in cols else (
                "COALESCE(s.rank_score, 0)" if "rank_score" in cols else "CAST(0 AS numeric)"
            )
        )
        provider_rank_expr = "COALESCE(s.has_major_provider, 0)" if "has_major_provider" in cols else "CAST(0 AS numeric)"
        sort_value = str(sort or "popular").strip().lower()

        if sort_value == "latest":
            ranking_sql = (
                f"{year_expr} DESC NULLS LAST, {rating_expr} DESC NULLS LAST, "
                f"{vote_expr} DESC NULLS LAST, s.title ASC"
            )
        elif sort_value in {"rating", "imdb", "top_imdb", "top-imdb"}:
            ranking_sql = (
                f"{rating_expr} DESC NULLS LAST, {year_expr} DESC NULLS LAST, "
                f"{vote_expr} DESC NULLS LAST, s.title ASC"
            )
        elif provider_terms:
            ranking_sql = (
                f"{rating_expr} DESC NULLS LAST, {popularity_expr} DESC, "
                f"{year_expr} DESC NULLS LAST, s.title ASC"
            )
        else:
            ranking_sql = (
                f"{provider_rank_expr} DESC, "
                f"{popularity_expr} DESC, "
                f"{year_expr} DESC NULLS LAST, s.title ASC"
            )

        cur.execute(
            f"""
            SELECT
                s.*
            FROM public.{qident(WEBSERIES_SEARCH_TABLE)} s
            {where_clause}
            ORDER BY
                {order_sql}
                {ranking_sql}
            LIMIT %s
            """,
            params + order_params + [limit],
        )
        items = [webseries_card(dict(r)) for r in cur.fetchall()]
    finally:
        conn.close()

    return total, items


def webseries_availability(tmdb_id):
    if not tmdb_id:
        return []

    conn = get_conn()
    cur = conn.cursor()

    try:
        if table_exists(WEBSERIES_SEARCH_TABLE) and "availability_json" in table_columns(WEBSERIES_SEARCH_TABLE):
            cur.execute(
                f"""
                SELECT availability_json
                FROM public.{qident(WEBSERIES_SEARCH_TABLE)}
                WHERE tmdb_id = %s
                LIMIT 1
                """,
                [tmdb_id],
            )
            row = cur.fetchone()
            availability_rows = parse_json(row.get("availability_json") if row else None, [])
            if isinstance(availability_rows, list) and availability_rows:
                return [
                    {
                        "provider_display_name": item.get("provider_name") or item.get("provider_display_name"),
                        "provider_key": normalize_provider_key(item.get("provider_key") or item.get("normalized_provider_name") or item.get("provider_name")),
                        "provider_type": item.get("monetization_type") or item.get("provider_type"),
                        "region": item.get("watch_region") or item.get("region"),
                        "final_url": item.get("tmdb_watch_link") or item.get("final_url"),
                        "button_label": f"Watch on {item.get('provider_name')}" if item.get("provider_name") else "Watch",
                    }
                    for item in availability_rows
                    if isinstance(item, dict)
                ]

        if not table_exists(WEBSERIES_AVAILABILITY_TABLE):
            return []

        cur.execute(
            f"""
            SELECT provider_name, normalized_provider_name, monetization_type, watch_region,
                   display_priority, tmdb_watch_link, is_major_indian_provider
            FROM public.{qident(WEBSERIES_AVAILABILITY_TABLE)}
            WHERE tmdb_id = %s
            ORDER BY display_priority NULLS LAST, provider_name
            """,
            [tmdb_id],
        )
        return [
            {
                "provider_display_name": r.get("provider_name"),
                "provider_key": normalize_provider_key(r.get("normalized_provider_name") or r.get("provider_name")),
                "provider_type": r.get("monetization_type"),
                "region": r.get("watch_region"),
                "final_url": r.get("tmdb_watch_link"),
                "button_label": f"Watch on {r.get('provider_name')}" if r.get("provider_name") else "Watch",
                "priority": r.get("display_priority"),
                "is_major_provider": as_bool(r.get("is_major_indian_provider")),
            }
            for r in cur.fetchall()
        ]
    finally:
        conn.close()


def person_search_card(row: Dict[str, Any], domain: str = "historical"):
    person_slug = row.get("person_slug")
    person_name = row.get("person_name")
    source_domain = row.get("domain") or row.get("source_domain") or domain
    movie_url = f"/person/{person_slug}" if person_slug else None

    return {
        "domain": "person",
        "content_type": "person",
        "source_domain": source_domain,
        "source_label": "People",
        "title": person_name,
        "person_name": person_name,
        "person_slug": person_slug,
        "slug": person_slug,
        "movie_url": movie_url,
        "primary_role": row.get("primary_role"),
        "release_year": row.get("last_year"),
        "year": row.get("last_year"),
        "movie_count": row.get("movie_count"),
        "youtube_movie_count": row.get("youtube_movie_count"),
        "quality_score": row.get("search_rank") or row.get("movie_count") or 0,
        "aliases": list_value(row.get("aliases_json")),
        "disambiguation_label": row.get("disambiguation_label"),
        "raw": dict(row),
    }


PERSON_SEARCH_STOPWORDS = {
    "movie",
    "movies",
    "film",
    "films",
    "cinema",
    "filmography",
    "actor",
    "actress",
    "director",
    "producer",
    "music",
}


def normalize_people_query(query: str) -> str:
    text = str(query or "").strip()
    if not text:
        return ""

    tokens = re.findall(r"[a-z0-9]+", text.lower())
    meaningful = [token for token in tokens if token not in PERSON_SEARCH_STOPWORDS]
    return " ".join(meaningful).strip() or text


def compact_people_query(query: str) -> str:
    return "".join(ch.lower() for ch in str(query or "") if ch.isalnum())


SHORT_PERSON_ALIAS_SLUGS = {
    "ntr": "n-t-rama-rao",
    "anr": "akkineni-nageswara-rao",
    "nbk": "nandamuri-balakrishna",
    "shobanbabu": "sobhan-babu",
    "shobhanbabu": "sobhan-babu",
    "uppalapatikrishnamraju": "krishnam-raju",
    "ukrishnamraju": "krishnam-raju",
}


def exact_short_person_slug(query: str) -> Optional[str]:
    compact = compact_people_query(normalize_people_query(query))
    return SHORT_PERSON_ALIAS_SLUGS.get(compact)


def people_language_filter_sql(language: Optional[str], slug_column: str = "person_slug") -> Tuple[str, List[Any]]:
    language_values = language_match_values(language)
    if not language_values:
        return "", []

    clauses = []
    params: List[Any] = []
    for table_name in ("historical_movie_people_seo_preprod_v1", "modern_movie_people_seo_preprod_v1"):
        if not table_exists(table_name):
            continue

        cols = set(table_columns_cached(table_name))
        if "person_slug" not in cols:
            continue

        language_cols = [
            col
            for col in ("language_slug", "primary_language", "language_name", "language")
            if col in cols
        ]
        if not language_cols:
            continue

        language_checks = []
        for col in language_cols:
            language_checks.append(f"LOWER(CAST({qident(col)} AS TEXT)) = ANY(%s)")
            params.append(language_values)

        clauses.append(
            f"""
            {slug_column} IN (
                SELECT DISTINCT person_slug
                FROM public.{qident(table_name)}
                WHERE {" OR ".join(language_checks)}
            )
            """
        )

    if not clauses:
        return "", []

    return "(" + " OR ".join(clauses) + ")", params


def people_language_score_sql(language: Optional[str], outer_slug_column: str = "person_slug") -> Tuple[str, List[Any]]:
    language_values = language_match_values(language)
    if not language_values:
        return "", []

    clauses = []
    params: List[Any] = []
    for table_name in ("historical_movie_people_seo_preprod_v1", "modern_movie_people_seo_preprod_v1"):
        if not table_exists(table_name):
            continue

        cols = set(table_columns_cached(table_name))
        if "person_slug" not in cols:
            continue

        language_cols = [
            col
            for col in ("language_slug", "primary_language", "language_name", "language")
            if col in cols
        ]
        if not language_cols:
            continue

        language_checks = []
        for col in language_cols:
            language_checks.append(f"LOWER(CAST(mp.{qident(col)} AS TEXT)) = ANY(%s)")
            params.append(language_values)

        clauses.append(
            f"""
            (
                SELECT COUNT(*)
                FROM public.{qident(table_name)} mp
                WHERE mp.person_slug = {outer_slug_column}
                  AND ({" OR ".join(language_checks)})
            )
            """
        )

    if not clauses:
        return "", []

    return " + ".join(clauses), params


def search_people_cache(query: str, limit: int, scope: str, language: Optional[str] = None):
    # FLIXYFY_PREPROD_V5_PATCH:
    # Disable stale PEOPLE_SEARCH_CACHE_TABLE path. Pre-prod V5 people source is
    # historical_people_seo_preprod_v1, and old cache tables may lack compatibility columns.
    return None

    if not table_exists(PEOPLE_SEARCH_CACHE_TABLE):
        return None

    person_query = normalize_people_query(query)
    compact_person_query = compact_people_query(person_query)
    exact_short_slug = exact_short_person_slug(person_query)
    where = ["1 = 1"]
    params: List[Any] = []

    if scope == "indian":
        where.append("domain IN ('modern', 'historical', 'modern_historical_bridge')")

    language_sql, language_params = people_language_filter_sql(language, "p.person_slug")
    if language_sql:
        where.append(language_sql)
        params.extend(language_params)

    if person_query:
        if exact_short_slug:
            where.append("(person_slug = %s OR compact_aliases_text LIKE %s)")
            params.extend([exact_short_slug, f"%|{compact_person_query}|%"])
        elif len(compact_person_query) <= 3:
            where.append(
                "("
                "normalized_display_name = %s "
                "OR compact_display_name = %s "
                "OR compact_aliases_text LIKE %s"
                ")"
            )
            params.extend([person_query.lower(), compact_person_query, f"%|{compact_person_query}|%"])
        else:
            where.append(
                "("
                "display_name ILIKE %s "
                "OR aliases_search_text ILIKE %s "
                "OR compact_display_name LIKE %s "
                "OR compact_aliases_text LIKE %s"
                ")"
            )
            params.extend(
                [
                    f"%{person_query}%",
                    f"%{person_query}%",
                    f"{compact_person_query}%",
                    f"%|{compact_person_query}|%",
                ]
            )

    where_clause = "WHERE " + " AND ".join(where)
    conn = get_conn()
    cur = conn.cursor()

    try:
        total = None
        if not person_query:
            cur.execute(
                f"SELECT COUNT(*) AS total FROM public.{qident(PEOPLE_SEARCH_CACHE_TABLE)} p {where_clause}",
                params,
            )
            total = cur.fetchone()["total"]

        order_sql = ""
        order_params: List[Any] = []
        if person_query:
            order_sql = """
                CASE
                    WHEN person_slug = %s THEN 0
                    WHEN normalized_display_name = %s THEN 1
                    WHEN compact_display_name = %s THEN 2
                    WHEN compact_aliases_text LIKE %s THEN 3
                    WHEN display_name ILIKE %s THEN 4
                    WHEN aliases_search_text ILIKE %s THEN 5
                    ELSE 6
                END,
            """
            order_params = [
                exact_short_slug or "",
                person_query.lower(),
                compact_person_query,
                f"%|{compact_person_query}|%",
                f"{person_query}%",
                f"%{person_query}%",
            ]

        language_order_sql, language_order_params = people_language_score_sql(language, "p.person_slug")
        if language_order_sql:
            language_order_sql = f"({language_order_sql}) DESC,"

        cur.execute(
            f"""
            SELECT
                person_slug,
                display_name AS person_name,
                domain,
                primary_role,
                movie_count,
                youtube_movie_count,
                NULL AS last_year,
                aliases_json,
                NULL AS disambiguation_label,
                search_rank
            FROM public.{qident(PEOPLE_SEARCH_CACHE_TABLE)} p
            {where_clause}
            ORDER BY
                {order_sql}
                {language_order_sql}
                COALESCE(search_rank, 0) DESC,
                COALESCE(youtube_movie_count, 0) DESC,
                COALESCE(movie_count, 0) DESC,
                display_name ASC
            LIMIT %s
            """,
            params + order_params + language_order_params + [limit],
        )
        items = [person_search_card(dict(r), r.get("domain") or "historical") for r in cur.fetchall()]
        if total is None:
            total = len(items) + (1 if len(items) == limit else 0)
        return total, items
    finally:
        conn.close()


def search_people(query: str, limit: int, scope: str, language: Optional[str] = None):
    # FLIXYFY_V5_AUDITED_PATCH:
    # Search the historical people SEO table directly with only columns that exist.
    # Avoid stale people cache and avoid hardcoded indexable/youtube_count columns.
    table_name = "historical_people_seo_preprod_fixed_v1"
    if not table_exists(table_name):
        return 0, []

    cols = table_columns(table_name)
    person_query = normalize_people_query(query)
    compact_person_query = compact_people_query(person_query)
    exact_short_slug = exact_short_person_slug(person_query)

    where = []
    params: List[Any] = []

    if person_query:
        if exact_short_slug and "person_slug" in cols:
            where.append("person_slug = %s")
            params.append(exact_short_slug)
        elif len(compact_person_query) <= 3:
            parts = []
            if "person_name" in cols:
                parts.append("LOWER(person_name) = LOWER(%s)")
                params.append(person_query)
                parts.append("regexp_replace(LOWER(COALESCE(person_name, '')), '[^a-z0-9]+', '', 'g') = %s")
                params.append(compact_person_query)
            if "person_slug" in cols:
                parts.append("regexp_replace(LOWER(COALESCE(person_slug, '')), '[^a-z0-9]+', '', 'g') = %s")
                params.append(compact_person_query)
            if parts:
                where.append("(" + " OR ".join(parts) + ")")
        else:
            parts = []
            for col in ("person_name", "display_name", "name", "seo_title", "search_text"):
                if col in cols:
                    parts.append(f"LOWER(COALESCE(CAST({qident(col)} AS TEXT), '')) LIKE LOWER(%s)")
                    params.append(f"%{person_query}%")
            if parts:
                where.append("(" + " OR ".join(parts) + ")")

    if language:
        language_values = language_match_values(language)
        for col in ("primary_language_slug", "language_slug", "language"):
            if col in cols:
                where.append(f"LOWER(COALESCE(CAST({qident(col)} AS TEXT), '')) = ANY(%s)")
                params.append(language_values)
                break

    where_clause = "WHERE " + " AND ".join(where) if where else ""

    name_col = next((c for c in ("person_name", "display_name", "name") if c in cols), None)
    name_expr = f"COALESCE({qident(name_col)}, '')" if name_col else "''"

    count_col = next((c for c in ("movie_count", "total_movie_count", "credit_count") if c in cols), None)
    count_expr = f"COALESCE({qident(count_col)}, 0)" if count_col else "0"

    youtube_col = next((c for c in ("youtube_movie_count", "youtube_count") if c in cols), None)
    youtube_expr = f"COALESCE({qident(youtube_col)}, 0)" if youtube_col else "0"

    order_params: List[Any] = []
    if person_query and name_col:
        order_sql = f"""
                CASE
                    WHEN LOWER({qident(name_col)}) = LOWER(%s) THEN 1
                    WHEN regexp_replace(LOWER(COALESCE({qident(name_col)}, '')), '[^a-z0-9]+', '', 'g') = %s THEN 2
                    WHEN LOWER({qident(name_col)}) LIKE LOWER(%s) THEN 3
                    ELSE 4
                END,
        """
        order_params = [person_query, compact_person_query, f"{person_query}%"]
    else:
        order_sql = ""

    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            f"SELECT COUNT(*) AS total FROM public.{qident(table_name)} {where_clause}",
            params,
        )
        total = int(cur.fetchone()["total"] or 0)

        cur.execute(
            f"""
            SELECT *
            FROM public.{qident(table_name)}
            {where_clause}
            ORDER BY
                {order_sql}
                {youtube_expr} DESC,
                {count_expr} DESC,
                {name_expr} ASC
            LIMIT %s
            """,
            params + order_params + [limit],
        )

        items = [person_search_card(dict(r), "historical") for r in cur.fetchall()]
    finally:
        conn.close()

    return total, items


def suggestion_item(row: Dict[str, Any], domain: str, content_type: str = "movie") -> Dict[str, Any]:
    title = row.get("title") or row.get("person_name")
    slug = row.get("slug") or row.get("person_slug")
    year = row.get("release_year") or row.get("year") or row.get("latest_air_year") or row.get("first_air_year") or row.get("last_year")

    if content_type == "person":
        movie_url = f"/historical/person/{slug}" if domain == "historical" and slug else (f"/person/{slug}" if slug else None)
        label = "People"
    elif domain == "webseries":
        movie_url = f"/webseries/{slug}" if slug else None
        label = "Webseries"
    else:
        movie_url = route_for(domain, slug)
        label = domain_label(domain)

    return {
        "title": title,
        "slug": slug,
        "domain": "person" if content_type == "person" else domain,
        "source_domain": domain,
        "content_type": content_type,
        "source_label": label,
        "movie_url": movie_url,
        "release_year": year,
        "year": year,
        "primary_language": row.get("language_name") or row.get("primary_language") or row.get("language_slug") or row.get("language"),
        "poster_url": fix_image_url(row.get("poster_url") or row.get("poster_path")),
        "rank_score": row.get("rank_score") or row.get("quality_score") or row.get("popularity") or row.get("movie_count") or 0,
        "aliases": list_value(row.get("aliases_json")),
        "disambiguation_label": row.get("disambiguation_label"),
    }


def suggestion_rows(
    table_name: str,
    domain: str,
    query: str,
    limit: int,
    language: Optional[str] = None,
    cur=None,
) -> List[Dict[str, Any]]:
    if not table_exists(table_name):
        return []

    cols = table_columns(table_name)
    title_col = "title" if "title" in cols else None
    if not title_col:
        return []

    where = [f"{qident(title_col)} ILIKE %s"]
    params: List[Any] = [f"{query}%"]

    if "original_title" in cols:
        where[0] = f"({where[0]} OR original_title ILIKE %s)"
        params.append(f"{query}%")

    if language:
        lang = language.strip().lower()
        if "language_slug" in cols:
            where.append("LOWER(CAST(language_slug AS TEXT)) = LOWER(%s)")
            params.append(lang)
        elif "primary_language" in cols:
            where.append("LOWER(CAST(primary_language AS TEXT)) = LOWER(%s)")
            params.append(lang)
        elif "language" in cols:
            where.append("LOWER(CAST(language AS TEXT)) = LOWER(%s)")
            params.append(lang)

    score_col = next((col for col in ["rank_score", "search_rank", "quality_score", "popularity", "rating"] if col in cols), None)
    year_col = "release_year" if "release_year" in cols else ("year" if "year" in cols else ("latest_air_year" if "latest_air_year" in cols else ("first_air_year" if "first_air_year" in cols else None)))
    score_sql = f"COALESCE({qident(score_col)}, 0) DESC NULLS LAST," if score_col else ""
    year_sql = f"{qident(year_col)} DESC NULLS LAST," if year_col else ""
    select_cols = [
        col
        for col in [
            title_col,
            "original_title",
            "slug",
            "release_year",
            "year",
            "latest_air_year",
            "first_air_year",
            "language_slug",
            "language_name",
            "primary_language",
            "language",
            "poster_url",
            "poster_path",
            "rank_score",
            "search_rank",
            "quality_score",
            "popularity",
        ]
        if col and col in cols
    ]
    select_sql = ", ".join(qident(col) for col in dict.fromkeys(select_cols))

    own_conn = None
    if cur is None:
        own_conn = get_conn()
        cur = own_conn.cursor()

    try:
        cur.execute(
            f"""
            SELECT {select_sql}
            FROM public.{qident(table_name)}
            WHERE {' AND '.join(where)}
            ORDER BY {score_sql} {year_sql} {qident(title_col)} ASC
            LIMIT %s
            """,
            params + [limit],
        )
        return [suggestion_item(dict(row), domain, "webseries" if domain == "webseries" else "movie") for row in cur.fetchall()]
    finally:
        if own_conn is not None:
            own_conn.close()


@lru_cache(maxsize=1)
def cached_people_suggestion_rows() -> Tuple[Tuple[Any, ...], ...]:
    if table_exists(PEOPLE_SEARCH_CACHE_TABLE):
        conn = get_conn()
        cur = conn.cursor()
        try:
            cur.execute(
                f"""
                SELECT
                    display_name AS person_name,
                    person_slug,
                    domain,
                    primary_role,
                    movie_count,
                    youtube_movie_count,
                    NULL AS last_year,
                    aliases_json,
                    compact_aliases_text,
                    search_rank,
                    NULL AS disambiguation_label
                FROM public.{qident(PEOPLE_SEARCH_CACHE_TABLE)}
                WHERE 1 = 1
                ORDER BY COALESCE(search_rank, 0) DESC, display_name ASC
                LIMIT 10000
                """
            )
            return tuple(
                (
                    row.get("person_name"),
                    row.get("person_slug"),
                    row.get("domain") or "historical",
                    row.get("primary_role"),
                    row.get("movie_count"),
                    row.get("youtube_movie_count"),
                    row.get("last_year"),
                    row.get("aliases_json"),
                    row.get("compact_aliases_text"),
                    row.get("search_rank"),
                    row.get("disambiguation_label"),
                )
                for row in cur.fetchall()
            )
        finally:
            conn.close()

    if not table_exists("historical_people_seo_preprod_fixed_v1"):
        return tuple()

    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT person_name, person_slug, primary_role, movie_count, youtube_movie_count, NULL AS last_year
            FROM public.historical_people_seo_preprod_fixed_v1
            WHERE 1 = 1
            ORDER BY COALESCE(youtube_movie_count, 0) DESC, COALESCE(movie_count, 0) DESC, person_name ASC
            LIMIT 5000
            """
        )
        return tuple(
            (
                row.get("person_name"),
                row.get("person_slug"),
                "historical",
                row.get("primary_role"),
                row.get("movie_count"),
                row.get("youtube_movie_count"),
                row.get("last_year"),
                None,
                None,
                None,
                None,
            )
            for row in cur.fetchall()
        )
    finally:
        conn.close()


def people_suggestion_rows(query: str, limit: int, cur=None) -> List[Dict[str, Any]]:
    person_query = normalize_people_query(query)
    compact_query = compact_people_query(person_query)
    exact_short_slug = exact_short_person_slug(person_query)
    if len(compact_query) < 2:
        return []

    cached = cached_people_suggestion_rows()
    if cached:
        matches = []
        for name, slug, domain, role, movie_count, youtube_count, last_year, aliases_json, compact_aliases_text, search_rank, disambiguation_label in cached:
            name_text = str(name or "")
            slug_text = str(slug or "")
            compact_name = compact_people_query(name_text)
            compact_slug = compact_people_query(slug_text)
            compact_aliases = str(compact_aliases_text or "")
            exact_alias = f"|{compact_query}|" in compact_aliases
            if not (compact_name.startswith(compact_query) or compact_slug.startswith(compact_query) or exact_alias):
                continue
            exact = 2 if exact_short_slug and slug_text == exact_short_slug else (1 if compact_name == compact_query or exact_alias else 0)
            matches.append(
                (
                    exact,
                    int(search_rank or 0),
                    int(youtube_count or 0),
                    int(movie_count or 0),
                    {
                        "person_name": name,
                        "person_slug": slug,
                        "domain": domain,
                        "primary_role": role,
                        "movie_count": movie_count,
                        "youtube_movie_count": youtube_count,
                        "last_year": last_year,
                        "aliases_json": aliases_json,
                        "disambiguation_label": disambiguation_label,
                    },
                )
            )
        matches.sort(key=lambda item: (item[0], item[1], item[2], item[3], str(item[4].get("person_name") or "")), reverse=True)
        return [suggestion_item(row, row.get("domain") or "historical", "person") for *_score, row in matches[:limit]]

    if not table_exists("historical_people_seo_preprod_fixed_v1"):
        return []

    own_conn = None
    if cur is None:
        own_conn = get_conn()
        cur = own_conn.cursor()

    try:
        cur.execute(
            """
            SELECT *
            FROM public.historical_people_seo_preprod_fixed_v1
            WHERE 1 = 1
              AND (person_name ILIKE %s OR person_slug ILIKE %s)
            ORDER BY
              CASE
                WHEN LOWER(person_name) = LOWER(%s) THEN 1
                WHEN person_name ILIKE %s THEN 2
                ELSE 3
              END,
              COALESCE(youtube_movie_count, 0) DESC,
              COALESCE(movie_count, 0) DESC,
              person_name ASC
            LIMIT %s
            """,
            [f"{person_query}%", f"{compact_query}%", person_query, f"{person_query}%", limit],
        )
        return [suggestion_item(dict(row), "historical", "person") for row in cur.fetchall()]
    finally:
        if own_conn is not None:
            own_conn.close()


@router.get("/api/v3/search-suggestions")
def search_suggestions(
    q: str = Query("", min_length=0),
    limit: int = Query(8, ge=1, le=12),
    domain: Optional[str] = Query(None),
    content_type: Optional[str] = Query(None, alias="type"),
    language: Optional[str] = None,
):
    query = q.strip()
    if len(query) < 3:
        return {"query": query, "items": []}

    if domain:
        requested = {d.strip().lower() for d in domain.split(",") if d.strip()}
    else:
        requested = {"modern", "hollywood", "historical", "webseries"}

    requested_type = str(content_type or "all").strip().lower()
    if requested_type not in {"movies", "webseries", "people", "all"}:
        requested_type = "all"

    items: List[Dict[str, Any]] = []
    per_source_limit = max(limit, 6)
    compact_query = "".join(ch.lower() for ch in query if ch.isalnum())

    conn = get_conn()
    try:
        cur = conn.cursor()

        if requested_type == "people" and 2 < len(compact_query) <= 4:
            people_items = people_suggestion_rows(query, per_source_limit, cur)
            strong_people = [
                item
                for item in people_items
                if "".join(ch.lower() for ch in str(item.get("title") or "") if ch.isalnum()).startswith(compact_query)
            ]
            if strong_people:
                return {"query": query, "items": strong_people[:limit]}

        if requested_type in {"movies", "all"}:
            if "modern" in requested or "indian" in requested:
                items.extend(suggestion_rows(MODERN_SEARCH_TABLE if table_exists(MODERN_SEARCH_TABLE) else MODERN_TABLE, "modern", query, per_source_limit, language, cur))
            if "historical" in requested:
                items.extend(suggestion_rows(HISTORICAL_SEARCH_TABLE if table_exists(HISTORICAL_SEARCH_TABLE) else HISTORICAL_TABLE, "historical", query, per_source_limit, language, cur))
            if "hollywood" in requested:
                items.extend(suggestion_rows(HOLLYWOOD_SEARCH_TABLE if table_exists(HOLLYWOOD_SEARCH_TABLE) else HOLLYWOOD_TABLE, "hollywood", query, per_source_limit, None, cur))

        if requested_type in {"webseries", "all"}:
            items.extend(suggestion_rows(WEBSERIES_SEARCH_TABLE, "webseries", query, per_source_limit, None, cur))

        if requested_type == "people":
            items.extend(people_suggestion_rows(query, per_source_limit, cur))
    finally:
        conn.close()

    seen = set()
    deduped = []

    def score(item: Dict[str, Any]) -> Tuple[int, int, str]:
        title = str(item.get("title") or "")
        compact_title = "".join(ch.lower() for ch in title if ch.isalnum())
        exact = 0
        if compact_title == compact_query:
            exact = 3
        elif compact_title.startswith(compact_query):
            exact = 2
        elif compact_query in compact_title:
            exact = 1
        try:
            rank = int(float(item.get("rank_score") or 0))
        except Exception:
            rank = 0
        return exact, rank, title.lower()

    for item in sorted(items, key=score, reverse=True):
        key = (item.get("domain"), item.get("slug"), item.get("title"))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
        if len(deduped) >= limit:
            break

    return {"query": query, "items": deduped}


@router.get("/api/v3/webseries/{slug}")
def webseries_detail(slug: str):
    row = row_by_slug(WEBSERIES_DETAIL_TABLE, slug)

    if not row:
        raise HTTPException(status_code=404, detail="Webseries not found")

    data = webseries_card(row)
    availability = webseries_availability(row.get("tmdb_id"))

    data.update(
        {
            "overview": row.get("overview") or row.get("card_overview") or row.get("omdb_plot"),
            "genres": parse_json(row.get("genres"), []),
            "backdrop_url": fix_image_url(row.get("backdrop_path")),
            "imdb_id": row.get("imdb_id"),
            "imdb_rating": row.get("omdb_imdb_rating"),
            "imdb_votes": row.get("omdb_imdb_votes"),
            "awards": row.get("omdb_awards"),
            "first_air_date": row.get("first_air_date"),
            "number_of_seasons": row.get("number_of_seasons"),
            "number_of_episodes": row.get("number_of_episodes"),
            "series_status": row.get("series_status") or row.get("status"),
            "series_type": row.get("series_type"),
            "availability": availability,
            "ott_all": availability,
            "watch_providers": availability,
        }
    )
    return data


@router.get("/api/v3/domain-health")
def domain_health():
    conn = get_conn()
    cur = conn.cursor()

    try:
        return {
            "status": "ok",
            "domains": {
                "modern": {
                    "table": MODERN_TABLE,
                    "movies": count_table(cur, MODERN_TABLE),
                },
                "hollywood": {
                    "table": HOLLYWOOD_TABLE,
                    "movies": count_table(cur, HOLLYWOOD_TABLE),
                    "availability_table": HOLLYWOOD_AVAILABILITY_TABLE,
                    "availability": count_table(cur, HOLLYWOOD_AVAILABILITY_TABLE),
                },
                "historical": {
                    "table": HISTORICAL_TABLE,
                    "movies": count_table(cur, HISTORICAL_TABLE),
                    "availability_table": HISTORICAL_AVAILABILITY_TABLE,
                    "availability": count_table(cur, HISTORICAL_AVAILABILITY_TABLE),
                },
            },
        }
    finally:
        conn.close()


@router.get("/api/v3/hollywood")
def hollywood_movies(
    page: int = Query(1, ge=1),
    limit: int = Query(24, ge=1, le=100),
    q: str = Query("", min_length=0),
    year: Optional[int] = None,
    provider: Optional[str] = None,
    availability: Optional[str] = None,
    has_ott: Optional[str] = None,
    sort: str = Query("popular"),
):
    table_name = HOLLYWOOD_CARD_TABLE if table_exists(HOLLYWOOD_CARD_TABLE) else HOLLYWOOD_TABLE

    return fetch_rows(
        table_name=table_name,
        domain="hollywood",
        page=page,
        limit=limit,
        search_query=q.strip(),
        language=None,
        year=year,
        sort=sort,
        provider=provider,
        availability=availability or ("ott" if str(has_ott or "").lower() in ("1", "true", "yes") else None),
    )


@router.get("/api/v3/hollywood/{slug}")
def hollywood_detail(slug: str):
    row = row_by_slug(HOLLYWOOD_DETAIL_TABLE, slug) or row_by_slug(HOLLYWOOD_TABLE, slug)

    if not row:
        raise HTTPException(status_code=404, detail="Hollywood movie not found")

    return domain_detail(row, "hollywood")





# FLIXYFY_DOMAIN_HISTORICAL_ROUTES_PATCH_V1
def _fhp_db_url():
    import os
    for key in ("DATABASE_URL", "POSTGRES_URL", "NEON_DATABASE_URL", "DATABASE_PUBLIC_URL"):
        value = os.getenv(key)
        if value:
            return value
    return None


def _fhp_rows(sql, params=None):
    params = params or []
    database_url = _fhp_db_url()
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
        print("historical domain patch query failed:", repr(exc))
        return []


def _fhp_table_exists(table_name):
    rows = _fhp_rows(
        """
        SELECT 1 FROM information_schema.tables WHERE table_schema='public' AND table_name=%s
        UNION ALL
        SELECT 1 FROM information_schema.views WHERE table_schema='public' AND table_name=%s
        LIMIT 1
        """,
        [table_name, table_name],
    )
    return bool(rows)


def _fhp_columns(table_name):
    rows = _fhp_rows(
        "SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name=%s ORDER BY ordinal_position",
        [table_name],
    )
    return [row.get("column_name") for row in rows if row.get("column_name")]


def _fhp_pick(row, *names):
    if not isinstance(row, dict):
        return None

    for name in names:
        value = row.get(name)
        if value is not None and str(value).strip() != "":
            return value

    return None


def _fhp_bool(value):
    if value is True:
        return True
    if value is False or value is None:
        return False
    return str(value).strip().lower() in ("1", "true", "yes", "y")


def _fhp_int(value, default=0):
    try:
        if value is None or str(value).strip() == "":
            return default
        return int(float(str(value)))
    except Exception:
        return default


def _fhp_has_real_poster(row):
    poster = str(_fhp_pick(row, "poster_url", "poster", "image_url") or "").strip().lower()
    if not poster:
        return False
    if "placeholder" in poster:
        return False
    if "classic-indian" in poster:
        return False
    if "no-poster" in poster or "no_poster" in poster:
        return False
    if "default" in poster:
        return False
    return poster.startswith("http://") or poster.startswith("https://") or poster.startswith("/")


def _fhp_is_youtube_url(value):
    text = str(value or "").strip().lower()
    return bool(text and ("youtube.com" in text or "youtu.be" in text))


def _fhp_bad_person_row(row):
    title = str(_fhp_pick(row, "title", "name") or "").strip().lower()
    slug = str(_fhp_pick(row, "slug") or "").strip().lower()

    title_norm = title.replace(".", " ").replace("_", " ").replace("-", " ")
    title_norm = " ".join(title_norm.split())

    blocked_titles = {
        "a r rahman",
        "ar rahman",
        "a venkatesh",
        "ajith kumar",
    }

    blocked_slug_parts = (
        "a-r-rahman",
        "ar-rahman",
        "a-venkatesh",
        "ajith-kumar",
    )

    if title_norm in blocked_titles:
        return True

    if any(part in slug for part in blocked_slug_parts):
        return True

    return False


def _fhp_route_for(slug):
    return f"/historical/{slug}" if slug else None


def _fhp_verified_links(slug):
    slug = (slug or "").strip()
    if not slug:
        return []

    out = []

    if _fhp_table_exists(YOUTUBE_LINK_TABLE):
        rows = _fhp_rows(
            f"""
            SELECT *
            FROM public.{qident(YOUTUBE_LINK_TABLE)}
            WHERE content_slug=%s
              AND COALESCE(active, 1)=1
              AND LOWER(COALESCE(content_domain, 'historical')) IN ('historical', 'classic')
            ORDER BY COALESCE(is_primary, 0) DESC,
                     COALESCE(source_rank, 0) DESC,
                     COALESCE(quality_score, match_score, 0) DESC,
                     COALESCE(view_count, 0) DESC,
                     youtube_video_id ASC
            LIMIT 20
            """,
            [slug],
        )
        for row in rows:
            url = _fhp_pick(row, "youtube_url", "final_url", "url")
            if not _fhp_is_youtube_url(url):
                continue

            out.append(
                {
                    "domain": "historical",
                    "slug": slug,
                    "title": _fhp_pick(row, "title", "youtube_title"),
                    "provider": "YouTube",
                    "provider_key": "youtube",
                    "provider_display_name": "YouTube",
                    "provider_type": "free",
                    "button_label": "Watch on YouTube",
                    "final_url": url,
                    "url": url,
                    "youtube_url": url,
                    "youtube_title": _fhp_pick(row, "youtube_title", "title"),
                    "youtube_video_id": _fhp_pick(row, "youtube_video_id", "video_id"),
                    "youtube_language": _fhp_pick(row, "youtube_language", "language_slug"),
                    "youtube_view_count": _fhp_pick(row, "view_count", "youtube_view_count"),
                    "youtube_duration_seconds": _fhp_pick(row, "duration_seconds", "youtube_duration_seconds"),
                    "youtube_match_score": _fhp_pick(row, "match_score", "youtube_match_score"),
                    "youtube_match_type": _fhp_pick(row, "match_type", "youtube_match_type"),
                    "youtube_confidence": _fhp_pick(row, "confidence_score", "youtube_confidence"),
                    "youtube_source": _fhp_pick(row, "source_table") or YOUTUBE_LINK_TABLE,
                    "active": True,
                    "is_free": True,
                    "has_youtube": True,
                    "has_ott": True,
                    "ott_primary": "YouTube",
                    "ott_primary_key": "youtube",
                }
            )

    if out:
        return out

    if _fhp_table_exists("historical_youtube_verified_links_v1"):
        rows = _fhp_rows(
            "SELECT * FROM historical_youtube_verified_links_v1 "
            "WHERE slug=%s AND COALESCE(active, TRUE)=TRUE "
            "ORDER BY COALESCE(is_primary, FALSE) DESC, COALESCE(link_rank, 999999) ASC, id ASC",
            [slug],
        )

        for row in rows:
            url = _fhp_pick(row, "youtube_url", "final_url", "url")
            if not _fhp_is_youtube_url(url):
                continue

            out.append(
                {
                    "domain": "historical",
                    "slug": slug,
                    "title": _fhp_pick(row, "title", "youtube_title"),
                    "provider": "YouTube",
                    "provider_key": "youtube",
                    "provider_display_name": "YouTube",
                    "provider_type": "free",
                    "button_label": "Watch on YouTube",
                    "final_url": url,
                    "url": url,
                    "youtube_url": url,
                    "youtube_title": _fhp_pick(row, "youtube_title", "title"),
                    "youtube_video_id": _fhp_pick(row, "youtube_video_id", "video_id"),
                    "youtube_language": _fhp_pick(row, "youtube_language", "language"),
                    "youtube_view_count": _fhp_pick(row, "youtube_view_count"),
                    "youtube_duration_seconds": _fhp_pick(row, "youtube_duration_seconds"),
                    "youtube_match_score": _fhp_pick(row, "youtube_match_score"),
                    "youtube_match_type": _fhp_pick(row, "youtube_match_type"),
                    "youtube_confidence": _fhp_pick(row, "youtube_confidence"),
                    "youtube_source": _fhp_pick(row, "youtube_source") or "historical_youtube_verified_links_v1",
                    "active": True,
                    "is_free": True,
                    "has_youtube": True,
                    "has_ott": True,
                    "ott_primary": "YouTube",
                    "ott_primary_key": "youtube",
                }
            )

    if out:
        return out

    fallback_tables = [
        HISTORICAL_DETAIL_TABLE,
        HISTORICAL_TABLE,
        HISTORICAL_AVAILABILITY_TABLE,
        HISTORICAL_CARD_TABLE,
        HISTORICAL_SEARCH_TABLE,
    ]

    for table in fallback_tables:
        if not _fhp_table_exists(table):
            continue

        cols = set(_fhp_columns(table))
        if "slug" not in cols or "youtube_url" not in cols:
            continue

        rows = _fhp_rows(
            'SELECT * FROM "' + table + '" '
            "WHERE slug=%s "
            "AND youtube_url IS NOT NULL "
            "AND TRIM(CAST(youtube_url AS TEXT)) <> '' "
            "AND (LOWER(CAST(youtube_url AS TEXT)) LIKE %s OR LOWER(CAST(youtube_url AS TEXT)) LIKE %s) "
            "LIMIT 20",
            [slug, "%youtube.com%", "%youtu.be%"],
        )

        for row in rows:
            url = _fhp_pick(row, "youtube_url", "final_url", "url")
            if not _fhp_is_youtube_url(url):
                continue

            out.append(
                {
                    "domain": "historical",
                    "slug": slug,
                    "title": _fhp_pick(row, "title", "youtube_title"),
                    "provider": "YouTube",
                    "provider_key": "youtube",
                    "provider_display_name": "YouTube",
                    "provider_type": "free",
                    "button_label": "Watch on YouTube",
                    "final_url": url,
                    "url": url,
                    "youtube_url": url,
                    "youtube_title": _fhp_pick(row, "youtube_title", "title"),
                    "youtube_video_id": _fhp_pick(row, "youtube_video_id", "video_id"),
                    "youtube_language": _fhp_pick(row, "youtube_language", "language"),
                    "youtube_view_count": _fhp_pick(row, "youtube_view_count"),
                    "youtube_duration_seconds": _fhp_pick(row, "youtube_duration_seconds"),
                    "youtube_match_score": _fhp_pick(row, "youtube_match_score"),
                    "youtube_match_type": _fhp_pick(row, "youtube_match_type"),
                    "youtube_confidence": _fhp_pick(row, "youtube_confidence"),
                    "youtube_source": _fhp_pick(row, "youtube_source"),
                    "active": True,
                    "is_free": True,
                    "has_youtube": True,
                    "has_ott": True,
                    "ott_primary": "YouTube",
                    "ott_primary_key": "youtube",
                }
            )

        if out:
            return out

    return []


def _fhp_card(row):
    slug = _fhp_pick(row, "slug")
    links = _fhp_verified_links(slug)
    primary = links[0] if links else None

    has_youtube = bool(primary)
    has_ott = has_youtube or _fhp_bool(_fhp_pick(row, "has_ott"))

    return {
        "domain": "historical",
        "source_domain": "historical",
        "source_label": "Historical Indian",
        "id": _fhp_pick(row, "id"),
        "tmdb_id": _fhp_pick(row, "tmdb_id"),
        "imdb_id": _fhp_pick(row, "imdb_id"),
        "title": _fhp_pick(row, "title"),
        "original_title": _fhp_pick(row, "original_title"),
        "slug": slug,
        "movie_url": _fhp_pick(row, "movie_url") or _fhp_route_for(slug),
        "release_year": _fhp_pick(row, "release_year", "year"),
        "year": _fhp_pick(row, "release_year", "year"),
        "primary_language": _fhp_pick(row, "primary_language", "language_name"),
        "language_name": _fhp_pick(row, "language_name", "primary_language"),
        "language_slug": _fhp_pick(row, "language_slug", "language"),
        "poster_url": _fhp_pick(row, "poster_url"),
        "backdrop_url": _fhp_pick(row, "backdrop_url"),
        "rating": _fhp_pick(row, "rating"),
        "vote_count": _fhp_pick(row, "vote_count"),
        "popularity": _fhp_pick(row, "popularity"),
        "quality_score": _fhp_pick(row, "quality_score"),
        "ott_primary": "YouTube" if has_youtube else _fhp_pick(row, "ott_primary"),
        "ott_primary_key": "youtube" if has_youtube else (_fhp_pick(row, "ott_primary_key") or ""),
        "ott_count": len(links) if has_youtube else _fhp_pick(row, "ott_count"),
        "has_ott": has_ott,
        "has_free_ott": has_youtube or _fhp_bool(_fhp_pick(row, "has_free_ott")),
        "has_subscription_ott": _fhp_bool(_fhp_pick(row, "has_subscription_ott")),
        "has_rent_ott": _fhp_bool(_fhp_pick(row, "has_rent_ott")),
        "has_buy_ott": _fhp_bool(_fhp_pick(row, "has_buy_ott")),
        "is_free": has_youtube or _fhp_bool(_fhp_pick(row, "is_free")),
        "youtube_url": primary.get("youtube_url") if primary else _fhp_pick(row, "youtube_url"),
        "youtube_title": primary.get("youtube_title") if primary else _fhp_pick(row, "youtube_title"),
        "youtube_video_id": primary.get("youtube_video_id") if primary else _fhp_pick(row, "youtube_video_id"),
        "youtube_count": len(links),
    }


def _fhp_list_card(row):
    slug = _fhp_pick(row, "slug")
    youtube_url = _fhp_pick(row, "youtube_url")
    ott_count = _fhp_pick(row, "ott_count")
    youtube_count = _fhp_int(_fhp_pick(row, "youtube_count"), 0)
    forced_provider_key = _fhp_pick(row, "fhp_provider_key")
    forced_provider_name = _fhp_pick(row, "fhp_provider_name") or forced_provider_key
    forced_provider_type = _fhp_pick(row, "fhp_provider_type")
    forced_provider_url = _fhp_pick(row, "fhp_provider_url")
    normalized_forced_provider = normalize_provider_key(forced_provider_key or forced_provider_name)
    has_youtube = (
        _fhp_is_youtube_url(youtube_url)
        or _fhp_bool(_fhp_pick(row, "has_youtube"))
        or _fhp_bool(_fhp_pick(row, "fhp_force_youtube"))
        or normalized_forced_provider == "youtube"
    )
    has_forced_provider = bool(forced_provider_key or forced_provider_name)
    has_ott = has_youtube or has_forced_provider or _fhp_bool(_fhp_pick(row, "has_ott"))
    provider_row = None
    if has_forced_provider:
        provider_row = {
            "provider_key": normalized_forced_provider,
            "provider_display_name": "YouTube" if has_youtube else forced_provider_name,
            "provider_name": "YouTube" if has_youtube else forced_provider_name,
            "provider_type": forced_provider_type or ("free" if has_youtube else None),
            "provider_category": forced_provider_type or ("free" if has_youtube else None),
            "final_url": forced_provider_url,
            "url": forced_provider_url,
            "button_label": f"Watch on {'YouTube' if has_youtube else forced_provider_name}",
        }

    data = {
        "domain": "historical",
        "source_domain": "historical",
        "source_label": "Historical Indian",
        "id": _fhp_pick(row, "id"),
        "tmdb_id": _fhp_pick(row, "tmdb_id"),
        "imdb_id": _fhp_pick(row, "imdb_id"),
        "title": _fhp_pick(row, "title"),
        "original_title": _fhp_pick(row, "original_title"),
        "slug": slug,
        "movie_url": _fhp_pick(row, "movie_url") or _fhp_route_for(slug),
        "release_year": _fhp_pick(row, "release_year", "year"),
        "year": _fhp_pick(row, "release_year", "year"),
        "primary_language": _fhp_pick(row, "primary_language", "language_name"),
        "language_name": _fhp_pick(row, "language_name", "primary_language"),
        "language_slug": _fhp_pick(row, "language_slug", "language"),
        "poster_url": _fhp_pick(row, "poster_url"),
        "backdrop_url": _fhp_pick(row, "backdrop_url"),
        "rating": _fhp_pick(row, "rating"),
        "vote_count": _fhp_pick(row, "vote_count"),
        "popularity": _fhp_pick(row, "popularity"),
        "quality_score": _fhp_pick(row, "quality_score"),
        "ott_primary": "YouTube" if has_youtube else (forced_provider_name or _fhp_pick(row, "ott_primary")),
        "ott_primary_key": "youtube" if has_youtube else (normalized_forced_provider or _fhp_pick(row, "ott_primary_key") or ""),
        "ott_count": ott_count if ott_count is not None else (1 if has_youtube else None),
        "has_ott": has_ott,
        "has_free_ott": has_youtube or _fhp_bool(_fhp_pick(row, "has_free_ott")),
        "has_subscription_ott": _fhp_bool(_fhp_pick(row, "has_subscription_ott")),
        "has_rent_ott": _fhp_bool(_fhp_pick(row, "has_rent_ott")),
        "has_buy_ott": _fhp_bool(_fhp_pick(row, "has_buy_ott")),
        "is_free": has_youtube or _fhp_bool(_fhp_pick(row, "is_free")),
        "youtube_url": youtube_url,
        "youtube_title": _fhp_pick(row, "youtube_title"),
        "youtube_video_id": _fhp_pick(row, "youtube_video_id"),
        "youtube_count": youtube_count if youtube_count > 0 else (1 if has_youtube else 0),
    }
    if provider_row:
        data["availability"] = [provider_row]
        data["ott_all"] = [provider_row]
        data["watch_providers"] = [provider_row]
    return data


def _fhp_detail(row):
    data = _fhp_list_card(row)
    slug = data.get("slug")
    youtube_url = _fhp_pick(row, "youtube_url")
    links = _fhp_verified_links(slug)
    if not links and _fhp_is_youtube_url(youtube_url):
        links.append(
            {
                "domain": "historical",
                "slug": slug,
                "title": _fhp_pick(row, "title", "youtube_title"),
                "provider": "YouTube",
                "provider_key": "youtube",
                "provider_display_name": "YouTube",
                "provider_type": "free",
                "button_label": "Watch on YouTube",
                "final_url": youtube_url,
                "url": youtube_url,
                "youtube_url": youtube_url,
                "youtube_title": _fhp_pick(row, "youtube_title", "title"),
                "youtube_video_id": _fhp_pick(row, "youtube_video_id", "video_id"),
                "youtube_language": _fhp_pick(row, "youtube_language", "language"),
                "active": True,
                "is_free": True,
                "has_youtube": True,
                "has_ott": True,
                "ott_primary": "YouTube",
                "ott_primary_key": "youtube",
            }
        )
    primary = links[0] if links else None

    data.update(
        {
            "overview": _fhp_pick(row, "overview"),
            "runtime": _fhp_pick(row, "runtime"),
            "genres": [],
            "director": _fhp_pick(row, "director"),
            "writers": _fhp_pick(row, "writers", "writer"),
            "actors": _fhp_pick(row, "actors", "cast"),
            "cast": _fhp_pick(row, "cast", "actors"),
            "certification": _fhp_pick(row, "certification"),
            "trailer_url": _fhp_pick(row, "trailer_url"),
            "production_companies": _fhp_pick(row, "production_companies", "production_company"),
            "availability": links,
            "ott_all": links,
            "watch_providers": links,
            "youtube_full_movies": links,
            "youtube_variants": links,
            "youtube_count": len(links),
            "raw": dict(row),
        }
    )

    if primary:
        data["youtube_url"] = primary.get("youtube_url")
        data["youtube_title"] = primary.get("youtube_title")
        data["youtube_video_id"] = primary.get("youtube_video_id")
        data["youtube_language"] = primary.get("youtube_language")
        data["has_youtube"] = True
        data["has_ott"] = True
        data["ott_primary"] = "YouTube"
        data["ott_primary_key"] = "youtube"
        data["ott_count"] = len(links)
        data["is_free"] = True
        data["raw"]["youtube_url"] = primary.get("youtube_url")
        data["raw"]["youtube_title"] = primary.get("youtube_title")
        data["raw"]["youtube_video_id"] = primary.get("youtube_video_id")
        data["raw"]["youtube_language"] = primary.get("youtube_language")
        data["raw"]["has_youtube"] = 1
        data["raw"]["has_ott"] = 1

    return data


def _fhp_fetch_historical_row(slug):
    for table in (HISTORICAL_DETAIL_TABLE, HISTORICAL_TABLE, HISTORICAL_CARD_TABLE):
        rows = _fhp_rows(f"SELECT * FROM public.{qident(table)} WHERE slug=%s LIMIT 1", [slug])
        if rows:
            return rows[0]

    return None


def _fhp_parse_json_list(value):
    if value is None or value == "":
        return []

    if isinstance(value, list):
        return value

    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, list) else []
    except Exception:
        return []


def _fhp_combo_payload(row):
    person_a = _fhp_pick(row, "person_a")
    person_b = _fhp_pick(row, "person_b")

    return {
        "combo_key": _fhp_pick(row, "combo_key"),
        "combo_type": _fhp_pick(row, "combo_type"),
        "person_a": person_a,
        "person_a_slug": _fhp_pick(row, "person_a_slug"),
        "person_b": person_b,
        "person_b_slug": _fhp_pick(row, "person_b_slug"),
        "title": f"{person_a} + {person_b} Movies" if person_a and person_b else _fhp_pick(row, "seo_title"),
        "movie_count": _fhp_int(_fhp_pick(row, "movie_count"), 0),
        "youtube_movie_count": _fhp_int(_fhp_pick(row, "youtube_movie_count"), 0),
        "languages": _fhp_parse_json_list(_fhp_pick(row, "languages_json")),
        "years": _fhp_parse_json_list(_fhp_pick(row, "years_json")),
        "seo_url": _fhp_pick(row, "seo_url"),
        "seo_title": _fhp_pick(row, "seo_title"),
        "meta_description": _fhp_pick(row, "meta_description"),
        "indexable": True if _fhp_pick(row, "indexable") is None else _fhp_bool(_fhp_pick(row, "indexable")),
        "index_reason": _fhp_pick(row, "index_reason"),
    }


def _fhp_combo_movie_card(row):
    slug = _fhp_pick(row, "slug", "movie_slug")
    data = _fhp_card({**row, "slug": slug})
    data["id"] = _fhp_pick(row, "movie_id", "id")
    data["title"] = _fhp_pick(row, "title", "combo_title") or data.get("title")
    data["release_year"] = _fhp_pick(row, "release_year", "combo_release_year", "year") or data.get("release_year")
    data["year"] = data.get("release_year")
    data["language_slug"] = _fhp_pick(row, "language_slug", "combo_language_slug", "language") or data.get("language_slug")
    data["movie_url"] = _fhp_route_for(slug)

    if _fhp_bool(_fhp_pick(row, "combo_has_youtube", "has_youtube")) and not data.get("has_ott"):
        data["has_ott"] = True
        data["has_free_ott"] = True
        data["is_free"] = True
        data["ott_primary"] = "YouTube"
        data["ott_primary_key"] = "youtube"

    return data


def _fhp_person_payload(row):
    return {
        "person_id": _fhp_pick(row, "person_id"),
        "person_name": _fhp_pick(row, "person_name"),
        "person_slug": _fhp_pick(row, "person_slug"),
        "movie_count": _fhp_int(_fhp_pick(row, "movie_count"), 0),
        "actor_count": _fhp_int(_fhp_pick(row, "actor_count"), 0),
        "director_count": _fhp_int(_fhp_pick(row, "director_count"), 0),
        "producer_count": _fhp_int(_fhp_pick(row, "producer_count"), 0),
        "music_count": _fhp_int(_fhp_pick(row, "music_count"), 0),
        "youtube_movie_count": _fhp_int(_fhp_pick(row, "youtube_movie_count"), 0),
        "primary_role": _fhp_pick(row, "primary_role"),
        "seo_url": _fhp_pick(row, "seo_url"),
        "seo_title": _fhp_pick(row, "seo_title"),
        "meta_description": _fhp_pick(row, "meta_description"),
        "indexable": True if _fhp_pick(row, "indexable") is None else _fhp_bool(_fhp_pick(row, "indexable")),
        "index_reason": _fhp_pick(row, "index_reason"),
        "title": _fhp_pick(row, "seo_title") or f"{_fhp_pick(row, 'person_name')} Movies",
    }


def _fhp_person_movie_card(row):
    slug = _fhp_pick(row, "slug", "movie_slug")
    data = _fhp_list_card({**row, "slug": slug})
    data["id"] = _fhp_pick(row, "movie_id", "id")
    data["title"] = _fhp_pick(row, "title", "person_movie_title") or data.get("title")
    data["release_year"] = _fhp_pick(row, "release_year", "person_release_year", "year") or data.get("release_year")
    data["year"] = data.get("release_year")
    data["language_slug"] = _fhp_pick(row, "language_slug", "person_language_slug", "language") or data.get("language_slug")
    data["movie_url"] = _fhp_route_for(slug)

    if _fhp_bool(_fhp_pick(row, "person_has_youtube", "has_youtube")) and not data.get("has_ott"):
        data["has_ott"] = True
        data["has_free_ott"] = True
        data["is_free"] = True
        data["ott_primary"] = "YouTube"
        data["ott_primary_key"] = "youtube"
        data["youtube_count"] = 1

    return data


PROTECTED_PERSON_LANGUAGE_SLUGS = {
    "n-t-rama-rao": {"te", "telugu"},
    "ntr": {"te", "telugu"},
    "n-t-rama-rao-jr": {"te", "telugu", "hi", "hindi"},
    "nandamuri-balakrishna": {"te", "telugu"},
    "balakrishna": {"te", "telugu"},
    "mahesh-babu": {"te", "telugu", "ta", "tamil"},
    "chiranjeevi": {"te", "telugu"},
    "rajinikanth": {"ta", "tamil", "te", "telugu", "hi", "hindi", "kn", "kannada", "ml", "malayalam"},
    "kamal-haasan": {"ta", "tamil", "te", "telugu", "hi", "hindi", "kn", "kannada", "ml", "malayalam"},
    "mohanlal": {"ml", "malayalam", "ta", "tamil", "te", "telugu", "hi", "hindi", "kn", "kannada"},
    "mammootty": {"ml", "malayalam", "ta", "tamil", "te", "telugu", "hi", "hindi", "kn", "kannada"},
    "amitabh-bachchan": {"hi", "hindi", "bn", "bengali", "te", "telugu", "ta", "tamil"},
}


PROTECTED_PERSON_HISTORICAL_LANGUAGE_SLUGS = {
    **PROTECTED_PERSON_LANGUAGE_SLUGS,
    "amitabh-bachchan": {"hi", "hindi", "bn", "bengali"},
}


def _person_language_filter(alias: str, column: str = "mp.language_slug", profile: str = "historical"):
    source = PROTECTED_PERSON_LANGUAGE_SLUGS
    if profile == "historical":
        source = PROTECTED_PERSON_HISTORICAL_LANGUAGE_SLUGS
    allowed = source.get(str(alias or "").strip().lower())
    if not allowed:
        return "", []
    return f" AND lower(coalesce({column}, '')) = ANY(%s)", [sorted(allowed)]


def _unified_person_payload(row: Dict[str, Any]):
    name = _fhp_pick(row, "display_name", "person_name") or ""
    slug = _fhp_pick(row, "person_slug")
    movie_count = _fhp_int(_fhp_pick(row, "movie_count"), 0)
    youtube_count = _fhp_int(_fhp_pick(row, "youtube_movie_count"), 0)
    role = _fhp_pick(row, "primary_role") or "actor"
    return {
        "person_slug": slug,
        "person_name": name,
        "display_name": name,
        "domain": _fhp_pick(row, "domain", "source_domain") or "person",
        "source_domain": _fhp_pick(row, "domain", "source_domain") or "person",
        "primary_role": role,
        "movie_count": movie_count,
        "actor_count": _fhp_int(_fhp_pick(row, "actor_count"), 0),
        "director_count": _fhp_int(_fhp_pick(row, "director_count"), 0),
        "producer_count": _fhp_int(_fhp_pick(row, "producer_count"), 0),
        "music_count": _fhp_int(_fhp_pick(row, "music_count"), 0),
        "youtube_movie_count": youtube_count,
        "active_year_max": _fhp_pick(row, "active_year_max", "last_year"),
        "aliases": list_value(_fhp_pick(row, "aliases_json")),
        "disambiguation_label": _fhp_pick(row, "disambiguation_label"),
        "seo_url": f"/person/{slug}" if slug else None,
        "seo_title": f"{name} Movies - Filmography, Cast and Watch Links" if name else "Person Movies",
        "meta_description": (
            f"Explore {name} movies, roles, filmography, and available YouTube full movie links on Flixyfy."
            if name
            else "Explore movies, roles, filmography, and available YouTube full movie links on Flixyfy."
        ),
        "title": f"{name} Movies" if name else "Person Movies",
    }


def _resolve_person_slug(slug: str) -> Tuple[str, Optional[str]]:
    requested = str(slug or "").strip().strip("/").lower()
    if not requested:
        return "", None

    short_slug = exact_short_person_slug(requested)
    if short_slug:
        return short_slug, requested if requested != short_slug else None

    if _fhp_table_exists(PERSON_SLUG_REDIRECT_TABLE):
        rows = _fhp_rows(
            f"""
            SELECT canonical_slug
            FROM public.{qident(PERSON_SLUG_REDIRECT_TABLE)}
            WHERE old_slug=%s
              AND COALESCE(active, 1)=1
            LIMIT 1
            """,
            [requested],
        )
        if rows and rows[0].get("canonical_slug"):
            canonical = str(rows[0]["canonical_slug"]).strip()
            return canonical, requested if canonical != requested else None

    return requested, None


def _modern_person_movie_rows(person_slug: str, limit: int, offset: int):
    if not _fhp_table_exists("modern_movie_people_seo_preprod_v1"):
        return []
    lang_sql, lang_params = _person_language_filter(person_slug, "mp.language_slug", "modern")
    join_table = MODERN_SEARCH_TABLE if table_exists(MODERN_SEARCH_TABLE) else MODERN_TABLE
    return _fhp_rows(
        f"""
        SELECT
            COALESCE(m.slug, mp.movie_slug) AS slug,
            COALESCE(m.title, mp.title) AS title,
            COALESCE(m.release_year, mp.release_year) AS release_year,
            COALESCE(m.language_slug, mp.language_slug) AS language_slug,
            COALESCE(m.primary_language, mp.primary_language) AS primary_language,
            m.id,
            m.tmdb_id,
            m.imdb_id,
            m.poster_url,
            m.poster_path,
            m.backdrop_url,
            m.backdrop_path,
            m.vote_average,
            m.vote_count,
            m.rating,
            m.popularity,
            m.quality_score,
            m.ott_primary,
            m.ott_primary_key,
            m.ott_count,
            m.has_ott,
            m.has_free_ott,
            mp.role AS role_type
        FROM public.{qident("modern_movie_people_seo_preprod_v1")} mp
        LEFT JOIN public.{qident(join_table)} m ON m.slug = mp.movie_slug
        WHERE mp.person_slug=%s
          {lang_sql}
        ORDER BY COALESCE(m.release_year, mp.release_year) DESC NULLS LAST,
                 COALESCE(m.popularity, mp.popularity_score, 0) DESC NULLS LAST,
                 COALESCE(m.title, mp.title) ASC
        LIMIT %s OFFSET %s
        """,
        [person_slug] + lang_params + [limit, offset],
    )


def _historical_person_movie_rows(person_slug: str, limit: int, offset: int):
    if not _fhp_table_exists("historical_movie_people_seo_preprod_v1"):
        return []
    lang_sql, lang_params = _person_language_filter(person_slug, profile="historical")
    return _fhp_rows(
        f"""
        SELECT h.*,
               mp.movie_id,
               mp.movie_slug,
               mp.title AS person_movie_title,
               mp.release_year AS person_release_year,
               mp.language_slug AS person_language_slug,
               mp.role_type,
               mp.has_youtube AS person_has_youtube
        FROM historical_movie_people_seo_preprod_v1 mp
        LEFT JOIN public.{qident(HISTORICAL_CARD_TABLE)} h ON h.slug = mp.movie_slug
        WHERE mp.person_slug=%s
          {lang_sql}
        ORDER BY COALESCE(mp.has_youtube, 0) DESC, mp.release_year DESC NULLS LAST, mp.title ASC
        LIMIT %s OFFSET %s
        """,
        [person_slug] + lang_params + [limit, offset],
    )


def _modern_person_movie_count(person_slug: str) -> int:
    if not _fhp_table_exists("modern_movie_people_seo_preprod_v1"):
        return 0
    lang_sql, lang_params = _person_language_filter(person_slug, "language_slug", "modern")
    rows = _fhp_rows(
        f"""
        SELECT COUNT(DISTINCT movie_slug) AS total
        FROM modern_movie_people_seo_preprod_v1
        WHERE person_slug=%s
          {lang_sql}
        """,
        [person_slug] + lang_params,
    )
    return _fhp_int(rows[0].get("total") if rows else 0, 0)


def _historical_person_movie_count(person_slug: str) -> int:
    if not _fhp_table_exists("historical_movie_people_seo_preprod_v1"):
        return 0
    lang_sql, lang_params = _person_language_filter(person_slug)
    rows = _fhp_rows(
        f"""
        SELECT COUNT(DISTINCT movie_slug) AS total
        FROM historical_movie_people_seo_preprod_v1 mp
        WHERE person_slug=%s
          {lang_sql}
        """,
        [person_slug] + lang_params,
    )
    return _fhp_int(rows[0].get("total") if rows else 0, 0)


def _merge_person_movie_cards(modern_rows, historical_rows, limit: int):
    cards = []
    seen = set()
    for row in modern_rows:
        slug = _fhp_pick(row, "slug", "movie_slug")
        if not slug or slug in seen:
            continue
        seen.add(slug)
        cards.append(domain_card(dict(row), "modern"))
    for row in historical_rows:
        slug = _fhp_pick(row, "slug", "movie_slug")
        if not slug or slug in seen:
            continue
        seen.add(slug)
        cards.append(_fhp_person_movie_card(row))

    def row_score(item):
        try:
            year = int(item.get("release_year") or 0)
        except Exception:
            year = 0
        return (1 if item.get("has_ott") or item.get("has_free_ott") else 0, year, str(item.get("title") or ""))

    cards.sort(key=row_score, reverse=True)
    return cards[:limit]

# BEGIN PERSON_PAGE_SERVING_V1_API_FORCE_PATCH

PERSON_PAGE_SERVING_TABLE = os.getenv("PERSON_PAGE_SERVING_TABLE", "person_page_serving_v1")


def _pps_language(value):
    raw = str(value or "telugu").strip().lower()
    aliases = {
        "te": "telugu",
        "telugu": "telugu",
        "ta": "tamil",
        "tamil": "tamil",
        "hi": "hindi",
        "hindi": "hindi",
        "kn": "kannada",
        "kannada": "kannada",
        "ml": "malayalam",
        "malayalam": "malayalam",
        "bn": "bengali",
        "bengali": "bengali",
        "mr": "marathi",
        "marathi": "marathi",
        "pa": "punjabi",
        "punjabi": "punjabi",
        "gu": "gujarati",
        "gujarati": "gujarati",
        "or": "odia",
        "od": "odia",
        "odia": "odia",
        "as": "assamese",
        "assamese": "assamese",
    }
    return aliases.get(raw, raw or "telugu")


def _pps_row(person_slug: str, language: str):
    if not _fhp_table_exists(PERSON_PAGE_SERVING_TABLE):
        return None

    rows = _fhp_rows(
        f"""
        SELECT *
        FROM public.{qident(PERSON_PAGE_SERVING_TABLE)}
        WHERE person_slug=%s
          AND LOWER(COALESCE(primary_language_slug, ''))=LOWER(%s)
          AND COALESCE(page_status, '')='ready'
        LIMIT 1
        """,
        [str(person_slug or "").strip().lower(), _pps_language(language)],
    )

    return dict(rows[0]) if rows else None


def _pps_movie_card(movie):
    if not isinstance(movie, dict):
        return None

    slug = movie.get("slug") or movie.get("movie_slug")
    title = movie.get("title") or movie.get("movie_title")
    domain = str(movie.get("domain") or "modern").strip().lower()
    language = movie.get("language") or movie.get("language_slug")
    roles = movie.get("roles") or []

    if not slug and not title:
        return None

    return {
        "domain": domain,
        "source_domain": domain,
        "source_label": domain_label(domain),
        "content_type": "movie",
        "title": title,
        "slug": slug,
        "movie_url": route_for(domain, slug),
        "release_year": movie.get("year") or movie.get("release_year"),
        "year": movie.get("year") or movie.get("release_year"),
        "primary_language": language,
        "language_name": str(language or "").title() if language else None,
        "language_slug": language,
        "poster_url": fix_image_url(movie.get("poster_url")),
        "has_youtube": as_bool(movie.get("has_youtube")),
        "is_free": as_bool(movie.get("has_youtube")),
        "roles": roles,
        "role_type": ", ".join(roles) if isinstance(roles, list) else str(roles or ""),
        "raw": movie,
    }


def _pps_cards(value):
    movies = parse_json(value, [])
    if not isinstance(movies, list):
        return []

    out = []
    seen = set()

    for movie in movies:
        card = _pps_movie_card(movie)
        if not card:
            continue

        key = card.get("slug") or f"{card.get('title')}:{card.get('year')}:{card.get('language_slug')}"
        if key in seen:
            continue

        seen.add(key)
        out.append(card)

    return out


def _pps_payload(row):
    name = _fhp_pick(row, "display_name", "person_name") or ""
    slug = _fhp_pick(row, "person_slug")
    language = _pps_language(_fhp_pick(row, "primary_language_slug") or "telugu")

    primary_items = _pps_cards(_fhp_pick(row, "primary_language_filmography_json"))
    career_items = _pps_cards(_fhp_pick(row, "career_filmography_json"))

    primary_count = _fhp_int(_fhp_pick(row, "primary_language_movie_count"), len(primary_items))
    career_count = _fhp_int(_fhp_pick(row, "career_attached_movie_count", "career_movie_count"), len(career_items))

    person = {
        "person_slug": slug,
        "person_name": name,
        "display_name": name,
        "domain": "person",
        "source_domain": "person",
        "primary_role": _fhp_pick(row, "primary_role", "page_role") or "actor",
        "primary_language_slug": language,
        "primary_language_name": _fhp_pick(row, "primary_language_name") or language.title(),

        "movie_count": primary_count,
        "total_movie_count": primary_count,
        "primary_language_movie_count": primary_count,
        "primary_language_actor_count": _fhp_int(_fhp_pick(row, "primary_language_actor_count"), 0),
        "primary_language_director_count": _fhp_int(_fhp_pick(row, "primary_language_director_count"), 0),
        "primary_language_writer_count": _fhp_int(_fhp_pick(row, "primary_language_writer_count"), 0),

        "actor_count": _fhp_int(_fhp_pick(row, "primary_language_actor_count"), 0),
        "director_count": _fhp_int(_fhp_pick(row, "primary_language_director_count"), 0),
        "writer_count": _fhp_int(_fhp_pick(row, "primary_language_writer_count"), 0),

        "career_attached_movie_count": career_count,
        "career_movie_count": career_count,
        "career_actor_count": _fhp_int(_fhp_pick(row, "career_actor_count"), 0),
        "career_director_count": _fhp_int(_fhp_pick(row, "career_director_count"), 0),
        "career_writer_count": _fhp_int(_fhp_pick(row, "career_writer_count"), 0),

        "youtube_movie_count": _fhp_int(_fhp_pick(row, "youtube_movie_count"), 0),
        "primary_youtube_movie_count": _fhp_int(_fhp_pick(row, "primary_youtube_movie_count"), 0),
        "latest_year": _fhp_pick(row, "latest_year"),
        "career_latest_year": _fhp_pick(row, "career_latest_year"),

        "page_title": _fhp_pick(row, "page_title") or f"{name} {language.title()} Movies",
        "page_summary": _fhp_pick(row, "page_summary"),
        "seo_url": f"/person/{slug}" if slug else None,
        "seo_title": _fhp_pick(row, "meta_title") or f"{name} {language.title()} Movies | Flixyfy",
        "meta_title": _fhp_pick(row, "meta_title") or f"{name} {language.title()} Movies | Flixyfy",
        "meta_description": _fhp_pick(row, "meta_description", "page_summary"),
        "title": _fhp_pick(row, "page_title") or f"{name} Movies",
        "source_table": PERSON_PAGE_SERVING_TABLE,
    }

    return person, primary_items, career_items


@router.get("/api/v3/person/{person_slug}")
def unified_person_detail_v1(
    person_slug: str,
    page: int = 1,
    limit: int = 96,
    language: Optional[str] = Query("telugu"),
):
    slug, redirected_from = _resolve_person_slug(person_slug)
    if not slug:
        raise HTTPException(status_code=404, detail="Person not found")

    page = max(1, int(page or 1))
    limit = max(1, min(int(limit or 96), 200))
    offset = (page - 1) * limit
    requested_language = _pps_language(language)

    row = _pps_row(slug, requested_language)

    if row:
        person, primary_items, career_items = _pps_payload(row)

        if redirected_from:
            person["redirected_from"] = redirected_from
            person["canonical_slug"] = slug

        total = _fhp_int(person.get("primary_language_movie_count"), len(primary_items))
        page_items = primary_items[offset:offset + limit]

        return {
            "domain": "person",
            "source_table": PERSON_PAGE_SERVING_TABLE,
            "language": requested_language,
            "person": person,
            "page": page,
            "limit": limit,
            "total": total,
            "pages": (total + limit - 1) // limit if total else 0,
            "items": page_items,
            "primary_language_movie_count": person.get("primary_language_movie_count"),
            "primary_language_filmography": primary_items,
            "career_attached_movie_count": person.get("career_attached_movie_count"),
            "career_filmography": career_items,
        }

    person_rows = []
    if _fhp_table_exists(PEOPLE_SEARCH_CACHE_TABLE):
        person_rows = _fhp_rows(
            f"""
            SELECT *
            FROM public.{qident(PEOPLE_SEARCH_CACHE_TABLE)}
            WHERE person_slug=%s
            LIMIT 1
            """,
            [slug],
        )

    if not person_rows and _fhp_table_exists("historical_people_seo_preprod_fixed_v1"):
        person_rows = _fhp_rows(
            """
            SELECT
                person_slug,
                person_name AS display_name,
                'historical' AS domain,
                primary_role,
                movie_count,
                youtube_movie_count,
                NULL AS active_year_max,
                NULL AS aliases_json,
                NULL AS disambiguation_label
            FROM historical_people_seo_preprod_fixed_v1
            WHERE person_slug=%s
               OR seo_url=%s
               OR seo_url=%s
            LIMIT 1
            """,
            [slug, f"/historical/person/{slug}", f"historical/person/{slug}"],
        )

    if not person_rows:
        raise HTTPException(status_code=404, detail="Person not found")

    person = _unified_person_payload(person_rows[0])
    source_domain = str(person.get("source_domain") or "").lower()
    fetch_limit = offset + limit

    include_modern = source_domain in {"modern", "modern_historical_bridge"}
    include_historical = source_domain in {"historical", "modern_historical_bridge"}

    modern_rows = _modern_person_movie_rows(slug, fetch_limit, 0) if include_modern else []
    historical_rows = _historical_person_movie_rows(slug, fetch_limit, 0) if include_historical else []
    merged = _merge_person_movie_cards(modern_rows, historical_rows, fetch_limit)
    page_items = merged[offset:offset + limit]

    modern_total = _modern_person_movie_count(slug) if include_modern else 0
    historical_total = _historical_person_movie_count(slug) if include_historical else 0
    total = modern_total + historical_total
    person["movie_count"] = max(total, _fhp_int(person.get("movie_count"), 0))

    if redirected_from:
        person["redirected_from"] = redirected_from
        person["canonical_slug"] = slug

    return {
        "domain": "person",
        "person": person,
        "page": page,
        "limit": limit,
        "total": total,
        "pages": (total + limit - 1) // limit if total else 0,
        "items": page_items,
    }

# END PERSON_PAGE_SERVING_V1_API_FORCE_PATCH

@router.get("/api/v3/historical/combinations")
def historical_combinations_patched_v1(
    page: int = 1,
    limit: int = 48,
    q: str = None,
    min_movies: int = 5,
    youtube_only: bool = False,
):
    page = max(1, int(page or 1))
    limit = max(1, min(int(limit or 48), 100))
    offset = (page - 1) * limit

    where = ["1 = 1", "COALESCE(movie_count, 0) >= %s"]
    params = [max(1, int(min_movies or 1))]

    if q:
        text = f"%{str(q).strip().lower()}%"
        where.append("(LOWER(person_a) LIKE %s OR LOWER(person_b) LIKE %s OR LOWER(seo_title) LIKE %s)")
        params.extend([text, text, text])

    if youtube_only:
        where.append("COALESCE(youtube_movie_count, 0) > 0")

    where_sql = "WHERE " + " AND ".join(where)
    rows = _fhp_rows(
        f"""
        SELECT *
        FROM historical_combo_seo_preprod_v1
        {where_sql}
        ORDER BY COALESCE(youtube_movie_count, 0) DESC, COALESCE(movie_count, 0) DESC, person_a ASC, person_b ASC
        LIMIT %s OFFSET %s
        """,
        params + [limit, offset],
    )

    total = offset + len(rows) + (1 if len(rows) == limit else 0)

    return {
        "domain": "historical",
        "page": page,
        "limit": limit,
        "total": total,
        "pages": page + (1 if len(rows) == limit else 0),
        "items": [_fhp_combo_payload(row) for row in rows],
    }


@router.get("/api/v3/historical/people")
# FLIXYFY_PUBLIC_HISTORICAL_PEOPLE_LAUNCH_FILTER_V1
def historical_people_patched_v1(
    page: int = 1,
    limit: int = 48,
    q: str = "",
    language: str = "",
    min_movies: int = 50,
    youtube_only: bool = False,
):
    # FLIXYFY_HISTORICAL_PEOPLE_PRIMARY_LANGUAGE_STRICT_V1
    # This route must filter by primary_language_slug only.
    # Do not use career/all-language counts for language-wise historical rows.
    table_name = "historical_people_public_launch_v1"

    page = max(1, int(page or 1))
    limit = max(1, min(int(limit or 48), 100))
    offset = (page - 1) * limit
    min_movies = max(0, int(min_movies or 0))

    if not _fhp_table_exists(table_name):
        return {
            "domain": "historical",
            "page": page,
            "limit": limit,
            "total": 0,
            "pages": 0,
            "items": [],
            "source_table": table_name,
            "language": language or None,
        }

    cols = set(_fhp_columns(table_name))

    language_aliases = {
        "hi": ["hi", "hindi"],
        "hindi": ["hi", "hindi"],
        "bollywood": ["hi", "hindi"],

        "te": ["te", "telugu"],
        "telugu": ["te", "telugu"],
        "tollywood": ["te", "telugu"],

        "ta": ["ta", "tamil"],
        "tamil": ["ta", "tamil"],

        "kn": ["kn", "kannada"],
        "kannada": ["kn", "kannada"],

        "ml": ["ml", "malayalam"],
        "malayalam": ["ml", "malayalam"],
    }

    language_key = str(language or "").strip().lower()
    all_language_values = {"", "all", "all_languages", "all-languages", "all indian languages"}

    where = []
    params = []

    # FLIXYFY_HISTORICAL_PEOPLE_NOISE_BLOCKLIST_V1
    # Hide known non-person/noise entities from public people rows.
    blocked_person_slugs = {
        "mukhyamantri-chandru",
    }
    if "person_slug" in cols and blocked_person_slugs:
        placeholders = ", ".join(["%s"] * len(blocked_person_slugs))
        where.append(f"COALESCE(person_slug, '') NOT IN ({placeholders})")
        params.extend(sorted(blocked_person_slugs))

    movie_count_columns = [
        c for c in (
            "primary_language_movie_count",
            "movie_count",
            "career_attached_movie_count",
            "total_movie_count",
            "credit_count",
        )
        if c in cols
    ]

    if movie_count_columns:
        if len(movie_count_columns) == 1:
            movie_expr = f"COALESCE({qident(movie_count_columns[0])}, 0)"
        else:
            movie_expr = "GREATEST(" + ", ".join(
                f"COALESCE({qident(c)}, 0)" for c in movie_count_columns
            ) + ")"
    else:
        movie_expr = "0"

    if min_movies:
        where.append(f"{movie_expr} >= %s")
        params.append(min_movies)
    else:
        where.append(f"{movie_expr} > 0")

    if language_key not in all_language_values:
        language_values = language_aliases.get(language_key, [language_key])
        if "primary_language_slug" in cols:
            where.append(
                "LOWER(COALESCE(CAST(primary_language_slug AS TEXT), '')) = ANY(%s)"
            )
            params.append(language_values)
        else:
            # Without primary_language_slug this route cannot safely make language rows.
            where.append("1 = 0")

    query = str(q or "").strip()
    if query:
        import re as _flixyfy_re

        search_parts = []
        query_l = query.lower().strip()
        is_short_token = bool(_flixyfy_re.fullmatch(r"[a-z0-9. ]{1,4}", query_l))

        if is_short_token:
            # For short people queries like NTR, ANR, MGR:
            # match exact slug/name token only, never substring inside words like mukhyamaNTRi.
            compact = _flixyfy_re.sub(r"[^a-z0-9]+", "", query_l)
            token_pattern = rf"(^|[^a-z0-9]){_flixyfy_re.escape(compact)}([^a-z0-9]|$)"

            # FLIXYFY_HISTORICAL_PEOPLE_SHORT_ALIAS_V1
            # Common Indian cinema initials should resolve to canonical person slugs.
            short_alias_slugs = {
                "ntr": ["n-t-rama-rao"],
                "anr": ["akkineni-nageshwara-rao"],
                "mgr": ["m-g-ramachandran", "m-g-ramachandran"],
            }
            alias_slugs = short_alias_slugs.get(compact, [])
            if alias_slugs and "person_slug" in cols:
                placeholders = ", ".join(["%s"] * len(alias_slugs))
                search_parts.append(f"person_slug IN ({placeholders})")
                params.extend(alias_slugs)

            for col in ("person_slug", "person_name", "display_name", "page_title", "meta_title"):
                if col in cols:
                    normalized_expr = (
                        "LOWER(REGEXP_REPLACE("
                        f"COALESCE(CAST({qident(col)} AS TEXT), ''), "
                        "'[^a-zA-Z0-9]+', '', 'g'))"
                    )
                    token_expr = (
                        "LOWER(REGEXP_REPLACE("
                        f"COALESCE(CAST({qident(col)} AS TEXT), ''), "
                        "'[^a-zA-Z0-9]+', ' ', 'g'))"
                    )
                    search_parts.append(f"({normalized_expr} = %s OR {token_expr} ~ %s)")
                    params.extend([compact, token_pattern])
        else:
            for col in ("person_name", "display_name", "page_title", "meta_title", "person_slug"):
                if col in cols:
                    search_parts.append(
                        f"LOWER(COALESCE(CAST({qident(col)} AS TEXT), '')) LIKE LOWER(%s)"
                    )
                    params.append(f"%{query}%")

        if search_parts:
            where.append("(" + " OR ".join(search_parts) + ")")

    if youtube_only and "youtube_movie_count" in cols:
        where.append("COALESCE(youtube_movie_count, 0) > 0")

    youtube_expr = "COALESCE(youtube_movie_count, 0)" if "youtube_movie_count" in cols else "0"
    name_expr = (
        "COALESCE(person_name, display_name, person_slug, '')"
        if "display_name" in cols
        else "COALESCE(person_name, person_slug, '')"
    )

    where_sql = "WHERE " + " AND ".join(where) if where else ""

    rows = _fhp_rows(
        f"""
        SELECT *
        FROM public.{qident(table_name)}
        {where_sql}
        ORDER BY {youtube_expr} DESC, {movie_expr} DESC, {name_expr} ASC
        LIMIT %s OFFSET %s
        """,
        params + [limit, offset],
    )

    total_rows = _fhp_rows(
        f"""
        SELECT COUNT(*) AS total
        FROM public.{qident(table_name)}
        {where_sql}
        """,
        params,
    )

    total = int((total_rows[0] or {}).get("total") or 0) if total_rows else 0
    pages = (total + limit - 1) // limit if total else 0

    def strict_payload(row):
        data = _fhp_person_payload(row)

        primary_count = _fhp_int(
            _fhp_pick(row, "primary_language_movie_count", "movie_count", "career_attached_movie_count"),
            0,
        )
        career_count = _fhp_int(
            _fhp_pick(row, "career_attached_movie_count", "total_movie_count", "movie_count"),
            primary_count,
        )

        data["movie_count"] = primary_count
        data["primary_language_movie_count"] = primary_count
        data["career_attached_movie_count"] = career_count
        data["total_movie_count"] = career_count
        data["primary_language_slug"] = _fhp_pick(row, "primary_language_slug")
        data["primary_language_name"] = _fhp_pick(row, "primary_language_name")
        data["source_table"] = table_name
        data["language"] = _fhp_pick(row, "primary_language_slug")
        return data

    items = [strict_payload(row) for row in rows]

    return {
        "domain": "historical",
        "source_table": table_name,
        "page": page,
        "limit": limit,
        "total": total,
        "count": len(items),
        "pages": pages,
        "language": language or None,
        "items": items,
    }

@router.get("/api/v3/historical/person/{person_slug}")
def historical_person_detail_patched_v1(person_slug: str, page: int = 1, limit: int = 96):
    people_table = "historical_people_seo_preprod_fixed_v1"
    slug, redirected_from = _resolve_person_slug(person_slug)

    if not _fhp_table_exists(people_table):
        raise HTTPException(status_code=404, detail="Historical person not found")

    table_cols = set(_fhp_columns(people_table))

    requested_slug = str(person_slug or "").strip().strip("/").lower()
    requested_slug = re.sub(r"^historical/person/", "", requested_slug)

    lookup_values = []

    def add_lookup(value):
        value = str(value or "").strip().strip("/").lower()
        if value and value not in lookup_values:
            lookup_values.append(value)

    add_lookup(slug)
    add_lookup(requested_slug)

    ntr_aliases = {
        "ntr",
        "n-t-rama-rao",
        "n-t-rama-rao-sr",
        "nandamuri-taraka-rama-rao",
    }
    if slug in ntr_aliases or requested_slug in ntr_aliases:
        for alias in sorted(ntr_aliases):
            add_lookup(alias)

    compact_values = []
    for value in lookup_values:
        compact = compact_people_query(value)
        if compact and compact not in compact_values:
            compact_values.append(compact)

    where_parts = []
    params = []

    slug_lookup_cols = (
        "slug",
        "person_slug",
        "name_slug",
        "alias_slug",
        "canonical_slug",
        "person_key",
        "canonical_person_key",
    )
    for col in slug_lookup_cols:
        if col in table_cols and lookup_values:
            where_parts.append(f"LOWER(COALESCE(CAST({qident(col)} AS TEXT), '')) = ANY(%s)")
            params.append(lookup_values)

    name_lookup_cols = ("person_name", "name", "display_name")
    for col in name_lookup_cols:
        if col in table_cols and compact_values:
            normalized_expr = (
                "LOWER(REGEXP_REPLACE("
                f"COALESCE(CAST({qident(col)} AS TEXT), ''), "
                "'[^a-zA-Z0-9]+', '', 'g'))"
            )
            where_parts.append(f"{normalized_expr} = ANY(%s)")
            params.append(compact_values)

    alias_lookup_cols = ("aliases", "aliases_json", "compact_aliases_text")
    for col in alias_lookup_cols:
        if col in table_cols and lookup_values:
            alias_parts = []
            for value in lookup_values + compact_values:
                alias_parts.append(f"LOWER(COALESCE(CAST({qident(col)} AS TEXT), '')) LIKE %s")
                params.append(f"%{value}%")
            if alias_parts:
                where_parts.append("(" + " OR ".join(alias_parts) + ")")

    if "seo_url" in table_cols and lookup_values:
        seo_values = []
        for value in lookup_values:
            for seo_value in (
                value,
                f"/historical/person/{value}",
                f"historical/person/{value}",
            ):
                if seo_value not in seo_values:
                    seo_values.append(seo_value)
        where_parts.append("LOWER(COALESCE(CAST(seo_url AS TEXT), '')) = ANY(%s)")
        params.append(seo_values)

    if not where_parts:
        raise HTTPException(status_code=404, detail="Historical person not found")

    order_parts = []
    if "person_slug" in table_cols:
        order_parts.append("WHEN LOWER(COALESCE(CAST(person_slug AS TEXT), '')) = %s THEN 0")
        params.append(slug)
    for col in ("slug", "canonical_slug", "name_slug", "alias_slug"):
        if col in table_cols:
            order_parts.append(f"WHEN LOWER(COALESCE(CAST({qident(col)} AS TEXT), '')) = %s THEN 1")
            params.append(slug)

    order_sql = ""
    if order_parts:
        order_sql = "ORDER BY CASE " + " ".join(order_parts) + " ELSE 10 END"

    person_rows = _fhp_rows(
        f"""
        SELECT *
        FROM public.{qident(people_table)}
        WHERE {" OR ".join(where_parts)}
        {order_sql}
        LIMIT 1
        """,
        params,
    )

    if not person_rows:
        raise HTTPException(status_code=404, detail="Historical person not found")

    person = _fhp_person_payload(person_rows[0])
    if redirected_from:
        person["redirected_from"] = redirected_from
        person["canonical_slug"] = person["person_slug"]
    page = max(1, int(page or 1))
    limit = max(1, min(int(limit or 96), 200))
    offset = (page - 1) * limit

    movie_rows = _historical_person_movie_rows(person["person_slug"], limit, offset)

    items = [_fhp_person_movie_card(row) for row in movie_rows if _fhp_pick(row, "slug", "movie_slug")]
    total = _historical_person_movie_count(person["person_slug"])
    person["movie_count"] = total

    return {
        "domain": "historical",
        "person": person,
        "page": page,
        "limit": limit,
        "total": total,
        "pages": page + (1 if len(movie_rows) == limit else 0),
        "items": items,
    }


@router.get("/api/v3/historical/combination/{combo_slug}")
def historical_combination_detail_patched_v1(combo_slug: str, page: int = 1, limit: int = 96):
    if not _fhp_table_exists("historical_combo_seo_preprod_v1") or not _fhp_table_exists("historical_combo_movie_index_preprod_v1"):
        raise HTTPException(status_code=404, detail="Historical combination data not found")

    slug = str(combo_slug or "").strip().strip("/")
    seo_url = f"/historical/combination/{slug}"
    alt_seo_url = f"historical/combination/{slug}"

    combo_rows = _fhp_rows(
        """
        SELECT *
        FROM historical_combo_seo_preprod_v1
        WHERE seo_url IN (%s, %s)
           OR (person_a_slug || '-' || person_b_slug) = %s
           OR (person_b_slug || '-' || person_a_slug) = %s
        LIMIT 1
        """,
        [seo_url, alt_seo_url, slug, slug],
    )

    if not combo_rows:
        raise HTTPException(status_code=404, detail="Historical combination not found")

    combo = _fhp_combo_payload(combo_rows[0])
    page = max(1, int(page or 1))
    limit = max(1, min(int(limit or 96), 200))
    offset = (page - 1) * limit

    count_rows = _fhp_rows(
        "SELECT COUNT(*) AS total FROM historical_combo_movie_index_preprod_v1 WHERE combo_key=%s",
        [combo["combo_key"]],
    )
    total = _fhp_int(count_rows[0].get("total") if count_rows else 0, 0)

    serving_table = HISTORICAL_CARD_TABLE if _fhp_table_exists(HISTORICAL_CARD_TABLE) else None
    if serving_table:
        movie_rows = _fhp_rows(
            f"""
            SELECT h.*,
                   cm.movie_id,
                   cm.movie_slug,
                   cm.title AS combo_title,
                   cm.release_year AS combo_release_year,
                   cm.language_slug AS combo_language_slug,
                   cm.has_youtube AS combo_has_youtube
            FROM historical_combo_movie_index_preprod_v1 cm
            LEFT JOIN {serving_table} h ON h.slug = cm.movie_slug
            WHERE cm.combo_key=%s
            ORDER BY COALESCE(cm.has_youtube, 0) DESC, cm.release_year DESC NULLS LAST, cm.title ASC
            LIMIT %s OFFSET %s
            """,
            [combo["combo_key"], limit, offset],
        )
    else:
        movie_rows = _fhp_rows(
            """
            SELECT *
            FROM historical_combo_movie_index_preprod_v1
            WHERE combo_key=%s
            ORDER BY COALESCE(has_youtube, 0) DESC, release_year DESC NULLS LAST, title ASC
            LIMIT %s OFFSET %s
            """,
            [combo["combo_key"], limit, offset],
        )

    items = [_fhp_combo_movie_card(row) for row in movie_rows if _fhp_pick(row, "slug", "movie_slug")]

    return {
        "domain": "historical",
        "combo": combo,
        "page": page,
        "limit": limit,
        "total": total,
        "pages": (total + limit - 1) // limit if limit else 0,
        "items": items,
    }


@router.get("/api/v3/historical")
def historical_movies_patched_v1(
    page: int = 1,
    limit: int = 24,
    q: str = None,
    year: str = None,
    language: str = None,
    sort: str = "popular",
    provider: str = None,
    has_ott: str = None,
    availability: str = None,
):
    page = max(1, int(page or 1))
    limit = max(1, min(int(limit or 24), 100))
    offset = (page - 1) * limit

    table = HISTORICAL_CARD_TABLE

    where = []
    params = []

    if q:
        where.append("(LOWER(title) LIKE %s OR LOWER(slug) LIKE %s)")
        params.extend([f"%{str(q).lower()}%", f"%{str(q).lower()}%"])

    if year:
        where.append("CAST(release_year AS TEXT) = %s")
        params.append(str(year))

    if language:
        where.append("(LOWER(CAST(language_slug AS TEXT)) = %s OR LOWER(CAST(language_name AS TEXT)) = %s)")
        params.extend([str(language).lower(), str(language).lower()])

    blocked_slugs = ["a-r-rahman-1999-ta", "a-venkatesh-1999-ta", "ajith-kumar-1999-ta"]
    where.append("slug <> ALL(%s)")
    params.append(blocked_slugs)

    provider_text = str(provider or "").strip().lower()
    availability_text = str(availability or "").strip().lower()
    has_ott_text = str(has_ott or "").strip().lower()

    youtube_provider_filter = provider_text == "youtube" or availability_text in ("youtube", "free")
    provider_terms = provider_match_terms(provider or ("youtube" if youtube_provider_filter else ""))
    ott_available_filter = (
        availability_text in ("true", "ott", "1")
        or has_ott_text in ("1", "true", "yes", "y", "ott", "youtube")
    )
    availability_filter = bool(provider_terms) or youtube_provider_filter or ott_available_filter

    join_sql = ""
    join_params = []
    table_cols = set(_fhp_columns(table))
    availability_cols = set(_fhp_columns(HISTORICAL_AVAILABILITY_TABLE)) if _fhp_table_exists(HISTORICAL_AVAILABILITY_TABLE) else set()
    direct_availability_filters = []

    if youtube_provider_filter:
        if "ott_primary_key" in table_cols:
            direct_availability_filters.append("LOWER(COALESCE(CAST(h.ott_primary_key AS TEXT), '')) = 'youtube'")
        if "ott_primary" in table_cols:
            direct_availability_filters.append("LOWER(COALESCE(CAST(h.ott_primary AS TEXT), '')) LIKE '%%youtube%%'")
        if "has_youtube" in table_cols:
            direct_availability_filters.append("LOWER(COALESCE(CAST(h.has_youtube AS TEXT), '')) IN ('1', 'true', 'yes', 'y')")
        if "youtube_count" in table_cols:
            direct_availability_filters.append("COALESCE(h.youtube_count, 0) > 0")
        if "youtube_url" in table_cols:
            direct_availability_filters.append(
                "(h.youtube_url IS NOT NULL AND TRIM(CAST(h.youtube_url AS TEXT)) <> '' "
                "AND (LOWER(CAST(h.youtube_url AS TEXT)) LIKE '%%youtube.com%%' "
                "OR LOWER(CAST(h.youtube_url AS TEXT)) LIKE '%%youtu.be%%'))"
            )

    if ott_available_filter or (youtube_provider_filter and not direct_availability_filters):
        if "has_ott" in table_cols:
            direct_availability_filters.append("LOWER(COALESCE(CAST(h.has_ott AS TEXT), '')) IN ('1', 'true', 'yes', 'y')")
        if "ott_count" in table_cols:
            direct_availability_filters.append("COALESCE(h.ott_count, 0) > 0")

    if provider_terms and "content_slug" in availability_cols:
        provider_conditions = []
        for term in provider_terms:
            key_term = term.replace(" ", "_").replace("-", "_")
            spaced_term = term.replace("_", " ").replace("-", " ")
            if "provider_key" in availability_cols:
                provider_conditions.append("LOWER(COALESCE(CAST(a.provider_key AS TEXT), '')) LIKE %s")
                join_params.append(f"%{key_term}%")
            if "provider_name" in availability_cols:
                provider_conditions.append("LOWER(COALESCE(CAST(a.provider_name AS TEXT), '')) LIKE %s")
                join_params.append(f"%{spaced_term}%")
            if "provider_display_name" in availability_cols:
                provider_conditions.append("LOWER(COALESCE(CAST(a.provider_display_name AS TEXT), '')) LIKE %s")
                join_params.append(f"%{spaced_term}%")
            if "normalized_provider_name" in availability_cols:
                provider_conditions.append("LOWER(COALESCE(CAST(a.normalized_provider_name AS TEXT), '')) LIKE %s")
                join_params.append(f"%{key_term}%")

        provider_key_expr = "a.provider_key" if "provider_key" in availability_cols else (
            "a.normalized_provider_name" if "normalized_provider_name" in availability_cols else "NULL"
        )
        provider_name_expr = "a.provider_name" if "provider_name" in availability_cols else (
            "a.provider_display_name" if "provider_display_name" in availability_cols else (
                "a.normalized_provider_name" if "normalized_provider_name" in availability_cols else provider_key_expr
            )
        )
        provider_type_expr = "a.monetization_type" if "monetization_type" in availability_cols else (
            "a.provider_type" if "provider_type" in availability_cols else (
                "a.availability_type" if "availability_type" in availability_cols else "NULL"
            )
        )
        provider_url_expr = "a.final_url" if "final_url" in availability_cols else (
            "a.deep_link" if "deep_link" in availability_cols else (
                "a.search_url" if "search_url" in availability_cols else "NULL"
            )
        )
        provider_where = "WHERE " + " OR ".join(provider_conditions) if provider_conditions else ""
        join_sql = (
            " JOIN ("
            "   SELECT DISTINCT ON (a.content_slug) "
            "          a.content_slug, "
            f"         {provider_key_expr} AS fhp_provider_key, "
            f"         {provider_name_expr} AS fhp_provider_name, "
            f"         {provider_type_expr} AS fhp_provider_type, "
            f"         {provider_url_expr} AS fhp_provider_url "
            f"   FROM public.{qident(HISTORICAL_AVAILABILITY_TABLE)} a "
            f"   {provider_where} "
            "   ORDER BY a.content_slug, "
            "            CASE LOWER(COALESCE(CAST("
            f"{provider_key_expr}"
            " AS TEXT), '')) WHEN 'youtube' THEN 0 ELSE 1 END, "
            "            LOWER(COALESCE(CAST("
            f"{provider_name_expr}"
            " AS TEXT), ''))"
            " ) hav ON hav.content_slug = h.slug "
        )
    elif ott_available_filter and "content_slug" in availability_cols:
        provider_key_expr = "a.provider_key" if "provider_key" in availability_cols else (
            "a.normalized_provider_name" if "normalized_provider_name" in availability_cols else "NULL"
        )
        provider_name_expr = "a.provider_name" if "provider_name" in availability_cols else (
            "a.provider_display_name" if "provider_display_name" in availability_cols else (
                "a.normalized_provider_name" if "normalized_provider_name" in availability_cols else provider_key_expr
            )
        )
        provider_type_expr = "a.monetization_type" if "monetization_type" in availability_cols else (
            "a.provider_type" if "provider_type" in availability_cols else (
                "a.availability_type" if "availability_type" in availability_cols else "NULL"
            )
        )
        provider_url_expr = "a.final_url" if "final_url" in availability_cols else (
            "a.deep_link" if "deep_link" in availability_cols else (
                "a.search_url" if "search_url" in availability_cols else "NULL"
            )
        )
        join_sql = (
            " JOIN ("
            "   SELECT DISTINCT ON (a.content_slug) "
            "          a.content_slug, "
            f"         {provider_key_expr} AS fhp_provider_key, "
            f"         {provider_name_expr} AS fhp_provider_name, "
            f"         {provider_type_expr} AS fhp_provider_type, "
            f"         {provider_url_expr} AS fhp_provider_url "
            f"   FROM public.{qident(HISTORICAL_AVAILABILITY_TABLE)} a "
            "   ORDER BY a.content_slug, LOWER(COALESCE(CAST("
            f"{provider_name_expr}"
            " AS TEXT), ''))"
            " ) hav ON hav.content_slug = h.slug "
        )
    elif availability_filter and direct_availability_filters:
        where.append("(" + " OR ".join(direct_availability_filters) + ")")
    elif availability_filter and _fhp_table_exists(YOUTUBE_LINK_TABLE):
        join_sql = (
            " JOIN ("
            f"   SELECT DISTINCT content_slug FROM public.{qident(YOUTUBE_LINK_TABLE)} "
            "   WHERE COALESCE(active, 1)=1 "
            "     AND LOWER(COALESCE(content_domain, 'historical')) IN ('historical', 'classic')"
            " ) ytv ON ytv.content_slug = h.slug "
        )
    elif availability_filter and _fhp_table_exists("historical_youtube_verified_links_v1"):
        join_sql = (
            " JOIN ("
            "   SELECT DISTINCT slug FROM historical_youtube_verified_links_v1 "
            "   WHERE COALESCE(active, TRUE)=TRUE"
            " ) ytv ON ytv.slug = h.slug "
        )
    elif availability_filter:
        where.append(
            "youtube_url IS NOT NULL AND TRIM(CAST(youtube_url AS TEXT)) <> '' "
            "AND (LOWER(CAST(youtube_url AS TEXT)) LIKE %s OR LOWER(CAST(youtube_url AS TEXT)) LIKE %s)"
        )
        params.extend(["%youtube.com%", "%youtu.be%"])

    where_sql = "WHERE " + " AND ".join(where) if where else ""

    order_sql = "ORDER BY"
    if sort == "latest":
        order_sql += " release_year DESC NULLS LAST, title ASC"
    elif sort == "title":
        order_sql += " title ASC"
    elif sort == "rating":
        order_sql += " rating DESC NULLS LAST, title ASC"
    else:
        order_sql += " release_year DESC NULLS LAST, title ASC"

    select_parts = ["h.*"]
    if youtube_provider_filter:
        select_parts.append("TRUE AS fhp_force_youtube")
    if join_sql and " hav " in join_sql:
        select_parts.extend([
            "hav.fhp_provider_key",
            "hav.fhp_provider_name",
            "hav.fhp_provider_type",
            "hav.fhp_provider_url",
        ])
    select_sql = ", ".join(select_parts)

    rows = _fhp_rows(
        f'SELECT {select_sql} FROM "{table}" h {join_sql} {where_sql} {order_sql} LIMIT %s OFFSET %s',
        join_params + params + [limit, offset],
    )

    items = [_fhp_list_card(row) for row in rows if not _fhp_bad_person_row(row)]

    total_rows = _fhp_rows(
        f'SELECT COUNT(*) AS total FROM "{table}" h {join_sql} {where_sql}',
        join_params + params,
    )
    total = int((total_rows[0] or {}).get("total") or 0) if total_rows else 0
    pages = (total + limit - 1) // limit if total else 0

    return {
        "domain": "historical",
        "source_domain": "historical",
        "page": page,
        "limit": limit,
        "total": total,
        "pages": pages,
        "items": items,
    }


@router.get("/api/v3/historical/{slug}")
def historical_detail_patched_v1(slug: str):
    row = _fhp_fetch_historical_row(slug)

    if not row:
        raise HTTPException(status_code=404, detail="Historical movie not found")

    return _fhp_detail(row)


@router.get("/api/v3/historical/movie/{slug}")
def historical_detail_movie_alias_patched_v1(slug: str):
    return historical_detail_patched_v1(slug)


@router.get("/api/v3/historical/movies/{slug}")
def historical_detail_movies_alias_patched_v1(slug: str):
    return historical_detail_patched_v1(slug)
# /FLIXYFY_DOMAIN_HISTORICAL_ROUTES_PATCH_V1


@router.get("/api/v3/historical")
def historical_movies(
    page: int = Query(1, ge=1),
    limit: int = Query(24, ge=1, le=100),
    q: str = Query("", min_length=0),
    language: Optional[str] = None,
    year: Optional[int] = None,
    sort: str = Query("popular"),
):
    table_name = HISTORICAL_CARD_TABLE if table_exists(HISTORICAL_CARD_TABLE) else HISTORICAL_TABLE

    return fetch_rows(
        table_name=table_name,
        domain="historical",
        page=page,
        limit=limit,
        search_query=q.strip(),
        language=language,
        year=year,
        sort=sort,
    )


@router.get("/api/v3/historical/{slug}")
def historical_detail(slug: str):
    row = row_by_slug(HISTORICAL_DETAIL_TABLE, slug) or row_by_slug(HISTORICAL_TABLE, slug)

    if not row:
        raise HTTPException(status_code=404, detail="Historical movie not found")

    return domain_detail(row, "historical")


@router.get("/api/v3/global-search")
def global_search(
    q: str = Query("", min_length=0),
    page: int = Query(1, ge=1),
    limit: int = Query(24, ge=1, le=100),
    language: Optional[str] = None,
    year: Optional[int] = None,
    sort: str = Query("popular"),
    availability: Optional[str] = None,
    provider: Optional[str] = None,
    domain: Optional[str] = Query(None),
    region: Optional[str] = Query(None),
    content_type: Optional[str] = Query(None, alias="type"),
):
    query = q.strip()
    offset = (page - 1) * limit
    fetch_limit = offset + limit

    if domain:
        requested = {d.strip().lower() for d in domain.split(",") if d.strip()}
    else:
        requested = {"modern", "hollywood", "historical"}

    requested_type = str(content_type or "movies").strip().lower()
    if requested_type not in {"movies", "webseries", "people", "all"}:
        requested_type = "movies"

    search_movies = requested_type in {"movies", "all"}
    search_series = requested_type in {"webseries", "all"}
    search_persons = requested_type == "people"

    requested_region = str(region or "").strip().lower()
    if requested_type == "webseries":
        if requested_region in {"indian", "india", "in"}:
            scope = "indian"
        elif requested_region in {"global", "world", "international"}:
            scope = "global"
        elif requested_region in {"korean", "korea", "kr"}:
            scope = "korean"
        else:
            scope = "indian" if requested and requested <= {"modern", "indian"} else "global"
    else:
        scope = "indian" if requested and requested <= {"modern", "indian"} else "global"

    total = 0
    items = []

    if search_movies and ("modern" in requested or "indian" in requested):
        modern_total, modern_items = search_modern(
            query=query,
            limit=fetch_limit,
            language=language,
            year=year,
            provider=provider,
            availability=availability,
            sort=sort,
        )
        total += modern_total
        items.extend(modern_items)

    if search_movies and "hollywood" in requested:
        table_name = HOLLYWOOD_SEARCH_TABLE if table_exists(HOLLYWOOD_SEARCH_TABLE) else HOLLYWOOD_TABLE
        hollywood_total, hollywood_items = search_domain(
            table_name=table_name,
            domain="hollywood",
            query=query,
            limit=fetch_limit,
            language=None,
            year=year,
        )
        total += hollywood_total
        items.extend(hollywood_items)

    if search_movies and "historical" in requested:
        table_name = HISTORICAL_SEARCH_TABLE if table_exists(HISTORICAL_SEARCH_TABLE) else HISTORICAL_TABLE
        historical_total, historical_items = search_domain(
            table_name=table_name,
            domain="historical",
            query=query,
            limit=fetch_limit,
            language=language,
            year=year,
        )
        total += historical_total
        items.extend(historical_items)

    if search_series:
        webseries_total, webseries_items = search_webseries(
            query=query,
            limit=fetch_limit,
            scope=scope,
            year=year,
            language=language,
            provider=provider,
            availability=availability,
            sort=sort,
        )
        total += webseries_total
        items.extend(webseries_items)

    if search_persons:
        people_total, people_items = search_people(
            query=query,
            limit=fetch_limit,
            scope=scope,
            language=language,
        )
        total += people_total
        items.extend(people_items)

    query_lower = query.lower()
    sort_value = str(sort or "popular").strip().lower()
    provider_selected = bool(str(provider or "").strip())

    def number_value(value, default=0.0):
        try:
            return float(value or default)
        except Exception:
            return default

    def score(item):
        title = str(item.get("title") or "").lower()
        release_year = item.get("release_year") or 0
        compact_query = "".join(ch.lower() for ch in query_lower if ch.isalnum())

        exact = 0

        if query_lower:
            if title == query_lower:
                exact = 100000
            elif title.startswith(query_lower):
                exact = 50000
            elif query_lower in title:
                exact = 25000

        if item.get("domain") == "person":
            compact_title = "".join(ch.lower() for ch in title if ch.isalnum())
            aliases = item.get("aliases") or []
            compact_aliases = ["".join(ch.lower() for ch in str(alias) if ch.isalnum()) for alias in aliases]
            if compact_query:
                if compact_title == compact_query or compact_query in compact_aliases:
                    exact = max(exact, 100000)
                elif compact_title.startswith(compact_query) or any(alias.startswith(compact_query) for alias in compact_aliases):
                    exact = max(exact, 50000)
                elif compact_query in compact_title or any(compact_query in alias for alias in compact_aliases):
                    exact = max(exact, 25000)

        domain_boost = {
            "modern": 300,
            "webseries": 250,
            "person": 240,
            "hollywood": 200,
            "historical": 100,
        }.get(item.get("domain"), 0)

        try:
            year_score = int(release_year or 0)
        except Exception:
            year_score = 0

        if item.get("domain") == "person":
            try:
                person_rank = int(float(item.get("quality_score") or item.get("movie_count") or 0))
            except Exception:
                person_rank = 0
            return (exact, domain_boost, person_rank)

        rating_score = number_value(item.get("rating") or item.get("quality_score"))
        popularity_score = number_value(item.get("popularity") or item.get("quality_score"))
        provider_score = 1 if item.get("has_ott") or item.get("ott_primary") else 0

        if sort_value == "latest":
            return (exact, year_score, rating_score, provider_score, domain_boost, popularity_score)

        if sort_value in {"rating", "imdb", "top_imdb", "top-imdb"}:
            return (exact, rating_score, year_score, provider_score, domain_boost, popularity_score)

        if provider_selected:
            return (exact, provider_score, rating_score, popularity_score, year_score, domain_boost)

        return (exact, domain_boost, year_score, provider_score, rating_score, popularity_score)

    if not search_persons:
        items.sort(key=score, reverse=True)

    page_items = items[offset:offset + limit]

    return {
        "query": query,
        "q": query,
        "page": page,
        "limit": limit,
        "total": total,
        "count": len(page_items),
        "pages": (total + limit - 1) // limit,
        "items": page_items,
    }




@router.get("/api/v3/domain-movie/{slug}")
def domain_movie(slug: str):
    row = row_by_slug(HOLLYWOOD_DETAIL_TABLE, slug) or row_by_slug(HOLLYWOOD_TABLE, slug)

    if row:
        return domain_detail(row, "hollywood")

    row = row_by_slug(HISTORICAL_DETAIL_TABLE, slug) or row_by_slug(HISTORICAL_TABLE, slug)

    if row:
        return domain_detail(row, "historical")

    raise HTTPException(status_code=404, detail="Domain movie not found")

