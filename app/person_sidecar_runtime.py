"""Materialize the accepted, immutable Person Search sidecar for Vercel."""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import sqlite3
import stat
import tempfile
from pathlib import Path

_ASSET_ROOT = Path(__file__).resolve().parents[1] / "runtime_assets"
_COMPRESSED = _ASSET_ROOT / "flixyfy_person_search_sidecar_runtime.db.gz"
_MANIFEST = _ASSET_ROOT / "PERSON_SIDECAR_MANIFEST.json"
_V10_COMPRESSED = _ASSET_ROOT / "flixyfy_person_search_sidecar_v10.db.gz"
_V10_MANIFEST = _ASSET_ROOT / "PERSON_SEARCH_SIDECAR_V10_MANIFEST.json"
_RUNTIME_NAME = "flixyfy_person_search_sidecar_runtime.db"
_V10_RUNTIME_NAME = "flixyfy_person_search_sidecar_v10.db"
_EXPECTED_IDENTITIES = 6248
_EXPECTED_EDGES = 140415
_V10_EXPECTED_IDENTITIES = 86539
_V10_EXPECTED_EDGES = 354829
_CHUNK_SIZE = 1024 * 1024


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(_CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_preview() -> bool:
    environment = (os.environ.get("VERCEL_ENV") or os.environ.get("FLIXYFY_RUNTIME_ENV") or "").strip().casefold()
    return environment == "preview"


def _manifest() -> dict[str, object]:
    manifest_path = _V10_MANIFEST if _is_preview() else _MANIFEST
    expected_identities = _V10_EXPECTED_IDENTITIES if _is_preview() else _EXPECTED_IDENTITIES
    expected_edges = _V10_EXPECTED_EDGES if _is_preview() else _EXPECTED_EDGES
    expected_source_size = 149118976 if _is_preview() else 48791552
    try:
        value = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeError("Person sidecar manifest is unavailable or invalid") from exc
    if (
        value.get("source_size") != expected_source_size
        or value.get("accepted_person_identities") != expected_identities
        or value.get("accepted_person_edges") != expected_edges
        or not isinstance(value.get("source_sha256"), str)
        or len(value["source_sha256"]) != 64
    ):
        raise RuntimeError("Person sidecar manifest does not describe the accepted dataset")
    return value


def materialize_person_sidecar() -> Path:
    """Return a verified SQLite copy under the writable system temp directory."""
    manifest = _manifest()
    preview = _is_preview()
    compressed = _V10_COMPRESSED if preview else _COMPRESSED
    runtime_name = _V10_RUNTIME_NAME if preview else _RUNTIME_NAME
    expected_size = int(manifest["source_size"])
    expected_sha256 = str(manifest["source_sha256"]).lower()
    if not compressed.is_file():
        raise RuntimeError("Bundled Person sidecar archive is missing")

    target = Path(tempfile.gettempdir()) / runtime_name
    if target.is_file() and target.stat().st_size == expected_size:
        if _file_sha256(target) == expected_sha256:
            return target

    fd, temporary_name = tempfile.mkstemp(prefix=".flixyfy-person-sidecar-", suffix=".tmp", dir=target.parent)
    temporary = Path(temporary_name)
    digest = hashlib.sha256()
    size = 0
    try:
        with os.fdopen(fd, "wb") as output, gzip.open(compressed, "rb") as source:
            for chunk in iter(lambda: source.read(_CHUNK_SIZE), b""):
                output.write(chunk)
                digest.update(chunk)
                size += len(chunk)
            output.flush()
            os.fsync(output.fileno())

        if size != expected_size or digest.hexdigest() != expected_sha256:
            raise RuntimeError("Person sidecar SHA-256 or size verification failed")

        database = sqlite3.connect(temporary.as_uri() + "?mode=ro", uri=True)
        try:
            integrity = database.execute("PRAGMA integrity_check").fetchone()
            if not integrity or integrity[0] != "ok":
                raise RuntimeError("Person sidecar SQLite integrity check failed")
            if preview:
                people = database.execute("SELECT COUNT(*) FROM people").fetchone()
                edges = database.execute("SELECT COUNT(*) FROM title_people").fetchone()
                resolution = database.execute("SELECT COUNT(*) FROM person_search_resolution_v1").fetchone()
                if (
                    not people or int(people[0]) != _V10_EXPECTED_IDENTITIES
                    or not edges or int(edges[0]) != _V10_EXPECTED_EDGES
                    or not resolution or int(resolution[0]) != int(manifest["resolver_rows"])
                ):
                    raise RuntimeError("V10 Person sidecar row-count verification failed")
        finally:
            database.close()

        os.chmod(temporary, stat.S_IREAD | stat.S_IRGRP | stat.S_IROTH)
        if target.exists():
            try:
                os.chmod(target, stat.S_IWRITE | stat.S_IREAD)
            except OSError:
                pass
        os.replace(temporary, target)
        return target
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def person_sidecar_runtime_binding() -> dict[str, object]:
    """Describe only the sidecar whose bytes were verified before app startup."""
    manifest = _manifest()
    preview = _is_preview()
    runtime_name = _V10_RUNTIME_NAME if preview else _RUNTIME_NAME
    runtime_path = Path(tempfile.gettempdir()) / runtime_name
    expected_size = int(manifest["source_size"])
    expected_sha256 = str(manifest["source_sha256"]).lower()
    if not runtime_path.is_file() or runtime_path.stat().st_size != expected_size:
        raise RuntimeError("Materialized Person sidecar is missing or has the wrong size")
    if _file_sha256(runtime_path) != expected_sha256:
        raise RuntimeError("Materialized Person sidecar does not match the pinned source SHA-256")
    binding: dict[str, object] = {
        "source_sha256": str(manifest["source_sha256"]).lower(),
        "source_size": int(manifest["source_size"]),
        "person_count": int(manifest["accepted_person_identities"]),
        "person_movie_edge_count": int(manifest["accepted_person_edges"]),
        "resolver_rows": int(manifest.get("resolver_rows", 0)),
        "runtime_path": str(runtime_path),
        "runtime_verified": True,
    }
    if preview:
        cohort_path = _ASSET_ROOT / "A00028_TOP100_CANDIDATE_ACCEPTANCE_V10.json"
        expected_cohort_sha256 = str(manifest.get("top100_acceptance_sha256") or "").lower()
        if not expected_cohort_sha256 or _file_sha256(cohort_path) != expected_cohort_sha256:
            raise RuntimeError("Preview Top100 acceptance cohort does not match its pinned SHA-256")
        binding["top100_acceptance_sha256"] = expected_cohort_sha256
    return binding
