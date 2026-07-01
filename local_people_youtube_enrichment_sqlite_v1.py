import argparse
import json
import shutil
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from historical_modern_people_youtube_enrichment_master_v1 import (
    LOCAL_HISTORICAL_DB,
    TIER1_PEOPLE,
    TIER1_PEOPLE_BY_SLUG,
    aliases_for_name,
    canonical_slug_override,
    compact_text,
    language_priority,
    normalize_text,
    safe_int,
    slugify,
    split_people_value,
)


FLIX_DB = Path(r"C:\Users\USER\Desktop\ott_project\data_factory\db\flixyfy.db")
REPORT_DIR = Path(r"C:\Users\USER\Desktop\flixyfy-deploy\flixyfy-api\reports")
BACKUP_DIR = Path(r"C:\Users\USER\Desktop\flixyfy-deploy\flixyfy-api\backups")

WIKI_PEOPLE_TABLE = "wiki_people_source_v1"
WIKI_FILMOGRAPHY_TABLE = "wiki_filmography_source_v1"
WIKI_MOVIE_TABLE = "wiki_movie_source_v1"
PEOPLE_TABLE = "people_canonical_v1"
ALIAS_TABLE = "people_alias_v1"
MOVIE_MAP_TABLE = "people_movie_map_v1"
SEARCH_CACHE_TABLE = "people_search_cache_v1"
POSTER_CACHE_TABLE = "movie_poster_enrichment_cache_v1"
YOUTUBE_CANDIDATE_TABLE = "youtube_movie_match_candidates_v1"
YOUTUBE_VALIDATED_TABLE = "youtube_movie_match_validated_v1"
YOUTUBE_REJECTED_TABLE = "youtube_movie_match_rejected_v1"
YOUTUBE_CACHE_TABLE = "youtube_movie_match_cache_v1"

BAD_YOUTUBE_TERMS = {
    "trailer",
    "teaser",
    "promo",
    "video song",
    "audio song",
    "lyrical",
    "jukebox",
    "scene",
    "fight scene",
    "comedy scene",
    "interview",
    "making",
    "behind the scenes",
    "review",
    "reaction",
    "shorts",
    "status",
    "episode",
    "serial",
    "press meet",
    "success meet",
}

