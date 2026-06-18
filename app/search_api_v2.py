import sqlite3
from fastapi import APIRouter, Query

DB_PATH = "data_factory/db/watchindia.db"

router = APIRouter()


# ----------------------------
# NORMALIZE QUERY
# ----------------------------
def normalize(text: str):
    return text.lower().strip()


# ----------------------------
# MAIN SEARCH ENGINE
# ----------------------------
@router.get("/search_v2")
def search_v2(q: str = Query(...)):

    q = normalize(q)

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # STEP 1: BASE SEARCH
    cursor.execute("""
        SELECT id, title, year, genre
        FROM movies_master
        WHERE LOWER(title) LIKE ?
        LIMIT 50
    """, (f"%{q}%",))

    results = cursor.fetchall()

    enriched = []

    for r in results:
        content_id = r[0]

        # TMDB JOIN
        cursor.execute("""
            SELECT rating, overview, poster
            FROM tmdb_enrichment
            WHERE content_id=?
        """, (content_id,))
        tmdb = cursor.fetchone()

        # OTT JOIN
        cursor.execute("""
            SELECT platform, watch_url
            FROM ott_availability
            WHERE content_id=?
        """, (content_id,))
        ott = cursor.fetchall()

        # YOUTUBE JOIN
        cursor.execute("""
            SELECT video_id, type, confidence
            FROM youtube_availability
            WHERE content_id=?
            ORDER BY confidence DESC
            LIMIT 1
        """, (content_id,))
        yt = cursor.fetchone()

        enriched.append({
            "id": content_id,
            "title": r[1],
            "year": r[2],
            "genre": r[3],
            "tmdb": tmdb,
            "ott": ott,
            "youtube": yt
        })

    conn.close()

    return {
        "query": q,
        "count": len(enriched),
        "results": enriched
    }