import argparse
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


DEFAULT_DB = Path(r"C:\Users\USER\Desktop\ott_project\data_factory\db\flixyfy.db")
REPORT_DIR = Path(r"C:\Users\USER\Desktop\flixyfy-deploy\flixyfy-api\reports")

V7_TABLE = "media_serving_v7_final"
V8_TABLE = "media_serving_v8_expanded"
REDIRECT_TABLE = "media_legacy_slug_redirect_v1"


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def now_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def qident(value: str) -> str:
    return '"' + str(value).replace('"', '""') + '"'


def scalar(cur, sql: str, params=None):
    row = cur.execute(sql, params or []).fetchone()
    return row[0] if row else None


def table_exists(cur, table: str) -> bool:
    return (
        cur.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
            [table],
        ).fetchone()
        is not None
    )


def index_exists(cur, index_name: str) -> bool:
    return (
        cur.execute(
            "SELECT 1 FROM sqlite_master WHERE type='index' AND name=? LIMIT 1",
            [index_name],
        ).fetchone()
        is not None
    )


def table_count(cur, table: str) -> int:
    if not table_exists(cur, table):
        return 0
    return scalar(cur, f"SELECT COUNT(*) FROM {qident(table)}")


def db_size(path: Path) -> dict:
    size = path.stat().st_size if path.exists() else 0
    return {"bytes": size, "pretty": f"{size / 1024 / 1024:.1f} MB"}


def create_redirects(cur) -> dict:
    cur.execute(f"DROP TABLE IF EXISTS {qident(REDIRECT_TABLE)}")
    cur.execute(
        f"""
        CREATE TABLE {qident(REDIRECT_TABLE)} AS
        SELECT
            v7.slug AS old_slug,
            v8.slug AS new_slug,
            v7.tmdb_id AS tmdb_id,
            v8.title AS title,
            v8.release_year AS release_year,
            v8.language_slug AS language_slug,
            CASE WHEN v7.slug = v8.slug THEN 1 ELSE 0 END AS same_slug,
            ? AS created_at
        FROM {qident(V7_TABLE)} v7
        JOIN {qident(V8_TABLE)} v8
          ON v8.tmdb_id = v7.tmdb_id
        WHERE v7.slug IS NOT NULL
          AND v8.slug IS NOT NULL
        """,
        [now_iso()],
    )

    duplicate_old_slugs = scalar(
        cur,
        f"""
        SELECT COUNT(*)
        FROM (
            SELECT old_slug
            FROM {qident(REDIRECT_TABLE)}
            GROUP BY old_slug
            HAVING COUNT(*) > 1
        )
        """,
    )
    if duplicate_old_slugs:
        raise RuntimeError(f"{duplicate_old_slugs} duplicate old_slug values found; refusing redirect table")

    cur.execute(f"CREATE UNIQUE INDEX idx_{REDIRECT_TABLE}_old_slug ON {qident(REDIRECT_TABLE)}(old_slug)")
    cur.execute(f"CREATE INDEX idx_{REDIRECT_TABLE}_new_slug ON {qident(REDIRECT_TABLE)}(new_slug)")

    sample = [
        dict(row)
        for row in cur.execute(
            f"""
            SELECT old_slug, new_slug, tmdb_id, title, release_year, language_slug
            FROM {qident(REDIRECT_TABLE)}
            WHERE same_slug = 0
            ORDER BY release_year DESC, title ASC
            LIMIT 25
            """
        ).fetchall()
    ]
    return {
        "redirect_rows": table_count(cur, REDIRECT_TABLE),
        "changed_slug_rows": scalar(cur, f"SELECT COUNT(*) FROM {qident(REDIRECT_TABLE)} WHERE same_slug = 0"),
        "same_slug_rows": scalar(cur, f"SELECT COUNT(*) FROM {qident(REDIRECT_TABLE)} WHERE same_slug = 1"),
        "changed_slug_sample": sample,
    }


