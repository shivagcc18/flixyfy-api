import argparse
import json
import os
import re
import sqlite3
import sys
import traceback
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import psycopg2
import psycopg2.extras


REPORT_DIR = Path(r"C:\Users\USER\Desktop\flixyfy-deploy\flixyfy-api\reports")
LOCAL_HISTORICAL_DB = Path(
    r"C:\Users\USER\Desktop\ott_project\data_factory\historical_indian_1960_1999_v1\db\historical_indian_1960_1999_v1.db"
)

WIKI_PEOPLE_TABLE = "wiki_people_source_v1"
WIKI_FILMOGRAPHY_TABLE = "wiki_filmography_source_v1"
WIKI_MOVIE_TABLE = "wiki_movie_source_v1"
WIKI_ALIAS_TABLE = "wiki_person_alias_source_v1"

PEOPLE_TABLE = "people_canonical_v1"
ALIAS_TABLE = "people_alias_v1"
MOVIE_MAP_TABLE = "people_movie_map_v1"
SEARCH_CACHE_TABLE = "people_search_cache_v1"
POSTER_CACHE_TABLE = "movie_poster_enrichment_cache_v1"

YOUTUBE_QUEUE_TABLE = "youtube_person_match_queue_v1"
YOUTUBE_CANDIDATE_TABLE = "youtube_movie_match_candidates_v1"
YOUTUBE_VALIDATED_TABLE = "youtube_movie_match_validated_v1"
YOUTUBE_REJECTED_TABLE = "youtube_movie_match_rejected_v1"
YOUTUBE_CACHE_TABLE = "youtube_movie_match_cache_v1"

HISTORICAL_PEOPLE_SOURCE = "historical_people_seo_preprod_v1"
HISTORICAL_MOVIE_PEOPLE_SOURCE = "historical_movie_people_seo_preprod_v1"
MODERN_PEOPLE_SOURCE = "modern_people_seo_preprod_v1"
MODERN_MOVIE_PEOPLE_SOURCE = "modern_movie_people_seo_preprod_v1"

YOUTUBE_SOURCE_TABLES = [
    "youtube_full_movie_links_v2",
    "youtube_full_movie_links_preprod_stage_v2",
    "youtube_full_movie_links_final_preprod_v2",
    "youtube_full_movie_links_final_candidate_v1",
    "youtube_safe_match_auto_promote_canonical_clean_v1",
    "youtube_canonical_clean_local_stage_v1",
    "historical_youtube_verified_links_v1",
]

TIER1_LANGUAGE_SLUGS = {"telugu", "tamil", "hindi", "kannada", "malayalam", "te", "ta", "hi", "kn", "ml"}
TIER2_LANGUAGE_SLUGS = {
    "bengali",
    "marathi",
    "gujarati",
    "punjabi",
    "odia",
    "assamese",
    "bhojpuri",
    "urdu",
    "sanskrit",
    "bn",
    "mr",
    "gu",
    "pa",
    "or",
    "as",
    "bhojpuri",
    "ur",
    "sa",
}

BAD_YOUTUBE_TERMS = [
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
]

TIER1_PEOPLE = [
    {
        "person_slug": "n-t-rama-rao",
        "display_name": "N. T. Rama Rao",
        "domain": "historical",
        "primary_role": "actor",
        "active_year_min": 1949,
        "active_year_max": 1994,
        "wiki_url": "https://en.wikipedia.org/wiki/N._T._Rama_Rao",
        "aliases": ["NTR", "Sr NTR", "N. T. Rama Rao", "Nandamuri Taraka Rama Rao", "N T Rama Rao"],
        "disambiguation_label": "Sr NTR, historical actor and former Andhra Pradesh chief minister",
    },
    {
        "person_slug": "n-t-rama-rao-jr",
        "display_name": "Jr NTR",
        "domain": "modern",
        "primary_role": "actor",
        "active_year_min": 2001,
        "active_year_max": 2026,
        "wiki_url": "https://en.wikipedia.org/wiki/N._T._Rama_Rao_Jr.",
        "aliases": ["Jr NTR", "NTR Jr", "N. T. Rama Rao Jr", "Tarak", "Junior NTR", "NTR"],
        "disambiguation_label": "Jr NTR, modern Telugu actor",
    },
    {
        "person_slug": "nandamuri-balakrishna",
        "display_name": "Nandamuri Balakrishna",
        "domain": "modern_historical_bridge",
        "primary_role": "actor",
        "active_year_min": 1974,
        "active_year_max": 2026,
        "wiki_url": "https://en.wikipedia.org/wiki/Nandamuri_Balakrishna",
        "aliases": ["Balakrishna", "Nandamuri Balakrishna", "NBK", "N. Balakrishna", "Balayya"],
        "disambiguation_label": "Nandamuri Balakrishna, Telugu actor",
    },
    {
        "person_slug": "mahesh-babu",
        "display_name": "Mahesh Babu",
        "domain": "modern",
        "primary_role": "actor",
        "active_year_min": 1979,
        "active_year_max": 2026,
        "wiki_url": "https://en.wikipedia.org/wiki/Mahesh_Babu",
        "aliases": ["Mahesh Babu", "Ghattamaneni Mahesh Babu", "Prince Mahesh", "Mahesh"],
        "disambiguation_label": "Mahesh Babu, modern Telugu actor",
    },
    {
        "person_slug": "akkineni-nageswara-rao",
        "display_name": "Akkineni Nageswara Rao",
        "domain": "historical",
        "primary_role": "actor",
        "active_year_min": 1941,
        "active_year_max": 2014,
        "wiki_url": "https://en.wikipedia.org/wiki/Akkineni_Nageswara_Rao",
        "aliases": ["Akkineni Nageswara Rao", "A. Nageswara Rao", "A Nageswara Rao", "Nageswara Rao", "ANR"],
        "disambiguation_label": "ANR, Telugu cinema legend",
    },
    {
        "person_slug": "krishna-ghattamaneni",
        "display_name": "Krishna",
        "domain": "historical",
        "primary_role": "actor",
        "active_year_min": 1965,
        "active_year_max": 2016,
        "wiki_url": "https://en.wikipedia.org/wiki/Krishna_(Telugu_actor)",
        "aliases": ["Krishna", "Ghattamaneni Krishna", "Krishna Ghattamaneni", "Ghattamaneni Siva Rama Krishna", "Superstar Krishna"],
        "disambiguation_label": "Superstar Krishna, Telugu actor",
    },
    {
        "person_slug": "krishnam-raju",
        "display_name": "Krishnam Raju",
        "domain": "historical",
        "primary_role": "actor",
        "active_year_min": 1966,
        "active_year_max": 2022,
        "wiki_url": "https://en.wikipedia.org/wiki/Krishnam_Raju",
        "aliases": ["Krishnam Raju", "U. V. Krishnam Raju", "UV Krishnam Raju", "Uppalapati Venkata Krishnam Raju", "Rebel Star"],
        "disambiguation_label": "Krishnam Raju, Telugu actor",
    },
    {
        "person_slug": "sobhan-babu",
        "display_name": "Sobhan Babu",
        "domain": "historical",
        "primary_role": "actor",
        "active_year_min": 1959,
        "active_year_max": 1996,
        "wiki_url": "https://en.wikipedia.org/wiki/Sobhan_Babu",
        "aliases": ["Sobhan Babu", "Shoban Babu", "Uppu Sobhana Chalapathi Rao"],
        "disambiguation_label": "Sobhan Babu, Telugu actor",
    },
    {
        "person_slug": "amitabh-bachchan",
        "display_name": "Amitabh Bachchan",
        "domain": "modern_historical_bridge",
        "primary_role": "actor",
        "active_year_min": 1969,
        "active_year_max": 2026,
        "wiki_url": "https://en.wikipedia.org/wiki/Amitabh_Bachchan",
        "aliases": ["Amitabh Bachchan", "Amitabh", "Big B"],
        "disambiguation_label": "Amitabh Bachchan, Hindi actor",
    },
    {
        "person_slug": "rajinikanth",
        "display_name": "Rajinikanth",
        "domain": "modern_historical_bridge",
        "primary_role": "actor",
        "active_year_min": 1975,
        "active_year_max": 2026,
        "wiki_url": "https://en.wikipedia.org/wiki/Rajinikanth",
        "aliases": ["Rajinikanth", "Rajini", "Shivaji Rao Gaikwad"],
        "disambiguation_label": "Rajinikanth, Tamil actor",
    },
    {
        "person_slug": "m-g-ramachandran",
        "display_name": "M. G. Ramachandran",
        "domain": "historical",
        "primary_role": "actor",
        "active_year_min": 1936,
        "active_year_max": 1978,
        "wiki_url": "https://en.wikipedia.org/wiki/M._G._Ramachandran",
        "aliases": ["M. G. Ramachandran", "M G Ramachandran", "MGR", "M.G.R.", "Maruthur Gopalan Ramachandran"],
        "disambiguation_label": "MGR, Tamil cinema legend and former Tamil Nadu chief minister",
    },
    {
        "person_slug": "jayalalithaa",
        "display_name": "Jayalalithaa",
        "domain": "historical",
        "primary_role": "actor",
        "active_year_min": 1961,
        "active_year_max": 1980,
        "wiki_url": "https://en.wikipedia.org/wiki/J._Jayalalithaa",
        "aliases": ["Jayalalithaa", "Jayalalitha", "J. Jayalalithaa", "J Jayalalithaa", "Jaya"],
        "disambiguation_label": "Jayalalithaa, Tamil actor and former Tamil Nadu chief minister",
    },
    {
        "person_slug": "kamal-haasan",
        "display_name": "Kamal Haasan",
        "domain": "modern_historical_bridge",
        "primary_role": "actor",
        "active_year_min": 1960,
        "active_year_max": 2026,
        "wiki_url": "https://en.wikipedia.org/wiki/Kamal_Haasan",
        "aliases": ["Kamal Haasan", "Kamal Hassan", "Kamal"],
        "disambiguation_label": "Kamal Haasan, Tamil actor",
    },
    {
        "person_slug": "chiranjeevi",
        "display_name": "Chiranjeevi",
        "domain": "modern_historical_bridge",
        "primary_role": "actor",
        "active_year_min": 1978,
        "active_year_max": 2026,
        "wiki_url": "https://en.wikipedia.org/wiki/Chiranjeevi",
        "aliases": ["Chiranjeevi", "Megastar Chiranjeevi", "Konidela Chiranjeevi"],
        "disambiguation_label": "Chiranjeevi, Telugu actor",
    },
    {
        "person_slug": "mohanlal",
        "display_name": "Mohanlal",
        "domain": "modern_historical_bridge",
        "primary_role": "actor",
        "active_year_min": 1978,
        "active_year_max": 2026,
        "wiki_url": "https://en.wikipedia.org/wiki/Mohanlal",
        "aliases": ["Mohanlal", "Mohanlal Viswanathan", "Lalettan"],
        "disambiguation_label": "Mohanlal, Malayalam actor",
    },
    {
        "person_slug": "mammootty",
        "display_name": "Mammootty",
        "domain": "modern_historical_bridge",
        "primary_role": "actor",
        "active_year_min": 1971,
        "active_year_max": 2026,
        "wiki_url": "https://en.wikipedia.org/wiki/Mammootty",
        "aliases": ["Mammootty", "Muhammad Kutty", "Mammukka"],
        "disambiguation_label": "Mammootty, Malayalam actor",
    },
    {
        "person_slug": "rajkumar",
        "display_name": "Rajkumar",
        "domain": "historical",
        "primary_role": "actor",
        "active_year_min": 1954,
        "active_year_max": 2000,
        "wiki_url": "https://en.wikipedia.org/wiki/Dr._Rajkumar",
        "aliases": ["Rajkumar", "Dr Rajkumar", "Annavru", "Singanalluru Puttaswamaiah Muthuraj"],
        "disambiguation_label": "Dr. Rajkumar, Kannada actor",
    },
    {
        "person_slug": "vishnuvardhan",
        "display_name": "Vishnuvardhan",
        "domain": "historical",
        "primary_role": "actor",
        "active_year_min": 1972,
        "active_year_max": 2009,
        "wiki_url": "https://en.wikipedia.org/wiki/Vishnuvardhan_(actor)",
        "aliases": ["Vishnuvardhan", "Dr Vishnuvardhan", "Sampath Kumar"],
        "disambiguation_label": "Vishnuvardhan, Kannada actor",
    },
]