YOUTUBE_SOURCE_TABLES = [
    "youtube_full_movie_links_final_preprod_v2",
    "youtube_full_movie_links_final_preprod_v1",
    "youtube_full_movie_links_prod_mirror_v1",
    "youtube_full_movie_links_v2",
    "historical_youtube_verified_links_v1",
    "youtube_full_movies_v1",
]


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def now_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def qident(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1", [table]).fetchone() is not None


def table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({qident(table)})")}


def first(row: dict[str, Any], keys: list[str], default=None):
    for key in keys:
        value = row.get(key)
        if value is not None and str(value).strip() != "":
            return value
    return default


def json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def backup_db() -> Path:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    backup = BACKUP_DIR / f"flixyfy_before_people_youtube_enrichment_{now_stamp()}.db"
    shutil.copy2(FLIX_DB, backup)
    return backup


def create_tables(conn: sqlite3.Connection) -> None:
    for table in [
        WIKI_PEOPLE_TABLE,
        WIKI_FILMOGRAPHY_TABLE,
        WIKI_MOVIE_TABLE,
        PEOPLE_TABLE,
        ALIAS_TABLE,
        MOVIE_MAP_TABLE,
        SEARCH_CACHE_TABLE,
        POSTER_CACHE_TABLE,
        YOUTUBE_CANDIDATE_TABLE,
        YOUTUBE_VALIDATED_TABLE,
        YOUTUBE_REJECTED_TABLE,
        YOUTUBE_CACHE_TABLE,
    ]:
        conn.execute(f"DROP TABLE IF EXISTS {qident(table)}")

    conn.execute(
        f"""
        CREATE TABLE {qident(WIKI_PEOPLE_TABLE)} (
            person_slug TEXT PRIMARY KEY,
            display_name TEXT NOT NULL,
            domain TEXT,
            primary_role TEXT,
            active_year_min INTEGER,
            active_year_max INTEGER,
            wiki_url TEXT,
            aliases_json TEXT,
            disambiguation_label TEXT,
            source TEXT,
            updated_at TEXT
        )
        """
    )
    conn.execute(
        f"""
        CREATE TABLE {qident(WIKI_FILMOGRAPHY_TABLE)} (
            person_slug TEXT,
            person_name TEXT,
            movie_slug TEXT,
            title TEXT,
            release_year INTEGER,
            language_slug TEXT,
            role_type TEXT,
            source TEXT,
            confidence INTEGER,
            updated_at TEXT,
            PRIMARY KEY(person_slug, movie_slug, role_type)
        )
        """
    )
    conn.execute(
        f"""
        CREATE TABLE {qident(WIKI_MOVIE_TABLE)} (
            movie_slug TEXT PRIMARY KEY,
            title TEXT,
            release_year INTEGER,
            language_slug TEXT,
            wiki_url TEXT,
            poster_url TEXT,
            source TEXT,
            updated_at TEXT
        )
        """
    )
    conn.execute(
        f"""
        CREATE TABLE {qident(PEOPLE_TABLE)} (
            person_slug TEXT PRIMARY KEY,
            display_name TEXT,
            domain TEXT,
            primary_role TEXT,
            active_year_min INTEGER,
            active_year_max INTEGER,
            movie_count INTEGER,
            youtube_movie_count INTEGER,
            disambiguation_label TEXT,
            source_tables_json TEXT,
            updated_at TEXT
        )
        """
    )
    conn.execute(
        f"""
        CREATE TABLE {qident(ALIAS_TABLE)} (
            person_slug TEXT,
            alias TEXT,
            normalized_alias TEXT,
            compact_alias TEXT,
            source TEXT,
            priority INTEGER,
            updated_at TEXT,
            PRIMARY KEY(person_slug, normalized_alias)
        )
        """
    )
    conn.execute(
        f"""
        CREATE TABLE {qident(MOVIE_MAP_TABLE)} (
            person_slug TEXT,
            person_name TEXT,
            movie_slug TEXT,
            movie_title TEXT,
            release_year INTEGER,
            language_slug TEXT,
            language_priority INTEGER,
            domain TEXT,
            role_type TEXT,
            has_youtube INTEGER,
            source TEXT,
            updated_at TEXT,
            PRIMARY KEY(person_slug, movie_slug, role_type)
        )
        """
    )
    conn.execute(
        f"""
        CREATE TABLE {qident(SEARCH_CACHE_TABLE)} (
            person_slug TEXT PRIMARY KEY,
            display_name TEXT,
            normalized_display_name TEXT,
            compact_display_name TEXT,
            domain TEXT,
            primary_role TEXT,
            active_year_min INTEGER,
            active_year_max INTEGER,
            movie_count INTEGER,
            youtube_movie_count INTEGER,
            aliases_json TEXT,
            aliases_search_text TEXT,
            compact_aliases_text TEXT,
            disambiguation_label TEXT,
            search_rank INTEGER,
            indexable INTEGER,
            updated_at TEXT
        )
        """
    )
    conn.execute(
        f"""
        CREATE TABLE {qident(POSTER_CACHE_TABLE)} (
            movie_slug TEXT PRIMARY KEY,
            title TEXT,
            domain TEXT,
            release_year INTEGER,
            poster_url TEXT,
            poster_source TEXT,
            is_official_poster INTEGER,
            updated_at TEXT
        )
        """
    )
    conn.execute(
        f"""
        CREATE TABLE {qident(YOUTUBE_CANDIDATE_TABLE)} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            person_slug TEXT,
            display_name TEXT,
            movie_slug TEXT,
            movie_title TEXT,
            release_year INTEGER,
            language_slug TEXT,
            language_priority INTEGER,
            domain TEXT,
            youtube_video_id TEXT,
            youtube_url TEXT,
            youtube_title TEXT,
            youtube_channel TEXT,
            duration_seconds INTEGER,
            thumbnail_url TEXT,
            source_table TEXT,
            title_score INTEGER,
            alias_score INTEGER,
            year_score INTEGER,
            language_score INTEGER,
            duration_score INTEGER,
            confidence_score INTEGER,
            validation_status TEXT,
            reject_reason TEXT,
            updated_at TEXT
        )
        """
    )
    conn.execute(
        f"""
        CREATE TABLE {qident(YOUTUBE_VALIDATED_TABLE)} (
            person_slug TEXT,
            movie_slug TEXT,
            youtube_video_id TEXT,
            confidence_score INTEGER,
            validation_status TEXT,
            payload_json TEXT,
            updated_at TEXT,
            PRIMARY KEY(person_slug, movie_slug, youtube_video_id)
        )
        """
    )
    conn.execute(
        f"""
        CREATE TABLE {qident(YOUTUBE_REJECTED_TABLE)} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            person_slug TEXT,
            movie_slug TEXT,
            youtube_video_id TEXT,
            reject_reason TEXT,
            payload_json TEXT,
            updated_at TEXT
        )
        """
    )
    conn.execute(
        f"""
        CREATE TABLE {qident(YOUTUBE_CACHE_TABLE)} (
            movie_slug TEXT,
            youtube_video_id TEXT,
            person_slug TEXT,
            youtube_url TEXT,
            youtube_title TEXT,
            confidence_score INTEGER,
            validation_status TEXT,
            updated_at TEXT,
            PRIMARY KEY(movie_slug, youtube_video_id)
        )
        """
    )

    for table, cols in {
        ALIAS_TABLE: ["compact_alias", "normalized_alias"],
        MOVIE_MAP_TABLE: ["person_slug", "movie_slug", "domain", "language_priority"],
        SEARCH_CACHE_TABLE: ["compact_display_name", "domain", "search_rank"],
        YOUTUBE_CANDIDATE_TABLE: ["person_slug", "movie_slug", "youtube_video_id", "validation_status"],
        YOUTUBE_CACHE_TABLE: ["person_slug", "validation_status"],
    }.items():
        for col in cols:
            conn.execute(f"CREATE INDEX idx_{table}_{col} ON {qident(table)}({qident(col)})")


def seed_tier1(conn: sqlite3.Connection) -> int:
    for person in TIER1_PEOPLE:
        aliases = sorted(aliases_for_name(person["display_name"], person["aliases"]))
        conn.execute(
            f"""
            INSERT OR REPLACE INTO {qident(WIKI_PEOPLE_TABLE)}
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                person["person_slug"],
                person["display_name"],
                person["domain"],
                person["primary_role"],
                person["active_year_min"],
                person["active_year_max"],
                person["wiki_url"],
                json_text(aliases),
                person["disambiguation_label"],
                "tier1_manual_identity_lock",
                now_iso(),
            ],
        )
    return len(TIER1_PEOPLE)


def import_local_wiki(conn: sqlite3.Connection) -> dict[str, int]:
    hist = sqlite3.connect(LOCAL_HISTORICAL_DB)
    hist.row_factory = sqlite3.Row
    movies = {}
    people = {}
    filmography = {}
    try:
        for row in hist.execute(
            """
            SELECT slug, title, release_year, language_slug, source_page, poster_url,
                   director, hero, heroine, [cast]
            FROM historical_wiki_serving_v1
            WHERE slug IS NOT NULL AND title IS NOT NULL
            """
        ):
            data = dict(row)
            movie_slug = data["slug"]
            movies[movie_slug] = (
                movie_slug,
                data["title"],
                safe_int(data["release_year"]),
                data["language_slug"],
                data["source_page"],
                data["poster_url"],
                "local_historical_wiki_serving_v1",
                now_iso(),
            )

            role_people = []
            for name in split_people_value(data.get("cast"))[:20]:
                role_people.append((name, "actor"))
            for name in split_people_value(data.get("hero"))[:4]:
                role_people.append((name, "actor"))
            for name in split_people_value(data.get("heroine"))[:4]:
                role_people.append((name, "actor"))
            for name in split_people_value(data.get("director"))[:6]:
                role_people.append((name, "director"))

            for name, role in role_people:
                person_slug = canonical_slug_override(name, WIKI_FILMOGRAPHY_TABLE, slugify(name))
                bucket = people.setdefault(
                    person_slug,
                    {
                        "display_name": name,
                        "min_year": safe_int(data["release_year"]),
                        "max_year": safe_int(data["release_year"]),
                        "aliases": set(),
                    },
                )
                year = safe_int(data["release_year"])
                if year is not None:
                    bucket["min_year"] = year if bucket["min_year"] is None else min(bucket["min_year"], year)
                    bucket["max_year"] = year if bucket["max_year"] is None else max(bucket["max_year"], year)
                bucket["aliases"].update(aliases_for_name(name))
                filmography[(person_slug, movie_slug, role)] = (
                    person_slug,
                    name,
                    movie_slug,
                    data["title"],
                    safe_int(data["release_year"]),
                    data["language_slug"],
                    role,
                    "local_historical_wiki_serving_v1",
                    100,
                    now_iso(),
                )
    finally:
        hist.close()

    conn.executemany(f"INSERT OR REPLACE INTO {qident(WIKI_MOVIE_TABLE)} VALUES (?, ?, ?, ?, ?, ?, ?, ?)", movies.values())
    for slug, data in people.items():
        locked = TIER1_PEOPLE_BY_SLUG.get(slug)
        display = locked["display_name"] if locked else data["display_name"]
        aliases = sorted(data["aliases"] | (aliases_for_name(locked["display_name"], locked["aliases"]) if locked else set()))
        conn.execute(
            f"""
            INSERT OR REPLACE INTO {qident(WIKI_PEOPLE_TABLE)}
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                slug,
                display,
                locked["domain"] if locked else "historical",
                locked["primary_role"] if locked else "actor",
                locked["active_year_min"] if locked else data["min_year"],
                locked["active_year_max"] if locked else data["max_year"],
                locked["wiki_url"] if locked else None,
                json_text(aliases),
                locked["disambiguation_label"] if locked else None,
                "local_historical_wiki_serving_v1",
                now_iso(),
            ],
        )
    conn.executemany(f"INSERT OR REPLACE INTO {qident(WIKI_FILMOGRAPHY_TABLE)} VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", filmography.values())
    return {
        "local_wiki_movies": len(movies),
        "local_wiki_people": len(people),
        "local_wiki_filmography": len(filmography),
    }


