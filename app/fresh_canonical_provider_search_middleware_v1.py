from __future__ import annotations

import json
import os
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response


_DATABASE_ENV_NAMES = (
    "FLIXYFY_FRESH_DATABASE_URL",
    "FLIXYFY_NEON_PROD_DATABASE_URL",
    "DATABASE_URL",
    "NEON_DATABASE_URL",
)


def _database_url() -> str | None:
    for name in _DATABASE_ENV_NAMES:
        value = os.environ.get(name)
        if value:
            return value
    return None


def _connect():
    url = _database_url()
    if not url:
        return None

    try:
        import psycopg  # type: ignore
        return psycopg.connect(url, autocommit=True)
    except Exception:
        pass

    try:
        import psycopg2  # type: ignore
        connection = psycopg2.connect(url)
        connection.autocommit = True
        return connection
    except Exception:
        return None


def _canonical_current_youtube() -> tuple[int | None, set[str] | None]:
    connection = _connect()
    if connection is None:
        return None, None

    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT COUNT(DISTINCT content_slug)
                FROM provider_availability_serving_v2
                WHERE lower(coalesce(domain::text, '')) = 'current'
                  AND lower(coalesce(provider_key::text, '')) = 'youtube'
                """
            )
            total = int(cursor.fetchone()[0])

            cursor.execute(
                """
                SELECT DISTINCT content_slug::text
                FROM provider_availability_serving_v2
                WHERE lower(coalesce(domain::text, '')) = 'current'
                  AND lower(coalesce(provider_key::text, '')) = 'youtube'
                """
            )
            slugs = {str(row[0]) for row in cursor.fetchall() if row and row[0]}
            return total, slugs
    except Exception:
        return None, None
    finally:
        try:
            connection.close()
        except Exception:
            pass


def _title(item: dict[str, Any]) -> str:
    for key in ("title", "name", "display_title", "primary_title"):
        value = item.get(key)
        if value:
            return str(value)
    return ""


def _slug(item: dict[str, Any]) -> str:
    for key in ("slug", "content_slug", "movie_slug", "canonical_slug"):
        value = item.get(key)
        if value:
            return str(value)
    return ""


def _sort_exact(items: list[Any], query: str) -> list[Any]:
    q = query.strip().casefold()
    if not q:
        return items

    def rank(value: Any) -> tuple[int, int, str]:
        if not isinstance(value, dict):
            return (9, 999999, "")
        title = _title(value).strip()
        folded = title.casefold()
        if folded == q:
            tier = 0
        elif folded.startswith(q):
            tier = 1
        elif q in folded:
            tier = 2
        else:
            tier = 3
        return (tier, len(title), folded)

    return sorted(items, key=rank)


def _find_list(payload: dict[str, Any]) -> tuple[dict[str, Any], str] | None:
    for key in ("items", "results", "movies", "content"):
        if isinstance(payload.get(key), list):
            return payload, key

    data = payload.get("data")
    if isinstance(data, dict):
        for key in ("items", "results", "movies", "content"):
            if isinstance(data.get(key), list):
                return data, key
    return None


class FreshCanonicalProviderSearchMiddlewareV1(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)

        path = request.url.path
        is_current_youtube = (
            path in {"/api/v4/movies", "/api/v4/current", "/api/v3/movies"}
            and (request.query_params.get("provider") or "").strip().lower() == "youtube"
        )
        is_search = path in {"/api/v4/search", "/api/v3/search", "/api/search"}

        if not is_current_youtube and not is_search:
            return response

        content_type = response.headers.get("content-type", "")
        if "application/json" not in content_type.lower():
            return response

        body = b""
        async for chunk in response.body_iterator:
            body += chunk

        try:
            payload = json.loads(body.decode("utf-8"))
        except Exception:
            return Response(
                content=body,
                status_code=response.status_code,
                headers=dict(response.headers),
                media_type=response.media_type,
            )

        if isinstance(payload, dict):
            located = _find_list(payload)

            if is_current_youtube:
                canonical_total, canonical_slugs = _canonical_current_youtube()
                if canonical_total is not None:
                    payload["total"] = canonical_total

                if located and canonical_slugs is not None:
                    owner, key = located
                    filtered = []
                    for item in owner[key]:
                        if not isinstance(item, dict):
                            continue
                        slug = _slug(item)
                        if not slug or slug in canonical_slugs:
                            filtered.append(item)
                    owner[key] = filtered

            if is_search and located:
                owner, key = located
                query = (
                    request.query_params.get("q")
                    or request.query_params.get("query")
                    or ""
                )
                owner[key] = _sort_exact(owner[key], query)

        headers = dict(response.headers)
        headers.pop("content-length", None)
        return Response(
            content=json.dumps(payload, ensure_ascii=False, default=str),
            status_code=response.status_code,
            headers=headers,
            media_type="application/json",
        )


def install_fresh_canonical_provider_search_middleware_v1(app) -> None:
    marker = "_flixyfy_fresh_canonical_provider_search_middleware_v1"
    if getattr(app.state, marker, False):
        return
    app.add_middleware(FreshCanonicalProviderSearchMiddlewareV1)
    setattr(app.state, marker, True)
