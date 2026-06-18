import json
import sqlite3
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware

ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "data_factory" / "db" / "watchindia.db"

TABLE = "media_serving_v5"

app = FastAPI(
    title="WATCHINDIA API V3",
    version="3.0.0",
    description="MVP launch API using media_serving_v5",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
@app.get("/api/language/{language_slug}")
def get_language_movies(language_slug: str, limit: int = 120):

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    cur = conn.cursor()

    rows = cur.execute("""
        SELECT
            tmdb_id,
            title,
            slug,
            movie_url,
            release_year,
            primary_language,
            language_slug,
            genres,
            poster_url,
            overview,
            runtime,
            rating,
            vote_count,
            ott_primary,
            ott_count,
            has_ott
        FROM media_serving_v5
        WHERE language_slug = ?
        ORDER BY popularity_rank ASC
        LIMIT ?
    """, (language_slug, limit)).fetchall()

    conn.close()

    return {
        "language": language_slug,
        "count": len(rows),
        "movies": [dict(x) for x in rows]
    }

def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def parse_json(value, fallback):
    if value is None or value == "":
        return fallback
    try:
        return json.loads(value)
    except Exception:
        return fallback


def movie_card(row):
    return {
        "tmdb_id": row["tmdb_id"],
        "title": row["title"],
        "original_title": row["original_title"],
        "slug": row["slug"],
        "movie_url": row["movie_url"],
        "release_year": row["release_year"],
        "year_bucket": row["year_bucket"],
        "primary_language": row["primary_language"],
        "language_slug": row["language_slug"],
        "poster_url": row["poster_url"],
        "backdrop_url": row["backdrop_url"],
        "rating": row["rating"],
        "vote_count": row["vote_count"],
        "popularity": row["popularity"],
        "quality_score": row["quality_score"],
        "popularity_rank": row["popularity_rank"],
        "ott_primary": row["ott_primary"],
        "ott_primary_key": row["ott_primary_key"],
        "ott_count": row["ott_count"],
        "has_ott": bool(row["has_ott"]),
        "has_free_ott": bool(row["has_free_ott"]),
        "has_subscription_ott": bool(row["has_subscription_ott"]),
        "has_rent_ott": bool(row["has_rent_ott"]),
        "has_buy_ott": bool(row["has_buy_ott"]),
        "is_free": bool(row["is_free"]),
    }


def movie_detail(row):
    data = movie_card(row)
    data.update({
        "overview": row["overview"],
        "runtime": row["runtime"],
        "genres": parse_json(row["genres"], []),
        "ott_all": parse_json(row["ott_all"], []),
        "search_rank": row["search_rank"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
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
        where.append("language_slug = ?")
        params.append(language.strip().lower())

    if year:
        where.append("release_year = ?")
        params.append(year)

    if has_ott is not None:
        where.append("has_ott = ?")
        params.append(1 if has_ott else 0)

    if is_free is not None:
        where.append("is_free = ?")
        params.append(1 if is_free else 0)

    if provider:
        where.append("(LOWER(ott_primary) = LOWER(?) OR LOWER(ott_all) LIKE LOWER(?))")
        params.append(provider.strip())
        params.append(f"%{provider.strip()}%")

    sql = ""
    if where:
        sql = "WHERE " + " AND ".join(where)

    return sql, params


@app.get("/")
def root():
    return {
        "name": "WATCHINDIA API",
        "version": "3.0.0",
        "source": TABLE,
        "status": "ok",
    }


@app.get("/api/v3/health")
def health():
    conn = get_conn()
    cur = conn.cursor()

    try:
        total = cur.execute(f"SELECT COUNT(*) FROM {TABLE}").fetchone()[0]
        ott = cur.execute(f"SELECT COUNT(*) FROM {TABLE} WHERE has_ott=1").fetchone()[0]
    finally:
        conn.close()

    return {
        "status": "ok",
        "db": str(DB_PATH),
        "table": TABLE,
        "movies": total,
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
    sort: str = Query("popular"),
):
    offset = (page - 1) * limit

    where_sql, params = build_filters(language, year, has_ott, is_free, provider)

    allowed_sorts = {
        "popular": "popularity_rank ASC",
        "latest": "release_year DESC, popularity_rank ASC",
        "rating": "rating DESC, vote_count DESC",
        "ott": "has_ott DESC, popularity_rank ASC",
        "search": "search_rank DESC",
        "title": "title ASC",
    }

    order_sql = allowed_sorts.get(sort, allowed_sorts["popular"])

    conn = get_conn()
    cur = conn.cursor()

    try:
        total = cur.execute(
            f"SELECT COUNT(*) FROM {TABLE} {where_sql}",
            params,
        ).fetchone()[0]

        rows = cur.execute(
            f"""
            SELECT *
            FROM {TABLE}
            {where_sql}
            ORDER BY {order_sql}
            LIMIT ? OFFSET ?
            """,
            params + [limit, offset],
        ).fetchall()
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
        row = cur.execute(
            f"SELECT * FROM {TABLE} WHERE slug = ? LIMIT 1",
            (slug,),
        ).fetchone()
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
        "(LOWER(title) LIKE LOWER(?) OR LOWER(original_title) LIKE LOWER(?))"
    ]
    params = [f"%{query}%", f"%{query}%"]

    if language:
        where.append("language_slug = ?")
        params.append(language.strip().lower())

    if has_ott is not None:
        where.append("has_ott = ?")
        params.append(1 if has_ott else 0)

    where_sql = "WHERE " + " AND ".join(where)

    conn = get_conn()
    cur = conn.cursor()

    try:
        total = cur.execute(
            f"SELECT COUNT(*) FROM {TABLE} {where_sql}",
            params,
        ).fetchone()[0]

        rows = cur.execute(
            f"""
            SELECT *
            FROM {TABLE}
            {where_sql}
            ORDER BY
                    CASE
                    WHEN LOWER(title) = LOWER(?) THEN 1
                    WHEN LOWER(title) LIKE LOWER(?) THEN 2
                    WHEN LOWER(original_title) = LOWER(?) THEN 3
                    WHEN LOWER(original_title) LIKE LOWER(?) THEN 4
        ELSE 5
    END,
    has_ott DESC,
    release_year DESC,
    search_rank DESC,
    popularity_rank ASC
            LIMIT ? OFFSET ?
            """,
            params + [query, f"{query}%", query, f"{query}%", limit, offset],
        ).fetchall()
    finally:
        conn.close()

    return {
        "q": query,
        "page": page,
        "limit": limit,
        "total": total,
        "pages": (total + limit - 1) // limit,
        "items": [movie_card(r) for r in rows],
    }


@app.get("/api/v3/languages")
def languages():
    conn = get_conn()
    cur = conn.cursor()

    try:
        rows = cur.execute(
            f"""
            SELECT
                language_slug,
                primary_language,
                COUNT(*) AS movie_count,
                SUM(has_ott) AS ott_count
            FROM {TABLE}
            GROUP BY language_slug, primary_language
            ORDER BY movie_count DESC
            """
        ).fetchall()
    finally:
        conn.close()

    return {
        "items": [
            {
                "language_slug": r["language_slug"],
                "primary_language": r["primary_language"],
                "movie_count": r["movie_count"],
                "ott_count": r["ott_count"],
                "url": f"/{r['language_slug']}-movies",
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
        rows = cur.execute(
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
        ).fetchall()
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
        trending = cur.execute(
            f"""
            SELECT *
            FROM {TABLE}
            WHERE has_ott=1
            ORDER BY popularity_rank ASC
            LIMIT 24
            """
        ).fetchall()

        latest = cur.execute(
            f"""
            SELECT *
            FROM {TABLE}
            ORDER BY release_year DESC, popularity_rank ASC
            LIMIT 24
            """
        ).fetchall()

        free = cur.execute(
            f"""
            SELECT *
            FROM {TABLE}
            WHERE is_free=1
            ORDER BY popularity_rank ASC
            LIMIT 24
            """
        ).fetchall()

        hindi = cur.execute(
            f"""
            SELECT *
            FROM {TABLE}
            WHERE language_slug='hindi'
            ORDER BY popularity_rank ASC
            LIMIT 24
            """
        ).fetchall()

        telugu = cur.execute(
            f"""
            SELECT *
            FROM {TABLE}
            WHERE language_slug='telugu'
            ORDER BY popularity_rank ASC
            LIMIT 24
            """
        ).fetchall()

        tamil = cur.execute(
            f"""
            SELECT *
            FROM {TABLE}
            WHERE language_slug='tamil'
            ORDER BY popularity_rank ASC
            LIMIT 24
            """
        ).fetchall()
    finally:
        conn.close()

    return {
        "trending": [movie_card(r) for r in trending],
        "latest": [movie_card(r) for r in latest],
        "free": [movie_card(r) for r in free],
        "hindi": [movie_card(r) for r in hindi],
        "telugu": [movie_card(r) for r in telugu],
        "tamil": [movie_card(r) for r in tamil],
    }


@app.get("/api/v3/stats")
def stats():
    conn = get_conn()
    cur = conn.cursor()

    try:
        total = cur.execute(f"SELECT COUNT(*) FROM {TABLE}").fetchone()[0]
        ott = cur.execute(f"SELECT COUNT(*) FROM {TABLE} WHERE has_ott=1").fetchone()[0]
        free = cur.execute(f"SELECT COUNT(*) FROM {TABLE} WHERE is_free=1").fetchone()[0]
        sub = cur.execute(f"SELECT COUNT(*) FROM {TABLE} WHERE has_subscription_ott=1").fetchone()[0]
        poster = cur.execute(
            f"SELECT COUNT(*) FROM {TABLE} WHERE poster_url IS NOT NULL AND poster_url != ''"
        ).fetchone()[0]
        overview = cur.execute(
            f"SELECT COUNT(*) FROM {TABLE} WHERE overview IS NOT NULL AND overview != ''"
        ).fetchone()[0]
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