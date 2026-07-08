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
        public.domain_availability_serving_v5 is canonical.
        public.youtube_link_from_provider_v2 is the YouTube compatibility view.
"""

from app.main_v3 import app

__all__ = ["app"]

# FLIXYFY_PROVIDER_FILTERS_V5_MIDDLEWARE_INSTALL_V1
# Provider-filtered list requests use existing v5 availability tables only.
try:
    from app.provider_filter_v5_middleware import install_provider_filter_v5_middleware
    install_provider_filter_v5_middleware(app)
except Exception as _flixyfy_provider_filter_v5_exc:
    print("FLIXYFY_PROVIDER_FILTERS_V5_MIDDLEWARE_INSTALL_V1_ERROR", repr(_flixyfy_provider_filter_v5_exc))
# END_FLIXYFY_PROVIDER_FILTERS_V5_MIDDLEWARE_INSTALL_V1
# FLIXYFY_BACKEND_PROVIDER_FILTERS_V5_INSTALL_START
# FLIXYFY_BACKEND_PROVIDER_FILTERS_V5_AUDIT_APPLY_V2
# Provider-filtered list requests use existing domain availability_serving_v5 tables only.
try:
    from app.provider_filter_v5_middleware import install_provider_filter_v5_middleware
    install_provider_filter_v5_middleware(app)
except Exception as exc:
    print(f"FLIXYFY provider filter v5 middleware disabled: {exc}")
# FLIXYFY_BACKEND_PROVIDER_FILTERS_V5_INSTALL_END
