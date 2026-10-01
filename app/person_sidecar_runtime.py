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
_RUNTIME_NAME = "flixyfy_person_search_sidecar_runtime.db"
_EXPECTED_IDENTITIES = 6248
_EXPECTED_EDGES = 140415
_CHUNK_SIZE = 1024 * 1024


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(_CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _manifest() -> dict[str, object]:
    try:
        value = json.loads(_MANIFEST.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeError("Person sidecar manifest is unavailable or invalid") from exc
    if (
        value.get("source_size") != 48791552
        or value.get("accepted_person_identities") != _EXPECTED_IDENTITIES
        or value.get("accepted_person_edges") != _EXPECTED_EDGES
        or not isinstance(value.get("source_sha256"), str)
        or len(value["source_sha256"]) != 64
    ):
        raise RuntimeError("Person sidecar manifest does not describe the accepted dataset")
    return value


def materialize_person_sidecar() -> Path:
    """Return a verified SQLite copy under the writable system temp directory."""
    manifest = _manifest()
    expected_size = int(manifest["source_size"])
    expected_sha256 = str(manifest["source_sha256"]).lower()
    if not _COMPRESSED.is_file():
        raise RuntimeError("Bundled Person sidecar archive is missing")

    target = Path(tempfile.gettempdir()) / _RUNTIME_NAME
    if target.is_file() and target.stat().st_size == expected_size:
        if _file_sha256(target) == expected_sha256:
            return target

    fd, temporary_name = tempfile.mkstemp(prefix=".flixyfy-person-sidecar-", suffix=".tmp", dir=target.parent)
    temporary = Path(temporary_name)
    digest = hashlib.sha256()
    size = 0
    try:
        with os.fdopen(fd, "wb") as output, gzip.open(_COMPRESSED, "rb") as source:
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