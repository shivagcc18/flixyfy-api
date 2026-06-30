import json
import os
from functools import lru_cache
from typing import Optional, Dict, Any, List, Tuple
from urllib.parse import quote_plus

import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

from fastapi import APIRouter, Query, HTTPException

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")

MODERN_TABLE = os.getenv("SERVING_TABLE", "media_serving_v8_expanded")
MODERN_SEARCH_TABLE = os.getenv("MODERN_SEARCH_TABLE", "media_serving_v8_expanded")

WEBSERIES_SEARCH_TABLE = os.getenv("WEBSERIES_SEARCH_TABLE", "webseries_search_serving_v1")
WEBSERIES_CARD_TABLE = os.getenv("WEBSERIES_CARD_TABLE", "webseries_card_serving_v1")
WEBSERIES_DETAIL_TABLE = os.getenv("WEBSERIES_DETAIL_TABLE", "webseries_detail_serving_v1")
WEBSERIES_AVAILABILITY_TABLE = os.getenv("WEBSERIES_AVAILABILITY_TABLE", "webseries_availability_serving_v1")

HOLLYWOOD_TABLE = os.getenv("HOLLYWOOD_SERVING_TABLE", "hollywood_serving_v3")
HOLLYWOOD_CARD_TABLE = os.getenv("HOLLYWOOD_CARD_TABLE", "hollywood_card_serving_v3")
HOLLYWOOD_DETAIL_TABLE = os.getenv("HOLLYWOOD_DETAIL_TABLE", "hollywood_detail_serving_v3")
HOLLYWOOD_SEARCH_TABLE = os.getenv("HOLLYWOOD_SEARCH_TABLE", "hollywood_search_serving_v3")
HOLLYWOOD_AVAILABILITY_TABLE = os.getenv("HOLLYWOOD_AVAILABILITY_TABLE", "hollywood_availability_v3")

HISTORICAL_TABLE = os.getenv("HISTORICAL_SERVING_TABLE", "historical_serving_v1")
HISTORICAL_CARD_TABLE = os.getenv("HISTORICAL_CARD_TABLE", "historical_card_serving_v1")
HISTORICAL_DETAIL_TABLE = os.getenv("HISTORICAL_DETAIL_TABLE", "historical_detail_serving_v1")
HISTORICAL_SEARCH_TABLE = os.getenv("HISTORICAL_SEARCH_TABLE", "historical_search_serving_v1")
HISTORICAL_AVAILABILITY_TABLE = os.getenv("HISTORICAL_AVAILABILITY_TABLE", "historical_availability_v2")

router = APIRouter()

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
                params.append(f"%{search_query}%")
                params.append(f"%{normalized_query}%")

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
    total = offset + len(rows) + (1 if has_more else 0)

    return {
        "domain": domain,
        "source_domain": domain,
        "page": page,
        "limit": limit,
        "total": total,
        "pages": page + (1 if has_more else 0),
        "items": [domain_card(dict(r), domain) for r in rows],
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


def domain_detail(row: Dict[str, Any], domain: str):
    data = domain_card(row, domain)

    raw_availability = availability_rows(
        domain=domain,
        movie_id=first(row, ["id", "movie_id"]),
        tmdb_id=row.get("tmdb_id"),
        imdb_id=row.get("imdb_id"),
        slug=row.get("slug"),
        title=row.get("title"),
    )

    availability = normalize_availability(raw_availability, domain)

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


def search_modern(query: str, limit: int, language: Optional[str], year: Optional[int]):
    table_name = MODERN_SEARCH_TABLE if table_exists(MODERN_SEARCH_TABLE) else MODERN_TABLE

    if not table_exists(table_name):
        return 0, []

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

    where_clause = "WHERE " + " AND ".join(where) if where else ""

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
                    WHEN LOWER(title) LIKE LOWER(%s) THEN 2
                    WHEN LOWER(COALESCE(original_title, '')) = LOWER(%s) THEN 3
                    WHEN LOWER(COALESCE(original_title, '')) LIKE LOWER(%s) THEN 4
                    ELSE 5
                END,
            """
            order_params = [query, f"{query}%", query, f"{query}%"]
        else:
            order_sql = ""

        cur.execute(
            f"""
            SELECT *
            FROM public.{qident(table_name)}
            {where_clause}
            ORDER BY
                {order_sql}
                has_ott DESC NULLS LAST,
                release_year DESC NULLS LAST,
                COALESCE(rating, 0) DESC NULLS LAST,
                title ASC
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
                row.update(summary)
                row["ott_all"] = summary["watch_providers"]
                row["availability"] = summary["watch_providers"]

            items.append(modern_card(row))
    finally:
        conn.close()

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

    order = order_sql(table_name, "popular")

    conn = get_conn()
    cur = conn.cursor()

    try:
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
            params + [limit],
        )

        items = [domain_card(dict(r), domain) for r in cur.fetchall()]
    finally:
        conn.close()

    return total, items


