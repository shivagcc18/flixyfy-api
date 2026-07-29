from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query

from app.config import API_VERSION, SERVING_DB_PUBLIC_ID
from app.db import read_connection
from app.services.search import (
    InvalidMovieIdentifier,
    list_movies,
    movie_detail_by_identifier,
    movie_detail_by_canonical,
    search_movies,
    suggestions,
)

router = APIRouter(prefix="/api/v1")
PayloadMode = Literal["full", "compact"]


def _public_meta_value(key: str, value: str) -> str:
    lowered = key.lower()
    if "path" in lowered or "database" in lowered or lowered.endswith("db"):
        return "redacted"
    if "C:\\" in value or "C:/" in value or value.endswith(".db"):
        return "redacted"
    return value


@router.get("/meta")
def meta():
    with read_connection() as con:
        data = {
            row["key"]: _public_meta_value(str(row["key"]), str(row["value"]))
            for row in con.execute("SELECT key,value FROM app_meta")
        }
    data.update({
        "api_version": API_VERSION,
        "serving_database": {"authority": SERVING_DB_PUBLIC_ID, "path_exposed": False},
        "provider_policy": "USER_APPROVED_FINAL_FRESH_SNAPSHOT",
        "youtube_candidate_database_connected": False,
        "canonical_movie_identity": {
            "supported_prefixes": ["TMDB", "HIST"],
            "tmdb_route_compatibility": "/api/v1/movies/{tmdb_id}",
            "canonical_route": "/api/v1/movies/by-canonical/{canonical_movie_id}",
            "search_id_route_compatibility": "/api/v1/movies/{search_id}",
            "supported_search_id_forms": ["tmdb-{tmdb_id}", "hist-{historical_id}"],
        },
    })
    return data


@router.get("/home")
def home(payload: Annotated[PayloadMode, Query(description="full or compact card payload")] = "full"):
    compact = payload == "compact"
    with read_connection() as con:
        return {
            "hero": list_movies(con, limit=6, sort="popular", has_provider=True, compact=compact)["items"],
            "sections": [
                {"key": "trending", "title": "Trending Indian Movies", "items": list_movies(con, limit=12, sort="popular", compact=compact)["items"]},
                {"key": "where_to_watch", "title": "Where to Watch Now", "items": list_movies(con, limit=12, sort="popular", has_provider=True, compact=compact)["items"]},
                {"key": "telugu", "title": "Popular Telugu Movies", "items": list_movies(con, limit=12, sort="popular", language="te", compact=compact)["items"]},
                {"key": "classics", "title": "Indian Classics", "items": list_movies(con, limit=12, sort="rating", domain="historical", compact=compact)["items"]},
                {"key": "recent", "title": "Recent Releases", "items": list_movies(con, limit=12, sort="newest", year_from=2020, compact=compact)["items"]},
            ],
        }


@router.get("/movies")
def movies(
    limit: Annotated[int, Query(ge=1, le=100)] = 24,
    offset: Annotated[int, Query(ge=0)] = 0,
    provider: str | None = None,
    language: str | None = None,
    genre: str | None = None,
    year_from: Annotated[int | None, Query(ge=1900, le=2100)] = None,
    year_to: Annotated[int | None, Query(ge=1900, le=2100)] = None,
    domain: Literal["current", "historical"] | None = None,
    has_provider: bool | None = None,
    sort: Literal["popular", "rating", "newest", "oldest", "title"] = "popular",
    response_mode: PayloadMode = "full",
    payload: PayloadMode | None = None,
):
    mode = payload or response_mode
    with read_connection() as con:
        return list_movies(con, limit=limit, offset=offset, provider=provider, language=language, genre=genre, year_from=year_from, year_to=year_to, domain=domain, has_provider=has_provider, sort=sort, compact=mode == "compact")


@router.get("/movies/canonical/{canonical_movie_id:path}")
@router.get("/movies/by-canonical/{canonical_movie_id:path}")
def movie_by_canonical(canonical_movie_id: str):
    try:
        with read_connection() as con:
            result = movie_detail_by_canonical(con, canonical_movie_id)
    except InvalidMovieIdentifier as exc:
        raise HTTPException(
            status_code=400,
            detail={"code": "INVALID_MOVIE_ID", "message": str(exc)},
        ) from exc
    if result is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "MOVIE_NOT_FOUND", "message": "Movie not found"},
        )
    return result


@router.get("/movies/{movie_identifier}")
def movie(movie_identifier: str):
    try:
        with read_connection() as con:
            result = movie_detail_by_identifier(con, movie_identifier)
    except InvalidMovieIdentifier as exc:
        raise HTTPException(
            status_code=400,
            detail={"code": "INVALID_MOVIE_ID", "message": str(exc)},
        ) from exc
    if result is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "MOVIE_NOT_FOUND", "message": "Movie not found"},
        )
    return result


@router.get("/search")
@router.get("/search/intelligence")
def search(
    q: Annotated[str, Query(min_length=1, max_length=180)],
    limit: Annotated[int, Query(ge=1, le=100)] = 24,
    offset: Annotated[int, Query(ge=0)] = 0,
    provider: str | None = None,
    language: str | None = None,
    genre: str | None = None,
    year_from: Annotated[int | None, Query(ge=1900, le=2100)] = None,
    year_to: Annotated[int | None, Query(ge=1900, le=2100)] = None,
    domain: Literal["current", "historical"] | None = None,
    sort: Literal["relevance", "popular"] = "relevance",
    response_mode: PayloadMode = "full",
    payload: PayloadMode | None = None,
):
    mode = payload or response_mode
    with read_connection() as con:
        return search_movies(con, q, limit=limit, offset=offset, provider=provider, language=language, genre=genre, year_from=year_from, year_to=year_to, domain=domain, sort=sort, compact=mode == "compact")


@router.get("/search/suggest")
def suggest(q: Annotated[str, Query(min_length=1, max_length=100)], limit: Annotated[int, Query(ge=1, le=20)] = 10):
    with read_connection() as con:
        return {"query": q, "items": suggestions(con, q, limit)}


@router.get("/providers")
def providers():
    with read_connection() as con:
        rows = con.execute(
            """SELECT provider_key,MAX(provider_name) provider_name,
                      MAX(provider_category) category,
                      COUNT(DISTINCT canonical_movie_id) movie_count,
                      SUM(CASE WHEN availability_type='flatrate' THEN 1 ELSE 0 END) flatrate_rows,
                      SUM(CASE WHEN availability_type='rent' THEN 1 ELSE 0 END) rent_rows,
                      SUM(CASE WHEN availability_type='buy' THEN 1 ELSE 0 END) buy_rows,
                      MAX(home_url) home_url,MAX(search_template) search_template
               FROM provider_serving GROUP BY provider_key
               ORDER BY movie_count DESC,provider_name"""
        ).fetchall()
    return {"items": [dict(row) for row in rows]}

