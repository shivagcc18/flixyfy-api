import argparse
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import psycopg2
import psycopg2.extras


FLIX_DB = Path(r"C:\Users\USER\Desktop\ott_project\data_factory\db\flixyfy.db")
REPORT_DIR = Path(r"C:\Users\USER\Desktop\flixyfy-deploy\flixyfy-api\reports")

SAFE_TABLES = [
    "people_canonical_v1",
    "people_alias_v1",
    "people_search_cache_v1",
    "movie_poster_enrichment_cache_v1",
    "youtube_movie_match_validated_v1",
    "youtube_movie_match_cache_v1",
]

INT_COLUMNS = {
    "active_year_min",
    "active_year_max",
    "movie_count",
    "youtube_movie_count",
    "search_rank",
    "indexable",
    "release_year",
    "is_official_poster",
    "confidence_score",
    "priority",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def now_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def qident(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


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


def sqlite_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [row["name"] for row in conn.execute(f"PRAGMA table_info({qident(table)})")]


def sqlite_count(conn: sqlite3.Connection, table: str) -> int:
    return int(conn.execute(f"SELECT COUNT(*) FROM {qident(table)}").fetchone()[0])


def pg_table_exists(cur, table: str) -> bool:
    cur.execute(
        """
        SELECT 1
        FROM information_schema.tables
        WHERE table_schema='public' AND table_name=%s
        LIMIT 1
        """,
        [table],
    )
    return cur.fetchone() is not None


def create_pg_table(cur, table: str, columns: list[str]) -> None:
    defs = []
    for col in columns:
        col_type = "BIGINT" if col in INT_COLUMNS else "TEXT"
        defs.append(f"{qident(col)} {col_type}")
    cur.execute(f"CREATE TABLE IF NOT EXISTS public.{qident(table)} ({', '.join(defs)})")


def pg_columns(cur, table: str) -> list[str]:
    cur.execute(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema='public' AND table_name=%s
        ORDER BY ordinal_position
        """,
        [table],
    )
    return [row["column_name"] for row in cur.fetchall()]


def coerce_value(col: str, value: Any) -> Any:
    if value is None:
        return None
    if col in INT_COLUMNS:
        try:
            return int(value)
        except Exception:
            return None
    return str(value)


def sync_table(sqlite_conn: sqlite3.Connection, pg_cur, table: str, batch_size: int) -> dict[str, Any]:
    local_cols = sqlite_columns(sqlite_conn, table)
    if not local_cols:
        return {"table": table, "local_rows": 0, "synced_rows": 0, "status": "missing_local_columns"}

    local_count = sqlite_count(sqlite_conn, table)
    create_pg_table(pg_cur, table, local_cols)
    remote_cols = pg_columns(pg_cur, table)
    insert_cols = [col for col in local_cols if col in remote_cols]

    pg_cur.execute(f"TRUNCATE TABLE public.{qident(table)}")

    synced = 0
    select_sql = f"SELECT {', '.join(qident(col) for col in insert_cols)} FROM {qident(table)}"
    insert_sql = f"INSERT INTO public.{qident(table)} ({', '.join(qident(col) for col in insert_cols)}) VALUES %s"
    batch = []
    for row in sqlite_conn.execute(select_sql):
        batch.append(tuple(coerce_value(col, row[col]) for col in insert_cols))
        if len(batch) >= batch_size:
            psycopg2.extras.execute_values(pg_cur, insert_sql, batch, page_size=batch_size)
            synced += len(batch)
            batch = []
    if batch:
        psycopg2.extras.execute_values(pg_cur, insert_sql, batch, page_size=batch_size)
        synced += len(batch)

    return {"table": table, "local_rows": local_count, "synced_rows": synced, "columns": insert_cols, "status": "ok"}


def create_indexes(cur) -> None:
    index_specs = {
        "people_canonical_v1": ["person_slug", "domain"],
        "people_alias_v1": ["person_slug", "compact_alias", "normalized_alias"],
        "people_search_cache_v1": ["person_slug", "compact_display_name", "domain", "search_rank"],
        "movie_poster_enrichment_cache_v1": ["movie_slug"],
        "youtube_movie_match_validated_v1": ["person_slug", "movie_slug", "youtube_video_id", "validation_status"],
        "youtube_movie_match_cache_v1": ["movie_slug", "youtube_video_id", "person_slug", "validation_status"],
    }
    for table, cols in index_specs.items():
        if not pg_table_exists(cur, table):
            continue
        remote_cols = set(pg_columns(cur, table))
        for col in cols:
            if col in remote_cols:
                cur.execute(f"CREATE INDEX IF NOT EXISTS idx_{table}_{col} ON public.{qident(table)} ({qident(col)})")


def write_report(report: dict[str, Any]) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORT_DIR / f"sync_local_people_youtube_safe_to_prod_{now_stamp()}.json"
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync only local safe people/YouTube enrichment tables to Neon.")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--batch-size", type=int, default=5000)
    args = parser.parse_args()

    report: dict[str, Any] = {
        "created_at": now_iso(),
        "mode": "apply" if args.apply else "dry_run",
        "local_db": str(FLIX_DB),
        "safe_tables": SAFE_TABLES,
        "excluded_tables": ["youtube_movie_match_candidates_v1", "youtube_movie_match_rejected_v1", "people_movie_map_v1", "wiki_*_source_v1"],
    }

    sqlite_conn = sqlite3.connect(str(FLIX_DB))
    sqlite_conn.row_factory = sqlite3.Row
    pg_conn = psycopg2.connect(database_url(), cursor_factory=psycopg2.extras.RealDictCursor)
    pg_conn.autocommit = False
    try:
        with pg_conn.cursor() as cur:
            table_reports = []
            for table in SAFE_TABLES:
                table_reports.append(sync_table(sqlite_conn, cur, table, args.batch_size))
            create_indexes(cur)
            report["tables"] = table_reports
            report["total_synced_rows"] = sum(row.get("synced_rows", 0) for row in table_reports)

        if args.apply:
            pg_conn.commit()
            report["status"] = "APPLIED"
        else:
            pg_conn.rollback()
            report["status"] = "DRY_RUN_ROLLED_BACK"

        path = write_report(report)
        print(json.dumps(report, indent=2, ensure_ascii=False, default=str))
        print(f"REPORT: {path}")
        return 0
    except Exception as exc:
        pg_conn.rollback()
        report["status"] = "FAILED"
        report["error"] = str(exc)
        path = write_report(report)
        print(json.dumps(report, indent=2, ensure_ascii=False, default=str))
        print(f"REPORT: {path}")
        return 1
    finally:
        sqlite_conn.close()
        pg_conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
