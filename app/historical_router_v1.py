from typing import Optional

from fastapi import APIRouter, Query, HTTPException

router = APIRouter(
    prefix="/api/v3/historical",
    tags=["Historical"]
)


def historical_card(row):
    return {
        "title": row["title"],
        "slug": row["slug"],
        "movie_url": row["movie_url"],
        "release_year": row["release_year"],
        "language_slug": row["language_slug"],
        "language_name": row["language_name"],
        "poster_url": row["poster_url"],
        "has_youtube": bool(row["has_youtube"]),
        "has_ott": bool(row["has_ott"]),
        "availability_status": row["availability_status"],
    }


def historical_detail(row):
    return dict(row)


@router.get("")
def historical_movies(
    request,
    page: int = Query(1, ge=1),
    limit: int = Query(24, ge=1, le=100),
    language: Optional[str] = None,
    year: Optional[int] = None,
):

    conn = request.app.state.db
    cur = conn.cursor()

    where = []
    params = []

    if language:
        where.append("language_slug = ?")
        params.append(language)

    if year:
        where.append("release_year = ?")
        params.append(year)

    where_sql = (
        "WHERE " + " AND ".join(where)
        if where else ""
    )

    total = cur.execute(
        f"""
        SELECT COUNT(*)
        FROM historical_card_serving_v1
        {where_sql}
        """,
        params
    ).fetchone()[0]

    offset = (page - 1) * limit

    rows = cur.execute(
        f"""
        SELECT *
        FROM historical_card_serving_v1
        {where_sql}
        ORDER BY release_year DESC, title
        LIMIT ? OFFSET ?
        """,
        params + [limit, offset]
    ).fetchall()

    return {
        "page": page,
        "limit": limit,
        "total": total,
        "pages": (total + limit - 1) // limit,
        "items": [
            historical_card(r)
            for r in rows
        ]
    }


@router.get("/search")
def historical_search(
    request,
    q: str = Query(""),
    page: int = Query(1, ge=1),
    limit: int = Query(24, ge=1, le=100),
):

    conn = request.app.state.db
    cur = conn.cursor()

    query = q.strip()

    if query:

        total = cur.execute(
            """
            SELECT COUNT(*)
            FROM historical_search_serving_v1
            WHERE
                title LIKE ?
                OR search_text LIKE ?
            """,
            (
                f"%{query}%",
                f"%{query}%"
            )
        ).fetchone()[0]

        offset = (page - 1) * limit

        rows = cur.execute(
            """
            SELECT *
            FROM historical_search_serving_v1
            WHERE
                title LIKE ?
                OR search_text LIKE ?
            ORDER BY release_year DESC, title
            LIMIT ?
            OFFSET ?
            """,
            (
                f"%{query}%",
                f"%{query}%",
                limit,
                offset
            )
        ).fetchall()

    else:

        total = cur.execute(
            """
            SELECT COUNT(*)
            FROM historical_search_serving_v1
            """
        ).fetchone()[0]

        offset = (page - 1) * limit

        rows = cur.execute(
            """
            SELECT *
            FROM historical_search_serving_v1
            ORDER BY release_year DESC, title
            LIMIT ?
            OFFSET ?
            """,
            (
                limit,
                offset
            )
        ).fetchall()

    return {
        "query": query,
        "page": page,
        "limit": limit,
        "total": total,
        "pages": (total + limit - 1) // limit,
        "items": [dict(r) for r in rows]
    }


@router.get("/movie/{slug}")
def historical_movie(
    slug: str,
    request,
):

    conn = request.app.state.db
    cur = conn.cursor()

    row = cur.execute(
        """
        SELECT *
        FROM historical_detail_serving_v1
        WHERE slug = ?
        LIMIT 1
        """,
        (slug,)
    ).fetchone()

    if not row:
        raise HTTPException(
            status_code=404,
            detail="Historical movie not found"
        )

    return historical_detail(row)


@router.get("/stats")
def historical_stats(request):

    conn = request.app.state.db
    cur = conn.cursor()

    total = cur.execute(
        """
        SELECT COUNT(*)
        FROM historical_serving_v1
        """
    ).fetchone()[0]

    youtube = cur.execute(
        """
        SELECT COUNT(*)
        FROM historical_serving_v1
        WHERE has_youtube = 1
        """
    ).fetchone()[0]

    ott = cur.execute(
        """
        SELECT COUNT(*)
        FROM historical_serving_v1
        WHERE has_ott = 1
        """
    ).fetchone()[0]

    return {
        "historical_movies": total,
        "youtube_links": youtube,
        "ott_links": ott,
    }