def verify_redirects(cur) -> dict:
    return {
        "v7_rows": table_count(cur, V7_TABLE),
        "v8_rows": table_count(cur, V8_TABLE),
        "v7_tmdb_missing_in_v8": scalar(
            cur,
            f"""
            SELECT COUNT(*)
            FROM {qident(V7_TABLE)} v7
            WHERE v7.tmdb_id IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1 FROM {qident(V8_TABLE)} v8 WHERE v8.tmdb_id = v7.tmdb_id
              )
            """,
        )
        if table_exists(cur, V7_TABLE)
        else 0,
        "v7_slug_missing_redirect": scalar(
            cur,
            f"""
            SELECT COUNT(*)
            FROM {qident(V7_TABLE)} v7
            WHERE v7.slug IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1 FROM {qident(REDIRECT_TABLE)} r WHERE r.old_slug = v7.slug
              )
            """,
        )
        if table_exists(cur, V7_TABLE) and table_exists(cur, REDIRECT_TABLE)
        else 0,
        "broken_redirect_targets": scalar(
            cur,
            f"""
            SELECT COUNT(*)
            FROM {qident(REDIRECT_TABLE)} r
            WHERE NOT EXISTS (
                SELECT 1 FROM {qident(V8_TABLE)} v8 WHERE v8.slug = r.new_slug
            )
            """,
        )
        if table_exists(cur, REDIRECT_TABLE)
        else 0,
    }


def write_report(report: dict) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORT_DIR / f"drop_duplicate_modern_v7_sqlite_{now_stamp()}.json"
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    return path


def print_report(report: dict) -> None:
    print(json.dumps(report, indent=2, ensure_ascii=True, default=str))


def main() -> int:
    parser = argparse.ArgumentParser(description="Create local v7 legacy redirects, verify, then optionally drop duplicate v7.")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--no-vacuum", action="store_true")
    args = parser.parse_args()

    db_path = args.db.resolve()
    report = {
        "created_at": now_iso(),
        "mode": "apply" if args.apply else "dry_run",
        "db_path": str(db_path),
        "vacuum": bool(args.apply and not args.no_vacuum),
        "before_database_file": db_size(db_path),
    }

    if not db_path.exists():
        report["status"] = "FAILED"
        report["error"] = f"DB not found: {db_path}"
        path = write_report(report)
        print_report(report)
        print(f"REPORT: {path}")
        return 1

    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        cur = con.cursor()
        cur.execute("BEGIN")
        report["before_v7_rows"] = table_count(cur, V7_TABLE)
        report["before_v8_rows"] = table_count(cur, V8_TABLE)
        report["before_redirect_rows"] = table_count(cur, REDIRECT_TABLE)

        if not table_exists(cur, V7_TABLE):
            report["status"] = "SKIPPED_V7_ALREADY_MISSING"
        elif not table_exists(cur, V8_TABLE):
            raise RuntimeError(f"{V8_TABLE} missing; refusing to drop {V7_TABLE}")
        else:
            report["redirect"] = create_redirects(cur)
            report["verification_before_drop"] = verify_redirects(cur)
            safe_to_drop = (
                report["verification_before_drop"]["v7_tmdb_missing_in_v8"] == 0
                and report["verification_before_drop"]["v7_slug_missing_redirect"] == 0
                and report["verification_before_drop"]["broken_redirect_targets"] == 0
            )
            report["safe_to_drop"] = safe_to_drop
            if not safe_to_drop:
                raise RuntimeError("Redirect verification failed; refusing to drop v7")

            if args.apply:
                cur.execute(f"DROP TABLE {qident(V7_TABLE)}")
                report["dropped_table"] = V7_TABLE
            else:
                report["dropped_table"] = None

        if args.apply:
            con.commit()
        else:
            con.rollback()
            report["status"] = "DRY_RUN_ROLLED_BACK"

        if args.apply and not args.no_vacuum:
            con.execute("VACUUM")
            con.commit()

        if args.apply:
            report["status"] = "APPLIED"
            report["after_database_file"] = db_size(db_path)
            report["after_v7_rows"] = table_count(cur, V7_TABLE)
            report["after_redirect_rows"] = table_count(cur, REDIRECT_TABLE)
            report["redirect_indexes"] = {
                "old_slug": index_exists(cur, f"idx_{REDIRECT_TABLE}_old_slug"),
                "new_slug": index_exists(cur, f"idx_{REDIRECT_TABLE}_new_slug"),
            }

        path = write_report(report)
        print_report(report)
        print(f"REPORT: {path}")
        return 0
    except Exception as exc:
        con.rollback()
        report["status"] = "FAILED"
        report["error"] = str(exc)
        path = write_report(report)
        print_report(report)
        print(f"REPORT: {path}")
        return 1
    finally:
        con.close()


if __name__ == "__main__":
    raise SystemExit(main())
