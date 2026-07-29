from __future__ import annotations

import sqlite3

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routes import router
from app.config import API_VERSION, CORS_ORIGINS, SERVING_DB_PUBLIC_ID
from app.db import QueryTimeoutError, SchemaError, health_check

app = FastAPI(
    title="FLIXYFY Search Intelligence API",
    version=API_VERSION,
    description="Local Web/Mobile/TV movie discovery and where-to-watch API.",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "OPTIONS"],
    allow_headers=["*"],
)
app.include_router(router)


def _error_response(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message}, "status": "error"},
    )


@app.exception_handler(QueryTimeoutError)
async def query_timeout_handler(_request: Request, _exc: QueryTimeoutError) -> JSONResponse:
    return _error_response(504, "QUERY_TIMEOUT", "Database query exceeded the configured timeout")


@app.exception_handler(HTTPException)
async def http_exception_handler(_request: Request, exc: HTTPException) -> JSONResponse:
    detail = exc.detail if isinstance(exc.detail, dict) else {}
    code = str(detail.get("code") or "HTTP_ERROR")
    message = str(detail.get("message") or exc.detail or "Request failed")
    return _error_response(exc.status_code, code, message)


@app.exception_handler(RequestValidationError)
async def validation_error_handler(_request: Request, _exc: RequestValidationError) -> JSONResponse:
    return _error_response(422, "VALIDATION_ERROR", "Request validation failed")


@app.exception_handler(SchemaError)
async def schema_error_handler(_request: Request, exc: SchemaError) -> JSONResponse:
    return _error_response(503, "SCHEMA_ERROR", str(exc))


@app.exception_handler(sqlite3.DatabaseError)
async def database_error_handler(_request: Request, _exc: sqlite3.DatabaseError) -> JSONResponse:
    return _error_response(503, "DATABASE_ERROR", "Serving database query failed")


@app.exception_handler(RuntimeError)
async def runtime_error_handler(_request: Request, exc: RuntimeError) -> JSONResponse:
    return _error_response(503, "CONFIGURATION_ERROR", str(exc))


@app.get("/health")
def health():
    checks = health_check()
    raw_counts = checks.get("counts", {}) if isinstance(checks.get("counts"), dict) else {}
    counts = {
        "canonical_identities": raw_counts.get("movies", 0),
        "tmdb_backed_movies": raw_counts.get("tmdb_routable", 0),
        "historical_only_movies": raw_counts.get("hist_only_routable", 0),
        "current_movies": raw_counts.get("current", 0),
        "historical_movies": raw_counts.get("historical", 0),
        "provider_claims": raw_counts.get("ott_availability_rows", 0),
        "search_documents": raw_counts.get("search_documents", 0),
        "youtube_active": raw_counts.get("youtube_active_rows", 0),
    }
    ok = all(
        bool(checks[key])
        for key in (
            "database_exists",
            "read_only_connection",
            "schema_checks",
            "critical_objects_exist",
            "counts_nonzero",
        )
    )
    return {
        "status": "ok" if ok else "degraded",
        "service": "flixyfy-api",
        "api_version": API_VERSION,
        "serving_database": {"authority": SERVING_DB_PUBLIC_ID, "path_exposed": False},
        "provider_data_mode": "USER_APPROVED_FINAL_FRESH_SNAPSHOT",
        "counts": counts,
        "production_write": False,
        "checks": checks,
    }

