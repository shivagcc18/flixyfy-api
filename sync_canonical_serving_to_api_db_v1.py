import argparse
import json
import os
import sqlite3
from pathlib import Path

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv


LOCAL_DB = Path(r"C:\Users\USER\Desktop\ott_project\data_factory\db\flixyfy.db")
TABLES = [
    "people_serving_v1",
    "person_slug_redirect_v1",
    "webseries_serving_v1",
    "youtube_link_serving_v1",
]

JSON_COLUMNS = {
    "people_serving_v1": {"aliases_json", "source_tables_json"},
    "webseries_serving_v1": {"availability_json"},
}

INTEGER_COLUMNS = {
    "people_serving_v1": {
        "movie_count",
        "youtube_movie_count",
        "actor_count",
        "director_count",
        "producer_count",
        "music_count",
        "search_rank",
        "indexable",
    },
    "person_slug_redirect_v1": {"active"},
    "webseries_serving_v1": {
        "tmdb_id",
        "first_air_year",
        "latest_air_year",
        "vote_count",
        "has_major_provider",
        "availability_count",
        "availability_row_count",
        "number_of_seasons",
        "number_of_episodes",
    },
    "youtube_link_serving_v1": {
        "release_year",
        "duration_seconds",
        "view_count",
        "confidence_score",
        "source_rank",
        "is_primary",
        "active",
    },
}

FLOAT_COLUMNS = {
    "person_slug_redirect_v1": {"confidence"},
    "webseries_serving_v1": {"popularity_score", "vote_average"},
    "youtube_link_serving_v1": {"match_score", "quality_score"},
}


def load_env():
    load_dotenv()
    load_dotenv(".env.local")


def db_url():
    load_env()
    for key in ("DATABASE_URL", "POSTGRES_URL", "NEON_DATABASE_URL", "DATABASE_PUBLIC_URL"):
        if os.environ.get(key):
            return os.environ[key]
    raise RuntimeError("DATABASE_URL not found")


def qident(value):
    return '"' + str(value).replace('"', '""') + '"'


def sqlite_columns(cur, table):
    return [(row[1], row[2] or "TEXT") for row in cur.execute(f"PRAGMA table_info({qident(table)})")]


def pg_type(table, column, sqlite_type, as_json=False):
    if as_json:
        return "JSONB"
    if column in INTEGER_COLUMNS.get(table, set()):
        return "BIGINT"
    if column in FLOAT_COLUMNS.get(table, set()):
        return "DOUBLE PRECISION"
    return "TEXT"


def create_pg_table(pg_cur, table, columns):
    json_cols = JSON_COLUMNS.get(table, set())
    col_sql = ", ".join(f"{qident(name)} {pg_type(table, name, sqlite_type, name in json_cols)}" for name, sqlite_type in columns)
    pg_cur.execute(f"DROP TABLE IF EXISTS public.{qident(table)}")
    pg_cur.execute(f"CREATE TABLE public.{qident(table)} ({col_sql})")

    if table == "people_serving_v1":
        pg_cur.execute(f"ALTER TABLE public.{qident(table)} ADD PRIMARY KEY (person_slug)")
        pg_cur.execute(f"CREATE INDEX ix_people_serving_v1_compact ON public.{qident(table)} (compact_display_name)")
        pg_cur.execute(f"CREATE INDEX ix_people_serving_v1_rank ON public.{qident(table)} (search_rank DESC)")
    elif table == "person_slug_redirect_v1":
        pg_cur.execute(f"ALTER TABLE public.{qident(table)} ADD PRIMARY KEY (old_slug)")
        pg_cur.execute(f"CREATE INDEX ix_person_slug_redirect_v1_active ON public.{qident(table)} (active)")
    elif table == "webseries_serving_v1":
        pg_cur.execute(f"ALTER TABLE public.{qident(table)} ADD PRIMARY KEY (slug)")
        pg_cur.execute(f"CREATE INDEX ix_webseries_serving_v1_latest ON public.{qident(table)} (latest_air_year DESC)")
        pg_cur.execute(f"CREATE INDEX ix_webseries_serving_v1_region ON public.{qident(table)} (region)")
    elif table == "youtube_link_serving_v1":
        pg_cur.execute(f"ALTER TABLE public.{qident(table)} ADD PRIMARY KEY (content_slug, youtube_video_id)")
        pg_cur.execute(f"CREATE INDEX ix_youtube_link_serving_v1_slug ON public.{qident(table)} (content_slug, active, is_primary)")
        pg_cur.execute(f"CREATE INDEX ix_youtube_link_serving_v1_domain ON public.{qident(table)} (content_domain)")


def cast_value(table, column, value):
    if value is None:
        return None
    if column in JSON_COLUMNS.get(table, set()):
        try:
            return json.dumps(json.loads(value), ensure_ascii=False)
        except Exception:
            return json.dumps(value, ensure_ascii=False)
    if column in INTEGER_COLUMNS.get(table, set()):
        try:
            text = str(value).strip()
            if text == "" or text.upper() == "N/A":
                return None
            return int(float(text))
        except Exception:
            return None
    if column in FLOAT_COLUMNS.get(table, set()):
        try:
            text = str(value).strip()
            if text == "" or text.upper() == "N/A":
                return None
            return float(text)
        except Exception:
            return None
    return value


def sync_table(sqlite_cur, pg_cur, table):
    sqlite_cur.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", [table])
    if sqlite_cur.fetchone() is None:
        raise RuntimeError(f"Missing local table {table}")

    columns = sqlite_columns(sqlite_cur, table)
    names = [name for name, _ in columns]
    create_pg_table(pg_cur, table, columns)

    rows = sqlite_cur.execute(f"SELECT {','.join(qident(name) for name in names)} FROM {qident(table)}").fetchall()
    values = [[cast_value(table, name, row[name]) for name in names] for row in rows]
    if values:
        psycopg2.extras.execute_values(
            pg_cur,
            f"INSERT INTO public.{qident(table)} ({','.join(qident(name) for name in names)}) VALUES %s",
            values,
            page_size=1000,
        )
    return len(values)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    sqlite_con = sqlite3.connect(str(LOCAL_DB))
    sqlite_con.row_factory = sqlite3.Row
    pg_con = psycopg2.connect(db_url())
    try:
        sqlite_cur = sqlite_con.cursor()
        pg_cur = pg_con.cursor()
        counts = {}
        for table in TABLES:
            if args.apply:
                counts[table] = sync_table(sqlite_cur, pg_cur, table)
            else:
                sqlite_cur.execute(f"SELECT COUNT(*) FROM {qident(table)}")
                counts[table] = sqlite_cur.fetchone()[0]
        if args.apply:
            pg_con.commit()
        else:
            pg_con.rollback()
        print(json.dumps({"apply": args.apply, "counts": counts}, indent=2))
    except Exception:
        pg_con.rollback()
        raise
    finally:
        sqlite_con.close()
        pg_con.close()


if __name__ == "__main__":
    main()