def webseries_card(row: Dict[str, Any]):
    providers = parse_json(row.get("provider_names"), [])
    major_providers = parse_json(row.get("major_provider_names"), [])
    provider_list = major_providers if major_providers else providers
    primary_provider = provider_list[0] if provider_list else None

    return {
        "domain": "webseries",
        "content_type": "webseries",
        "source_label": "Webseries",
        "tmdb_id": row.get("tmdb_id"),
        "title": row.get("title"),
        "slug": row.get("slug"),
        "movie_url": f"/webseries/{row.get('slug')}" if row.get("slug") else None,
        "release_year": row.get("first_air_year"),
        "year": row.get("first_air_year"),
        "primary_language": row.get("original_language"),
        "language_slug": row.get("original_language"),
        "poster_url": fix_image_url(row.get("poster_path")),
        "rating": row.get("vote_average"),
        "vote_count": row.get("vote_count"),
        "popularity": row.get("popularity_score"),
        "quality_score": row.get("popularity_score"),
        "ott_primary": primary_provider,
        "ott_count": row.get("availability_count"),
        "has_ott": as_bool(row.get("has_major_provider")) or bool(primary_provider),
        "raw": dict(row),
    }


def search_webseries(query: str, limit: int, scope: str, year: Optional[int]):
    if not table_exists(WEBSERIES_SEARCH_TABLE):
        return 0, []

    where = []
    params = []

    if query:
        where.append(
            "(LOWER(s.title) LIKE LOWER(%s) "
            "OR LOWER(COALESCE(s.normalized_title, '')) LIKE LOWER(%s) "
            "OR LOWER(COALESCE(s.search_text, '')) LIKE LOWER(%s))"
        )
        params.extend([f"%{query}%", f"%{query}%", f"%{query}%"])

    if scope == "indian":
        where.append("LOWER(COALESCE(s.region, '')) = 'indian'")

    if year:
        where.append("s.first_air_year = %s")
        params.append(year)

    where_clause = "WHERE " + " AND ".join(where) if where else ""

    conn = get_conn()
    cur = conn.cursor()

    try:
        cur.execute(
            f"SELECT COUNT(*) AS total FROM public.{qident(WEBSERIES_SEARCH_TABLE)} s {where_clause}",
            params,
        )
        total = cur.fetchone()["total"]

        order_params = []
        if query:
            order_sql = """
                CASE
                    WHEN LOWER(s.title) = LOWER(%s) THEN 1
                    WHEN LOWER(s.title) LIKE LOWER(%s) THEN 2
                    WHEN LOWER(COALESCE(s.normalized_title, '')) = LOWER(%s) THEN 3
                    WHEN LOWER(COALESCE(s.normalized_title, '')) LIKE LOWER(%s) THEN 4
                    ELSE 5
                END,
            """
            order_params = [query, f"{query}%", query, f"{query}%"]
        else:
            order_sql = ""

        cur.execute(
            f"""
            SELECT s.*, c.poster_path, c.vote_average, c.major_provider_names, c.availability_count
            FROM public.{qident(WEBSERIES_SEARCH_TABLE)} s
            LEFT JOIN public.{qident(WEBSERIES_CARD_TABLE)} c ON c.slug = s.slug
            {where_clause}
            ORDER BY
                {order_sql}
                COALESCE(s.has_major_provider, 0) DESC,
                COALESCE(s.popularity_score, 0) DESC,
                s.first_air_year DESC NULLS LAST,
                s.title ASC
            LIMIT %s
            """,
            params + order_params + [limit],
        )
        items = [webseries_card(dict(r)) for r in cur.fetchall()]
    finally:
        conn.close()

    return total, items


