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
TABLE = os.getenv("SERVING_TABLE", "media_serving_v7_final")

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


def is_bad_ott_link(url):
    if not url:
        return True
    u = str(url).lower()
    return "themoviedb.org" in u or "/watch?locale=" in u


def provider_final_url(provider_key, title, deep_link=None):
    key = normalize_provider_key(provider_key)
    q = quote_plus(title or "")

    if deep_link and not is_bad_ott_link(deep_link):
        return deep_link, "deep_link"

    if key in PROVIDER_SEARCH:
        return PROVIDER_SEARCH[key].format(q=q), "provider_search"

    if key in PROVIDER_HOME:
        return PROVIDER_HOME[key], "provider_homepage"

    return f"https://www.google.com/search?q={q}", "google_search"


def normalize_ott_links(ott_items, title):
    fixed = []

    for item in ott_items or []:
        if not isinstance(item, dict):
            continue

        provider_key = (
            item.get("provider_key")
            or item.get("key")
            or item.get("provider")
            or item.get("provider_name")
        )

        key = normalize_provider_key(provider_key)
        deep_link = item.get("deep_link")

        fallback_search_url = (
            PROVIDER_SEARCH[key].format(q=quote_plus(title or ""))
            if key in PROVIDER_SEARCH
            else None
        )

        homepage_url = PROVIDER_HOME.get(key)

        final_url, final_url_source = provider_final_url(
            provider_key=key,
            title=title,
            deep_link=deep_link,
        )

        item["homepage_url"] = homepage_url
        item["fallback_search_url"] = fallback_search_url
        item["final_url"] = final_url
        item["final_url_source"] = final_url_source

        fixed.append(item)

    return fixed


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
        "popularity_rank": row.get("popularity_rank"),
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


def movie_detail(row):
    data = movie_card(row)
    ott_all = normalize_ott_links(parse_json(row.get("ott_all"), []), row.get("title"))

    data.update({
        "overview": row.get("overview"),
        "runtime": row.get("runtime"),
        "genres": parse_json(row.get("genres"), []),
        "imdb_id": row.get("imdb_id"),
        "imdb_rating": row.get("imdb_rating"),
        "imdb_votes": row.get("imdb_votes"),
        "omdb_runtime": row.get("omdb_runtime"),
        "omdb_genre": row.get("omdb_genre"),
        "director": row.get("director"),
        "writers": row.get("writers"),
        "actors": row.get("actors"),
        "awards": row.get("awards"),
        "certification": row.get("certification"),
        "trailer_url": row.get("trailer_url"),
        "production_companies": row.get("production_companies"),
        "ott_all": ott_all,
        "search_rank": row.get("search_rank"),
        "created_at": str(row.get("created_at")) if row.get("created_at") else None,
        "updated_at": str(row.get("updated_at")) if row.get("updated_at") else None,
    })

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
        where.append(
            "(LOWER(COALESCE(ott_primary, '')) = LOWER(%s) "
            "OR LOWER(COALESCE(ott_all::text, '')) LIKE LOWER(%s))"
        )
        params.append(provider.strip())
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

    return {"status": "ok", "table": TABLE, "movies": total, "ott": ott, "ott_coverage": ott}


@app.get("/api/v3/movies")
def movies(
    page: int = Query(1, ge=1),
    limit: int = Query(24, ge=1, le=100),
    language: Optional[str] = None,
    year: Optional[int] = None,
    has_ott: Optional[int] = None,
    is_free: Optional[int] = None,
    provider: Optional[str] = None,
    sort: str = Query("popular"),
):
    offset = (page - 1) * limit
    where_sql, params = build_filters(language, year, has_ott, is_free, provider)

    allowed_sorts = {
        "popular": "popularity_rank ASC NULLS LAST",
        "latest": "release_year DESC NULLS LAST, popularity_rank ASC NULLS LAST",
        "rating": "rating DESC NULLS LAST, vote_count DESC NULLS LAST",
        "ott": "has_ott DESC NULLS LAST, popularity_rank ASC NULLS LAST",
        "search": "search_rank DESC NULLS LAST",
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
    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute(f"SELECT * FROM {TABLE} WHERE slug = %s LIMIT 1", (slug,))
        row = cur.fetchone()
    finally:
        conn.close()

    if not row:
        raise HTTPException(status_code=404, detail="Movie not found")

    return movie_detail(row)


@app.get("/api/v3/search")
def search(
    q: str = Query(..., min_length=1),
    page: int = Query(1, ge=1),
    limit: int = Query(24, ge=1, le=100),
    language: Optional[str] = None,
    has_ott: Optional[int] = None,
):
    offset = (page - 1) * limit
    query = q.strip()

    where = [
        "(LOWER(title) LIKE LOWER(%s) "
        "OR LOWER(COALESCE(original_title, '')) LIKE LOWER(%s))"
    ]
    params = [f"%{query}%", f"%{query}%"]

    if language:
        where.append("language_slug = %s")
        params.append(language.strip().lower())

    if has_ott is not None:
        where.append("has_ott = %s")
        params.append(1 if has_ott else 0)

    where_sql = "WHERE " + " AND ".join(where)

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
            ORDER BY
                CASE
                    WHEN LOWER(title) = LOWER(%s) THEN 1
                    WHEN LOWER(title) LIKE LOWER(%s) THEN 2
                    WHEN LOWER(COALESCE(original_title, '')) = LOWER(%s) THEN 3
                    WHEN LOWER(COALESCE(original_title, '')) LIKE LOWER(%s) THEN 4
                    ELSE 5
                END,
                has_ott DESC NULLS LAST,
                release_year DESC NULLS LAST,
                search_rank DESC NULLS LAST,
                popularity_rank ASC NULLS LAST
            LIMIT %s OFFSET %s
            """,
            params + [query, f"{query}%", query, f"{query}%", limit, offset],
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
    has_ott: Optional[int] = None,
):
    return movies(
        page=page,
        limit=limit,
        language=language_slug,
        year=year,
        has_ott=has_ott,
        is_free=None,
        provider=None,
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
        cur.execute(f"SELECT * FROM {TABLE} WHERE has_ott = 1 ORDER BY popularity_rank ASC NULLS LAST LIMIT 24")
        trending = cur.fetchall()

        cur.execute(f"SELECT * FROM {TABLE} ORDER BY release_year DESC NULLS LAST, popularity_rank ASC NULLS LAST LIMIT 24")
        latest = cur.fetchall()

        cur.execute(f"SELECT * FROM {TABLE} WHERE is_free = 1 ORDER BY popularity_rank ASC NULLS LAST LIMIT 24")
        free = cur.fetchall()

        cur.execute(f"SELECT * FROM {TABLE} WHERE language_slug = 'hindi' ORDER BY popularity_rank ASC NULLS LAST LIMIT 24")
        hindi = cur.fetchall()

        cur.execute(f"SELECT * FROM {TABLE} WHERE language_slug = 'telugu' ORDER BY popularity_rank ASC NULLS LAST LIMIT 24")
        telugu = cur.fetchall()

        cur.execute(f"SELECT * FROM {TABLE} WHERE language_slug = 'tamil' ORDER BY popularity_rank ASC NULLS LAST LIMIT 24")
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
            {"title": "Trending Now", "items": [movie_card(r) for r in trending]},
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