TIER1_PEOPLE_BY_SLUG = {person["person_slug"]: person for person in TIER1_PEOPLE}


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def now_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def qident(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def normalize_text(value: Any) -> str:
    text = re.sub(r"[^a-z0-9]+", " ", str(value or "").lower())
    return " ".join(text.split())


def compact_text(value: Any) -> str:
    return "".join(ch for ch in normalize_text(value) if ch.isalnum())


def slugify(value: Any) -> str:
    text = re.sub(r"[^a-z0-9]+", "-", str(value or "").lower()).strip("-")
    return re.sub(r"-+", "-", text)


def safe_int(value: Any, default=None):
    try:
        if value is None or value == "":
            return default
        return int(float(str(value).replace(",", "").strip()))
    except Exception:
        return default


def language_priority(value: Any) -> int:
    lang = normalize_text(value)
    if lang in TIER1_LANGUAGE_SLUGS:
        return 1
    if lang in TIER2_LANGUAGE_SLUGS:
        return 2
    if lang:
        return 3
    return 9


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def load_env() -> None:
    here = Path(__file__).resolve().parent
    for path in [here / ".env.local", here / ".env"]:
        load_env_file(path)


def database_url() -> str:
    load_env()
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
        WHERE table_schema='public' AND table_name=%s
        LIMIT 1
        """,
        [table],
    )
    return cur.fetchone() is not None


def table_columns(cur, table: str) -> set[str]:
    cur.execute(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema='public' AND table_name=%s
        """,
        [table],
    )
    return {row["column_name"] for row in cur.fetchall()}


def first(row: dict[str, Any], keys: list[str], default=None):
    for key in keys:
        value = row.get(key)
        if value is not None and str(value).strip() != "":
            return value
    return default


def aliases_for_name(name: str, extra: list[str] | None = None) -> set[str]:
    aliases = {str(name or "").strip(), normalize_text(name), slugify(name)}
    parts = normalize_text(name).split()
    if len(parts) >= 2:
        aliases.add("".join(part[0] for part in parts if part))
    for item in extra or []:
        aliases.add(str(item or "").strip())
        aliases.add(normalize_text(item))
        aliases.add(slugify(item))
    return {alias for alias in aliases if alias}


