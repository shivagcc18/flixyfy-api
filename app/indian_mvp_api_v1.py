import sqlite3
from fastapi import APIRouter, Query

from data_factory.config.settings import DB_PATH

router = APIRouter(prefix="/api/v2", tags=["Indian Movies MVP"])

TMDB_IMAGE_BASE = "https://image.tmdb.org/t/p/w500"

LANGUAGE_NAMES = {
    "hi": "Hindi",
    "te": "Telugu",
    "ta": "Tamil",
    "ml": "Malayalam",
    "kn": "Kannada",
    "bn": "Bengali",
    "mr": "Marathi",
    "pa": "Punjabi",
    "gu": "Gujarati",
    "or": "Odia",
    "as": "Assamese",
    "ur": "Urdu",
}


def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def fix_image_url(url):
    if not url:
        return None
    if url.startswith("http"):
        return url
    if url.startswith("/"):
        return TMDB_IMAGE_BASE + url
    return url


def normalize_movie(row):
    item = dict(row)
    item["poster_url"] = fix_image_url(item.get("poster_url"))
    item["backdrop_url"] = fix_image_url(item.get("backdrop_url"))
    item["language_name"] = LANGUAGE_NAMES.get(
        item.get("original_language"),
        item.get("original_language"),
    )
    return item


def to_dicts(rows):
    return [normalize_movie(row) for row in rows]


@router.get("/search")
def search_indian_movies(
    q: str = Query("", min_length=0),
    language: str = "",
    year: int | None = None,
    has_ott: int | None = None,
    limit: int = 30,
):
    conn = connect()
    cur = conn.cursor()

    sql = """
        SELECT
            tmdb_id,
            title,
            original_title,
            release_year,
            original_language,
            overview,
            vote_average,
            vote_count,
            popularity,
            poster_url,
            backdrop_url,
            runtime,
            clean_rank,
            has_ott,
            provider_count,
            free_providers,
            subscription_providers,
            rent_providers,
            buy_providers
        FROM media_serving_v4
        WHERE 1=1
    """

    params = []
    q_clean = q.strip().lower()

    if q_clean:
        sql += """
            AND (
                LOWER(title) LIKE ?
                OR LOWER(original_title) LIKE ?
            )
        """
        params.extend([f"%{q_clean}%", f"%{q_clean}%"])

    if language:
        sql += " AND original_language = ?"
        params.append(language)

    if year:
        sql += " AND release_year = ?"
        params.append(year)

    if has_ott is not None:
        sql += " AND has_ott = ?"
        params.append(has_ott)

    sql += """
        ORDER BY
            CASE
                WHEN LOWER(title) = ? THEN 0
                WHEN LOWER(title) LIKE ? THEN 1
                ELSE 2
            END,
            has_ott DESC,
            clean_rank ASC,
            popularity DESC
        LIMIT ?
    """

    params.extend([q_clean, f"{q_clean}%", limit])

    rows = cur.execute(sql, params).fetchall()
    conn.close()

    return {
        "query": q,
        "count": len(rows),
        "table": "media_serving_v4",
        "results": to_dicts(rows),
    }


@router.get("/movie/{tmdb_id}")
def indian_movie_detail(tmdb_id: int):
    conn = connect()
    cur = conn.cursor()

    row = cur.execute("""
        SELECT *
        FROM media_serving_v4
        WHERE tmdb_id = ?
        LIMIT 1
    """, (tmdb_id,)).fetchone()

    if not row:
        conn.close()
        return {"status": "not_found"}

    providers = cur.execute("""
        SELECT
            provider_name,
            provider_logo,
            provider_type,
            region,
            deep_link,
            priority,
            source
        FROM ott_availability_indian_v1
        WHERE tmdb_id = ?
        ORDER BY priority ASC, provider_name ASC
    """, (tmdb_id,)).fetchall()

    conn.close()

    item = normalize_movie(row)
    item["providers"] = [dict(p) for p in providers]
    return item


@router.get("/home")
def indian_home(limit: int = 30):
    conn = connect()
    cur = conn.cursor()

    sections = {}

    config = [
        ("Trending Indian Movies", None),
        ("Free / Watch Available", "ott"),
        ("Hindi Movies", "hi"),
        ("Telugu Movies", "te"),
        ("Tamil Movies", "ta"),
        ("Malayalam Movies", "ml"),
        ("Kannada Movies", "kn"),
    ]

    for section_name, lang in config:
        if lang == "ott":
            rows = cur.execute("""
                SELECT *
                FROM media_serving_v4
                WHERE has_ott = 1
                ORDER BY clean_rank ASC, popularity DESC
                LIMIT ?
            """, (limit,)).fetchall()
        elif lang:
            rows = cur.execute("""
                SELECT *
                FROM media_serving_v4
                WHERE original_language = ?
                ORDER BY clean_rank ASC, popularity DESC
                LIMIT ?
            """, (lang, limit)).fetchall()
        else:
            rows = cur.execute("""
                SELECT *
                FROM media_serving_v4
                ORDER BY clean_rank ASC, popularity DESC
                LIMIT ?
            """, (limit,)).fetchall()

        sections[section_name] = to_dicts(rows)

    conn.close()
    return sections


@router.get("/stats")
def indian_mvp_stats():
    conn = connect()
    cur = conn.cursor()

    total = cur.execute("SELECT COUNT(*) FROM media_serving_v4").fetchone()[0]
    with_ott = cur.execute("SELECT COUNT(*) FROM media_serving_v4 WHERE has_ott = 1").fetchone()[0]
    missing_overview = cur.execute("""
        SELECT COUNT(*)
        FROM media_serving_v4
        WHERE overview IS NULL OR TRIM(overview) = ''
    """).fetchone()[0]
    missing_poster = cur.execute("""
        SELECT COUNT(*)
        FROM media_serving_v4
        WHERE poster_url IS NULL OR TRIM(poster_url) = ''
    """).fetchone()[0]

    by_language = cur.execute("""
        SELECT original_language, COUNT(*) AS count
        FROM media_serving_v4
        GROUP BY original_language
        ORDER BY count DESC
    """).fetchall()

    conn.close()

    return {
        "table": "media_serving_v4",
        "total": total,
        "with_ott": with_ott,
        "without_ott": total - with_ott,
        "missing_overview": missing_overview,
        "missing_poster": missing_poster,
        "overview_coverage": round(((total - missing_overview) / total) * 100, 2),
        "poster_coverage": round(((total - missing_poster) / total) * 100, 2),
        "by_language": [
            {
                "language": row["original_language"],
                "language_name": LANGUAGE_NAMES.get(row["original_language"], row["original_language"]),
                "count": row["count"],
            }
            for row in by_language
        ],
    }