"""Vercel's root FastAPI entrypoint; app.main remains the app authority."""

import json
import os

from app.person_sidecar_runtime import materialize_person_sidecar, person_sidecar_runtime_binding

_sidecar_path = materialize_person_sidecar()
os.environ["FLIXYFY_SEARCH_SIDECAR_DB"] = str(_sidecar_path)
_runtime_environment = (os.environ.get("VERCEL_ENV") or os.environ.get("FLIXYFY_RUNTIME_ENV") or "").strip().casefold()
if _runtime_environment == "preview":
    _binding = person_sidecar_runtime_binding()
    _binding["runtime_path"] = str(_sidecar_path)
    _binding["deployment_commit"] = os.environ.get("VERCEL_GIT_COMMIT_SHA")
    _binding["deployment_branch"] = os.environ.get("VERCEL_GIT_COMMIT_REF")
    os.environ["FLIXYFY_PERSON_SIDECAR_RUNTIME_BINDING"] = json.dumps(_binding, sort_keys=True)

from app.main import app  # noqa: E402,F401
