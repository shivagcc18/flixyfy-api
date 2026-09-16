from fastapi.testclient import TestClient

from app import main


def _row(language="te", year=2024, poster=True):
    return {"title": "Fixture", "original_language": language, "release_year": year,
            "poster_url": "https://example.test/poster.jpg" if poster else None,
            "backdrop_url": "https://example.test/backdrop.jpg"}


def test_discovery_home_contract_and_scoped_queries(monkeypatch):
    calls = []

    def fake_movie_rows(domain, page=1, limit=24, provider=None, language=None, year=None, sort=None, year_from=None, year_to=None):
        calls.append({"domain": domain, "limit": limit, "provider": provider, "language": language,
                      "sort": sort, "year_from": year_from, "year_to": year_to})
        if year_from is not None:
            return 1, [_row("hi", 1965)]
        if provider == "youtube":
            return 1, [_row("ta")]
        if language:
            return 1, [_row(language)]
        return 1, [_row()]

    monkeypatch.setattr(main, "_movie_rows", fake_movie_rows)
    payload = TestClient(main.app).get("/api/v4/discovery/home").json()
    assert set(payload) == {"hero", "trending", "new_releases", "languages", "classics", "youtube"}
    assert set(payload["languages"]) == {"te", "hi", "ta", "kn", "ml"}
    assert all(len(payload[key]) <= 12 for key in ("hero", "trending", "new_releases", "classics", "youtube"))
    assert any(call["year_from"] == 1960 and call["year_to"] == 1999 for call in calls)
    assert any(call["provider"] == "youtube" for call in calls)
    assert all(call["limit"] <= 24 for call in calls)


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