def split_people_value(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        parts = value
    else:
        text = str(value).strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                parts = parsed
            else:
                parts = [text]
        except Exception:
            text = re.sub(r"\s+ and \s+", ", ", text, flags=re.I)
            text = text.replace(";", ",").replace("|", ",")
            parts = text.split(",")

    out = []
    seen = set()
    for part in parts:
        name = re.sub(r"\s*\([^)]*\)", "", str(part or "")).strip()
        name = re.sub(r"\s+", " ", name)
        if not name or normalize_text(name) in {"unknown", "various", "n a", "na", "none"}:
            continue
        key = normalize_text(name)
        if key in seen:
            continue
        seen.add(key)
        out.append(name)
    return out


def canonical_slug_override(display_name: str, source: str, default_slug: str) -> str:
    compact = compact_text(display_name)
    sr_ntr = {"ntr", "srntr", "ntramarao", "nandamuritarakaramarao"}
    jr_ntr = {"jrntr", "ntrjr", "juniorntr", "ntramaraojr"}
    balakrishna = {"balakrishna", "nbk", "nbalakrishna", "nandamuribalakrishna", "balayya"}
    mahesh = {"maheshbabu", "ghattamanenimaheshbabu", "princemahesh"}
    anr = {"anr", "akkineninageswararao", "anageswararao", "nageswararao"}
    krishna = {"krishnaghattamaneni", "ghattamanenikrishna", "ghattamanenisivaramakrishna", "superstarkrishna"}
    krishnam_raju = {"krishnamraju", "uvkrishnamraju", "uppalapativenkatakrishnamraju", "rebelstar"}
    sobhan_babu = {"sobhanbabu", "shobanbabu", "uppusobhanachalapathirao"}
    mgr = {"mgr", "mgramachandran", "maruthurgopalanramachandran"}
    jayalalithaa = {"jayalalithaa", "jayalalitha", "jjayalalithaa", "jaya"}
    rajinikanth = {"rajinikanth", "srajinikanth", "rajini", "shivajiraogaikwad"}
    vishnuvardhan = {"vishnuvardhan", "drvishnuvardhan", "sampathkumar"}

    if source in {HISTORICAL_PEOPLE_SOURCE, HISTORICAL_MOVIE_PEOPLE_SOURCE, WIKI_FILMOGRAPHY_TABLE} and compact in sr_ntr:
        return "n-t-rama-rao"
    if compact in jr_ntr or (source in {MODERN_PEOPLE_SOURCE, MODERN_MOVIE_PEOPLE_SOURCE} and compact == "ntr"):
        return "n-t-rama-rao-jr"
    if compact in balakrishna:
        return "nandamuri-balakrishna"
    if compact in mahesh:
        return "mahesh-babu"
    if compact in anr:
        return "akkineni-nageswara-rao"
    if compact in krishna:
        return "krishna-ghattamaneni"
    if source in {HISTORICAL_PEOPLE_SOURCE, HISTORICAL_MOVIE_PEOPLE_SOURCE, WIKI_FILMOGRAPHY_TABLE} and compact == "krishna":
        return "krishna-ghattamaneni"
    if compact in krishnam_raju:
        return "krishnam-raju"
    if compact in sobhan_babu:
        return "sobhan-babu"
    if compact in mgr:
        return "m-g-ramachandran"
    if compact in jayalalithaa:
        return "jayalalithaa"
    if compact in rajinikanth:
        return "rajinikanth"
    if compact in vishnuvardhan:
        return "vishnuvardhan"
    return default_slug


def create_tables(cur) -> None:
    cur.execute(
        f"""
        CREATE TABLE IF NOT EXISTS public.{qident(WIKI_PEOPLE_TABLE)} (
            person_slug TEXT PRIMARY KEY,
            display_name TEXT NOT NULL,
            domain TEXT,
            primary_role TEXT,
            active_year_min INTEGER,
            active_year_max INTEGER,
            wiki_url TEXT,
            aliases_json JSONB DEFAULT '[]'::jsonb,
            disambiguation_label TEXT,
            source TEXT,
            updated_at TEXT
        )
        """
    )
    cur.execute(
        f"""
        CREATE TABLE IF NOT EXISTS public.{qident(WIKI_FILMOGRAPHY_TABLE)} (
            id BIGSERIAL PRIMARY KEY,
            person_slug TEXT NOT NULL,
            person_name TEXT,
            movie_slug TEXT NOT NULL,
            title TEXT NOT NULL,
            release_year INTEGER,
            language_slug TEXT,
            role_type TEXT DEFAULT 'actor',
            source TEXT,
            confidence INTEGER DEFAULT 100,
            updated_at TEXT,
            UNIQUE(person_slug, movie_slug, role_type)
        )
        """
    )
    cur.execute(
        f"""
        CREATE TABLE IF NOT EXISTS public.{qident(WIKI_MOVIE_TABLE)} (
            movie_slug TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            release_year INTEGER,
            language_slug TEXT,
            wiki_url TEXT,
            poster_url TEXT,
            source TEXT,
            updated_at TEXT
        )
        """
    )
    cur.execute(
        f"""
        CREATE TABLE IF NOT EXISTS public.{qident(WIKI_ALIAS_TABLE)} (
            id BIGSERIAL PRIMARY KEY,
            person_slug TEXT NOT NULL,
            alias TEXT NOT NULL,
            normalized_alias TEXT NOT NULL,
            compact_alias TEXT NOT NULL,
            source TEXT,
            updated_at TEXT,
            UNIQUE(person_slug, normalized_alias)
        )
        """
    )
    cur.execute(
        f"""
        CREATE TABLE IF NOT EXISTS public.{qident(PEOPLE_TABLE)} (
            person_slug TEXT PRIMARY KEY,
            display_name TEXT NOT NULL,
            domain TEXT NOT NULL,
            primary_role TEXT,
            active_year_min INTEGER,
            active_year_max INTEGER,
            movie_count INTEGER DEFAULT 0,
            youtube_movie_count INTEGER DEFAULT 0,
            disambiguation_label TEXT,
            source_tables JSONB DEFAULT '[]'::jsonb,
            updated_at TEXT
        )
        """
    )
    cur.execute(
        f"""
        CREATE TABLE IF NOT EXISTS public.{qident(ALIAS_TABLE)} (
            id BIGSERIAL PRIMARY KEY,
            person_slug TEXT NOT NULL,
            alias TEXT NOT NULL,
            normalized_alias TEXT NOT NULL,
            compact_alias TEXT NOT NULL,
            source TEXT,
            priority INTEGER DEFAULT 50,
            updated_at TEXT,
            UNIQUE(person_slug, normalized_alias)
        )
        """
    )
    cur.execute(
        f"""
        CREATE TABLE IF NOT EXISTS public.{qident(MOVIE_MAP_TABLE)} (
            id BIGSERIAL PRIMARY KEY,
            person_slug TEXT NOT NULL,
            person_name TEXT,
            movie_slug TEXT NOT NULL,
            movie_title TEXT NOT NULL,
            release_year INTEGER,
            language_slug TEXT,
            language_priority INTEGER DEFAULT 9,
            domain TEXT,
            role_type TEXT DEFAULT 'actor',
            has_youtube INTEGER DEFAULT 0,
            source TEXT,
            updated_at TEXT,
            UNIQUE(person_slug, movie_slug, role_type)
        )
        """
    )
    cur.execute(
        f"""
        CREATE TABLE IF NOT EXISTS public.{qident(SEARCH_CACHE_TABLE)} (
            person_slug TEXT PRIMARY KEY,
            display_name TEXT NOT NULL,
            normalized_display_name TEXT NOT NULL,
            compact_display_name TEXT NOT NULL,
            domain TEXT NOT NULL,
            primary_role TEXT,
            active_year_min INTEGER,
            active_year_max INTEGER,
            movie_count INTEGER DEFAULT 0,
            youtube_movie_count INTEGER DEFAULT 0,
            aliases_json JSONB DEFAULT '[]'::jsonb,
            aliases_search_text TEXT DEFAULT '',
            compact_aliases_text TEXT DEFAULT '',
            disambiguation_label TEXT,
            search_rank INTEGER DEFAULT 0,
            indexable INTEGER DEFAULT 1,
            updated_at TEXT
        )
        """
    )
    cur.execute(
        f"""
        CREATE TABLE IF NOT EXISTS public.{qident(POSTER_CACHE_TABLE)} (
            movie_slug TEXT PRIMARY KEY,
            title TEXT,
            domain TEXT,
            release_year INTEGER,
            poster_url TEXT,
            poster_source TEXT,
            is_official_poster INTEGER DEFAULT 0,
            updated_at TEXT
        )
        """
    )
    cur.execute(
        f"""
        CREATE TABLE IF NOT EXISTS public.{qident(YOUTUBE_QUEUE_TABLE)} (
            person_slug TEXT PRIMARY KEY,
            display_name TEXT,
            domain TEXT,
            alias_count INTEGER DEFAULT 0,
            movie_count INTEGER DEFAULT 0,
            status TEXT DEFAULT 'ready',
            updated_at TEXT
        )
        """
    )
    cur.execute(
        f"""
        CREATE TABLE IF NOT EXISTS public.{qident(YOUTUBE_CANDIDATE_TABLE)} (
            id BIGSERIAL PRIMARY KEY,
            person_slug TEXT,
            display_name TEXT,
            movie_slug TEXT,
            movie_title TEXT,
            release_year INTEGER,
            language_slug TEXT,
            language_priority INTEGER DEFAULT 9,
            domain TEXT,
            youtube_video_id TEXT,
            youtube_url TEXT,
            youtube_title TEXT,
            youtube_channel TEXT,
            duration_seconds INTEGER,
            thumbnail_url TEXT,
            source_table TEXT,
            title_score INTEGER DEFAULT 0,
            alias_score INTEGER DEFAULT 0,
            year_score INTEGER DEFAULT 0,
            language_score INTEGER DEFAULT 0,
            duration_score INTEGER DEFAULT 0,
            confidence_score INTEGER DEFAULT 0,
            validation_status TEXT,
            reject_reason TEXT,
            updated_at TEXT
        )
        """
    )
    cur.execute(
        f"""
        CREATE TABLE IF NOT EXISTS public.{qident(YOUTUBE_VALIDATED_TABLE)} (
            id BIGSERIAL PRIMARY KEY,
            person_slug TEXT,
            movie_slug TEXT,
            youtube_video_id TEXT,
            confidence_score INTEGER,
            validation_status TEXT,
            payload_json JSONB,
            updated_at TEXT,
            UNIQUE(person_slug, movie_slug, youtube_video_id)
        )
        """
    )
    cur.execute(
        f"""
        CREATE TABLE IF NOT EXISTS public.{qident(YOUTUBE_REJECTED_TABLE)} (
            id BIGSERIAL PRIMARY KEY,
            person_slug TEXT,
            movie_slug TEXT,
            youtube_video_id TEXT,
            reject_reason TEXT,
            payload_json JSONB,
            updated_at TEXT
        )
        """
    )
    cur.execute(
        f"""
        CREATE TABLE IF NOT EXISTS public.{qident(YOUTUBE_CACHE_TABLE)} (
            movie_slug TEXT NOT NULL,
            youtube_video_id TEXT NOT NULL,
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

    for table in [MOVIE_MAP_TABLE, YOUTUBE_CANDIDATE_TABLE]:
        cur.execute(f"ALTER TABLE public.{qident(table)} ADD COLUMN IF NOT EXISTS language_priority INTEGER DEFAULT 9")

    for table, cols in {
        PEOPLE_TABLE: ["domain", "movie_count"],
        ALIAS_TABLE: ["compact_alias", "normalized_alias"],
        MOVIE_MAP_TABLE: ["person_slug", "movie_slug", "domain", "language_priority"],
        SEARCH_CACHE_TABLE: ["compact_display_name", "domain", "search_rank"],
        YOUTUBE_CANDIDATE_TABLE: ["person_slug", "movie_slug", "youtube_video_id", "validation_status", "language_priority"],
        YOUTUBE_CACHE_TABLE: ["person_slug", "validation_status"],
    }.items():
        for col in cols:
            cur.execute(f"CREATE INDEX IF NOT EXISTS idx_{table}_{col} ON public.{qident(table)} ({qident(col)})")


def seed_tier1_wiki_people(cur) -> int:
    rows = []
    alias_rows = []
    seen_alias_rows = set()
    for person in TIER1_PEOPLE:
        rows.append(
            (
                person["person_slug"],
                person["display_name"],
                person["domain"],
                person["primary_role"],
                person["active_year_min"],
                person["active_year_max"],
                person["wiki_url"],
                json.dumps(sorted(aliases_for_name(person["display_name"], person["aliases"]))),
                person["disambiguation_label"],
                "tier1_manual_wiki_seed",
                now_iso(),
            )
        )
        for alias in aliases_for_name(person["display_name"], person["aliases"]):
            alias_key = (person["person_slug"], normalize_text(alias))
            if alias_key in seen_alias_rows:
                continue
            seen_alias_rows.add(alias_key)
            alias_rows.append((person["person_slug"], alias, normalize_text(alias), compact_text(alias), "tier1_manual_wiki_seed", now_iso()))

    psycopg2.extras.execute_values(
        cur,
        f"""
        INSERT INTO public.{qident(WIKI_PEOPLE_TABLE)} (
            person_slug, display_name, domain, primary_role, active_year_min, active_year_max,
            wiki_url, aliases_json, disambiguation_label, source, updated_at
        )
        VALUES %s
        ON CONFLICT (person_slug) DO UPDATE SET
            display_name=EXCLUDED.display_name,
            domain=EXCLUDED.domain,
            primary_role=EXCLUDED.primary_role,
            active_year_min=EXCLUDED.active_year_min,
            active_year_max=EXCLUDED.active_year_max,
            wiki_url=EXCLUDED.wiki_url,
            aliases_json=EXCLUDED.aliases_json,
            disambiguation_label=EXCLUDED.disambiguation_label,
            source=EXCLUDED.source,
            updated_at=EXCLUDED.updated_at
        """,
        rows,
    )
    psycopg2.extras.execute_values(
        cur,
        f"""
        INSERT INTO public.{qident(WIKI_ALIAS_TABLE)} (
            person_slug, alias, normalized_alias, compact_alias, source, updated_at
        )
        VALUES %s
        ON CONFLICT (person_slug, normalized_alias) DO UPDATE SET
            alias=EXCLUDED.alias,
            compact_alias=EXCLUDED.compact_alias,
            source=EXCLUDED.source,
            updated_at=EXCLUDED.updated_at
        """,
        alias_rows,
    )
    return len(rows)


def import_local_historical_wiki_sources(cur) -> dict[str, int]:
    if not LOCAL_HISTORICAL_DB.exists():
        return {"local_wiki_available": 0, "local_wiki_movies": 0, "local_wiki_people": 0, "local_wiki_filmography": 0}

    conn = sqlite3.connect(str(LOCAL_HISTORICAL_DB))
    conn.row_factory = sqlite3.Row
    try:
        rows = [
            dict(row)
            for row in conn.execute(
                """
                SELECT
                    slug,
                    title,
                    release_year,
                    language_slug,
                    source_page,
                    poster_url,
                    director,
                    hero,
                    heroine,
                    [cast]
                FROM historical_wiki_serving_v1
                WHERE title IS NOT NULL
                  AND slug IS NOT NULL
                """
            )
        ]
    finally:
        conn.close()

    movie_values = []
    people: dict[str, dict[str, Any]] = {}
    filmography: dict[tuple[str, str, str], tuple] = {}

    for row in rows:
        movie_slug = row.get("slug") or slugify(f"{row.get('title')} {row.get('release_year') or ''} {row.get('language_slug') or ''}")
        title = row.get("title")
        if not movie_slug or not title:
            continue
        movie_values.append(
            (
                movie_slug,
                title,
                safe_int(row.get("release_year")),
                row.get("language_slug"),
                row.get("source_page"),
                row.get("poster_url"),
                "local_historical_wiki_serving_v1",
                now_iso(),
            )
        )

        role_people = []
        for name in split_people_value(row.get("cast"))[:20]:
            role_people.append((name, "actor"))
        for name in split_people_value(row.get("hero"))[:4]:
            role_people.append((name, "actor"))
        for name in split_people_value(row.get("heroine"))[:4]:
            role_people.append((name, "actor"))
        for name in split_people_value(row.get("director"))[:6]:
            role_people.append((name, "director"))

        for person_name, role_type in role_people:
            default_slug = slugify(person_name)
            person_slug = canonical_slug_override(person_name, WIKI_FILMOGRAPHY_TABLE, default_slug)
            if not person_slug:
                continue
            bucket = people.setdefault(
                person_slug,
                {
                    "display_name": person_name,
                    "active_year_min": safe_int(row.get("release_year")),
                    "active_year_max": safe_int(row.get("release_year")),
                    "aliases": set(),
                },
            )
            year = safe_int(row.get("release_year"))
            if year is not None:
                bucket["active_year_min"] = year if bucket["active_year_min"] is None else min(bucket["active_year_min"], year)
                bucket["active_year_max"] = year if bucket["active_year_max"] is None else max(bucket["active_year_max"], year)
            bucket["aliases"].update(aliases_for_name(person_name))

            key = (person_slug, movie_slug, role_type)
            filmography[key] = (
                person_slug,
                person_name,
                movie_slug,
                title,
                safe_int(row.get("release_year")),
                row.get("language_slug"),
                role_type,
                "local_historical_wiki_serving_v1",
                100,
                now_iso(),
            )

    if movie_values:
        movie_values = list({row[0]: row for row in movie_values}.values())
        psycopg2.extras.execute_values(
            cur,
            f"""
            INSERT INTO public.{qident(WIKI_MOVIE_TABLE)} (
                movie_slug, title, release_year, language_slug, wiki_url, poster_url, source, updated_at
            )
            VALUES %s
            ON CONFLICT (movie_slug) DO UPDATE SET
                title=EXCLUDED.title,
                release_year=EXCLUDED.release_year,
                language_slug=EXCLUDED.language_slug,
                wiki_url=EXCLUDED.wiki_url,
                poster_url=COALESCE(public.{qident(WIKI_MOVIE_TABLE)}.poster_url, EXCLUDED.poster_url),
                source=EXCLUDED.source,
                updated_at=EXCLUDED.updated_at
            """,
            movie_values,
            page_size=10000,
        )

    if people:
        people_values = [
            (
                slug,
                data["display_name"],
                "historical",
                "actor",
                data.get("active_year_min"),
                data.get("active_year_max"),
                None,
                json.dumps(sorted(data["aliases"])),
                None,
                "local_historical_wiki_serving_v1",
                now_iso(),
            )
            for slug, data in people.items()
        ]
        psycopg2.extras.execute_values(
            cur,
            f"""
            INSERT INTO public.{qident(WIKI_PEOPLE_TABLE)} (
                person_slug, display_name, domain, primary_role, active_year_min, active_year_max,
                wiki_url, aliases_json, disambiguation_label, source, updated_at
            )
            VALUES %s
            ON CONFLICT (person_slug) DO UPDATE SET
                display_name=COALESCE(public.{qident(WIKI_PEOPLE_TABLE)}.display_name, EXCLUDED.display_name),
                active_year_min=LEAST(COALESCE(public.{qident(WIKI_PEOPLE_TABLE)}.active_year_min, EXCLUDED.active_year_min), COALESCE(EXCLUDED.active_year_min, public.{qident(WIKI_PEOPLE_TABLE)}.active_year_min)),
                active_year_max=GREATEST(COALESCE(public.{qident(WIKI_PEOPLE_TABLE)}.active_year_max, EXCLUDED.active_year_max), COALESCE(EXCLUDED.active_year_max, public.{qident(WIKI_PEOPLE_TABLE)}.active_year_max)),
                aliases_json=(
                    SELECT jsonb_agg(DISTINCT value)
                    FROM jsonb_array_elements_text(public.{qident(WIKI_PEOPLE_TABLE)}.aliases_json || EXCLUDED.aliases_json) AS value
                ),
                source=EXCLUDED.source,
                updated_at=EXCLUDED.updated_at
            """,
            people_values,
            page_size=10000,
        )

    if filmography:
        psycopg2.extras.execute_values(
            cur,
            f"""
            INSERT INTO public.{qident(WIKI_FILMOGRAPHY_TABLE)} (
                person_slug, person_name, movie_slug, title, release_year, language_slug,
                role_type, source, confidence, updated_at
            )
            VALUES %s
            ON CONFLICT (person_slug, movie_slug, role_type) DO UPDATE SET
                person_name=EXCLUDED.person_name,
                title=EXCLUDED.title,
                release_year=EXCLUDED.release_year,
                language_slug=EXCLUDED.language_slug,
                confidence=GREATEST(public.{qident(WIKI_FILMOGRAPHY_TABLE)}.confidence, EXCLUDED.confidence),
                source=EXCLUDED.source,
                updated_at=EXCLUDED.updated_at
            """,
            list(filmography.values()),
            page_size=10000,
        )

    return {
        "local_wiki_available": 1,
        "local_wiki_movies": len(movie_values),
        "local_wiki_people": len(people),
        "local_wiki_filmography": len(filmography),
    }


def select_existing(cur, table: str, preferred: list[str]) -> list[dict[str, Any]]:
    if not table_exists(cur, table):
        return []
    cols = table_columns(cur, table)
    selected = [col for col in preferred if col in cols]
    if not selected:
        return []
    cur.execute(f"SELECT {', '.join(qident(col) for col in selected)} FROM public.{qident(table)}")
    return [dict(row) for row in cur.fetchall()]


def load_people_sources(cur) -> dict[str, dict[str, Any]]:
    people: dict[str, dict[str, Any]] = {}

    def merge(row: dict[str, Any], source: str, default_domain: str) -> None:
        display_name = first(row, ["display_name", "person_name", "name"])
        person_slug = first(row, ["person_slug", "slug"]) or slugify(display_name)
        person_slug = canonical_slug_override(display_name, source, person_slug)
        if not display_name or not person_slug:
            return

        bucket = people.setdefault(
            person_slug,
            {
                "person_slug": person_slug,
                "display_name": display_name,
                "domain": row.get("domain") or default_domain,
                "primary_role": first(row, ["primary_role", "role_type"], "actor"),
                "active_year_min": safe_int(row.get("active_year_min")),
                "active_year_max": safe_int(row.get("active_year_max") or row.get("last_year")),
                "movie_count": 0,
                "youtube_movie_count": 0,
                "disambiguation_label": row.get("disambiguation_label"),
                "aliases": set(),
                "source_tables": set(),
            },
        )
        existing_sources = bucket["source_tables"]
        modern_is_primary = MODERN_PEOPLE_SOURCE in existing_sources or source == MODERN_PEOPLE_SOURCE
        wiki_is_validation_only = source == WIKI_PEOPLE_TABLE and bucket.get("domain") == "modern"

        if source == MODERN_PEOPLE_SOURCE:
            bucket["display_name"] = display_name
            bucket["domain"] = "modern"
            bucket["primary_role"] = first(row, ["primary_role", "role_type"], bucket.get("primary_role"))
        elif source == HISTORICAL_PEOPLE_SOURCE and not modern_is_primary:
            bucket["display_name"] = display_name
            bucket["domain"] = "historical"
            bucket["primary_role"] = first(row, ["primary_role", "role_type"], bucket.get("primary_role"))
        elif not wiki_is_validation_only:
            bucket["display_name"] = bucket["display_name"] or display_name
            bucket["domain"] = row.get("domain") or bucket["domain"] or default_domain
            bucket["primary_role"] = first(row, ["primary_role", "role_type"], bucket.get("primary_role"))

        if source != WIKI_PEOPLE_TABLE or not modern_is_primary:
            bucket["movie_count"] = max(safe_int(bucket.get("movie_count"), 0), safe_int(row.get("movie_count"), 0))
            bucket["youtube_movie_count"] = max(safe_int(bucket.get("youtube_movie_count"), 0), safe_int(row.get("youtube_movie_count"), 0))
        for year_key, reducer in [("active_year_min", min), ("active_year_max", max)]:
            existing = safe_int(bucket.get(year_key))
            incoming = safe_int(row.get(year_key) or (row.get("last_year") if year_key == "active_year_max" else None))
            if incoming is not None:
                bucket[year_key] = incoming if existing is None else reducer(existing, incoming)
        if source != WIKI_PEOPLE_TABLE or not modern_is_primary:
            bucket["disambiguation_label"] = row.get("disambiguation_label") or bucket.get("disambiguation_label")
        elif not bucket.get("disambiguation_label"):
            bucket["disambiguation_label"] = row.get("disambiguation_label")
        bucket["aliases"].update(aliases_for_name(display_name))
        for alias in row.get("aliases_json") or []:
            bucket["aliases"].update(aliases_for_name(alias))
        bucket["source_tables"].add(source)

    for row in select_existing(
        cur,
        HISTORICAL_PEOPLE_SOURCE,
        ["person_slug", "person_name", "primary_role", "movie_count", "youtube_movie_count", "last_year"],
    ):
        merge(row, HISTORICAL_PEOPLE_SOURCE, "historical")

    for row in select_existing(
        cur,
        MODERN_PEOPLE_SOURCE,
        ["person_slug", "person_name", "primary_role", "movie_count", "youtube_movie_count", "last_year"],
    ):
        merge(row, MODERN_PEOPLE_SOURCE, "modern")

    for row in select_existing(
        cur,
        WIKI_PEOPLE_TABLE,
        ["person_slug", "display_name", "domain", "primary_role", "active_year_min", "active_year_max", "aliases_json", "disambiguation_label"],
    ):
        if isinstance(row.get("aliases_json"), str):
            row["aliases_json"] = json.loads(row["aliases_json"] or "[]")
        merge(row, WIKI_PEOPLE_TABLE, row.get("domain") or "historical")

    return people


def load_movie_map_sources(cur, people: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    for table, domain in [(HISTORICAL_MOVIE_PEOPLE_SOURCE, "historical"), (MODERN_MOVIE_PEOPLE_SOURCE, "modern")]:
        for row in select_existing(
            cur,
            table,
            ["person_slug", "person_name", "movie_slug", "title", "release_year", "language_slug", "primary_language", "role_type", "has_youtube"],
        ):
            title = row.get("title")
            movie_slug = row.get("movie_slug") or slugify(f"{title} {row.get('release_year') or ''} {row.get('language_slug') or ''}")
            person_name = row.get("person_name")
            person_slug = canonical_slug_override(person_name, table, row.get("person_slug"))
            if not person_slug or not movie_slug or not title:
                continue
            rows.append(
                {
                    "person_slug": person_slug,
                    "person_name": person_name,
                    "movie_slug": movie_slug,
                    "movie_title": title,
                    "release_year": safe_int(row.get("release_year")),
                    "language_slug": row.get("language_slug") or row.get("primary_language"),
                    "language_priority": language_priority(row.get("language_slug") or row.get("primary_language")),
                    "domain": domain,
                    "role_type": row.get("role_type") or "actor",
                    "has_youtube": safe_int(row.get("has_youtube"), 0) or 0,
                    "source": table,
                    "updated_at": now_iso(),
                }
            )

    for row in select_existing(
        cur,
        WIKI_FILMOGRAPHY_TABLE,
        ["person_slug", "person_name", "movie_slug", "title", "release_year", "language_slug", "role_type", "source"],
    ):
        person_slug = canonical_slug_override(row.get("person_name") or row.get("person_slug"), WIKI_FILMOGRAPHY_TABLE, row["person_slug"])
        person_meta = people.get(person_slug, {})
        if person_meta.get("domain") == "modern":
            continue
        rows.append(
            {
                "person_slug": person_slug,
                "person_name": row.get("person_name"),
                "movie_slug": row.get("movie_slug") or slugify(f"{row.get('title')} {row.get('release_year') or ''} {row.get('language_slug') or ''}"),
                "movie_title": row.get("title"),
                "release_year": safe_int(row.get("release_year")),
                "language_slug": row.get("language_slug"),
                "language_priority": language_priority(row.get("language_slug")),
                "domain": person_meta.get("domain") or "historical",
                "role_type": row.get("role_type") or "actor",
                "has_youtube": 0,
                "source": row.get("source") or WIKI_FILMOGRAPHY_TABLE,
                "updated_at": now_iso(),
            }
        )

    return rows


def write_people_layers(cur, people: dict[str, dict[str, Any]], movie_rows: list[dict[str, Any]]) -> dict[str, int]:
    cur.execute(f"TRUNCATE public.{qident(PEOPLE_TABLE)}, public.{qident(ALIAS_TABLE)}, public.{qident(MOVIE_MAP_TABLE)}, public.{qident(SEARCH_CACHE_TABLE)}, public.{qident(YOUTUBE_QUEUE_TABLE)}")

    merged_movie_rows: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in movie_rows:
        key = (row.get("person_slug"), row.get("movie_slug"), row.get("role_type") or "actor")
        if not all(key):
            continue
        existing = merged_movie_rows.get(key)
        if not existing:
            merged_movie_rows[key] = dict(row)
            continue
        existing["has_youtube"] = max(safe_int(existing.get("has_youtube"), 0), safe_int(row.get("has_youtube"), 0))
        existing["language_priority"] = min(
            safe_int(existing.get("language_priority"), language_priority(existing.get("language_slug"))),
            safe_int(row.get("language_priority"), language_priority(row.get("language_slug"))),
        )
        for col in ["person_name", "movie_title", "release_year", "language_slug", "domain", "source", "updated_at"]:
            existing[col] = existing.get(col) or row.get(col)
    movie_rows = list(merged_movie_rows.values())

    by_person_movies: dict[str, set[str]] = defaultdict(set)
    by_person_youtube: dict[str, set[str]] = defaultdict(set)
    for row in movie_rows:
        by_person_movies[row["person_slug"]].add(row["movie_slug"])
        if safe_int(row.get("has_youtube"), 0):
            by_person_youtube[row["person_slug"]].add(row["movie_slug"])

    people_values = []
    alias_values = []
    cache_values = []
    queue_values = []
    for slug, row in sorted(people.items()):
        locked = TIER1_PEOPLE_BY_SLUG.get(slug)
        if locked:
            row["display_name"] = locked["display_name"]
            row["domain"] = locked["domain"]
            row["primary_role"] = locked["primary_role"]
            row["active_year_min"] = locked["active_year_min"]
            row["active_year_max"] = locked["active_year_max"]
            row["disambiguation_label"] = locked["disambiguation_label"]
            row["aliases"].update(aliases_for_name(locked["display_name"], locked["aliases"]))
            row["source_tables"].add("tier1_manual_identity_lock")
        movie_count = max(safe_int(row.get("movie_count"), 0), len(by_person_movies.get(slug, set())))
        youtube_count = max(safe_int(row.get("youtube_movie_count"), 0), len(by_person_youtube.get(slug, set())))
        aliases = sorted(alias for alias in row["aliases"] if alias)
        source_tables = sorted(row["source_tables"])
        search_rank = (100000 if locked else 0) + youtube_count * 1000 + movie_count

        people_values.append(
            (
                slug,
                row["display_name"],
                row["domain"],
                row.get("primary_role"),
                row.get("active_year_min"),
                row.get("active_year_max"),
                movie_count,
                youtube_count,
                row.get("disambiguation_label"),
                json.dumps(source_tables),
                now_iso(),
            )
        )
        for alias in aliases:
            alias_values.append((slug, alias, normalize_text(alias), compact_text(alias), "canonical_builder", 100 if normalize_text(alias) == normalize_text(row["display_name"]) else 50, now_iso()))
        compact_aliases = "|" + "|".join(sorted({compact_text(alias) for alias in aliases if compact_text(alias)})) + "|"
        cache_values.append(
            (
                slug,
                row["display_name"],
                normalize_text(row["display_name"]),
                compact_text(row["display_name"]),
                row["domain"],
                row.get("primary_role"),
                row.get("active_year_min"),
                row.get("active_year_max"),
                movie_count,
                youtube_count,
                json.dumps(aliases),
                " ".join(sorted({normalize_text(alias) for alias in aliases if normalize_text(alias)})),
                compact_aliases,
                row.get("disambiguation_label"),
                search_rank,
                1,
                now_iso(),
            )
        )
        queue_values.append((slug, row["display_name"], row["domain"], len(aliases), movie_count, "ready", now_iso()))

    psycopg2.extras.execute_values(
        cur,
        f"""
        INSERT INTO public.{qident(PEOPLE_TABLE)} (
            person_slug, display_name, domain, primary_role, active_year_min, active_year_max,
            movie_count, youtube_movie_count, disambiguation_label, source_tables, updated_at
        )
        VALUES %s
        """,
        people_values,
        page_size=5000,
    )
    if alias_values:
        psycopg2.extras.execute_values(
            cur,
            f"""
            INSERT INTO public.{qident(ALIAS_TABLE)} (
                person_slug, alias, normalized_alias, compact_alias, source, priority, updated_at
            )
            VALUES %s
            ON CONFLICT (person_slug, normalized_alias) DO NOTHING
            """,
            alias_values,
            page_size=10000,
        )
    if movie_rows:
        psycopg2.extras.execute_values(
            cur,
            f"""
            INSERT INTO public.{qident(MOVIE_MAP_TABLE)} (
                person_slug, person_name, movie_slug, movie_title, release_year, language_slug,
                language_priority, domain, role_type, has_youtube, source, updated_at
            )
            VALUES %s
            ON CONFLICT (person_slug, movie_slug, role_type) DO UPDATE SET
                has_youtube=GREATEST(public.{qident(MOVIE_MAP_TABLE)}.has_youtube, EXCLUDED.has_youtube),
                updated_at=EXCLUDED.updated_at
            """,
            [
                (
                    row["person_slug"],
                    row.get("person_name"),
                    row["movie_slug"],
                    row["movie_title"],
                    row.get("release_year"),
                    row.get("language_slug"),
                    row.get("language_priority", language_priority(row.get("language_slug"))),
                    row.get("domain"),
                    row.get("role_type"),
                    row.get("has_youtube"),
                    row.get("source"),
                    row.get("updated_at"),
                )
                for row in movie_rows
                if row.get("person_slug") in people
            ],
            page_size=10000,
        )
    psycopg2.extras.execute_values(
        cur,
        f"""
        INSERT INTO public.{qident(SEARCH_CACHE_TABLE)} (
            person_slug, display_name, normalized_display_name, compact_display_name, domain,
            primary_role, active_year_min, active_year_max, movie_count, youtube_movie_count,
            aliases_json, aliases_search_text, compact_aliases_text, disambiguation_label,
            search_rank, indexable, updated_at
        )
        VALUES %s
        """,
        cache_values,
        page_size=5000,
    )
    psycopg2.extras.execute_values(
        cur,
        f"""
        INSERT INTO public.{qident(YOUTUBE_QUEUE_TABLE)} (
            person_slug, display_name, domain, alias_count, movie_count, status, updated_at
        )
        VALUES %s
        """,
        queue_values,
        page_size=5000,
    )
    return {
        "people_rows": len(people_values),
        "alias_rows": len(alias_values),
        "movie_map_rows": len(movie_rows),
        "search_cache_rows": len(cache_values),
        "queue_rows": len(queue_values),
    }


def youtube_video_id(url: Any) -> str:
    text = str(url or "")
    match = re.search(r"[?&]v=([^&#]+)", text)
    if match:
        return match.group(1)
    match = re.search(r"youtu\.be/([^?&#/]+)", text)
    if match:
        return match.group(1)
    return ""


def has_bad_youtube_term(title: str) -> str:
    blob = f" {normalize_text(title)} "
    for term in BAD_YOUTUBE_TERMS:
        normalized = normalize_text(term)
        if normalized and f" {normalized} " in blob:
            return term
    return ""


def load_youtube_rows(cur, max_videos: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    preferred = [
        "movie_slug",
        "title",
        "movie_title",
        "release_year",
        "year",
        "language_slug",
        "catalog_language",
        "youtube_video_id",
        "video_id",
        "youtube_url",
        "url",
        "final_url",
        "youtube_title",
        "video_title",
        "youtube_channel",
        "channel_title",
        "channel_name",
        "duration_seconds",
        "youtube_duration_seconds",
        "thumbnail_url",
        "youtube_thumbnail_url",
    ]
    per_table_limit = max(1000, max_videos // max(1, len(YOUTUBE_SOURCE_TABLES))) if max_videos else 0
    for table in YOUTUBE_SOURCE_TABLES:
        if not table_exists(cur, table):
            continue
        cols = table_columns(cur, table)
        selected = [col for col in preferred if col in cols]
        if not selected:
            continue
        where = []
        if "youtube_url" in cols:
            where.append("youtube_url IS NOT NULL")
        elif "url" in cols:
            where.append("url IS NOT NULL")
        elif "final_url" in cols:
            where.append("final_url IS NOT NULL")
        if "provider_name" in cols:
            where.append("(provider_name IS NULL OR provider_name ILIKE '%%youtube%%')")
        sql = f"SELECT {', '.join(qident(col) for col in selected)} FROM public.{qident(table)}"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " LIMIT %s"
        cur.execute(sql, [per_table_limit])
        for row in cur.fetchall():
            item = dict(row)
            item["_source_table"] = table
            rows.append(item)
    return rows[:max_videos] if max_videos else rows


def score_youtube_match(video: dict[str, Any], movie: dict[str, Any], aliases: set[str]) -> tuple[int, dict[str, int], str]:
    video_title = first(video, ["youtube_title", "video_title", "title", "movie_title"], "")
    video_text = normalize_text(f"{video_title} {first(video, ['youtube_channel', 'channel_title', 'channel_name'], '')}")
    movie_title = movie["movie_title"]
    movie_norm = normalize_text(movie_title)
    title_score = 0
    if movie_norm and video_text == movie_norm:
        title_score = 50
    elif movie_norm and f" {movie_norm} " in f" {video_text} ":
        title_score = 45
    elif movie_norm and all(token in video_text.split() for token in movie_norm.split()[:3]):
        title_score = 30

    alias_score = 0
    compact_video = compact_text(video_text)
    for alias in aliases:
        compact_alias = compact_text(alias)
        if len(compact_alias) >= 3 and compact_alias in compact_video:
            alias_score = 10
            break

    video_year = safe_int(first(video, ["release_year", "year"]))
    movie_year = safe_int(movie.get("release_year"))
    year_score = 0
    if movie_year and video_year:
        year_score = 10 if movie_year == video_year else (5 if abs(movie_year - video_year) <= 1 else 0)
    elif movie_year:
        year_score = 3

    video_lang = normalize_text(first(video, ["language_slug", "catalog_language"], ""))
    movie_lang = normalize_text(movie.get("language_slug") or "")
    language_score = 5 if video_lang and movie_lang and video_lang == movie_lang else 0

    duration = safe_int(first(video, ["duration_seconds", "youtube_duration_seconds"]))
    duration_score = 0
    if duration is not None:
        duration_score = 10 if 6000 <= duration <= 15000 else (5 if 3600 <= duration <= 18000 else 0)

    bad = has_bad_youtube_term(video_title)
    reject_reason = ""
    if bad:
        reject_reason = f"bad_title_term:{bad}"
    elif duration is not None and duration < 3600:
        reject_reason = f"duration_too_short:{duration}"
    elif title_score < 30:
        reject_reason = "weak_title_match"

    score = title_score + alias_score + year_score + language_score + duration_score
    return score, {
        "title_score": title_score,
        "alias_score": alias_score,
        "year_score": year_score,
        "language_score": language_score,
        "duration_score": duration_score,
    }, reject_reason


def build_youtube_matches(cur, max_videos: int, max_candidates: int) -> dict[str, int]:
    cur.execute(f"TRUNCATE public.{qident(YOUTUBE_CANDIDATE_TABLE)}, public.{qident(YOUTUBE_VALIDATED_TABLE)}, public.{qident(YOUTUBE_REJECTED_TABLE)}, public.{qident(YOUTUBE_CACHE_TABLE)}")

    cur.execute(
        f"""
        SELECT *
        FROM public.{qident(MOVIE_MAP_TABLE)}
        WHERE role_type IN ('actor', 'actress', 'lead', 'person')
        ORDER BY language_priority ASC, release_year DESC NULLS LAST
        """
    )
    movies = [dict(row) for row in cur.fetchall()]
    cur.execute(f"SELECT person_slug, alias FROM public.{qident(ALIAS_TABLE)}")
    aliases_by_person: dict[str, set[str]] = defaultdict(set)
    for row in cur.fetchall():
        aliases_by_person[row["person_slug"]].add(row["alias"])

    title_index: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for movie in movies:
        tokens = normalize_text(movie.get("movie_title") or "").split()
        if tokens:
            title_index[tokens[0]].append(movie)

    candidate_values = []
    validated_values = []
    rejected_values = []
    cache_values = []
    seen = set()
    for video in load_youtube_rows(cur, max_videos):
        if len(candidate_values) >= max_candidates:
            break
        url = first(video, ["youtube_url", "url", "final_url"], "")
        video_id = first(video, ["youtube_video_id", "video_id"]) or youtube_video_id(url)
        if not video_id:
            continue
        video_title = first(video, ["youtube_title", "video_title", "title", "movie_title"], "")
        tokens = normalize_text(video_title).split()
        candidate_movies: list[dict[str, Any]] = []
        for token in tokens[:8]:
            candidate_movies.extend(title_index.get(token, []))
        for movie in candidate_movies[:80]:
            if len(candidate_values) >= max_candidates:
                break
            key = (movie["person_slug"], movie["movie_slug"], video_id)
            if key in seen:
                continue
            seen.add(key)
            score, parts, reject_reason = score_youtube_match(video, movie, aliases_by_person[movie["person_slug"]])
            status = "AUTO_SAFE" if score >= 75 and not reject_reason and parts["title_score"] >= 30 else ("REVIEW" if score >= 55 and not reject_reason else "REJECT")
            payload = {
                "person_slug": movie["person_slug"],
                "movie_slug": movie["movie_slug"],
                "movie_title": movie["movie_title"],
                "youtube_video_id": video_id,
                "youtube_url": url,
                "youtube_title": video_title,
                "source_table": video["_source_table"],
                "confidence_score": score,
                "validation_status": status,
                "reject_reason": reject_reason,
            }
            row_tuple = (
                movie["person_slug"],
                movie.get("person_name"),
                movie["movie_slug"],
                movie["movie_title"],
                movie.get("release_year"),
                movie.get("language_slug"),
                movie.get("language_priority", language_priority(movie.get("language_slug"))),
                movie.get("domain"),
                video_id,
                url,
                video_title,
                first(video, ["youtube_channel", "channel_title", "channel_name"], ""),
                safe_int(first(video, ["duration_seconds", "youtube_duration_seconds"])),
                first(video, ["thumbnail_url", "youtube_thumbnail_url"], ""),
                video["_source_table"],
                parts["title_score"],
                parts["alias_score"],
                parts["year_score"],
                parts["language_score"],
                parts["duration_score"],
                score,
                status,
                reject_reason,
                now_iso(),
            )
            candidate_values.append(row_tuple)
            if status == "AUTO_SAFE":
                validated_values.append((movie["person_slug"], movie["movie_slug"], video_id, score, status, json.dumps(payload), now_iso()))
                cache_values.append((movie["movie_slug"], video_id, movie["person_slug"], url, video_title, score, status, now_iso()))
            elif status == "REJECT":
                rejected_values.append((movie["person_slug"], movie["movie_slug"], video_id, reject_reason, json.dumps(payload), now_iso()))

    if validated_values:
        deduped_validated = {}
        for row in validated_values:
            key = (row[0], row[1], row[2])
            if key not in deduped_validated or safe_int(row[3], 0) > safe_int(deduped_validated[key][3], 0):
                deduped_validated[key] = row
        validated_values = list(deduped_validated.values())

    if cache_values:
        deduped_cache = {}
        for row in cache_values:
            key = (row[0], row[1])
            if key not in deduped_cache or safe_int(row[5], 0) > safe_int(deduped_cache[key][5], 0):
                deduped_cache[key] = row
        cache_values = list(deduped_cache.values())

    if candidate_values:
        psycopg2.extras.execute_values(
            cur,
            f"""
            INSERT INTO public.{qident(YOUTUBE_CANDIDATE_TABLE)} (
                person_slug, display_name, movie_slug, movie_title, release_year, language_slug,
                language_priority, domain, youtube_video_id, youtube_url, youtube_title, youtube_channel, duration_seconds,
                thumbnail_url, source_table, title_score, alias_score, year_score, language_score,
                duration_score, confidence_score, validation_status, reject_reason, updated_at
            )
            VALUES %s
            """,
            candidate_values,
            page_size=10000,
        )
    if validated_values:
        psycopg2.extras.execute_values(
            cur,
            f"""
            INSERT INTO public.{qident(YOUTUBE_VALIDATED_TABLE)} (
                person_slug, movie_slug, youtube_video_id, confidence_score, validation_status, payload_json, updated_at
            )
            VALUES %s
            ON CONFLICT (person_slug, movie_slug, youtube_video_id) DO NOTHING
            """,
            validated_values,
            page_size=10000,
        )
    if rejected_values:
        psycopg2.extras.execute_values(
            cur,
            f"""
            INSERT INTO public.{qident(YOUTUBE_REJECTED_TABLE)} (
                person_slug, movie_slug, youtube_video_id, reject_reason, payload_json, updated_at
            )
            VALUES %s
            """,
            rejected_values,
            page_size=10000,
        )
    if cache_values:
        psycopg2.extras.execute_values(
            cur,
            f"""
            INSERT INTO public.{qident(YOUTUBE_CACHE_TABLE)} (
                movie_slug, youtube_video_id, person_slug, youtube_url, youtube_title, confidence_score, validation_status, updated_at
            )
            VALUES %s
            ON CONFLICT (movie_slug, youtube_video_id) DO UPDATE SET
                confidence_score=GREATEST(public.{qident(YOUTUBE_CACHE_TABLE)}.confidence_score, EXCLUDED.confidence_score),
                validation_status=EXCLUDED.validation_status,
                updated_at=EXCLUDED.updated_at
            """,
            cache_values,
            page_size=10000,
        )
        cur.execute(
            f"""
            UPDATE public.{qident(MOVIE_MAP_TABLE)} mp
            SET has_youtube=1, updated_at=%s
            WHERE EXISTS (
                SELECT 1
                FROM public.{qident(YOUTUBE_CACHE_TABLE)} yc
                WHERE yc.movie_slug=mp.movie_slug
                  AND yc.person_slug=mp.person_slug
                  AND yc.validation_status='AUTO_SAFE'
            )
            """,
            [now_iso()],
        )

    return {
        "youtube_candidates": len(candidate_values),
        "youtube_auto_safe": len(validated_values),
        "youtube_rejected": len(rejected_values),
        "youtube_cache_rows": len(cache_values),
    }


def build_poster_cache(cur) -> int:
    cur.execute(f"TRUNCATE public.{qident(POSTER_CACHE_TABLE)}")
    values = []

    def add_from_table(table: str, domain: str, source: str, official: int, title_cols: list[str], poster_cols: list[str]) -> None:
        if not table_exists(cur, table):
            return
        cols = table_columns(cur, table)
        selected = [col for col in ["slug", "movie_slug", "title", "movie_title", "release_year", "year", "poster_url", "poster_path", "thumbnail_url", "youtube_thumbnail_url"] if col in cols]
        if not selected:
            return
        cur.execute(f"SELECT {', '.join(qident(col) for col in selected)} FROM public.{qident(table)} LIMIT 250000")
        for row in cur.fetchall():
            data = dict(row)
            movie_slug = first(data, ["movie_slug", "slug"])
            poster = first(data, poster_cols)
            title = first(data, title_cols)
            if not movie_slug or not poster:
                continue
            values.append((movie_slug, title, domain, safe_int(first(data, ["release_year", "year"])), poster, source, official, now_iso()))

    add_from_table("historical_card_serving_v1", "historical", "historical_card_serving_v1", 1, ["title"], ["poster_url", "poster_path"])
    add_from_table("media_serving_v8_expanded", "modern", "media_serving_v8_expanded", 1, ["title"], ["poster_url", "poster_path"])
    add_from_table(WIKI_MOVIE_TABLE, "historical", WIKI_MOVIE_TABLE, 1, ["title"], ["poster_url"])
    for table in YOUTUBE_SOURCE_TABLES:
        add_from_table(table, "unknown", table, 0, ["title", "movie_title", "youtube_title", "video_title"], ["thumbnail_url", "youtube_thumbnail_url"])

    if not values:
        return 0
    priority = {"historical_card_serving_v1": 1, "media_serving_v8_expanded": 1, WIKI_MOVIE_TABLE: 2}
    values.sort(key=lambda row: (row[0], priority.get(row[5], 9), -safe_int(row[6], 0)))
    deduped = {}
    for row in values:
        deduped.setdefault(row[0], row)

    psycopg2.extras.execute_values(
        cur,
        f"""
        INSERT INTO public.{qident(POSTER_CACHE_TABLE)} (
            movie_slug, title, domain, release_year, poster_url, poster_source, is_official_poster, updated_at
        )
        VALUES %s
        ON CONFLICT (movie_slug) DO UPDATE SET
            title=EXCLUDED.title,
            domain=EXCLUDED.domain,
            release_year=EXCLUDED.release_year,
            poster_url=EXCLUDED.poster_url,
            poster_source=EXCLUDED.poster_source,
            is_official_poster=EXCLUDED.is_official_poster,
            updated_at=EXCLUDED.updated_at
        """,
        list(deduped.values()),
        page_size=10000,
    )
    return len(deduped)


def audit_public_people(cur) -> dict[str, list[str]]:
    test_queries = [
        "ntr",
        "n t rama rao",
        "sr ntr",
        "jr ntr",
        "ntr jr",
        "balakrishna",
        "nandamuri balakrishna",
        "nbk",
        "mahesh babu",
        "ghattamaneni mahesh babu",
        "anr",
        "nageswara rao",
        "akkineni nageswara rao",
        "krishna",
        "superstar krishna",
        "krishnam raju",
        "shoban babu",
        "sobhan babu",
        "amitabh bachchan",
        "rajinikanth",
        "mgr",
        "m g ramachandran",
        "jayalalithaa",
        "jayalalitha",
        "kamal haasan",
        "chiranjeevi",
        "mohanlal",
        "mammootty",
        "rajkumar",
        "vishnuvardhan",
    ]
    out = {}
    for query in test_queries:
        compact = compact_text(query)
        cur.execute(
            f"""
            SELECT display_name, person_slug, domain
            FROM public.{qident(SEARCH_CACHE_TABLE)}
            WHERE compact_aliases_text LIKE %s
               OR compact_display_name LIKE %s
               OR aliases_search_text ILIKE %s
            ORDER BY search_rank DESC, display_name ASC
            LIMIT 5
            """,
            [f"%|{compact}|%", f"{compact}%", f"%{normalize_text(query)}%"],
        )
        out[query] = [f"{row['display_name']} ({row['domain']})" for row in cur.fetchall()]
    return out


def write_report(report: dict[str, Any]) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORT_DIR / f"people_youtube_enrichment_master_{now_stamp()}.json"
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description="Build canonical people, wiki source, poster, and actor-first YouTube enrichment tables.")
    parser.add_argument("--apply", action="store_true", help="Commit DB writes. Without this, the transaction is rolled back.")
    parser.add_argument("--max-videos", type=int, default=50000, help="Maximum crawled YouTube rows to consider across known source tables.")
    parser.add_argument("--max-candidates", type=int, default=20000, help="Maximum YouTube candidate rows to stage in Postgres per run.")
    parser.add_argument("--skip-youtube", action="store_true", help="Only build wiki/person/search/poster layers.")
    args = parser.parse_args()

    con = psycopg2.connect(database_url(), cursor_factory=psycopg2.extras.RealDictCursor)
    con.autocommit = False
    report: dict[str, Any] = {"created_at": now_iso(), "mode": "apply" if args.apply else "dry_run"}

    try:
        with con.cursor() as cur:
            create_tables(cur)
            report["tier1_wiki_seed_rows"] = seed_tier1_wiki_people(cur)
            report.update(import_local_historical_wiki_sources(cur))
            people = load_people_sources(cur)
            movie_rows = load_movie_map_sources(cur, people)
            report.update(write_people_layers(cur, people, movie_rows))
            if not args.skip_youtube:
                report.update(build_youtube_matches(cur, args.max_videos, args.max_candidates))
                people = load_people_sources(cur)
                movie_rows = select_existing(
                    cur,
                    MOVIE_MAP_TABLE,
                    ["person_slug", "person_name", "movie_slug", "movie_title", "release_year", "language_slug", "language_priority", "domain", "role_type", "has_youtube"],
                )
                report.update({f"refreshed_{k}": v for k, v in write_people_layers(cur, people, movie_rows).items()})
            report["poster_cache_rows"] = build_poster_cache(cur)
            report["public_people_audit"] = audit_public_people(cur)

        if args.apply:
            con.commit()
            report["status"] = "APPLIED"
        else:
            con.rollback()
            report["status"] = "DRY_RUN_ROLLED_BACK"

        report_path = write_report(report)
        print(json.dumps(report, indent=2, ensure_ascii=False, default=str))
        print(f"REPORT: {report_path}")
        return 0
    except Exception as exc:
        con.rollback()
        report["status"] = "FAILED"
        report["error"] = str(exc)
        report["traceback"] = traceback.format_exc()
        report_path = write_report(report)
        print(json.dumps(report, indent=2, ensure_ascii=False, default=str), file=sys.stderr)
        print(f"REPORT: {report_path}", file=sys.stderr)
        return 1
    finally:
        con.close()


if __name__ == "__main__":
    raise SystemExit(main())
