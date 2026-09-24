"""Read-only V1.3 person/search intelligence sidecar."""

from __future__ import annotations

import os
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class PersonSearch:
    query: str
    normalized_alias: str
    provider: str | None
    person_ids: tuple[str, ...]
    person_names: tuple[str, ...]
    canonical_movie_ids: tuple[str, ...]
    ambiguous: bool


def _sidecar_path() -> str | None:
    value = os.environ.get("FLIXYFY_SEARCH_SIDECAR_DB")
    return value.strip() if value and value.strip() else None


def _connect() -> sqlite3.Connection | None:
    path = _sidecar_path()
    if not path or not Path(path).is_file():
        return None
    timeout = max(0.1, float(os.environ.get("FLIXYFY_SQLITE_QUERY_TIMEOUT_MS", "30000")) / 1000)
    connection = sqlite3.connect(f"file:{Path(path).resolve().as_posix()}?mode=ro", uri=True, timeout=timeout)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    return connection


def _compact(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.casefold())


def _parse_query(query: str, provider: str | None) -> tuple[str, str | None]:
    value = re.sub(r"[^a-z0-9]+", " ", query.casefold()).strip()
    selected = (provider or "").strip().casefold().replace("-", " ") or None
    if selected in {"prime", "prime video", "amazon prime"}:
        selected = "prime_video"
    for phrase, key in (("prime video", "prime_video"), ("amazon prime", "prime_video"), ("prime", "prime_video"), ("netflix", "netflix"), ("youtube", "youtube")):
        if re.search(rf"(?:^|\s)(?:on|in)\s+{re.escape(phrase)}\s*$", value):
            value = re.sub(rf"(?:^|\s)(?:on|in)\s+{re.escape(phrase)}\s*$", "", value).strip()
            selected = key
            break
    value = re.sub(r"\s+(?:movies?|films?)\s*$", "", value).strip()
    return _compact(value), selected


def resolve_person_search(query: str, provider: str | None = None) -> PersonSearch | None:
    connection = _connect()
    if connection is None:
        return None
    normalized_alias, selected_provider = _parse_query(query, provider)
    if not normalized_alias:
        connection.close()
        return None
    try:
        people = connection.execute(
            """
            SELECT person_id, canonical_name
            FROM person_search_resolution_v1
            WHERE normalized_alias = ?
              AND is_active_best_candidate = 1
              AND global_best_rank = best_rank
            ORDER BY person_id
            """,
            (normalized_alias,),
        ).fetchall()
        if not people:
            return None
        person_ids = tuple(str(row["person_id"]) for row in people)
        movie_sql = [
            "SELECT DISTINCT tp.canonical_movie_id",
            "FROM title_people tp JOIN titles t ON t.canonical_movie_id = tp.canonical_movie_id",
        ]
        params: list[Any] = [*person_ids]
        conditions = [f"tp.person_id IN ({','.join('?' for _ in person_ids)})"]
        if selected_provider == "youtube":
            movie_sql.append("JOIN youtube_availability y ON y.canonical_movie_id = tp.canonical_movie_id")
        elif selected_provider:
            movie_sql.append("JOIN availability a ON a.canonical_movie_id = tp.canonical_movie_id")
            conditions.extend(["lower(a.provider_key) = ?", "upper(coalesce(a.country, '')) IN ('IN', 'INDIA')"])
            params.append(selected_provider)
        movie_sql.append("WHERE " + " AND ".join(conditions))
        movie_sql.append("ORDER BY t.release_year DESC NULLS LAST, t.title COLLATE NOCASE")
        movie_ids = tuple(str(row[0]) for row in connection.execute(" ".join(movie_sql), params).fetchall())
        return PersonSearch(
            query=query,
            normalized_alias=normalized_alias,
            provider=selected_provider,
            person_ids=person_ids,
            person_names=tuple(str(row["canonical_name"]) for row in people),
            canonical_movie_ids=movie_ids,
            ambiguous=len(person_ids) > 1,
        )
    finally:
        connection.close()


def search_metadata(result: PersonSearch) -> dict[str, Any]:
    return {
        "source": "v1.3_sidecar",
        "normalized_alias": result.normalized_alias,
        "resolved_person_ids": list(result.person_ids),
        "resolved_person_names": list(result.person_names),
        "ambiguous": result.ambiguous,
        "provider": result.provider,
    }
