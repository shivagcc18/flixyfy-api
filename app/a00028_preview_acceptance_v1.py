"""Read-only, Preview-gated A00028 runtime acceptance probe."""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

_EXPECTED_CANDIDATE_SHA = "88b97fd2ce6a0b8c4d4b934a1210691e1097e770f5888a419d4c65138e230fa2"
_COHORT_PATH = Path(__file__).resolve().parents[1] / "runtime_assets" / "A00028_TOP100_CANDIDATE_ACCEPTANCE_V10.json"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _resolution_ids(payload: dict[str, Any]) -> tuple[list[str], bool]:
    resolution = payload.get("person_resolution") or {}
    if not isinstance(resolution, dict):
        return [], False
    values = resolution.get("resolved_person_ids") or resolution.get("candidate_person_ids") or []
    canonical = resolution.get("canonical_person_id")
    ids = {str(value) for value in values if value is not None}
    if canonical is not None:
        ids.add(str(canonical))
    return sorted(ids), bool(resolution.get("ambiguous")) or str(resolution.get("outcome") or "").upper() == "AMBIGUOUS"


def _items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for item in (payload.get("items") or []) if isinstance(item, dict)]


def _run_person(main: Any, name: str, person_id: str, *, limit: int = 100) -> dict[str, Any]:
    search_page = main._flixyfy_v4_search(q=name, page=1, limit=limit)
    search_ids, search_ambiguous = _resolution_ids(search_page)
    people_page = main._flixyfy_v4_people(page=1, limit=100, q=name, domain="all")
    name_key = main._person_query_forms(name)[1]
    people_ids = sorted({
        str(item.get("person_id"))
        for item in _items(people_page)
        if item.get("person_id") is not None
        and any(
            main._person_query_forms(str(alias))[1] == name_key
            for alias in [item.get("display_name"), *(item.get("aliases") or [])]
            if alias
        )
    })
    detail_page = main._flixyfy_v4_person_detail(person_id=person_id, page=1, limit=limit)
    detail_person = detail_page.get("person") or {}
    detail_id = str(detail_page.get("canonical_person_id") or detail_person.get("person_id") or "")

    search_movies: list[str] = []
    detail_movies: list[str] = []
    search_duplicates = 0
    detail_duplicates = 0
    search_leaks = 0
    detail_leaks = 0
    pagination_loss = 0
    search_totals: set[int] = set()
    detail_totals: set[int] = set()
    first_search_page: set[str] = set()
    first_detail_page: set[str] = set()
    page = 1
    while True:
        current = search_page if page == 1 else main._flixyfy_v4_search(q=name, page=page, limit=limit)
        rows = _items(current)
        ids = [str(row.get("canonical_movie_id")) for row in rows if row.get("canonical_movie_id") is not None]
        if len(ids) != len(rows):
            pagination_loss += 1
        search_duplicates += len(ids) - len(set(ids))
        if page == 1:
            first_search_page = set(ids)
        elif first_search_page and set(ids) == first_search_page:
            pagination_loss += 1
        search_movies.extend(ids)
        if int(current.get("total") or 0) >= 0:
            search_totals.add(int(current.get("total") or 0))
        for row in rows:
            evidence = row.get("match_evidence") or {}
            if str(evidence.get("person_id") or "") != person_id:
                search_leaks += 1
        if not current.get("has_more"):
            break
        if not ids or page >= 100:
            pagination_loss += 1
            break
        page += 1

    page = 1
    while True:
        current = detail_page if page == 1 else main._flixyfy_v4_person_detail(person_id=person_id, page=page, limit=limit)
        rows = _items(current)
        ids = [str(row.get("canonical_movie_id")) for row in rows if row.get("canonical_movie_id") is not None]
        if len(ids) != len(rows):
            pagination_loss += 1
        detail_duplicates += len(ids) - len(set(ids))
        if page == 1:
            first_detail_page = set(ids)
        elif first_detail_page and set(ids) == first_detail_page:
            pagination_loss += 1
        detail_movies.extend(ids)
        if int(current.get("total") or 0) >= 0:
            detail_totals.add(int(current.get("total") or 0))
        for row in rows:
            evidence = row.get("match_evidence") or {}
            if str(evidence.get("person_id") or "") != person_id:
                detail_leaks += 1
        if not current.get("has_more"):
            break
        if not ids or page >= 100:
            pagination_loss += 1
            break
        page += 1

    search_unique = set(search_movies)
    detail_unique = set(detail_movies)
    identity_pass = (
        search_ids == [person_id]
        and not search_ambiguous
        and people_ids == [person_id]
        and detail_id == person_id
    )
    parity_pass = (
        search_unique == detail_unique
        and search_duplicates == 0
        and detail_duplicates == 0
        and pagination_loss == 0
        and search_leaks == 0
        and detail_leaks == 0
        and len(search_totals) == 1
        and len(detail_totals) == 1
        and next(iter(search_totals), -1) == len(search_unique)
        and next(iter(detail_totals), -1) == len(detail_unique)
    )
    return {
        "name": name,
        "person_id": person_id,
        "search_ids": search_ids,
        "people_ids": people_ids,
        "detail_id": detail_id,
        "identity_pass": identity_pass,
        "search_total": next(iter(search_totals), None),
        "person_total": next(iter(detail_totals), None),
        "search_paginated_unique": len(search_unique),
        "person_paginated_unique": len(detail_unique),
        "parity_pass": parity_pass,
        "search_duplicates": search_duplicates,
        "person_duplicates": detail_duplicates,
        "pagination_loss": pagination_loss,
        "wrong_person_leakage": search_leaks + detail_leaks,
    }


