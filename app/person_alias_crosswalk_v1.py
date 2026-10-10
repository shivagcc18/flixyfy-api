"""Evidence-backed person alias resolution for Preview runtimes only."""

from __future__ import annotations

import json
import os
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


@dataclass(frozen=True)
class AliasResolution:
    outcome: str
    normalized_alias: str
    canonical_person_id: str | None
    evidence: tuple[str, ...]
    candidate_person_ids: tuple[str, ...] = ()


def normalize_alias(value: str) -> str:
    ascii_value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "", ascii_value.casefold())


def resolve_person_alias(alias: str, entries: Iterable[dict[str, Any]]) -> AliasResolution | None:
    """Resolve only exact normalized aliases with explicit evidence and status."""
    normalized = normalize_alias(alias)
    if not normalized:
        return None
    matches = [entry for entry in entries if normalize_alias(str(entry.get("alias") or "")) == normalized]
    if not matches:
        return None

    ids = tuple(sorted({
        str(person_id)
        for entry in matches
        for person_id in ([entry.get("canonical_person_id")] + list(entry.get("candidate_person_ids") or []))
        if person_id
    }))
    evidence = tuple(sorted({str(item) for entry in matches for item in entry.get("evidence", []) if item}))
    outcomes = {str(entry.get("outcome") or "UNSUPPORTED").upper() for entry in matches}
    if "AMBIGUOUS" in outcomes or len(ids) > 1:
        return AliasResolution("AMBIGUOUS", normalized, None, evidence, ids)
    canonical_ids = tuple(sorted({str(entry.get("canonical_person_id")) for entry in matches if entry.get("canonical_person_id")}))
    if outcomes == {"UNIQUE_CANONICAL"} and len(canonical_ids) == 1 and evidence:
        return AliasResolution("UNIQUE_CANONICAL", normalized, canonical_ids[0], evidence, canonical_ids)
    return AliasResolution("UNSUPPORTED", normalized, None, evidence, ids)


def resolve_preview_person_alias(alias: str) -> AliasResolution | None:
    """Read the bundled crosswalk only in explicit Preview/development mode."""
    environment = (os.environ.get("VERCEL_ENV") or os.environ.get("FLIXYFY_RUNTIME_ENV") or "").strip().casefold()
    if environment not in {"preview", "development"}:
        return None
    configured_path = os.environ.get("FLIXYFY_PERSON_ALIAS_CROSSWALK_PATH")
    path = Path(configured_path) if configured_path else Path(__file__).with_name("person_alias_crosswalk_preview_v1.json")
    if not path.is_file():
        return AliasResolution("UNSUPPORTED", normalize_alias(alias), None, ("preview_crosswalk_file_missing",))
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return AliasResolution("UNSUPPORTED", normalize_alias(alias), None, ("preview_crosswalk_file_invalid",))
    return resolve_person_alias(alias, document.get("entries", []))


def resolution_metadata(result: AliasResolution) -> dict[str, Any]:
    return {
        "source": "preview_evidence_crosswalk_v1",
        "normalized_alias": result.normalized_alias,
        "outcome": result.outcome,
        "canonical_person_id": result.canonical_person_id,
        "candidate_person_ids": list(result.candidate_person_ids),
        "evidence": list(result.evidence),
        "ambiguous": result.outcome == "AMBIGUOUS",
    }
