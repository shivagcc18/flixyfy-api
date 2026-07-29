from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from app.config import CRITICAL_SERVING_OBJECTS, QUERY_TIMEOUT_MS, SERVING_DB_PATH


class SchemaError(RuntimeError):
    pass


class QueryTimeoutError(RuntimeError):
    pass


class DatabaseUnavailableError(RuntimeError):
    pass


SchemaValidationError = SchemaError


def public_database_ref(path: Path = SERVING_DB_PATH) -> dict[str, str | bool]:
    return {"configured": True, "authority": "external_serving_v3", "path_exposed": False}


def _install_v3_compatibility_views(con: sqlite3.Connection) -> None:
    is_v3 = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='movie_identity_serving_v3'"
    ).fetchone()
    if not is_v3:
        return
    con.executescript(
        """
        CREATE TEMP VIEW movie_serving AS
        SELECT canonical_movie_id,tmdb_id,imdb_id,wikidata_id,wikipedia_url,
               title,original_title,release_year,domain,identity_source,
               identity_confidence,legacy_only,original_language,
               original_language AS language_name,runtime,overview,
               poster AS poster_path,backdrop AS backdrop_path,rating AS tmdb_rating,
               NULL AS tmdb_votes,NULL AS imdb_rating,NULL AS imdb_votes,NULL AS metascore,
               0 AS popularity_score,ott_provider_count AS provider_count,
               youtube_video_count,availability_count
        FROM movie_identity_serving_v3;
        CREATE TEMP VIEW provider_serving AS
        SELECT i.canonical_movie_id,i.tmdb_id,p.source_tmdb_id,p.provider_key,p.provider_name,
               p.raw_provider_names,p.country,p.availability_type,p.provider_category,
               p.confidence_score,p.source,p.source_updated_at,p.home_url,p.search_template
        FROM provider_ott_availability_v3 p
        JOIN movie_identity_serving_v3 i USING(canonical_movie_id);
        CREATE TEMP VIEW movie_genre_serving AS
        SELECT i.canonical_movie_id,i.tmdb_id,g.genre_id,g.genre_name,g.normalized_name
        FROM movie_genre_serving_v3 g JOIN movie_identity_serving_v3 i USING(canonical_movie_id);
        CREATE TEMP VIEW movie_language_serving AS
        SELECT i.canonical_movie_id,i.tmdb_id,l.language_code AS iso_639_1,l.language_name,l.normalized_name
        FROM movie_language_serving_v3 l JOIN movie_identity_serving_v3 i USING(canonical_movie_id);
        CREATE TEMP VIEW movie_people_serving AS
        SELECT i.canonical_movie_id,i.tmdb_id,p.person_id,p.name,p.role,p.character_name
        FROM movie_people_serving_v3 p JOIN movie_identity_serving_v3 i USING(canonical_movie_id);
        CREATE TEMP VIEW search_entity AS SELECT * FROM search_entity_v3;
        CREATE TEMP VIEW provider_alias AS
        SELECT entity_key AS provider_key,entity_name AS display_name,
               normalized_name AS normalized_raw_name
        FROM search_entity_v3 WHERE entity_type='provider';
        """
    )


@contextmanager
def read_connection() -> Iterator[sqlite3.Connection]:
    if not SERVING_DB_PATH.exists():
        raise DatabaseUnavailableError("Serving database not found")
    try:
        con = sqlite3.connect(
            f"file:{SERVING_DB_PATH.as_posix()}?mode=ro",
            uri=True,
            timeout=max(1, QUERY_TIMEOUT_MS / 1000),
            check_same_thread=False,
        )
        con.row_factory = sqlite3.Row
        _install_v3_compatibility_views(con)
        con.execute("PRAGMA query_only=ON")
        con.execute(f"PRAGMA busy_timeout={QUERY_TIMEOUT_MS}")
    except sqlite3.Error as exc:
        raise DatabaseUnavailableError("Read-only database connection failed") from exc
    try:
        yield con
    finally:
        con.close()


def object_exists(con: sqlite3.Connection, name: str) -> bool:
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE name=? AND type IN ('table','view')",
        (name,),
    ).fetchone() is not None


