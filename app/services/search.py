from __future__ import annotations

import re
import sqlite3
import unicodedata
import urllib.parse
from collections import Counter
from typing import Any, Iterable

CANONICAL_MOVIE_ID_PATTERN = re.compile(r"(?:TMDB:\d+|HIST:[A-Za-z0-9_-]+)")
MOVIE_SEARCH_ID_PATTERN = re.compile(r"(?:tmdb-\d+|hist-[A-Za-z0-9_-]+)")


class InvalidMovieIdentifier(ValueError):
    pass


GENERIC_WORDS = {
    "movie", "movies", "film", "films", "show", "shows", "watch", "where", "available",
    "availability", "on", "in", "from", "with", "actor", "actress", "director",
    "producer", "latest", "best", "all", "find", "give", "me",
}
LANGUAGE_ALIASES = {
    "hindi": "hi", "telugu": "te", "tamil": "ta", "malayalam": "ml", "kannada": "kn",
    "bengali": "bn", "bangla": "bn", "marathi": "mr", "punjabi": "pa", "gujarati": "gu",
    "odia": "or", "oriya": "or", "assamese": "as", "english": "en", "urdu": "ur",
}


def normalize(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", text.lower())).strip()


def compact(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", normalize(value))


def image_url(path: str | None, size: str = "w500") -> str | None:
    if not path:
        return None
    if path.startswith(("http://", "https://")):
        return path
    return f"https://image.tmdb.org/t/p/{size}{path}"


def fts_query(value: str) -> str:
    tokens = [t for t in normalize(value).split() if t]
    return " AND ".join(f'"{token}"*' for token in tokens)


def canonical_route(canonical_movie_id: str, tmdb_id: int | None) -> dict[str, Any]:
    search_id = movie_search_id(canonical_movie_id)
    if tmdb_id is not None:
        return {"kind": "TMDB", "route_key": str(tmdb_id), "search_id": search_id, "api_path": f"/api/v1/movies/{tmdb_id}"}
    return {
        "kind": "CANONICAL",
        "route_key": canonical_movie_id,
        "search_id": search_id,
        "api_path": f"/api/v1/movies/by-canonical/{urllib.parse.quote(canonical_movie_id, safe='')}",
    }


def movie_search_id(canonical_movie_id: str) -> str:
    return str(canonical_movie_id).replace(":", "-", 1).lower()


def canonical_from_search_id(search_id: str) -> str | None:
    value = str(search_id or "").strip()
    if not MOVIE_SEARCH_ID_PATTERN.fullmatch(value):
        return None
    prefix, key = value.split("-", 1)
    return f"{prefix.upper()}:{key}"


def parse_canonical_movie_id(value: str) -> str:
    identifier = str(value or "").strip()
    if not identifier:
        raise InvalidMovieIdentifier("Movie identifier is required")
    canonical = canonical_from_search_id(identifier)
    if canonical:
        return canonical
    if CANONICAL_MOVIE_ID_PATTERN.fullmatch(identifier):
        return identifier
    raise InvalidMovieIdentifier("Malformed movie identifier")


def parse_movie_route_identifier(value: str) -> tuple[str, int | str]:
    identifier = str(value or "").strip()
    if not identifier:
        raise InvalidMovieIdentifier("Movie identifier is required")
    if re.fullmatch(r"\d+", identifier):
        return "tmdb", int(identifier)
    try:
        return "canonical", parse_canonical_movie_id(identifier)
    except InvalidMovieIdentifier:
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{1,160}", identifier):
            return "search_id", identifier.lower()
        raise


def language_variant(movie_language: str | None, audio_language: str | None, variant_kind: str | None) -> str:
    kind = str(variant_kind or "").lower()
    if kind == "dubbed":
        return "dubbed"
    if audio_language and movie_language and audio_language == movie_language:
        return "original"
    return "unknown"


def build_ott_button(row: dict[str, Any], title: str, year: int | None) -> tuple[str | None, str]:
    query = urllib.parse.quote_plus(f"{title} {year or ''}".strip())
    template = row.get("search_template")
    home = row.get("home_url")
    if template:
        return str(template).replace("{query}", query), "SEARCH"
    if home:
        return str(home), "HOME"
    return None, "UNCONFIGURED"


def build_ott_availability(row: dict[str, Any], title: str, year: int | None, language_code: str | None, language_name: str | None) -> dict[str, Any]:
    url, kind = build_ott_button(row, title, year)
    availability_type = str(row.get("availability_type") or "unknown")
    access_model = {"flatrate": "paid_ott", "rent": "rent", "buy": "buy", "free": "free"}.get(availability_type, availability_type)
    provider_key = str(row["provider_key"])
    provider_name = str(row["provider_name"])
    availability_id = f"OTT:{provider_key}:{availability_type}"
    return {
        "availability_id": availability_id,
        "provider_variant_key": availability_id,
        "media_kind": "ott",
        "provider_key": provider_key,
        "provider_name": provider_name,
        "provider_category": row.get("provider_category"),
        "availability_type": availability_type,
        "access_model": access_model,
        "button_url": url,
        "button_label": f"Watch on {provider_name}",
        "navigation_kind": kind,
        "content_language_code": language_code,
        "content_language_name": language_name,
        "dubbed_language_code": None,
        "dubbed_language_name": None,
        "language_variant": "original",
        "confidence_score": row.get("confidence_score"),
        "source": row.get("source"),
    }


def build_youtube_availability(row: dict[str, Any]) -> dict[str, Any]:
    audio_code = row.get("audio_language_code")
    movie_language = row.get("movie_original_language")
    variant_kind = row.get("variant_kind")
    video_id = str(row["video_id"])
    variant = language_variant(movie_language, audio_code, variant_kind)
    return {
        "availability_id": f"YOUTUBE:{video_id}:{audio_code or 'und'}:{variant_kind or 'UNKNOWN'}",
        "provider_variant_key": f"YOUTUBE:{video_id}:{audio_code or 'und'}:{variant_kind or 'UNKNOWN'}",
        "media_kind": "youtube",
        "provider_key": "youtube",
        "provider_name": "YouTube",
        "provider_category": "free",
        "availability_type": "free",
        "access_model": "free",
        "video_id": video_id,
        "button_url": row.get("watch_url"),
        "button_label": row.get("button_label") or "Watch on YouTube",
        "navigation_kind": row.get("navigation_kind") or "DIRECT",
        "content_language_code": movie_language,
        "content_language_name": None,
        "dubbed_language_code": audio_code if variant == "dubbed" else None,
        "dubbed_language_name": row.get("audio_language_name") if variant == "dubbed" else None,
        "audio_language_code": audio_code,
        "audio_language_name": row.get("audio_language_name"),
        "language_variant": variant,
        "variant_kind": variant_kind,
        "evidence_tier": row.get("evidence_tier"),
        "serving_status": row.get("serving_status"),
    }


def load_availability(con: sqlite3.Connection, canonical_ids: Iterable[str], lookup: dict[str, tuple[str, int | None, str | None, str | None]]) -> dict[str, dict[str, list[dict[str, Any]]]]:
    ids = list(dict.fromkeys(str(v) for v in canonical_ids))
    if not ids:
        return {}
    marks = ",".join("?" for _ in ids)
    result: dict[str, dict[str, list[dict[str, Any]]]] = {cid: {"providers": [], "availability": []} for cid in ids}
    provider_rows = con.execute(
        f"""SELECT * FROM provider_serving WHERE canonical_movie_id IN ({marks})
            ORDER BY CASE availability_type WHEN 'flatrate' THEN 1 WHEN 'rent' THEN 2 WHEN 'buy' THEN 3 ELSE 4 END,
                     provider_name,availability_type""",
        ids,
    )
    seen_legacy: set[tuple[str, str]] = set()
    seen_availability: set[tuple[str, str, str]] = set()
    for raw in provider_rows:
        row = dict(raw)
        cid = str(row["canonical_movie_id"])
        title, year, language_code, language_name = lookup[cid]
        item = build_ott_availability(row, title, year, language_code, language_name)
        availability_key = (cid, item["provider_key"], item["availability_type"])
        if availability_key not in seen_availability:
            seen_availability.add(availability_key)
            result[cid]["availability"].append(item)
        legacy_key = (cid, item["provider_key"])
        if legacy_key not in seen_legacy:
            seen_legacy.add(legacy_key)
            result[cid]["providers"].append({
                "provider_key": item["provider_key"],
                "provider_variant_key": item["provider_variant_key"],
                "provider_name": item["provider_name"],
                "availability_type": item["availability_type"],
                "provider_category": item["provider_category"],
                "button_url": item["button_url"],
                "button_label": item["button_label"],
                "navigation_kind": item["navigation_kind"],
            })
    youtube_rows = con.execute(
        f"""SELECT * FROM movie_youtube_availability_v3
            WHERE canonical_movie_id IN ({marks}) AND serving_status='ACTIVE'
            ORDER BY variant_kind,audio_language_name,video_id""",
        ids,
    )
    seen_youtube: set[tuple[str, str, str | None, str | None]] = set()
    for raw in youtube_rows:
        row = dict(raw)
        cid = str(row["canonical_movie_id"])
        key = (cid, str(row["video_id"]), row.get("audio_language_code"), row.get("variant_kind"))
        if key in seen_youtube:
            continue
        seen_youtube.add(key)
        result[cid]["availability"].append(build_youtube_availability(row))
    return result


def apply_identity(item: dict[str, Any]) -> None:
    canonical_id = str(item["canonical_movie_id"])
    tmdb_id = item.get("tmdb_id")
    search_id = movie_search_id(canonical_id)
    item["search_id"] = search_id
    item["movie_identity"] = {
        "canonical_movie_id": canonical_id,
        "tmdb_id": tmdb_id,
        "imdb_id": item.get("imdb_id"),
        "search_id": search_id,
        "route": canonical_route(canonical_id, tmdb_id),
    }


def compact_card(item: dict[str, Any]) -> dict[str, Any]:
    availability = item.get("availability", []) or []
    item = {**item, "availability_summary": {"count": len(availability), "providers": [a.get("provider_name") for a in availability[:8]]}}
    keep = {
        "canonical_movie_id", "tmdb_id", "imdb_id", "movie_identity", "title", "original_title",
        "release_year", "domain", "original_language", "language_name", "search_id", "poster_url",
        "tmdb_rating", "imdb_rating", "provider_count", "youtube_video_count", "availability_count",
        "providers", "availability", "availability_summary", "matched_fields",
    }
    return {key: value for key, value in item.items() if key in keep and value is not None}


def card_rows(con: sqlite3.Connection, rows: list[sqlite3.Row], *, compact_payload: bool = False) -> list[dict[str, Any]]:
    lookup = {
        str(row["canonical_movie_id"]): (str(row["title"]), row["release_year"], row["original_language"], row["language_name"])
        for row in rows
    }
    availability_map = load_availability(con, lookup.keys(), lookup)
    items = []
    for row in rows:
        item = dict(row)
        cid = str(item["canonical_movie_id"])
        item["poster_url"] = image_url(item.pop("poster_path", None), "w500")
        item["backdrop_url"] = image_url(item.pop("backdrop_path", None), "w1280")
        apply_identity(item)
        availability = availability_map.get(cid, {"providers": [], "availability": []})
        item["providers"] = availability["providers"]
        item["availability"] = availability["availability"]
        items.append(compact_card(item) if compact_payload else item)
    return items


def filter_parts(
    provider: str | None = None,
    language: str | None = None,
    genre: str | None = None,
    year_from: int | None = None,
    year_to: int | None = None,
    domain: str | None = None,
    has_provider: bool | None = None,
    person_keys: list[str] | None = None,
) -> tuple[list[str], list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if provider:
        provider_key = provider.lower()
        if provider_key in {"youtube", "yt"}:
            clauses.append("m.canonical_movie_id IN (SELECT y.canonical_movie_id FROM movie_youtube_availability_v3 y WHERE y.serving_status='ACTIVE')")
        else:
            clauses.append("m.canonical_movie_id IN (SELECT p.canonical_movie_id FROM provider_serving p WHERE p.provider_key=?)")
            params.append(provider)
    if language:
        clauses.append(
            """(m.original_language=? OR m.canonical_movie_id IN (
                SELECT l.canonical_movie_id FROM movie_language_serving l
                WHERE l.iso_639_1=? OR l.normalized_name=?))"""
        )
        params.extend([language, language, normalize(language)])
    if genre:
        clauses.append("m.canonical_movie_id IN (SELECT g.canonical_movie_id FROM movie_genre_serving g WHERE g.normalized_name=?)")
        params.append(normalize(genre))
    if person_keys:
        keys = [str(key) for key in person_keys if str(key)]
        if keys:
            marks = ",".join("?" for _ in keys)
            clauses.append(
                f"m.canonical_movie_id IN (SELECT pp.canonical_movie_id FROM movie_people_serving pp WHERE CAST(pp.person_id AS TEXT) IN ({marks}))"
            )
            params.extend(keys)
    if year_from is not None:
        clauses.append("m.release_year>=?")
        params.append(year_from)
    if year_to is not None:
        clauses.append("m.release_year<=?")
        params.append(year_to)
    if domain in {"current", "historical"}:
        clauses.append("m.domain=?")
        params.append(domain)
    if has_provider is True:
        clauses.append("m.availability_count>0")
    elif has_provider is False:
        clauses.append("m.availability_count=0")
    return clauses, params


def list_movies(
    con: sqlite3.Connection,
    *,
    limit: int = 24,
    offset: int = 0,
    provider: str | None = None,
    language: str | None = None,
    genre: str | None = None,
    year_from: int | None = None,
    year_to: int | None = None,
    domain: str | None = None,
    has_provider: bool | None = None,
    sort: str = "popular",
    compact: bool = False,
    person_keys: list[str] | None = None,
) -> dict[str, Any]:
    clauses, params = filter_parts(provider, language, genre, year_from, year_to, domain, has_provider, person_keys)
    where = "WHERE " + " AND ".join(clauses) if clauses else ""
    order = {
        "popular": "m.popularity_score DESC,m.title",
        "rating": "COALESCE(m.imdb_rating,m.tmdb_rating,0) DESC,m.title",
        "newest": "m.release_year DESC,m.popularity_score DESC",
        "oldest": "m.release_year ASC,m.popularity_score DESC",
        "title": "m.title COLLATE NOCASE",
    }.get(sort, "m.popularity_score DESC,m.title")
    total = int(con.execute(f"SELECT COUNT(*) FROM movie_serving m {where}", params).fetchone()[0])
    rows = con.execute(
        f"""SELECT m.canonical_movie_id,m.tmdb_id,m.imdb_id,m.title,m.original_title,m.release_year,
                   m.domain,m.original_language,m.language_name,m.runtime,m.overview,
                   m.poster_path,m.backdrop_path,m.tmdb_rating,m.tmdb_votes,
                   m.imdb_rating,m.imdb_votes,m.metascore,m.popularity_score,
                   m.provider_count,m.youtube_video_count,m.availability_count
            FROM movie_serving m {where}
            ORDER BY {order} LIMIT ? OFFSET ?""",
        [*params, limit, offset],
    ).fetchall()
    return {"total": total, "limit": limit, "offset": offset, "items": card_rows(con, rows, compact_payload=compact)}


def entity_matches(con: sqlite3.Connection, query: str) -> dict[str, list[dict[str, Any]]]:
    nq = normalize(query)
    matches: dict[str, list[dict[str, Any]]] = {"providers": [], "languages": [], "genres": [], "people": [], "years": []}
    seen: set[str] = set()
    for row in con.execute("SELECT DISTINCT provider_key,display_name,normalized_raw_name FROM provider_alias ORDER BY LENGTH(normalized_raw_name) DESC"):
        alias = str(row["normalized_raw_name"])
        if alias and re.search(rf"(^| ){re.escape(alias)}($| )", nq):
            key = str(row["provider_key"])
            if key not in seen:
                matches["providers"].append({"key": key, "name": row["display_name"], "matched": alias})
                seen.add(key)
    if re.search(r"(^| )(youtube|you tube|yt)($| )", nq):
        matches["providers"].append({"key": "youtube", "name": "YouTube", "matched": "youtube"})
    for name, code in LANGUAGE_ALIASES.items():
        if re.search(rf"(^| ){re.escape(name)}($| )", nq):
            matches["languages"].append({"key": code, "name": name.title(), "matched": name})
    for year in sorted(set(re.findall(r"\b(?:19|20)\d{2}\b", nq))):
        matches["years"].append({"key": year, "name": year, "matched": year})
    for row in con.execute("SELECT entity_key,entity_name,normalized_name,usage_count FROM search_entity WHERE entity_type='genre' ORDER BY LENGTH(normalized_name) DESC"):
        name = str(row["normalized_name"])
        if name and re.search(rf"(^| ){re.escape(name)}($| )", nq):
            matches["genres"].append({"key": row["entity_key"], "name": row["entity_name"], "matched": name})
    person_terms = [
        token for token in nq.split()
        if token not in GENERIC_WORDS and token not in LANGUAGE_ALIASES and not re.fullmatch(r"(?:19|20)\d{2}", token)
    ]
    person_query = " ".join(person_terms) or nq
    person_nq = normalize(person_query)
    cq = compact(person_query)
    if len(person_nq) >= 2:
        people = con.execute(
            """SELECT entity_key,entity_name,normalized_name,compact_name,usage_count
               FROM search_entity
               WHERE entity_type='person' AND (
                    normalized_name=? OR normalized_name LIKE ?
                    OR compact_name LIKE ?
               )
               ORDER BY CASE WHEN normalized_name=? THEN 0 ELSE 1 END,
                        usage_count DESC LIMIT 8""",
            (person_nq, person_nq + "%", cq + "%", person_nq),
        ).fetchall()
        matches["people"] = [{
            "key": row["entity_key"], "name": row["entity_name"],
            "matched": person_nq, "usage_count": row["usage_count"],
        } for row in people]
    return matches


def residual_query(query: str, entities: dict[str, list[dict[str, Any]]]) -> str:
    text = f" {normalize(query)} "
    remove = set(GENERIC_WORDS)
    for group in ("providers", "languages", "genres", "years"):
        remove.update(str(item["matched"]) for item in entities[group])
    for item in entities["people"]:
        remove.add(str(item["matched"]))
    for value in sorted(remove, key=len, reverse=True):
        text = re.sub(rf"(?<![a-z0-9]){re.escape(value)}(?![a-z0-9])", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def intent_summary(entities: dict[str, list[dict[str, Any]]]) -> str:
    if entities["people"]:
        base = f"Movies connected to {entities['people'][0]['name']}"
    elif entities["genres"]:
        base = f"{entities['genres'][0]['name']} movies"
    else:
        base = "Movie search"
    if entities["providers"]:
        base += f" on {entities['providers'][0]['name']}"
    if entities["languages"]:
        base += f" in {entities['languages'][0]['name']}"
    if entities["years"]:
        base += f" from {entities['years'][0]['name']}"
    return base


def facets(items: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    p = Counter()
    l = Counter()
    y = Counter()
    for item in items:
        if item.get("language_name"):
            l[str(item["language_name"])] += 1
        if item.get("release_year"):
            y[str(item["release_year"])] += 1
        for provider in item.get("providers", []):
            p[str(provider["provider_name"])] += 1
        for availability in item.get("availability", []):
            if availability.get("media_kind") == "youtube":
                p["YouTube"] += 1
    return {
        "providers": [{"name": k, "count": v} for k, v in p.most_common(12)],
        "languages": [{"name": k, "count": v} for k, v in l.most_common(12)],
        "years": [{"name": k, "count": v} for k, v in y.most_common(12)],
    }


def search_movies(
    con: sqlite3.Connection,
    query: str,
    *,
    limit: int = 24,
    offset: int = 0,
    provider: str | None = None,
    language: str | None = None,
    genre: str | None = None,
    year_from: int | None = None,
    year_to: int | None = None,
    domain: str | None = None,
    sort: str = "relevance",
    compact: bool = False,
) -> dict[str, Any]:
    entities = entity_matches(con, query)
    provider = provider or (entities["providers"][0]["key"] if entities["providers"] else None)
    language = language or (entities["languages"][0]["key"] if entities["languages"] else None)
    genre = genre or (entities["genres"][0]["name"] if entities["genres"] else None)
    person_keys = [str(item["key"]) for item in entities["people"][:4]]
    if entities["years"] and year_from is None and year_to is None:
        year_from = year_to = int(entities["years"][0]["key"])
    residual = residual_query(query, entities)
    clauses, params = filter_parts(provider, language, genre, year_from, year_to, domain, None, person_keys)
    query_for_fts = residual

    if not query_for_fts:
        result = list_movies(
            con,
            limit=limit,
            offset=offset,
            provider=provider,
            language=language,
            genre=genre,
            year_from=year_from,
            year_to=year_to,
            domain=domain,
            compact=compact,
            person_keys=person_keys,
        )
        if person_keys:
            for item in result["items"]:
                item["matched_fields"] = ["people"]
        return {
            **result,
            "query": query,
            "normalized_query": normalize(query),
            "residual_query": residual,
            "entities": entities,
            "intent_summary": intent_summary(entities),
            "facets": facets(result["items"]),
        }

    fts = fts_query(query_for_fts)
    where_parts = ["movie_search_fts_v3 MATCH ?"] + clauses
    where = "WHERE " + " AND ".join(where_parts)
    base_params: list[Any] = [fts, *params]
    total = int(con.execute(
        f"""SELECT COUNT(*) FROM movie_search_fts_v3 f
            JOIN movie_serving m ON m.canonical_movie_id=f.canonical_movie_id
            {where}""",
        base_params,
    ).fetchone()[0])
    exact = normalize(query)
    if sort == "relevance":
        order = """CASE WHEN LOWER(m.title)=? THEN 0
                        WHEN LOWER(m.title) LIKE ? THEN 1 ELSE 2 END,
                   bm25(movie_search_fts_v3),
                   m.popularity_score DESC"""
        order_params = [exact, exact + "%"]
    else:
        order = "m.popularity_score DESC"
        order_params = []
    rows = con.execute(
        f"""SELECT m.canonical_movie_id,m.tmdb_id,m.imdb_id,m.title,m.original_title,m.release_year,
                   m.domain,m.original_language,m.language_name,m.runtime,m.overview,
                   m.poster_path,m.backdrop_path,m.tmdb_rating,m.tmdb_votes,
                   m.imdb_rating,m.imdb_votes,m.metascore,m.popularity_score,
                   m.provider_count,m.youtube_video_count,m.availability_count,
                   d.actors,d.directors,d.producers,d.genres,d.languages,d.providers provider_names
            FROM movie_search_fts_v3 f
            JOIN movie_serving m ON m.canonical_movie_id=f.canonical_movie_id
            JOIN movie_search_document_v3 d ON d.canonical_movie_id=m.canonical_movie_id
            {where} ORDER BY {order} LIMIT ? OFFSET ?""",
        [*base_params, *order_params, limit, offset],
    ).fetchall()
    items = card_rows(con, rows, compact_payload=compact)
    nq = normalize(residual or query)
    for item in items:
        matched = []
        for label, key in (
            ("title", "title"), ("original title", "original_title"),
            ("actors", "actors"), ("directors", "directors"),
            ("genres", "genres"), ("languages", "languages"),
            ("providers", "provider_names"),
        ):
            if nq and nq in normalize(item.get(key)):
                matched.append(label)
        item["matched_fields"] = matched[:4]
        for key in ("actors", "directors", "producers", "genres", "languages", "provider_names"):
            item.pop(key, None)
    return {
        "query": query,
        "normalized_query": normalize(query),
        "residual_query": residual,
        "entities": entities,
        "intent_summary": intent_summary(entities),
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": items,
        "facets": facets(items),
    }


def suggestions(con: sqlite3.Connection, query: str, limit: int = 10) -> list[dict[str, Any]]:
    nq = normalize(query)
    cq = compact(query)
    if not nq:
        return []
    movies = [{
        "entity_type": "movie",
        "entity_key": str(row["canonical_movie_id"]),
        "entity_name": row["title"],
        "release_year": row["release_year"],
        "usage_count": row["availability_count"],
    } for row in con.execute(
        """SELECT canonical_movie_id,title,release_year,availability_count FROM movie_serving
           WHERE LOWER(title) LIKE ? OR LOWER(COALESCE(original_title,'')) LIKE ?
           ORDER BY CASE WHEN LOWER(title)=? THEN 0 ELSE 1 END,
                    popularity_score DESC LIMIT ?""",
        (nq + "%", nq + "%", nq, limit),
    )]
    entities = [dict(row) for row in con.execute(
        """SELECT entity_type,entity_key,entity_name,usage_count
           FROM search_entity
           WHERE normalized_name LIKE ? OR compact_name LIKE ?
           ORDER BY CASE entity_type WHEN 'person' THEN 1 WHEN 'provider' THEN 2
                    WHEN 'genre' THEN 3 WHEN 'language' THEN 4 ELSE 5 END,
                    usage_count DESC LIMIT ?""",
        (nq + "%", cq + "%", limit),
    )]
    result = []
    seen: set[tuple[str, str]] = set()
    for item in movies + entities:
        key = (str(item["entity_type"]), str(item["entity_key"]))
        if key not in seen:
            seen.add(key)
            result.append(item)
        if len(result) >= limit:
            break
    return result


def _movie_detail_from_row(con: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    cid = str(item["canonical_movie_id"])
    item["poster_url"] = image_url(item.pop("poster_path", None), "w780")
    item["backdrop_url"] = image_url(item.pop("backdrop_path", None), "original")
    apply_identity(item)
    item["genres"] = [dict(r) for r in con.execute(
        "SELECT genre_id,genre_name FROM movie_genre_serving WHERE canonical_movie_id=? ORDER BY genre_name",
        (cid,),
    )]
    item["languages"] = [dict(r) for r in con.execute(
        "SELECT iso_639_1,language_name FROM movie_language_serving WHERE canonical_movie_id=? ORDER BY language_name",
        (cid,),
    )]
    item["cast"] = [dict(r) for r in con.execute(
        """SELECT person_id,name,role,character_name FROM movie_people_serving
           WHERE canonical_movie_id=? AND LOWER(COALESCE(role,'')) LIKE '%cast%'
           ORDER BY name LIMIT 24""",
        (cid,),
    )]
    item["crew"] = [dict(r) for r in con.execute(
        """SELECT person_id,name,role,character_name FROM movie_people_serving
           WHERE canonical_movie_id=? AND LOWER(COALESCE(role,'')) NOT LIKE '%cast%'
           ORDER BY CASE WHEN LOWER(COALESCE(role,'')) LIKE '%director%' THEN 1
                         WHEN LOWER(COALESCE(role,'')) LIKE '%producer%' THEN 2
                         ELSE 3 END,name LIMIT 30""",
        (cid,),
    )]
    availability = load_availability(
        con,
        [cid],
        {cid: (item["title"], item["release_year"], item["original_language"], item["language_name"])},
    ).get(cid, {"providers": [], "availability": []})
    item["providers"] = availability["providers"]
    item["availability"] = availability["availability"]
    item["youtube"] = [dict(r) for r in con.execute(
        """SELECT video_id,audio_language_code,audio_language_name,variant_kind,
                  watch_url,button_label,navigation_kind,evidence_tier
           FROM movie_youtube_availability_v3
           WHERE canonical_movie_id=? AND serving_status='ACTIVE'
           ORDER BY variant_kind,audio_language_name,video_id""",
        (cid,),
    )]
    return item


def movie_detail(con: sqlite3.Connection, tmdb_id: int) -> dict[str, Any] | None:
    row = con.execute("SELECT * FROM movie_serving WHERE tmdb_id=?", (tmdb_id,)).fetchone()
    if row is None:
        return None
    return _movie_detail_from_row(con, row)


def movie_detail_by_canonical(con: sqlite3.Connection, canonical_movie_id: str) -> dict[str, Any] | None:
    canonical_id = parse_canonical_movie_id(canonical_movie_id)
    row = con.execute("SELECT * FROM movie_serving WHERE canonical_movie_id=?", (canonical_id,)).fetchone()
    if row is None:
        return None
    return _movie_detail_from_row(con, row)


def movie_detail_by_identifier(con: sqlite3.Connection, identifier: str) -> dict[str, Any] | None:
    kind, value = parse_movie_route_identifier(identifier)
    if kind == "tmdb":
        return movie_detail(con, int(value))
    if kind == "canonical":
        return movie_detail_by_canonical(con, str(value))
    if kind == "search_id":
        canonical_id = canonical_from_search_id(str(value))
        if canonical_id:
            return movie_detail_by_canonical(con, canonical_id)
    return None


