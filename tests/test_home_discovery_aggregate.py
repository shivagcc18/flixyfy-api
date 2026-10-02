from fastapi.testclient import TestClient

from app import main


def _row(language="te", year=2024, poster=True):
    return {"canonical_movie_id": f"TMDB:{year}", "tmdb_id": year, "domain": "current",
            "title": "Fixture", "original_language": language, "release_year": year,
            "poster": "https://example.test/poster.jpg" if poster else None,
            "backdrop": "https://example.test/backdrop.jpg", "rating": 7.0}


def test_discovery_home_contract_and_scoped_queries(monkeypatch):
    calls = []

    def fake_home_movie_rows(domain, limit=24, provider=None, language=None, year_from=None, year_to=None, sort=None):
        calls.append({"domain": domain, "limit": limit, "provider": provider, "language": language,
                      "sort": sort, "year_from": year_from, "year_to": year_to})
        if year_from is not None:
            return [_row("hi", 1965)]
        if provider == "youtube":
            return [_row("ta")]
        if language:
            return [_row(language)]
        return [_row()]

    monkeypatch.setattr(main, "_home_movie_rows", fake_home_movie_rows)
    response = TestClient(main.app).get(
        "/api/v4/discovery/home", headers={"Origin": "https://www.flixyfy.com"}
    )
    payload = response.json()
    assert set(payload) == {"hero", "trending", "new_releases", "languages", "classics", "youtube"}
    assert set(payload["languages"]) == {"te", "hi", "ta", "kn", "ml"}
    assert all(len(payload[key]) <= 12 for key in ("hero", "trending", "new_releases", "classics", "youtube"))
    assert any(call["year_from"] == 1960 and call["year_to"] == 1999 for call in calls)
    assert any(call["provider"] == "youtube" for call in calls)
    assert all(call["limit"] <= 24 for call in calls)
    assert len(calls) == 9
    assert response.headers["cache-control"] == "public, s-maxage=60, stale-while-revalidate=300"
    assert response.headers["access-control-allow-origin"] == "https://www.flixyfy.com"
    assert "origin" in response.headers["vary"].lower()


def test_home_rows_use_one_lean_select_without_count(monkeypatch):
    queries = []

    def fake_rows(sql, params=()):
        queries.append((sql, params))
        return [_row()]

    monkeypatch.setattr(main, "_rows", fake_rows)
    rows = main._home_movie_rows("current", 24, language="te", sort="popular")

    assert len(queries) == 1
    sql, params = queries[0]
    assert "COUNT(" not in sql.upper()
    assert "i.*" not in sql
    assert all(column in sql for column in (
        "i.canonical_movie_id", "i.tmdb_id", "i.title", "i.release_year", "i.domain",
        "i.original_language", "i.poster", "i.backdrop", "i.rating",
    ))
    assert params[-1] == 24
    assert rows[0]["title"] == "Fixture"


def test_movies_route_exposes_and_forwards_catalog_filters(monkeypatch):
    captured = {}

    def fake_content(*args):
        captured["args"] = args
        return {"ok": True}

    monkeypatch.setattr(main, "_v4_content", fake_content)
    response = TestClient(main.app).get("/api/v4/movies?domain=historical&sort=oldest&year_from=1960&year_to=1999&language=ta")
    assert response.status_code == 200
    assert captured["args"] == ("historical", 1, 24, None, "ta", None, "oldest", 1960, 1999)
    parameters = main.app.openapi()["paths"]["/api/v4/movies"]["get"]["parameters"]
    names = {parameter["name"] for parameter in parameters}
    assert {"domain", "sort", "year_from", "year_to", "provider", "language", "year", "page", "limit"} <= names
