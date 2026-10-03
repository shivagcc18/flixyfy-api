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

    def fake_movie_rows(domain, page, limit, provider, language, year, sort=None):
        captured.update(domain=domain, page=page, limit=limit, provider=provider, language=language, year=year, sort=sort)
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
        "sort": None,
    }
    assert payload["total"] == 1


def test_empty_structured_search_forwards_newest_sort(monkeypatch):
    captured = {}

    def fake_movie_rows(domain, page, limit, provider, language, year, sort=None):
        captured["sort"] = sort
        return 0, []

    monkeypatch.setattr(main, "_movie_rows", fake_movie_rows)
    main._v4_search(None, 2, 48, "current", None, "te", 2026, "newest")
    assert captured["sort"] == "newest"


def test_newest_order_uses_release_date_and_stable_ties(monkeypatch):
    queries = []
    monkeypatch.setattr(main, "_one", lambda sql, params=(): {"total": 0})

    def fake_rows(sql, params=()):
        queries.append(sql)
        return []

    monkeypatch.setattr(main, "_rows", fake_rows)
    monkeypatch.setattr(main, "_require_canonical_release_dates", lambda *args, **kwargs: None)
    main._movie_rows("current", sort="newest")

    query = queries[-1]
    assert "i.release_date DESC NULLS LAST" in query
    assert "i.release_year DESC NULLS LAST" in query
    assert "i.rating DESC NULLS LAST" in query
    assert "i.canonical_movie_id ASC" in query


def test_newest_order_fails_closed_on_invalid_calendar_date(monkeypatch):
    captured = {}

    def fake_rows(sql, params=()):
        captured["sql"] = sql
        return [{"release_date": "2026-02-31"}]

    monkeypatch.setattr(main, "_rows", fake_rows)
    try:
        main._require_canonical_release_dates("i", ["LOWER(i.domain) = %s"], ["current"])
    except main.HTTPException as error:
        assert error.status_code == 503
        assert "non-canonical release_date" in error.detail
    else:
        raise AssertionError("malformed calendar dates must fail closed")


def test_newest_search_date_validation_joins_only_matching_search_documents(monkeypatch):
    captured = {}
    monkeypatch.setattr(main, "_rows", lambda sql, params=(): (captured.update(sql=sql, params=params) or []))
    main._require_canonical_release_dates(
        "i", ["LOWER(i.domain) = %s", "LOWER(s.search_text) LIKE LOWER(%s)"],
        ["current", "%Peddi%"], include_search_documents=True,
    )
    assert "movie_search_document_v3" in captured["sql"]
    assert captured["params"] == ("current", "%Peddi%")
