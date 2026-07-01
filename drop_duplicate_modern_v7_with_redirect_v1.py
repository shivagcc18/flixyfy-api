import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import psycopg2
import psycopg2.extras


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


def table_exists(cur, table: str) -> bool:
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
    return cur.fetchone() is not None


def scalar(cur, sql: str, params=None):
    cur.execute(sql, params or [])
    row = cur.fetchone()
    return next(iter(row.values())) if row else None


def table_size(cur, table: str) -> dict:
    if not table_exists(cur, table):
        return {"exists": False, "bytes": 0, "pretty": "0 bytes", "rows": 0}
    cur.execute(
        f"""
        SELECT
            COUNT(*) AS rows,
            pg_total_relation_size(%s::regclass) AS bytes,
            pg_size_pretty(pg_total_relation_size(%s::regclass)) AS pretty
        FROM public.{qident(table)}
        """,
        [f"public.{qident(table)}", f"public.{qident(table)}"],
    )
    out = dict(cur.fetchone())
    out["exists"] = True
    return out


def create_redirects(cur) -> dict:
    cur.execute(f"DROP TABLE IF EXISTS public.{qident(REDIRECT_TABLE)}")
    cur.execute(
        f"""
        CREATE TABLE public.{qident(REDIRECT_TABLE)} AS
        SELECT
            v7.slug AS old_slug,
            v8.slug AS new_slug,
            v7.tmdb_id,
            v8.title AS title,
            v8.release_year,
            v8.language_slug,
            CASE WHEN v7.slug = v8.slug THEN 1 ELSE 0 END AS same_slug,
            %s AS created_at
        FROM public.{qident(V7_TABLE)} v7
        JOIN public.{qident(V8_TABLE)} v8
          ON v8.tmdb_id = v7.tmdb_id
        WHERE v7.slug IS NOT NULL
          AND v8.slug IS NOT NULL
        """
        ,
        [now_iso()],
    )
    cur.execute(f"CREATE UNIQUE INDEX idx_{REDIRECT_TABLE}_old_slug ON public.{qident(REDIRECT_TABLE)}(old_slug)")
    cur.execute(f"CREATE INDEX idx_{REDIRECT_TABLE}_new_slug ON public.{qident(REDIRECT_TABLE)}(new_slug)")

    counts = {
        "redirect_rows": scalar(cur, f"SELECT COUNT(*) FROM public.{qident(REDIRECT_TABLE)}"),
        "changed_slug_rows": scalar(cur, f"SELECT COUNT(*) FROM public.{qident(REDIRECT_TABLE)} WHERE same_slug = 0"),
        "same_slug_rows": scalar(cur, f"SELECT COUNT(*) FROM public.{qident(REDIRECT_TABLE)} WHERE same_slug = 1"),
    }

    cur.execute(
        f"""
        SELECT old_slug, new_slug, tmdb_id, title, release_year, language_slug
        FROM public.{qident(REDIRECT_TABLE)}
        WHERE same_slug = 0
        ORDER BY release_year DESC NULLS LAST, title ASC
        LIMIT 25
        """
    )
    counts["changed_slug_sample"] = [dict(row) for row in cur.fetchall()]
    return counts


def verify_redirects(cur) -> dict:
    return {
        "v7_rows": scalar(cur, f"SELECT COUNT(*) FROM public.{qident(V7_TABLE)}") if table_exists(cur, V7_TABLE) else 0,
        "v8_rows": scalar(cur, f"SELECT COUNT(*) FROM public.{qident(V8_TABLE)}") if table_exists(cur, V8_TABLE) else 0,
        "v7_tmdb_missing_in_v8": scalar(
            cur,
            f"""
            SELECT COUNT(*)
            FROM public.{qident(V7_TABLE)} v7
            WHERE v7.tmdb_id IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1 FROM public.{qident(V8_TABLE)} v8 WHERE v8.tmdb_id = v7.tmdb_id
              )
            """,
        )
        if table_exists(cur, V7_TABLE)
        else 0,
        "v7_slug_missing_redirect": scalar(
            cur,
            f"""
            SELECT COUNT(*)
            FROM public.{qident(V7_TABLE)} v7
            WHERE v7.slug IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1 FROM public.{qident(REDIRECT_TABLE)} r WHERE r.old_slug = v7.slug
              )
            """,
        )
        if table_exists(cur, V7_TABLE) and table_exists(cur, REDIRECT_TABLE)
        else 0,
        "broken_redirect_targets": scalar(
            cur,
            f"""
            SELECT COUNT(*)
            FROM public.{qident(REDIRECT_TABLE)} r
            WHERE NOT EXISTS (
                SELECT 1 FROM public.{qident(V8_TABLE)} v8 WHERE v8.slug = r.new_slug
            )
            """,
        )
        if table_exists(cur, REDIRECT_TABLE)
        else 0,
    }


def write_report(report: dict) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORT_DIR / f"drop_duplicate_modern_v7_with_redirect_{now_stamp()}.json"
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    return path


def print_report(report: dict) -> None:
    print(json.dumps(report, indent=2, ensure_ascii=True, default=str))


def main() -> int:
    parser = argparse.ArgumentParser(description="Create v7 legacy redirects, verify them, then optionally drop duplicate v7 table.")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    con = psycopg2.connect(database_url(), cursor_factory=psycopg2.extras.RealDictCursor)
    con.autocommit = False
    report = {"created_at": now_iso(), "mode": "apply" if args.apply else "dry_run"}
    try:
        with con.cursor() as cur:
            report["before_database_size"] = {
                "bytes": scalar(cur, "SELECT pg_database_size(current_database())"),
                "pretty": scalar(cur, "SELECT pg_size_pretty(pg_database_size(current_database()))"),
            }
            report["before_v7"] = table_size(cur, V7_TABLE)
            report["before_redirect"] = table_size(cur, REDIRECT_TABLE)

            if not table_exists(cur, V7_TABLE):
                report["status"] = "SKIPPED_V7_ALREADY_MISSING"
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
                    cur.execute(f"DROP TABLE public.{qident(V7_TABLE)}")
                    report["dropped_table"] = V7_TABLE
                else:
                    report["dropped_table"] = None

        if args.apply:
            con.commit()
        else:
            con.rollback()
        report["status"] = "APPLIED" if args.apply else "DRY_RUN_ROLLED_BACK"

        if args.apply:
            with con.cursor() as cur:
                report["after_database_size"] = {
                    "bytes": scalar(cur, "SELECT pg_database_size(current_database())"),
                    "pretty": scalar(cur, "SELECT pg_size_pretty(pg_database_size(current_database()))"),
                }
                report["after_v7"] = table_size(cur, V7_TABLE)
                report["after_redirect"] = table_size(cur, REDIRECT_TABLE)

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