def source_rows(conn: sqlite3.Connection, table: str, columns: list[str]) -> list[dict[str, Any]]:
    if not table_exists(conn, table):
        return []
    cols = table_columns(conn, table)
    selected = [col for col in columns if col in cols]
    if not selected:
        return []
    return [dict(row) for row in conn.execute(f"SELECT {', '.join(qident(c) for c in selected)} FROM {qident(table)}")]


def build_people_and_movies(conn: sqlite3.Connection) -> dict[str, int]:
    people = {}
    movie_rows = {}

    def merge_person(name: str, slug: str, domain: str, source: str, row: dict[str, Any]) -> None:
        slug = canonical_slug_override(name, source, slug)
        bucket = people.setdefault(
            slug,
            {
                "display_name": name,
                "domain": domain,
                "primary_role": first(row, ["primary_role", "role_type", "role"], "actor"),
                "min_year": safe_int(first(row, ["active_year_min", "first_year"])),
                "max_year": safe_int(first(row, ["active_year_max", "last_year"])),
                "movie_count": safe_int(row.get("movie_count"), 0),
                "youtube_movie_count": safe_int(row.get("youtube_movie_count"), 0),
                "aliases": set(),
                "sources": set(),
                "disambiguation_label": row.get("disambiguation_label"),
            },
        )
        locked = TIER1_PEOPLE_BY_SLUG.get(slug)
        if locked:
            bucket.update(
                {
                    "display_name": locked["display_name"],
                    "domain": locked["domain"],
                    "primary_role": locked["primary_role"],
                    "min_year": locked["active_year_min"],
                    "max_year": locked["active_year_max"],
                    "disambiguation_label": locked["disambiguation_label"],
                }
            )
            bucket["aliases"].update(aliases_for_name(locked["display_name"], locked["aliases"]))
        elif source == "modern_people_seo_preprod_v1":
            bucket["display_name"] = name
            bucket["domain"] = "modern"
        bucket["movie_count"] = max(bucket["movie_count"], safe_int(row.get("movie_count"), 0))
        bucket["youtube_movie_count"] = max(bucket["youtube_movie_count"], safe_int(row.get("youtube_movie_count"), 0))
        bucket["aliases"].update(aliases_for_name(name))
        for alias in json.loads(row.get("aliases_json") or "[]") if row.get("aliases_json") else []:
            bucket["aliases"].update(aliases_for_name(alias))
        bucket["sources"].add(source)

    for row in source_rows(conn, WIKI_PEOPLE_TABLE, ["person_slug", "display_name", "domain", "primary_role", "active_year_min", "active_year_max", "aliases_json", "disambiguation_label"]):
        merge_person(row["display_name"], row["person_slug"], row.get("domain") or "historical", WIKI_PEOPLE_TABLE, row)
    for row in source_rows(conn, "historical_people_seo_preprod_v1", ["person_name", "person_slug", "primary_role", "movie_count", "youtube_movie_count"]):
        merge_person(row["person_name"], row["person_slug"], "historical", "historical_people_seo_preprod_v1", row)
    for row in source_rows(conn, "modern_people_seo_preprod_v1", ["person_name", "person_slug", "primary_role", "movie_count", "last_year"]):
        merge_person(row["person_name"], row["person_slug"], "modern", "modern_people_seo_preprod_v1", row)

    for table, domain in [("historical_movie_people_seo_preprod_v1", "historical"), ("modern_movie_people_seo_preprod_v1", "modern")]:
        for row in source_rows(conn, table, ["person_slug", "person_name", "movie_slug", "title", "release_year", "language_slug", "primary_language", "role_type", "role", "has_youtube"]):
            name = row.get("person_name")
            slug = canonical_slug_override(name, table, row.get("person_slug"))
            role = row.get("role_type") or row.get("role") or "actor"
            key = (slug, row.get("movie_slug"), role)
            if not all(key) or not row.get("title"):
                continue
            movie_rows[key] = (
                slug,
                name,
                row.get("movie_slug"),
                row.get("title"),
                safe_int(row.get("release_year")),
                row.get("language_slug") or row.get("primary_language"),
                language_priority(row.get("language_slug") or row.get("primary_language")),
                domain,
                role,
                safe_int(row.get("has_youtube"), 0) or 0,
                table,
                now_iso(),
            )
    for row in source_rows(conn, WIKI_FILMOGRAPHY_TABLE, ["person_slug", "person_name", "movie_slug", "title", "release_year", "language_slug", "role_type"]):
        if people.get(row["person_slug"], {}).get("domain") == "modern":
            continue
        role = row.get("role_type") or "actor"
        movie_rows[(row["person_slug"], row["movie_slug"], role)] = (
            row["person_slug"],
            row.get("person_name"),
            row["movie_slug"],
            row["title"],
            safe_int(row.get("release_year")),
            row.get("language_slug"),
            language_priority(row.get("language_slug")),
            people.get(row["person_slug"], {}).get("domain", "historical"),
            role,
            0,
            WIKI_FILMOGRAPHY_TABLE,
            now_iso(),
        )

    by_person_movies = defaultdict(set)
    by_person_youtube = defaultdict(set)
    for row in movie_rows.values():
        by_person_movies[row[0]].add(row[2])
        if row[9]:
            by_person_youtube[row[0]].add(row[2])

    conn.executemany(f"INSERT OR REPLACE INTO {qident(MOVIE_MAP_TABLE)} VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", movie_rows.values())

    people_values = []
    alias_values = []
    cache_values = []
    for slug, row in people.items():
        locked = TIER1_PEOPLE_BY_SLUG.get(slug)
        movie_count = max(row["movie_count"], len(by_person_movies[slug]))
        youtube_count = max(row["youtube_movie_count"], len(by_person_youtube[slug]))
        aliases = sorted(row["aliases"])
        search_rank = (100000 if locked else 0) + youtube_count * 1000 + movie_count
        people_values.append((slug, row["display_name"], row["domain"], row["primary_role"], row["min_year"], row["max_year"], movie_count, youtube_count, row["disambiguation_label"], json_text(sorted(row["sources"])), now_iso()))
        for alias in aliases:
            alias_values.append((slug, alias, normalize_text(alias), compact_text(alias), "canonical_builder", 100 if normalize_text(alias) == normalize_text(row["display_name"]) else 50, now_iso()))
        compact_aliases = "|" + "|".join(sorted({compact_text(alias) for alias in aliases if compact_text(alias)})) + "|"
        cache_values.append((slug, row["display_name"], normalize_text(row["display_name"]), compact_text(row["display_name"]), row["domain"], row["primary_role"], row["min_year"], row["max_year"], movie_count, youtube_count, json_text(aliases), " ".join(sorted({normalize_text(a) for a in aliases})), compact_aliases, row["disambiguation_label"], search_rank, 1, now_iso()))

    conn.executemany(f"INSERT OR REPLACE INTO {qident(PEOPLE_TABLE)} VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", people_values)
    conn.executemany(f"INSERT OR REPLACE INTO {qident(ALIAS_TABLE)} VALUES (?, ?, ?, ?, ?, ?, ?)", alias_values)
    conn.executemany(f"INSERT OR REPLACE INTO {qident(SEARCH_CACHE_TABLE)} VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", cache_values)
    return {"people_rows": len(people_values), "alias_rows": len(alias_values), "movie_map_rows": len(movie_rows), "search_cache_rows": len(cache_values)}


