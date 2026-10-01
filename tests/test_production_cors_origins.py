import asyncio
import unittest

from fastapi.middleware.cors import CORSMiddleware

from app.main import app


ALLOWED_ORIGINS = [
    "https://www.flixyfy.com",
    "https://flixyfy.com",
]


def configured_cors_middleware() -> CORSMiddleware:
    spec = next(item for item in app.user_middleware if item.cls is CORSMiddleware)
    return CORSMiddleware(_ok_response, **spec.kwargs)


async def _ok_response(scope, receive, send):
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"ok"})


def invoke_cors(method: str, origin: str) -> tuple[int, dict[str, str]]:
    messages = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        messages.append(message)

    headers = [(b"origin", origin.encode("ascii"))]
    if method == "OPTIONS":
        headers.extend(
            [
                (b"access-control-request-method", b"GET"),
                (b"access-control-request-headers", b"content-type"),
            ]
        )
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "https",
        "path": "/api/v1/health",
        "raw_path": b"/api/v1/health",
        "query_string": b"",
        "headers": headers,
        "server": ("flixyfy-api-free.vercel.app", 443),
        "client": ("127.0.0.1", 12345),
    }
    asyncio.run(configured_cors_middleware()(scope, receive, send))
    response = next(message for message in messages if message["type"] == "http.response.start")
    return response["status"], {
        key.decode("latin-1").lower(): value.decode("latin-1")
        for key, value in response["headers"]
    }


class ProductionCorsOriginsTests(unittest.TestCase):
    def test_production_allowlist_and_existing_policy(self):
        spec = next(item for item in app.user_middleware if item.cls is CORSMiddleware)
        self.assertEqual(spec.kwargs["allow_origins"], ALLOWED_ORIGINS)
        self.assertNotIn("*", spec.kwargs["allow_origins"])
        self.assertFalse(spec.kwargs["allow_credentials"])
        self.assertEqual(spec.kwargs["allow_methods"], ["GET", "OPTIONS"])
        self.assertEqual(spec.kwargs["allow_headers"], ["*"])

    def test_allowed_origins_pass_preflight_and_get(self):
        for origin in ALLOWED_ORIGINS:
            with self.subTest(origin=origin):
                preflight_status, preflight_headers = invoke_cors("OPTIONS", origin)
                get_status, get_headers = invoke_cors("GET", origin)
                self.assertEqual(preflight_status, 200)
                self.assertEqual(get_status, 200)
                self.assertEqual(preflight_headers.get("access-control-allow-origin"), origin)
                self.assertEqual(get_headers.get("access-control-allow-origin"), origin)

    def test_untrusted_origin_is_not_authorized(self):
        origin = "https://evil.example"
        for method in ("OPTIONS", "GET"):
            with self.subTest(method=method):
                _, headers = invoke_cors(method, origin)
                self.assertNotIn("access-control-allow-origin", headers)


if __name__ == "__main__":
    unittest.main()