def webseries_availability(tmdb_id):
    if not tmdb_id or not table_exists(WEBSERIES_AVAILABILITY_TABLE):
        return []

    conn = get_conn()
    cur = conn.cursor()

    try:
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

    return {
        "domain": "person",
        "content_type": "person",
        "source_domain": domain,
        "source_label": "People",
        "title": person_name,
        "person_name": person_name,
        "person_slug": person_slug,
        "slug": person_slug,
        "movie_url": f"/historical/person/{person_slug}" if domain == "historical" and person_slug else None,
        "primary_role": row.get("primary_role"),
        "release_year": row.get("last_year"),
        "year": row.get("last_year"),
        "movie_count": row.get("movie_count"),
        "youtube_movie_count": row.get("youtube_movie_count"),
        "quality_score": row.get("movie_count") or 0,
        "raw": dict(row),
    }


def search_people(query: str, limit: int, scope: str):
    if not table_exists("historical_people_seo_preprod_v1"):
        return 0, []

    where = []
    params = []

    if query:
        where.append("(LOWER(person_name) LIKE LOWER(%s) OR LOWER(COALESCE(seo_title, '')) LIKE LOWER(%s))")
        params.extend([f"%{query}%", f"%{query}%"])

    where.append("COALESCE(indexable, 1) = 1")
    where_clause = "WHERE " + " AND ".join(where)

    conn = get_conn()
    cur = conn.cursor()

    try:
        cur.execute(
            f"SELECT COUNT(*) AS total FROM public.historical_people_seo_preprod_v1 {where_clause}",
            params,
        )
        total = cur.fetchone()["total"]

        order_params = []
        if query:
            order_sql = """
                CASE
                    WHEN LOWER(person_name) = LOWER(%s) THEN 1
                    WHEN LOWER(person_name) LIKE LOWER(%s) THEN 2
                    WHEN LOWER(COALESCE(seo_title, '')) LIKE LOWER(%s) THEN 3
                    ELSE 4
                END,
            """
            order_params = [query, f"{query}%", f"%{query}%"]
        else:
            order_sql = ""

        cur.execute(
            f"""
            SELECT *
            FROM public.historical_people_seo_preprod_v1
            {where_clause}
            ORDER BY
                {order_sql}
                COALESCE(youtube_movie_count, 0) DESC,
                COALESCE(movie_count, 0) DESC,
                person_name ASC
            LIMIT %s
            """,
            params + order_params + [limit],
        )
        items = [person_search_card(dict(r), "historical") for r in cur.fetchall()]
    finally:
        conn.close()

    return total, items