def bad_youtube_term(title: str) -> str:
    blob = f" {normalize_text(title)} "
    for term in BAD_YOUTUBE_TERMS:
        if f" {normalize_text(term)} " in blob:
            return term
    return ""


def video_id_from_url(url: Any) -> str:
    text = str(url or "")
    for marker in ["?v=", "&v="]:
        if marker in text:
            return text.split(marker, 1)[1].split("&", 1)[0].split("#", 1)[0]
    if "youtu.be/" in text:
        return text.split("youtu.be/", 1)[1].split("?", 1)[0].split("/", 1)[0]
    return ""


def youtube_rows(conn: sqlite3.Connection, max_videos: int) -> list[dict[str, Any]]:
    rows = []
    for table in YOUTUBE_SOURCE_TABLES:
        if not table_exists(conn, table):
            continue
        cols = table_columns(conn, table)
        selected = [col for col in ["movie_slug", "title", "movie_title", "clean_title", "year", "release_year", "language", "catalog_language", "youtube_language", "youtube_video_id", "video_id", "youtube_url", "video_url", "url", "youtube_title", "video_title", "youtube_channel", "channel_name", "duration_seconds", "youtube_duration_seconds", "view_count"] if col in cols]
        if not selected:
            continue
        sql = f"SELECT {', '.join(qident(c) for c in selected)} FROM {qident(table)}"
        params = []
        if max_videos and max_videos > 0:
            sql += " LIMIT ?"
            params.append(max(1, max_videos // len(YOUTUBE_SOURCE_TABLES)))
        for row in conn.execute(sql, params):
            item = dict(row)
            item["_source_table"] = table
            rows.append(item)
    return rows[:max_videos] if max_videos and max_videos > 0 else rows


def score_match(video: dict[str, Any], movie: dict[str, Any], aliases: set[str]) -> tuple[int, dict[str, int], str]:
    video_title = first(video, ["youtube_title", "video_title", "title", "clean_title"], "")
    video_text = normalize_text(f"{video_title} {first(video, ['youtube_channel', 'channel_name'], '')}")
    movie_norm = normalize_text(movie["movie_title"])
    title_score = 45 if movie_norm and f" {movie_norm} " in f" {video_text} " else 0
    if not title_score and movie_norm and all(token in video_text.split() for token in movie_norm.split()[:3]):
        title_score = 30
    alias_score = 10 if any(len(compact_text(alias)) >= 3 and compact_text(alias) in compact_text(video_text) for alias in aliases) else 0
    movie_year = safe_int(movie.get("release_year"))
    video_year = safe_int(first(video, ["year", "release_year"]))
    year_score = 10 if movie_year and video_year and movie_year == video_year else (3 if movie_year else 0)
    movie_lang = normalize_text(movie.get("language_slug"))
    video_lang = normalize_text(first(video, ["language", "catalog_language", "youtube_language"], ""))
    language_score = 5 if movie_lang and video_lang and movie_lang == video_lang else 0
    duration = safe_int(first(video, ["duration_seconds", "youtube_duration_seconds"]))
    duration_score = 10 if duration and 6000 <= duration <= 15000 else (5 if duration and 3600 <= duration <= 18000 else 0)
    reject = ""
    bad = bad_youtube_term(video_title)
    if bad:
        reject = f"bad_title_term:{bad}"
    elif duration is not None and duration < 3600:
        reject = f"duration_too_short:{duration}"
    elif title_score < 30:
        reject = "weak_title_match"
    return title_score + alias_score + year_score + language_score + duration_score, {"title": title_score, "alias": alias_score, "year": year_score, "language": language_score, "duration": duration_score}, reject


def build_youtube(conn: sqlite3.Connection, max_videos: int, max_candidates: int, lean: bool) -> dict[str, int]:
    aliases = defaultdict(set)
    for row in conn.execute(f"SELECT person_slug, alias FROM {qident(ALIAS_TABLE)}"):
        aliases[row["person_slug"]].add(row["alias"])
    title_index = defaultdict(list)
    for row in conn.execute(f"SELECT * FROM {qident(MOVIE_MAP_TABLE)} WHERE role_type IN ('actor', 'actress', 'lead', 'person') ORDER BY language_priority, release_year DESC"):
        movie = dict(row)
        tokens = normalize_text(movie["movie_title"]).split()
        if tokens:
            title_index[tokens[0]].append(movie)

    candidates = []
    validated = {}
    rejected = []
    cache = {}
    seen = set()
    candidate_seen = 0
    rejected_seen = 0
    review_seen = 0
    for video in youtube_rows(conn, max_videos):
        if max_candidates and max_candidates > 0 and len(candidates) >= max_candidates:
            break
        url = first(video, ["youtube_url", "video_url", "url"], "")
        video_id = first(video, ["youtube_video_id", "video_id"]) or video_id_from_url(url)
        video_title = first(video, ["youtube_title", "video_title", "title", "clean_title"], "")
        if not video_id or not video_title:
            continue
        candidate_movies = []
        for token in normalize_text(video_title).split()[:8]:
            candidate_movies.extend(title_index.get(token, []))
        for movie in candidate_movies[:80]:
            if max_candidates and max_candidates > 0 and len(candidates) >= max_candidates:
                break
            key = (movie["person_slug"], movie["movie_slug"], video_id)
            if key in seen:
                continue
            seen.add(key)
            score, parts, reject = score_match(video, movie, aliases[movie["person_slug"]])
            status = "AUTO_SAFE" if score >= 75 and not reject else ("REVIEW" if score >= 55 and not reject else "REJECT")
            candidate_seen += 1
            payload = {"person_slug": movie["person_slug"], "movie_slug": movie["movie_slug"], "youtube_video_id": video_id, "youtube_url": url, "youtube_title": video_title, "confidence_score": score, "validation_status": status, "reject_reason": reject}
            if not lean or status != "REJECT":
                candidates.append((movie["person_slug"], movie.get("person_name"), movie["movie_slug"], movie["movie_title"], movie.get("release_year"), movie.get("language_slug"), movie.get("language_priority"), movie.get("domain"), video_id, url, video_title, first(video, ["youtube_channel", "channel_name"], ""), safe_int(first(video, ["duration_seconds", "youtube_duration_seconds"])), None, video["_source_table"], parts["title"], parts["alias"], parts["year"], parts["language"], parts["duration"], score, status, reject, now_iso()))
            if status == "AUTO_SAFE":
                validated[key] = (movie["person_slug"], movie["movie_slug"], video_id, score, status, json_text(payload), now_iso())
                cache_key = (movie["movie_slug"], video_id)
                if cache_key not in cache or score > cache[cache_key][5]:
                    cache[cache_key] = (movie["movie_slug"], video_id, movie["person_slug"], url, video_title, score, status, now_iso())
            elif status == "REVIEW":
                review_seen += 1
            elif status == "REJECT":
                rejected_seen += 1
                if not lean:
                    rejected.append((movie["person_slug"], movie["movie_slug"], video_id, reject, json_text(payload), now_iso()))

    conn.executemany(f"INSERT INTO {qident(YOUTUBE_CANDIDATE_TABLE)} (person_slug, display_name, movie_slug, movie_title, release_year, language_slug, language_priority, domain, youtube_video_id, youtube_url, youtube_title, youtube_channel, duration_seconds, thumbnail_url, source_table, title_score, alias_score, year_score, language_score, duration_score, confidence_score, validation_status, reject_reason, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", candidates)
    conn.executemany(f"INSERT OR REPLACE INTO {qident(YOUTUBE_VALIDATED_TABLE)} VALUES (?, ?, ?, ?, ?, ?, ?)", validated.values())
    conn.executemany(f"INSERT INTO {qident(YOUTUBE_REJECTED_TABLE)} (person_slug, movie_slug, youtube_video_id, reject_reason, payload_json, updated_at) VALUES (?, ?, ?, ?, ?, ?)", rejected)
    conn.executemany(f"INSERT OR REPLACE INTO {qident(YOUTUBE_CACHE_TABLE)} VALUES (?, ?, ?, ?, ?, ?, ?, ?)", cache.values())
    return {
        "youtube_candidates_seen": candidate_seen,
        "youtube_candidates_stored": len(candidates),
        "youtube_auto_safe": len(validated),
        "youtube_review": review_seen,
        "youtube_rejected": rejected_seen if lean else len(rejected),
        "youtube_reject_rows_stored": 0 if lean else len(rejected),
        "youtube_cache_rows": len(cache),
        "youtube_lean_mode": 1 if lean else 0,
    }


def build_posters(conn: sqlite3.Connection) -> int:
    posters = {}
    sources = [
        ("historical_card_serving_v1", "historical", "historical_card_serving_v1", 1),
        ("media_serving_v8_expanded", "modern", "media_serving_v8_expanded", 1),
        (WIKI_MOVIE_TABLE, "historical", WIKI_MOVIE_TABLE, 1),
    ]
    for table, domain, source, official in sources:
        if not table_exists(conn, table):
            continue
        cols = table_columns(conn, table)
        selected = [col for col in ["slug", "movie_slug", "title", "release_year", "year", "poster_url", "poster_path"] if col in cols]
        if not selected:
            continue
        for row in conn.execute(f"SELECT {', '.join(qident(c) for c in selected)} FROM {qident(table)}"):
            data = dict(row)
            slug = data.get("movie_slug") or data.get("slug")
            poster = data.get("poster_url") or data.get("poster_path")
            if slug and poster and slug not in posters:
                posters[slug] = (slug, data.get("title"), domain, safe_int(data.get("release_year") or data.get("year")), poster, source, official, now_iso())
    conn.executemany(f"INSERT OR REPLACE INTO {qident(POSTER_CACHE_TABLE)} VALUES (?, ?, ?, ?, ?, ?, ?, ?)", posters.values())
    return len(posters)


def audit_people(conn: sqlite3.Connection) -> dict[str, list[str]]:
    queries = ["ntr", "jr ntr", "balakrishna", "nbk", "mahesh babu", "anr", "nageswara rao", "krishna", "superstar krishna", "krishnam raju", "sobhan babu", "mgr", "jayalalithaa", "chiranjeevi", "rajkumar", "vishnuvardhan"]
    out = {}
    for query in queries:
        compact = compact_text(query)
        rows = conn.execute(
            f"""
            SELECT display_name, domain
            FROM {qident(SEARCH_CACHE_TABLE)}
            WHERE compact_aliases_text LIKE ?
               OR compact_display_name LIKE ?
               OR aliases_search_text LIKE ?
            ORDER BY search_rank DESC, display_name ASC
            LIMIT 5
            """,
            [f"%|{compact}|%", f"{compact}%", f"%{normalize_text(query)}%"],
        ).fetchall()
        out[query] = [f"{row['display_name']} ({row['domain']})" for row in rows]
    return out


def write_report(report: dict[str, Any]) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORT_DIR / f"local_people_youtube_enrichment_sqlite_{now_stamp()}.json"
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description="Build people/wiki/YouTube enrichment tables in local flixyfy.db first.")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--skip-youtube", action="store_true")
    parser.add_argument("--max-videos", type=int, default=50000, help="Maximum YouTube rows to scan. Use 0 for all local source rows.")
    parser.add_argument("--max-candidates", type=int, default=100000, help="Maximum candidate rows to stage. Use 0 for no candidate cap.")
    parser.add_argument("--lean-youtube", action="store_true", help="Scan all requested videos but store only AUTO_SAFE/REVIEW candidates and safe cache rows.")
    args = parser.parse_args()

    report = {"created_at": now_iso(), "mode": "apply" if args.apply else "dry_run", "db": str(FLIX_DB)}
    backup = None
    if args.apply:
        backup = backup_db()
        report["backup_path"] = str(backup)

    conn = sqlite3.connect(str(FLIX_DB))
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("BEGIN")
        create_tables(conn)
        report["tier1_seed_rows"] = seed_tier1(conn)
        report.update(import_local_wiki(conn))
        report.update(build_people_and_movies(conn))
        if not args.skip_youtube:
            report.update(build_youtube(conn, args.max_videos, args.max_candidates, args.lean_youtube))
        report["poster_cache_rows"] = build_posters(conn)
        report["public_people_audit"] = audit_people(conn)

        if args.apply:
            conn.commit()
            report["status"] = "APPLIED"
        else:
            conn.rollback()
            report["status"] = "DRY_RUN_ROLLED_BACK"
        path = write_report(report)
        print(json.dumps(report, indent=2, ensure_ascii=False))
        print(f"REPORT: {path}")
        return 0
    except Exception as exc:
        conn.rollback()
        report["status"] = "FAILED"
        report["error"] = str(exc)
        path = write_report(report)
        print(json.dumps(report, indent=2, ensure_ascii=False))
        print(f"REPORT: {path}")
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
