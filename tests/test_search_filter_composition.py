import app.main as main


def test_language_aliases_are_case_insensitive_for_codes_and_names():
    for value, expected in (
        ("te", ("te", "telugu")),
        ("Te", ("te", "telugu")),
        ("TELUGU", ("te", "telugu")),
        ("tElUgU", ("te", "telugu")),
        ("TA", ("ta", "tamil")),
        ("Tamil", ("ta", "tamil")),
        ("HI", ("hi", "hindi")),
        ("Malayalam", ("ml", "malayalam")),
        ("Kn", ("kn", "kannada")),
        ("BENGALI", ("bn", "bengali")),
        ("Mr", ("mr", "marathi")),
    ):
        assert main._language_match_values(value) == expected


def test_telugu_aliases_use_identity_and_language_relation_fallbacks():
    predicates, params = main._movie_predicates("current", language="telugu")
    sql = " ".join(predicates)

    assert "i.original_language" in sql
    assert "movie_language_serving_v3" in sql
    assert params[-4:] == [["te", "telugu"]] * 4


def test_language_provider_year_predicates_are_composed_with_and():
    predicates, params = main._movie_predicates(
        "current",
        provider="vi_movies_and_tv",
        language="te",
        year=2024,
    )
    sql = " AND ".join(predicates)

    assert sql.count("EXISTS") >= 2
    assert "i.release_year = %s" in sql
    assert "p.provider_key" in sql
    assert params[-6:] == [["te", "telugu"], ["te", "telugu"], ["te", "telugu"], ["te", "telugu"], 2024, "vi_movies_and_tv"]


def test_empty_structured_search_delegates_to_movie_rows(monkeypatch):
    captured = {}

    def fake_movie_rows(domain, page, limit, provider, language, year):
        captured.update(domain=domain, page=page, limit=limit, provider=provider, language=language, year=year)
        return 1, [{"canonical_movie_id": "TMDB:1396798", "title": "Pushpak Vimaan"}]

    monkeypatch.setattr(main, "_movie_rows", fake_movie_rows)
    payload = main._v4_search(None, 1, 24, None, "vi_movies_and_tv", "telugu", 2024)

    assert captured == {
        "domain": "current",
        "page": 1,
        "limit": 24,
        "provider": "vi_movies_and_tv",
        "language": "telugu",
        "year": 2024,
    }
    assert payload["total"] == 1