@router.get("/api/v3/webseries/{slug}")
def webseries_detail(slug: str):
    row = row_by_slug(WEBSERIES_DETAIL_TABLE, slug)

    if not row:
        raise HTTPException(status_code=404, detail="Webseries not found")

    data = webseries_card(row)
    availability = webseries_availability(row.get("tmdb_id"))

    data.update(
        {
            "overview": row.get("overview") or row.get("omdb_plot"),
            "genres": parse_json(row.get("genres"), []),
            "backdrop_url": fix_image_url(row.get("backdrop_path")),
            "imdb_id": row.get("imdb_id"),
            "imdb_rating": row.get("omdb_imdb_rating"),
            "imdb_votes": row.get("omdb_imdb_votes"),
            "awards": row.get("omdb_awards"),
            "first_air_date": row.get("first_air_date"),
            "number_of_seasons": row.get("number_of_seasons"),
            "number_of_episodes": row.get("number_of_episodes"),
            "series_status": row.get("series_status"),
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
        "SELECT 1 FROM information_schema.tables WHERE table_schema='public' AND table_name=%s LIMIT 1",
        [table_name],
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
        "historical_detail_serving_v1",
        "historical_serving_v1",
        "historical_availability_v2",
        "historical_card_serving_v1",
        "historical_search_serving_v1",
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
    has_youtube = _fhp_is_youtube_url(youtube_url) or _fhp_bool(_fhp_pick(row, "has_youtube"))
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
        "ott_count": 1 if has_youtube else _fhp_pick(row, "ott_count"),
        "has_ott": has_ott,
        "has_free_ott": has_youtube or _fhp_bool(_fhp_pick(row, "has_free_ott")),
        "has_subscription_ott": _fhp_bool(_fhp_pick(row, "has_subscription_ott")),
        "has_rent_ott": _fhp_bool(_fhp_pick(row, "has_rent_ott")),
        "has_buy_ott": _fhp_bool(_fhp_pick(row, "has_buy_ott")),
        "is_free": has_youtube or _fhp_bool(_fhp_pick(row, "is_free")),
        "youtube_url": youtube_url,
        "youtube_title": _fhp_pick(row, "youtube_title"),
        "youtube_video_id": _fhp_pick(row, "youtube_video_id"),
        "youtube_count": 1 if has_youtube else 0,
    }


def _fhp_detail(row):
    data = _fhp_list_card(row)
    slug = data.get("slug")
    youtube_url = _fhp_pick(row, "youtube_url")
    links = []
    if _fhp_is_youtube_url(youtube_url):
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
    for table in ("historical_detail_serving_v1", "historical_serving_v1", "historical_card_serving_v1"):
        rows = _fhp_rows('SELECT * FROM "' + table + '" WHERE slug=%s LIMIT 1', [slug])
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
        "indexable": _fhp_bool(_fhp_pick(row, "indexable")),
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
        "indexable": _fhp_bool(_fhp_pick(row, "indexable")),
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


@router.get("/api/v3/historical/combinations")
def historical_combinations_patched_v1(
    page: int = 1,
    limit: int = 48,
    q: str = None,
    min_movies: int = 4,
    youtube_only: bool = False,
):
    page = max(1, int(page or 1))
    limit = max(1, min(int(limit or 48), 100))
    offset = (page - 1) * limit

    where = ["COALESCE(indexable, 0) = 1", "COALESCE(movie_count, 0) >= %s"]
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
def historical_people_patched_v1(
    page: int = 1,
    limit: int = 48,
    q: str = None,
    min_movies: int = 11,
    youtube_only: bool = False,
):
    page = max(1, int(page or 1))
    limit = max(1, min(int(limit or 48), 100))
    offset = (page - 1) * limit

    where = ["COALESCE(indexable, 0) = 1", "COALESCE(movie_count, 0) >= %s"]
    params = [max(1, int(min_movies or 1))]

    if q:
        text = f"%{str(q).strip().lower()}%"
        where.append("(LOWER(person_name) LIKE %s OR LOWER(seo_title) LIKE %s)")
        params.extend([text, text])

    if youtube_only:
        where.append("COALESCE(youtube_movie_count, 0) > 0")

    where_sql = "WHERE " + " AND ".join(where)
    rows = _fhp_rows(
        f"""
        SELECT *
        FROM historical_people_seo_preprod_v1
        {where_sql}
        ORDER BY COALESCE(youtube_movie_count, 0) DESC, COALESCE(movie_count, 0) DESC, person_name ASC
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
        "items": [_fhp_person_payload(row) for row in rows],
    }


@router.get("/api/v3/historical/person/{person_slug}")
def historical_person_detail_patched_v1(person_slug: str, page: int = 1, limit: int = 96):
    slug = str(person_slug or "").strip().strip("/")
    person_rows = _fhp_rows(
        """
        SELECT *
        FROM historical_people_seo_preprod_v1
        WHERE person_slug=%s
           OR seo_url=%s
           OR seo_url=%s
        LIMIT 1
        """,
        [slug, f"/historical/person/{slug}", f"historical/person/{slug}"],
    )

    if not person_rows:
        raise HTTPException(status_code=404, detail="Historical person not found")

    person = _fhp_person_payload(person_rows[0])
    page = max(1, int(page or 1))
    limit = max(1, min(int(limit or 96), 200))
    offset = (page - 1) * limit

    movie_rows = _fhp_rows(
        """
        SELECT h.*,
               mp.movie_id,
               mp.movie_slug,
               mp.title AS person_movie_title,
               mp.release_year AS person_release_year,
               mp.language_slug AS person_language_slug,
               mp.role_type,
               mp.has_youtube AS person_has_youtube
        FROM historical_movie_people_seo_preprod_v1 mp
        LEFT JOIN historical_card_serving_v1 h ON h.slug = mp.movie_slug
        WHERE mp.person_slug=%s
        ORDER BY COALESCE(mp.has_youtube, 0) DESC, mp.release_year DESC NULLS LAST, mp.title ASC
        LIMIT %s OFFSET %s
        """,
        [person["person_slug"], limit, offset],
    )

    items = [_fhp_person_movie_card(row) for row in movie_rows if _fhp_pick(row, "slug", "movie_slug")]
    total = _fhp_int(person.get("movie_count"), offset + len(items))

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

    serving_table = "historical_card_serving_v1" if _fhp_table_exists("historical_card_serving_v1") else None
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

    table = "historical_card_serving_v1"

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
    availability_text = str(availability or has_ott or "").strip().lower()

    youtube_only = provider_text == "youtube" or availability_text in ("youtube", "free", "true", "ott", "1")

    join_sql = ""
    if youtube_only and _fhp_table_exists("historical_youtube_verified_links_v1"):
        join_sql = (
            " JOIN ("
            "   SELECT DISTINCT slug FROM historical_youtube_verified_links_v1 "
            "   WHERE COALESCE(active, TRUE)=TRUE"
            " ) ytv ON ytv.slug = h.slug "
        )
    elif youtube_only:
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

    rows = _fhp_rows(
        f'SELECT h.* FROM "{table}" h {join_sql} {where_sql} {order_sql} LIMIT %s OFFSET %s',
        params + [limit, offset],
    )

    items = [_fhp_list_card(row) for row in rows if not _fhp_bad_person_row(row)]

    if provider_text == "youtube" or availability_text in ("youtube", "free", "true", "ott", "1"):
        items = [item for item in items if item.get("youtube_count", 0) > 0 or item.get("has_ott") is True]
    total = offset + len(items) + (1 if len(rows) == limit else 0)
    pages = page + (1 if len(rows) == limit else 0)

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
    domain: Optional[str] = Query(None),
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
    search_persons = requested_type in {"people", "all"}
    scope = "indian" if requested and requested <= {"modern", "indian"} else "global"

    total = 0
    items = []

    if search_movies and ("modern" in requested or "indian" in requested):
        modern_total, modern_items = search_modern(
            query=query,
            limit=fetch_limit,
            language=language,
            year=year,
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
        )
        total += webseries_total
        items.extend(webseries_items)

    if search_persons:
        people_total, people_items = search_people(
            query=query,
            limit=fetch_limit,
            scope=scope,
        )
        total += people_total
        items.extend(people_items)

    query_lower = query.lower()

    def score(item):
        title = str(item.get("title") or "").lower()
        release_year = item.get("release_year") or 0

        exact = 0

        if query_lower:
            if title == query_lower:
                exact = 100000
            elif title.startswith(query_lower):
                exact = 50000
            elif query_lower in title:
                exact = 25000

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

        return exact + domain_boost + year_score

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

