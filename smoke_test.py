from __future__ import annotations

import json
import sqlite3

from app.config import AUTHORITATIVE_SERVING_DB, SERVING_DB_PATH
from app.db import health_check, read_connection, serving_counts, validate_serving_schema
from app.services.search import list_movies, movie_detail, movie_detail_by_canonical, search_movies, suggestions

assert SERVING_DB_PATH == AUTHORITATIVE_SERVING_DB
assert SERVING_DB_PATH.exists()

with read_connection() as con:
    schema = validate_serving_schema(con)
    counts = serving_counts(con)
    assert counts["movies"] == counts["search_documents"]
    assert counts["movies"] > 0
    assert counts["current"] > 0
    assert counts["historical"] > 0
    assert counts["tmdb_routable"] > 0
    assert counts["hist_only_routable"] > 0
    assert counts["youtube_active_rows"] > 0
    current = con.execute(
        "SELECT canonical_movie_id,tmdb_id,title FROM movie_serving WHERE tmdb_id IS NOT NULL ORDER BY title LIMIT 1"
    ).fetchone()
    historical = con.execute(
        "SELECT canonical_movie_id,title FROM movie_serving WHERE tmdb_id IS NULL ORDER BY title LIMIT 1"
    ).fetchone()
    assert current is not None
    assert historical is not None
    result = search_movies(con, str(current["title"]), limit=5, compact=True)
    assert result["items"]
    detail = movie_detail(con, int(current["tmdb_id"]))
    assert detail is not None
    assert detail["canonical_movie_id"].startswith("TMDB:")
    hist_detail = movie_detail_by_canonical(con, str(historical["canonical_movie_id"]))
    assert hist_detail is not None
    assert hist_detail["tmdb_id"] is None
    assert hist_detail["canonical_movie_id"].startswith("HIST:")
    assert "availability" in detail
    assert suggestions(con, str(current["title"])[:3], 5)
    assert list_movies(con, limit=5, has_provider=True, compact=True)["items"]
    try:
        con.execute("CREATE TABLE should_not_write(id INTEGER)")
    except sqlite3.DatabaseError:
        read_only_write_blocked = True
    else:
        read_only_write_blocked = False
    assert read_only_write_blocked

health = health_check()
assert health["database_exists"] is True
assert health["read_only_connection"] is True
assert health["schema_checks"] is True
assert health["counts_nonzero"] is True

print(json.dumps({
    "status": "PASS",
    "runtime_db_authority": "external_serving_v3",
    "counts": counts,
    "critical_objects": schema["critical_objects"],
    "sample_search_results": len(result["items"]),
    "historical_canonical_sample": historical["canonical_movie_id"],
    "read_only_write_blocked": read_only_write_blocked,
}, indent=2))
