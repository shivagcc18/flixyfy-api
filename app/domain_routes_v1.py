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

HOLLYWOOD_TABLE = os.getenv("HOLLYWOOD_SERVING_TABLE", "hollywood_serving_v1")
HOLLYWOOD_CARD_TABLE = os.getenv("HOLLYWOOD_CARD_TABLE", "hollywood_card_serving_v1")
HOLLYWOOD_DETAIL_TABLE = os.getenv("HOLLYWOOD_DETAIL_TABLE", "hollywood_detail_serving_v1")
HOLLYWOOD_SEARCH_TABLE = os.getenv("HOLLYWOOD_SEARCH_TABLE", "hollywood_search_serving_v1")
HOLLYWOOD_AVAILABILITY_TABLE = os.getenv("HOLLYWOOD_AVAILABILITY_TABLE", "hollywood_availability_v2")

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

    score_expr = numeric_expr(score_col) if score_col else "0"

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

        for col in ["title", "original_title", "wiki_title"]:
            if col in cols:
                title_clauses.append(f"LOWER(CAST({qident(col)} AS TEXT)) LIKE LOWER(%s)")
                params.append(f"%{search_query}%")

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

    order = order_sql(table_name, sort)

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
            LIMIT %s OFFSET %s
            """,
            params + [limit, offset],
        )

        rows = cur.fetchall()
    finally:
        conn.close()

    return {
        "domain": domain,
        "source_domain": domain,
        "page": page,
        "limit": limit,
        "total": total,
        "pages": (total + limit - 1) // limit,
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


def availability_rows(domain: str, movie_id=None, slug=None, title=None):
    table_name = HOLLYWOOD_AVAILABILITY_TABLE if domain == "hollywood" else HISTORICAL_AVAILABILITY_TABLE

    if not table_exists(table_name):
        return []

    cols = table_columns(table_name)
    where = []
    params = []

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
    out = []

    for row in rows:
        item = dict(row)

        provider = first(
            item,
            [
                "provider",
                "provider_name",
                "provider_display_name",
                "ott_primary",
                "primary_provider",
                "top_provider",
            ],
        )

        provider_key = first(item, ["provider_key", "ott_primary_key"]) or normalize_provider_key(provider)

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

        item.update(
            {
                "domain": domain,
                "provider_key": provider_key,
                "provider_display_name": provider,
                "button_label": item.get("button_label") or provider or ("Watch on YouTube" if youtube_url else "Watch"),
                "final_url": final_url,
                "youtube_url": youtube_url,
                "youtube_title": youtube_title,
            }
        )

        out.append(item)

    return out


def domain_detail(row: Dict[str, Any], domain: str):
    data = domain_card(row, domain)

    raw_availability = availability_rows(
        domain=domain,
        movie_id=first(row, ["id", "movie_id"]),
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

        if youtube_url and (
            "youtube" in provider_key
            or "youtube" in str(youtube_url).lower()
            or status == "youtube_available"
        ):
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
        "is_free": as_bool(row.get("is_free")),
    }


def search_modern(query: str, limit: int, language: Optional[str], year: Optional[int]):
    if not table_exists(MODERN_TABLE):
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
            f"SELECT COUNT(*) AS total FROM public.{qident(MODERN_TABLE)} {where_clause}",
            params,
        )

        total = cur.fetchone()["total"]

        cur.execute(
            f"""
            SELECT *
            FROM public.{qident(MODERN_TABLE)}
            {where_clause}
            ORDER BY
                has_ott DESC NULLS LAST,
                release_year DESC NULLS LAST,
                COALESCE(rating, 0) DESC NULLS LAST,
                title ASC
            LIMIT %s
            """,
            params + [limit],
        )

        items = [modern_card(dict(r)) for r in cur.fetchall()]
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
    )


@router.get("/api/v3/hollywood/{slug}")
def hollywood_detail(slug: str):
    row = row_by_slug(HOLLYWOOD_DETAIL_TABLE, slug) or row_by_slug(HOLLYWOOD_TABLE, slug)

    if not row:
        raise HTTPException(status_code=404, detail="Hollywood movie not found")

    return domain_detail(row, "hollywood")


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
):
    query = q.strip()
    offset = (page - 1) * limit
    fetch_limit = offset + limit

    if domain:
        requested = {d.strip().lower() for d in domain.split(",") if d.strip()}
    else:
        requested = {"modern", "hollywood", "historical"}

    total = 0
    items = []

    if "modern" in requested or "indian" in requested:
        modern_total, modern_items = search_modern(
            query=query,
            limit=fetch_limit,
            language=language,
            year=year,
        )
        total += modern_total
        items.extend(modern_items)

    if "hollywood" in requested:
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

    if "historical" in requested:
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