def serving_counts(con: sqlite3.Connection) -> dict[str, int]:
    return {
        "movies": int(con.execute("SELECT COUNT(*) FROM movie_identity_serving_v3").fetchone()[0]),
        "tmdb_routable": int(con.execute("SELECT COUNT(*) FROM movie_identity_serving_v3 WHERE tmdb_id IS NOT NULL").fetchone()[0]),
        "hist_only_routable": int(con.execute("SELECT COUNT(*) FROM movie_identity_serving_v3 WHERE tmdb_id IS NULL").fetchone()[0]),
        "current": int(con.execute("SELECT COUNT(*) FROM movie_identity_serving_v3 WHERE domain='current'").fetchone()[0]),
        "historical": int(con.execute("SELECT COUNT(*) FROM movie_identity_serving_v3 WHERE domain='historical'").fetchone()[0]),
        "search_documents": int(con.execute("SELECT COUNT(*) FROM movie_search_document_v3").fetchone()[0]),
        "ott_availability_rows": int(con.execute("SELECT COUNT(*) FROM provider_ott_availability_v3").fetchone()[0]),
        "youtube_active_rows": int(con.execute("SELECT COUNT(*) FROM movie_youtube_availability_v3 WHERE serving_status='ACTIVE'").fetchone()[0]),
        "providers": int(con.execute("SELECT COUNT(DISTINCT provider_key) FROM provider_ott_availability_v3").fetchone()[0]),
    }


def validate_serving_schema(con: sqlite3.Connection) -> dict[str, Any]:
    missing = [name for name in CRITICAL_SERVING_OBJECTS if not object_exists(con, name)]
    if missing:
        raise SchemaError("Missing critical serving objects: " + ", ".join(missing))
    required_identity = {"canonical_movie_id", "tmdb_id", "title", "domain", "ott_provider_count", "youtube_video_count"}
    identity_cols = {row[1] for row in con.execute("PRAGMA table_info(movie_identity_serving_v3)")}
    missing_identity = sorted(required_identity - identity_cols)
    if missing_identity:
        raise SchemaError("movie_identity_serving_v3 missing columns: " + ", ".join(missing_identity))
    counts = serving_counts(con)
    zero_keys = [key for key in ("movies", "current", "historical", "search_documents", "ott_availability_rows", "youtube_active_rows") if counts[key] <= 0]
    if zero_keys:
        raise SchemaError("Nonzero serving counts required for: " + ", ".join(zero_keys))
    if counts["movies"] != counts["search_documents"]:
        raise SchemaError("Movie identity and search document counts differ")
    return {"critical_objects": list(CRITICAL_SERVING_OBJECTS), "counts": counts}


def health_check() -> dict[str, Any]:
    exists = SERVING_DB_PATH.exists()
    result: dict[str, Any] = {
        "database_exists": exists,
        "read_only_connection": False,
        "schema_checks": False,
        "critical_objects_exist": False,
        "counts_nonzero": False,
        "nonzero_counts": False,
        "counts": {},
    }
    if not exists:
        return result
    with read_connection() as con:
        result["read_only_connection"] = True
        schema = validate_serving_schema(con)
        counts = schema["counts"]
        result.update({
            "schema_checks": True,
            "critical_objects_exist": True,
            "counts_nonzero": all(counts[key] > 0 for key in ("movies", "current", "historical", "search_documents")),
            "nonzero_counts": all(counts[key] > 0 for key in ("movies", "current", "historical", "search_documents")),
            "counts": counts,
        })
    return result


def health_snapshot() -> dict[str, Any]:
    errors: list[dict[str, str]] = []
    try:
        checks = health_check()
        status = "ok" if all(bool(checks.get(key)) for key in (
            "database_exists", "read_only_connection", "schema_checks",
            "critical_objects_exist", "counts_nonzero",
        )) else "degraded"
        counts = checks.get("counts", {}) if isinstance(checks.get("counts"), dict) else {}
    except (SchemaError, QueryTimeoutError, sqlite3.DatabaseError, RuntimeError) as exc:
        checks = {"database_exists": SERVING_DB_PATH.exists(), "read_only_connection": False}
        counts = {}
        status = "degraded"
        errors.append({"type": exc.__class__.__name__, "message": str(exc)})
    return {
        "status": status,
        "service": "flixyfy-api",
        "serving_database": public_database_ref(),
        "checks": checks,
        "counts": counts,
        "provider_data_mode": "USER_APPROVED_FINAL_FRESH_SNAPSHOT",
        "youtube_candidate_database_connected": False,
        "production_write": False,
        "errors": errors,
    }


