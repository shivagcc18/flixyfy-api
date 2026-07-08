# FILE: C:\Users\USER\Desktop\flixyfy-deploy\flixyfy-api\app\main.py
r"""
FLIXYFY production FastAPI entrypoint.

Railway starts:
    uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}

The production app object currently lives in app.main_v3.

Do not create parallel FastAPI app entrypoints.
Do not place test, audit, repair, sync, or verification scripts in flixyfy-deploy.
Those belong under:
    C:\Users\USER\Desktop\ott_project\data_factory

Canonical production serving rules:
    - Person pages:
        public.person_page_serving_v1

    - YouTube full-movie links:
        public.provider_availability_serving_v2 is canonical.
        public.youtube_link_from_provider_v2 is the YouTube compatibility view.
"""

from app.main_v3 import app

__all__ = ["app"]
