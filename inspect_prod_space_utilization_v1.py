import json
import os
from pathlib import Path

import psycopg2
import psycopg2.extras


SAFE_TABLES = [
    "people_canonical_v1",
    "people_alias_v1",
    "people_search_cache_v1",
    "movie_poster_enrichment_cache_v1",
    "youtube_movie_match_validated_v1",
    "youtube_movie_match_cache_v1",
]


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def database_url() -> str:
    here = Path(__file__).resolve().parent
    for path in [here / ".env.local", here / ".env"]:
        load_env_file(path)
    for key in ("DATABASE_URL", "POSTGRES_URL", "NEON_DATABASE_URL", "DATABASE_PUBLIC_URL"):
        value = os.environ.get(key)
        if value:
            return value
    raise RuntimeError("DATABASE_URL not found")


def qident(value: str) -> str:
    return '"' + str(value).replace('"', '""') + '"'


def main() -> int:
    con = psycopg2.connect(database_url(), cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        with con.cursor() as cur:
            cur.execute(
                """
                SELECT
                    current_database() AS db,
                    pg_database_size(current_database()) AS bytes,
                    pg_size_pretty(pg_database_size(current_database())) AS pretty
                """
            )
            database = dict(cur.fetchone())

            cur.execute(
                """
                SELECT
                    c.relname AS table,
                    pg_total_relation_size(c.oid) AS bytes,
                    pg_size_pretty(pg_total_relation_size(c.oid)) AS pretty,
                    COALESCE(s.n_live_tup, 0) AS estimated_rows
                FROM pg_class c
                LEFT JOIN pg_stat_user_tables s ON s.relid = c.oid
                WHERE c.relkind = 'r'
                  AND c.relnamespace = 'public'::regnamespace
                ORDER BY pg_total_relation_size(c.oid) DESC
                LIMIT 25
                """
            )
            top_tables = [dict(row) for row in cur.fetchall()]

            safe_counts = {}
            safe_sizes = {}
            for table in SAFE_TABLES:
                cur.execute(
                    """
                    SELECT 1
                    FROM information_schema.tables
                    WHERE table_schema='public'
                      AND table_name=%s
                    LIMIT 1
                    """,
                    [table],
                )
                if not cur.fetchone():
                    continue
                cur.execute(f"SELECT COUNT(*) AS count FROM public.{qident(table)}")
                safe_counts[table] = cur.fetchone()["count"]
                cur.execute(
                    """
                    SELECT
                        pg_total_relation_size(%s::regclass) AS bytes,
                        pg_size_pretty(pg_total_relation_size(%s::regclass)) AS pretty
                    """,
                    [f"public.{qident(table)}", f"public.{qident(table)}"],
                )
                safe_sizes[table] = dict(cur.fetchone())

        report = {
            "database": database,
            "safe_counts": safe_counts,
            "safe_sizes": safe_sizes,
            "top_tables": top_tables,
            "neon_plan_limit_reference_bytes": 512 * 1024 * 1024,
            "neon_plan_limit_reference_pretty": "512 MB",
            "estimated_limit_used_percent": round((int(database["bytes"]) / (512 * 1024 * 1024)) * 100, 2),
            "estimated_limit_free_bytes": (512 * 1024 * 1024) - int(database["bytes"]),
        }
        print(json.dumps(report, indent=2, default=str))
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    raise SystemExit(main())
