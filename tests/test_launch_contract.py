from __future__ import annotations

import os

os.environ["FLIXYFY_PRODUCTION_CORS_ORIGINS"] = "https://app.flixyfy.example,https://prod.flixyfy.test"

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_is_sanitized_and_nonzero() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["checks"]["database_exists"] is True
    assert data["checks"]["read_only_connection"] is True
    assert data["checks"]["nonzero_counts"] is True
    assert data["counts"]["canonical_identities"] > 0
    assert "C:\\" not in response.text


def test_search_current_historical_and_compact_contracts() -> None:
    current = client.get("/api/v1/movies", params={"domain": "current", "limit": 3, "response_mode": "compact"})
    historical = client.get("/api/v1/movies", params={"domain": "historical", "limit": 3, "response_mode": "compact"})
    assert current.status_code == 200
    assert historical.status_code == 200
    assert current.json()["total"] > 0
    assert historical.json()["total"] > 0
    card = historical.json()["items"][0]
    assert "canonical_movie_id" in card
    assert "availability_summary" in card
    assert "overview" not in card

    search = client.get("/api/v1/search", params={"q": card["title"], "limit": 5, "response_mode": "compact"})
    assert search.status_code == 200
    assert search.json()["items"]


def test_search_intelligence_person_and_combined_filters() -> None:
    person = client.get(
        "/api/v1/search/intelligence",
        params={"q": "NTR movies", "limit": 5, "payload": "compact"},
    )
    assert person.status_code == 200
    person_data = person.json()
    assert person_data["entities"]["people"]
    assert person_data["items"]
    assert any("people" in item.get("matched_fields", []) for item in person_data["items"])

    combined = client.get(
        "/api/v1/search/intelligence",
        params={"q": "Telugu action movies", "limit": 5, "payload": "compact"},
    )
    assert combined.status_code == 200
    combined_data = combined.json()
    assert combined_data["entities"]["languages"]
    assert combined_data["entities"]["genres"]
    assert combined_data["items"]


def test_numeric_and_canonical_details() -> None:
    current = client.get("/api/v1/movies", params={"domain": "current", "limit": 10}).json()["items"]
    tmdb_card = next(item for item in current if item["tmdb_id"] is not None)
    numeric = client.get(f"/api/v1/movies/{tmdb_card['tmdb_id']}")
    assert numeric.status_code == 200
    numeric_data = numeric.json()
    assert numeric_data["tmdb_id"] == tmdb_card["tmdb_id"]
    assert "availability" in numeric_data

    historical = client.get("/api/v1/movies", params={"domain": "historical", "limit": 50}).json()["items"]
    hist_card = next(item for item in historical if item["tmdb_id"] is None)
    canonical = client.get(f"/api/v1/movies/by-canonical/{hist_card['canonical_movie_id']}")
    assert canonical.status_code == 200
    canonical_data = canonical.json()
    assert canonical_data["canonical_movie_id"] == hist_card["canonical_movie_id"]
    assert canonical_data["tmdb_id"] is None


def test_movie_detail_identifier_compatibility() -> None:
    current = client.get("/api/v1/movies", params={"domain": "current", "limit": 10}).json()["items"]
    tmdb_card = next(item for item in current if item["tmdb_id"] is not None)
    tmdb_search_id = tmdb_card["movie_identity"]["search_id"]
    assert tmdb_search_id == f"tmdb-{tmdb_card['tmdb_id']}"

    tmdb_canonical = client.get(f"/api/v1/movies/{tmdb_card['canonical_movie_id']}")
    tmdb_search = client.get(f"/api/v1/movies/{tmdb_search_id}")
    assert tmdb_canonical.status_code == 200
    assert tmdb_search.status_code == 200
    assert tmdb_canonical.json()["canonical_movie_id"] == tmdb_card["canonical_movie_id"]
    assert tmdb_search.json()["canonical_movie_id"] == tmdb_card["canonical_movie_id"]

    historical = client.get("/api/v1/movies", params={"domain": "historical", "limit": 50}).json()["items"]
    hist_card = next(item for item in historical if item["tmdb_id"] is None)
    hist_search_id = hist_card["movie_identity"]["search_id"]
    assert hist_search_id.startswith("hist-")

    hist_route = client.get(f"/api/v1/movies/{hist_search_id}")
    hist_canonical_route = client.get(f"/api/v1/movies/by-canonical/{hist_search_id}")
    assert hist_route.status_code == 200
    assert hist_canonical_route.status_code == 200
    assert hist_route.json()["canonical_movie_id"] == hist_card["canonical_movie_id"]
    assert hist_canonical_route.json()["canonical_movie_id"] == hist_card["canonical_movie_id"]


def test_movie_detail_missing_and_malformed_identifiers_are_structured() -> None:
    missing_numeric = client.get("/api/v1/movies/999999999")
    assert missing_numeric.status_code == 404
    assert missing_numeric.json()["error"]["code"] == "MOVIE_NOT_FOUND"

    unknown_slug_like = client.get("/api/v1/movies/not-a-stored-search-id")
    assert unknown_slug_like.status_code == 404
    assert unknown_slug_like.json()["error"]["code"] == "MOVIE_NOT_FOUND"

    malformed_route = client.get("/api/v1/movies/bad$id")
    assert malformed_route.status_code == 400
    assert malformed_route.json()["error"]["code"] == "INVALID_MOVIE_ID"

    malformed_canonical = client.get("/api/v1/movies/by-canonical/not:a:movie")
    assert malformed_canonical.status_code == 400
    assert malformed_canonical.json()["error"]["code"] == "INVALID_MOVIE_ID"


def test_providers_and_meta_are_public_safe() -> None:
    providers = client.get("/api/v1/providers")
    assert providers.status_code == 200
    assert providers.json()["items"]
    meta = client.get("/api/v1/meta")
    assert meta.status_code == 200
    assert "C:\\" not in meta.text
    assert meta.json()["youtube_candidate_database_connected"] is False


def test_cors_localhost_and_configured_production_origin() -> None:
    headers = {
        "Origin": "http://localhost:3000",
        "Access-Control-Request-Method": "GET",
    }
    local = client.options("/health", headers=headers)
    assert local.headers.get("access-control-allow-origin") == "http://localhost:3000"

    prod_headers = {
        "Origin": "https://prod.flixyfy.test",
        "Access-Control-Request-Method": "GET",
    }
    prod = client.options("/health", headers=prod_headers)
    assert prod.headers.get("access-control-allow-origin") == "https://prod.flixyfy.test"


def test_compact_is_smaller_than_full_and_details_stay_full() -> None:
    full = client.get("/api/v1/movies", params={"limit": 1, "response_mode": "full"})
    compact = client.get("/api/v1/movies", params={"limit": 1, "response_mode": "compact"})
    assert full.status_code == 200
    assert compact.status_code == 200
    assert len(compact.content) < len(full.content)
    full_item = full.json()["items"][0]
    compact_item = compact.json()["items"][0]
    assert "overview" in full_item
    assert "overview" not in compact_item

    detail = client.get(f"/api/v1/movies/canonical/{full_item['canonical_movie_id']}")
    assert detail.status_code == 200
    assert "overview" in detail.json()
    assert "availability" in detail.json()
