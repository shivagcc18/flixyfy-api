from __future__ import annotations

import json
import os

os.environ["FLIXYFY_PRODUCTION_CORS_ORIGINS"] = "https://app.flixyfy.example,https://prod.flixyfy.test"

from fastapi.testclient import TestClient

from app.db import read_connection
from app.main import app

client = TestClient(app)


def assert_no_local_path(payload: object) -> None:
    text = json.dumps(payload)
    assert "C:\\\\Users" not in text
    assert "C:/Users" not in text
    assert "flixyfy_launch_serving_v3.db" not in text


with read_connection() as con:
    current = con.execute(
        "SELECT canonical_movie_id,tmdb_id,title FROM movie_serving WHERE tmdb_id IS NOT NULL ORDER BY title LIMIT 1"
    ).fetchone()
    historical = con.execute(
        "SELECT canonical_movie_id,title FROM movie_serving WHERE tmdb_id IS NULL ORDER BY title LIMIT 1"
    ).fetchone()
    route_counts = dict(con.execute(
        """SELECT
             SUM(CASE WHEN domain='current' THEN 1 ELSE 0 END) current,
             SUM(CASE WHEN domain='historical' THEN 1 ELSE 0 END) historical,
             SUM(CASE WHEN tmdb_id IS NOT NULL THEN 1 ELSE 0 END) tmdb_routable,
             SUM(CASE WHEN tmdb_id IS NULL THEN 1 ELSE 0 END) hist_only_routable,
             COUNT(*) canonical_routable
           FROM movie_identity_serving_v3"""
    ).fetchone())

assert current is not None
assert historical is not None

health = client.get("/health")
assert health.status_code == 200, health.text
health_payload = health.json()
assert health_payload["status"] == "ok"
assert health_payload["checks"]["schema_checks"] is True
assert_no_local_path(health_payload)

meta = client.get("/api/v1/meta")
assert meta.status_code == 200, meta.text
assert meta.json()["canonical_movie_identity"]["supported_prefixes"] == ["TMDB", "HIST"]
assert_no_local_path(meta.json())

providers = client.get("/api/v1/providers")
assert providers.status_code == 200, providers.text
assert providers.json()["items"]

search_full = client.get("/api/v1/search", params={"q": current["title"], "limit": 5})
search_compact = client.get("/api/v1/search", params={"q": current["title"], "limit": 5, "payload": "compact"})
assert search_full.status_code == 200, search_full.text
assert search_compact.status_code == 200, search_compact.text
full_payload = search_full.json()
compact_payload = search_compact.json()
assert full_payload["items"]
assert compact_payload["items"]
assert len(json.dumps(compact_payload["items"][0])) < len(json.dumps(full_payload["items"][0]))
assert "overview" in full_payload["items"][0]
assert "overview" not in compact_payload["items"][0]

current_detail = client.get(f"/api/v1/movies/{int(current['tmdb_id'])}")
assert current_detail.status_code == 200, current_detail.text
assert current_detail.json()["tmdb_id"] == int(current["tmdb_id"])
assert current_detail.json()["movie_identity"]["route"]["kind"] == "TMDB"
assert "availability" in current_detail.json()

historical_detail = client.get(f"/api/v1/movies/by-canonical/{historical['canonical_movie_id']}")
assert historical_detail.status_code == 200, historical_detail.text
hist_payload = historical_detail.json()
assert hist_payload["tmdb_id"] is None
assert hist_payload["canonical_movie_id"].startswith("HIST:")
assert hist_payload["movie_identity"]["route"]["kind"] == "CANONICAL"

bad_numeric = client.get("/api/v1/movies/0")
assert bad_numeric.status_code == 404

localhost_cors = client.options(
    "/health",
    headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "GET"},
)
prod_cors = client.options(
    "/health",
    headers={"Origin": "https://app.flixyfy.example", "Access-Control-Request-Method": "GET"},
)
assert localhost_cors.headers.get("access-control-allow-origin") == "http://localhost:3000"
assert prod_cors.headers.get("access-control-allow-origin") == "https://app.flixyfy.example"

print(json.dumps({
    "status": "PASS",
    "route_counts": route_counts,
    "current_sample": {"tmdb_id": current["tmdb_id"], "canonical_movie_id": current["canonical_movie_id"]},
    "historical_sample": {"canonical_movie_id": historical["canonical_movie_id"]},
    "cors": {"localhost": "PASS", "production": "PASS"},
    "compact_payload": {
        "full_item_bytes": len(json.dumps(full_payload["items"][0])),
        "compact_item_bytes": len(json.dumps(compact_payload["items"][0])),
    },
}, indent=2))

