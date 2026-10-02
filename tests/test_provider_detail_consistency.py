import app.main as main


def offer(provider_key, availability_type, provider_category=None, country="IN"):
    return {
        "canonical_movie_id": "TMDB:1",
        "provider_key": provider_key,
        "provider_name": provider_key,
        "availability_type": availability_type,
        "provider_category": provider_category or availability_type,
        "country": country,
        "confidence_score": 100,
    }


def detail_with(monkeypatch, offers):
    monkeypatch.setattr(
        main,
        "_resolve_movie",
        lambda domain, slug: {
            "canonical_movie_id": "TMDB:1",
            "title": "Fixture Movie",
            "domain": "current",
        },
    )
    monkeypatch.setattr(main, "_provider_rows", lambda canonical_movie_id: offers)
    monkeypatch.setattr(main, "_youtube_rows", lambda canonical_movie_id: [])
    response = main.detail("current", "TMDB:1")
    return response["item"]["availability"]


def test_provider_serving_query_keeps_all_india_offer_categories(monkeypatch):
    captured = {}
    rows = [offer("prime_video", "FLATRATE"), offer("google_play_movies", "RENT", "BUY")]

    def fake_rows(sql, params):
        captured.update(sql=sql, params=params)
        return rows

    monkeypatch.setattr(main, "_rows", fake_rows)
    assert main._provider_rows("TMDB:1") == rows
    assert "LOWER(COALESCE(p.country, '')) IN ('in', 'india', 'ind')" in captured["sql"]
    assert "provider_category" not in captured["sql"]
    assert "availability_type" not in captured["sql"]


def test_one_provider_returns_one_detail_action(monkeypatch):
    result = detail_with(monkeypatch, [offer("prime_video", "FLATRATE")])
    assert [item["provider_key"] for item in result] == ["prime_video"]


def test_two_providers_return_both_ads_and_flatrate(monkeypatch):
    result = detail_with(
        monkeypatch,
        [offer("jiohotstar", "ADS"), offer("prime_video", "FLATRATE")],
    )
    assert {item["provider_key"] for item in result} == {"jiohotstar", "prime_video"}
    assert len(result) == 2


def test_three_providers_preserve_rent_buy_and_add_on_channel(monkeypatch):
    result = detail_with(
        monkeypatch,
        [
            offer("prime_video", "FLATRATE"),
            offer("amazon_video_store", "RENT"),
            offer("hoichoi_amazon_channel", "FLATRATE"),
        ],
    )
    assert {item["provider_key"] for item in result} == {
        "prime_video",
        "amazon_video_store",
        "hoichoi_amazon_channel",
    }
    assert len(result) == 3


def test_duplicate_offer_rows_dedupe_by_provider_key_not_offer_type(monkeypatch):
    result = detail_with(
        monkeypatch,
        [
            offer("prime_video", "FLATRATE"),
            offer("prime_video", "RENT"),
            offer("jiohotstar", "ADS"),
        ],
    )
    by_key = {item["provider_key"]: item for item in result}
    assert set(by_key) == {"prime_video", "jiohotstar"}
    assert len(result) == 2
    assert by_key["prime_video"]["availability_types"] == ["FLATRATE", "RENT"]


def test_search_provider_filter_uses_same_offer_category_policy():
    predicates, params = main._movie_predicates("current", provider="google_play_movies")
    sql = " AND ".join(predicates)
    assert "provider_category" not in sql
    assert "availability_type" not in sql
    assert "p.provider_key" in sql
    assert params[-1] == "google_play_movies"


def test_routable_fallback_and_label_only_are_both_preserved(monkeypatch):
    result = detail_with(
        monkeypatch,
        [offer("amazon_video_store", "RENT"), offer("regional_unknown", "ADS")],
    )
    by_key = {item["provider_key"]: item for item in result}
    assert by_key["amazon_video_store"]["navigation_kind"] == "SEARCH"
    assert by_key["amazon_video_store"]["button_url"].startswith("https://www.primevideo.com/search?")
    assert by_key["regional_unknown"]["navigation_kind"] == "LABEL_ONLY"
    assert by_key["regional_unknown"]["button_url"] is None
