import app.main as main
import pytest


def test_intelligence_person_page_filters_counts_and_offsets_in_sql(monkeypatch):
    captured = {"count": None, "page": None}
    monkeypatch.setattr(main, "_one", lambda sql, params=(): (captured.update(count=(sql, params)) or {"total": 299}))

    def rows(sql, params=()):
        captured["page"] = (sql, params)
        return [{"canonical_movie_id": "TMDB:2", "title": "Later", "matched_person_roles": ["actor"]}]

    monkeypatch.setattr(main, "_rows", rows)
    total, items = main._person_intelligence_page(1061913, "all", 2, 100, None, None, None, None)

    count_sql, count_params = captured["count"]
    page_sql, page_params = captured["page"]
    assert total == 299
    assert items[0]["canonical_movie_id"] == "TMDB:2"
    assert "COUNT(DISTINCT i.canonical_movie_id)" in count_sql
    assert "mp.person_id = %s" in count_sql
    assert "DISTINCT ON (i.canonical_movie_id)" in page_sql
    assert "ORDER BY rating DESC NULLS LAST, title ASC NULLS LAST, canonical_movie_id ASC" in page_sql
    assert "LIMIT %s OFFSET %s" in page_sql
    assert count_params.count(1061913) == 4
    assert page_params[0] == 1061913
    assert page_params[-2:] == (100, 100)


@pytest.mark.parametrize(("page", "expected_offset"), ((1, 0), (2, 100), (3, 200)))
def test_intelligence_person_pages_have_nonoverlapping_stable_offsets(monkeypatch, page, expected_offset):
    captured = {}
    monkeypatch.setattr(main, "_one", lambda sql, params=(): {"total": 250})
    monkeypatch.setattr(main, "_rows", lambda sql, params=(): (captured.update(sql=sql, params=params) or []))

    total, rows = main._person_intelligence_page(1061913, "current", page, 100, None, None, None, None)

    assert total == 250
    assert rows == []
    assert "DISTINCT ON (i.canonical_movie_id)" in captured["sql"]
    assert captured["params"][-2:] == (100, expected_offset)


def test_intelligence_person_page_response_has_total_and_has_more(monkeypatch):
    monkeypatch.setattr(
        main,
        "_person_intelligence_page",
        lambda *args: (299, [{"canonical_movie_id": "TMDB:101", "title": "Film", "matched_person_roles": ["actor"]}]),
    )
    payload = main.search_intelligence(
        q="Indrans", person_id="1061913", provider=None, domain="all",
        language=None, genre=None, year=None, page=2, limit=100,
    )
    assert payload["total"] == 299
    assert payload["page"] == 2
    assert payload["limit"] == 100
    assert payload["has_more"] is True
    assert payload["items"][0]["match_evidence"]["person_id"] == "1061913"


def test_people_primary_language_uses_serving_movie_edges_and_quarantines_ties(monkeypatch):
    queries = []
    monkeypatch.setattr(main, "_one", lambda sql, params=(): {"total": 1})
    monkeypatch.setattr(main, "_rows", lambda sql, params=(): (queries.append((sql, params)) or []))

    main._people_rows(domain="current", page=1, limit=24, language="te")

    assert len(queries) == 1
    sql, params = queries[0]
    assert "person_language_counts AS" in sql
    assert "movie_people_serving_v3" in sql
    assert "movie_identity_serving_v3" in sql
    assert "i.original_language" in sql
    assert "DENSE_RANK() OVER" in sql
    assert "ELSE NULL END AS primary_language" in sql
    assert "ELSE 'AMBIGUOUS' END AS language_status" in sql
    assert "ppl.primary_language = %s AND ppl.language_status = 'CONFIRMED'" in sql
    assert "movie_language_serving_v3" not in sql
    assert "telugu" in params


def test_all_tier_one_language_aliases_normalize_to_display_names():
    for value, expected in (
        ("te", "telugu"), ("Telugu", "telugu"),
        ("hi", "hindi"), ("Hindi", "hindi"),
        ("ta", "tamil"), ("Tamil", "tamil"),
        ("kn", "kannada"), ("Kannada", "kannada"),
        ("ml", "malayalam"), ("Malayalam", "malayalam"),
    ):
        assert main._language_match_values(value)[-1] == expected


def test_person_id_rejects_noncanonical_text_instead_of_name_fallback():
    with pytest.raises(main.HTTPException) as error:
        main._parse_person_id("Balakrishna")
    assert error.value.status_code == 400


def test_unknown_stable_person_returns_empty_page_and_zero_total(monkeypatch):
    monkeypatch.setattr(main, "_person_intelligence_page", lambda *args: (0, []))
    payload = main.search_intelligence(
        q=None, person_id="999999999999", provider=None, domain="all",
        language=None, genre=None, year=None, page=1, limit=100,
    )
    assert payload["items"] == []
    assert payload["total"] == 0
    assert payload["has_more"] is False