def run_a00028_preview_acceptance() -> dict[str, Any]:
    if (os.environ.get("VERCEL_ENV") or "").strip().casefold() != "preview":
        raise RuntimeError("A00028 runtime acceptance is available only in Vercel Preview")

    from app import main
    from app.person_sidecar_runtime import person_sidecar_runtime_binding
    from app.search_sidecar_v1 import resolve_person_search

    started = time.perf_counter()
    binding = person_sidecar_runtime_binding()
    if binding.get("source_sha256") != _EXPECTED_CANDIDATE_SHA or binding.get("runtime_verified") is not True:
        raise RuntimeError("V10 candidate runtime binding did not attest the expected SHA-256")
    manifest_path = _COHORT_PATH.parent / "PERSON_SEARCH_SIDECAR_V10_MANIFEST.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    cohort_sha = _sha256(_COHORT_PATH)
    if cohort_sha != manifest.get("top100_acceptance_sha256"):
        raise RuntimeError("Top100 acceptance input hash does not match the V10 runtime manifest")
    cohort = json.loads(_COHORT_PATH.read_text(encoding="utf-8"))
    rows = cohort.get("rows") or []
    safe_rows = [row for row in rows if row.get("parity_status") == "PARITY_RESOLVED"]
    quarantined = [row for row in rows if row.get("parity_status") == "TRUE_AMBIGUITY_QUARANTINED"]

    people: list[dict[str, Any]] = []
    for row in safe_rows:
        person_id = str(row.get("accepted_graph_person_id") or "")
        name = str(row.get("display_name") or "").strip()
        if not person_id or not name:
            people.append({"name": name, "person_id": person_id, "identity_pass": False, "parity_pass": False, "failure": "invalid_pinned_cohort_row"})
            continue
        try:
            people.append(_run_person(main, name, person_id))
        except Exception as exc:
            people.append({"name": name, "person_id": person_id, "identity_pass": False, "parity_pass": False, "failure": type(exc).__name__})

    aliases: dict[str, Any] = {}
    supported_aliases = {"ANR": "237254", "Akkineni Nageswara Rao": "237254", "Nithiin": "1001828", "Nithin Kumar Reddy": "1001828"}
    for alias, expected_id in supported_aliases.items():
        try:
            payload = main._flixyfy_v4_search(q=alias, page=1, limit=1)
            ids, ambiguous = _resolution_ids(payload)
            aliases[alias] = {"ids": ids, "expected_id": expected_id, "pass": ids == [expected_id] and not ambiguous}
        except Exception as exc:
            aliases[alias] = {"ids": [], "expected_id": expected_id, "pass": False, "failure": type(exc).__name__}

    ambiguous_aliases: dict[str, Any] = {}
    wrong_selection = 0
    for alias in ("Nitin", "Nithin", "Venkatesh"):
        sidecar = resolve_person_search(alias)
        try:
            payload = main._flixyfy_v4_search(q=alias, page=1, limit=1)
            ids, is_ambiguous = _resolution_ids(payload)
        except Exception as exc:
            payload = {}
            ids, is_ambiguous = [], False
            ambiguous_aliases[alias] = {"safe": False, "failure": type(exc).__name__}
            continue
        sidecar_ambiguous = bool(sidecar and (sidecar.ambiguous or len(sidecar.person_ids) > 1))
        selected = bool(ids) and len(ids) == 1 and not is_ambiguous
        safe = not selected and (is_ambiguous or sidecar is None or sidecar_ambiguous)
        if not safe:
            wrong_selection += 1
        ambiguous_aliases[alias] = {
            "safe": safe,
            "selected_ids": ids if selected else [],
            "sidecar_candidate_count": len(sidecar.person_ids) if sidecar else 0,
            "ambiguous": is_ambiguous or sidecar_ambiguous,
        }

    special_people: dict[str, Any] = {}
    by_id = {person["person_id"]: person for person in people}
    for label, query, expected_name, expected_id in (
        ("NTR_SR", "N.T. Rama Rao", "N.T. Rama Rao", "1003933"),
        ("INDRANS", "Indrans", "Indrans", "1061913"),
    ):
        result = by_id.get(expected_id)
        if result is None:
            try:
                result = _run_person(main, expected_name, expected_id)
            except Exception as exc:
                result = {"name": expected_name, "person_id": expected_id, "identity_pass": False, "parity_pass": False, "failure": type(exc).__name__}
        special_people[label] = {"query": query, **result}

    failures = [row for row in people if not row.get("identity_pass") or not row.get("parity_pass")]
    long_cases = [row for row in people if (row.get("person_total") or 0) > 100]
    for label, row in special_people.items():
        if row.get("person_total") and row["person_total"] > 100 and row.get("person_id") not in {x.get("person_id") for x in long_cases}:
            long_cases.append({"name": row.get("name"), "person_id": row.get("person_id"), "label": label, **row})

    return {
        "schema": "FLIXYFY_A00028_V10_PREVIEW_RUNTIME_ACCEPTANCE_V1",
        "read_only": True,
        "runtime_binding": binding,
        "top100": {
            "safe_people": len(safe_rows),
            "quarantined_people": len(quarantined),
            "identity_pass": sum(1 for row in people if row.get("identity_pass")),
            "wrong_id": sum(1 for row in people if not row.get("identity_pass")),
            "filmography_parity_pass": sum(1 for row in people if row.get("parity_pass")),
            "filmography_parity_fail": sum(1 for row in people if not row.get("parity_pass")),
            "duplicates": sum(row.get("search_duplicates", 0) + row.get("person_duplicates", 0) for row in people),
            "pagination_loss": sum(row.get("pagination_loss", 0) for row in people),
            "wrong_person_leakage": sum(row.get("wrong_person_leakage", 0) for row in people),
            "failures": failures[:20],
        },
        "pagination": {"long_people": long_cases, "pass": sum(1 for row in long_cases if row.get("parity_pass")), "fail": sum(1 for row in long_cases if not row.get("parity_pass"))},
        "NTR_SR": special_people["NTR_SR"],
        "INDRANS": special_people["INDRANS"],
        "aliases": {
            "supported": aliases,
            "supported_pass": sum(1 for row in aliases.values() if row.get("pass")),
            "ambiguous": ambiguous_aliases,
            "ambiguous_safe": sum(1 for row in ambiguous_aliases.values() if row.get("safe")),
            "wrong_selection": wrong_selection,
        },
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
        "result": "PASS" if not failures and len(safe_rows) == 99 and len(quarantined) == 1 and wrong_selection == 0 and all(row.get("pass") for row in aliases.values()) and len(ambiguous_aliases) == 3 and all(row.get("safe") for row in ambiguous_aliases.values()) else "FAIL",
    }
