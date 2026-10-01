"""Vercel's root FastAPI entrypoint; app.main remains the app authority."""

import os

from app.person_sidecar_runtime import materialize_person_sidecar

os.environ["FLIXYFY_SEARCH_SIDECAR_DB"] = str(materialize_person_sidecar())

from app.main import app  # noqa: E402,F401