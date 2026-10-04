import pytest

import app.main as main


def test_person_language_uses_serving_credits_and_quarantines_ties(monkeypatch):
    captured = []
    monkeypatch.setattr(main, "_one", lambda sql, params=(): {"total": 1})
    monkeypatch.setattr(main, "_rows", lambda sql, params=(): (captured.append((sql, params)) or []))

    main._people_rows(domain="current", page=1, limit=24, language="te")

    assert len(captured) == 1
    sql, params = captured[0]
    assert "person_language_counts AS" in sql
    assert "movie_people_serving_v3" in sql
    assert "movie_identity_serving_v3" in sql
    assert "i.original_language" in sql
    assert "DENSE_RANK() OVER" in sql
    assert "language_status = 'CONFIRMED'" in sql
    assert "movie_language_serving_v3" not in sql
    assert "telugu" in params[4]
    assert "p.person_id ASC LIMIT %s OFFSET %s" in sql


@pytest.mark.parametrize(("page", "offset"), ((1, 0), (2, 100), (3, 200)))
def test_person_filmography_counts_deduplicates_and_paginates_in_sql(monkeypatch, page, offset):
    captured = {}
    monkeypatch.setattr(main, "_rows", lambda sql, params=(): (captured.update(page=(sql, params)) or []))

    total, rows = main._person_intelligence_page(1061913, "all", page, 100, None, None, None, None)

    page_sql, page_params = captured["page"]
    assert total == 0 and rows == []
    assert "person_movie_ids AS MATERIALIZED" in page_sql
    assert "WHERE person_id = %s AND canonical_movie_id IS NOT NULL" in page_sql
    assert "matched_keys AS MATERIALIZED" in page_sql
    assert "DISTINCT ON (i.canonical_movie_id)" in page_sql
    assert "total AS (SELECT COUNT(*)::bigint AS total FROM matched_keys)" in page_sql
    assert "page_keys AS (SELECT canonical_movie_id, rating, title FROM matched_keys" in page_sql
    assert "LIMIT %s OFFSET %s), page_rows AS" in page_sql
    assert "SELECT DISTINCT mp_roles.role" in page_sql
    assert "s.*, i.poster, i.backdrop" in page_sql
    assert "i.rating, pk.canonical_movie_id" not in page_sql
    assert "SELECT total.total, page_rows.*" in page_sql
    assert "ORDER BY page_rows.rating DESC NULLS LAST, page_rows.title ASC NULLS LAST, " in page_sql
    assert page_params[0] == 1061913
    assert page_params[-3:-1] == (100, offset)
    assert page_params[-1] == 1061913


def test_person_filmography_response_reports_full_total_and_has_more(monkeypatch):
    monkeypatch.setattr(
        main,
        "_person_intelligence_page",
        lambda *args: (250, [{"canonical_movie_id": "TMDB:1", "matched_person_roles": ["actor"]}]),
    )
    payload = main.search_intelligence(
        q=None, person_id="1061913", provider=None, domain="all",
        language=None, genre=None, year=None, page=2, limit=100,
    )
    assert payload["total"] == 250
    assert payload["page"] == 2
    assert payload["page_size"] == 100
    assert payload["has_more"] is True
    assert payload["items"][0]["match_evidence"]["person_id"] == "1061913"


def test_person_filmography_empty_page_keeps_full_total(monkeypatch):
    monkeypatch.setattr(
        main, "_rows", lambda sql, params=(): [{"total": 372, "canonical_movie_id": None}]
    )
    total, rows = main._person_intelligence_page(1061913, "all", 20, 40, None, None, None, None)
    assert total == 372
    assert rows == []


@pytest.mark.parametrize(
    ("total", "rows", "page", "limit", "expected_count", "expected_has_more"),
    [
        (0, [], 1, 5, 0, False),                         # zero rows
        (1, [{"canonical_movie_id": "TMDB:1", "matched_person_roles": ["actor"]}], 1, 5, 1, False),
        (2, [{"canonical_movie_id": f"TMDB:{i}", "matched_person_roles": ["actor"]} for i in (1, 2)], 1, 5, 2, False),
        (5, [{"canonical_movie_id": f"TMDB:{i}", "matched_person_roles": ["actor"]} for i in range(1, 6)], 1, 5, 5, False),
        (8, [{"canonical_movie_id": f"TMDB:{i}", "matched_person_roles": ["actor"]} for i in range(1, 6)], 1, 5, 5, True),
        (250, [{"canonical_movie_id": f"TMDB:{i}", "matched_person_roles": ["actor"]} for i in range(201, 251)], 3, 100, 50, False),
        (6, [], 5, 2, 0, False),                           # page beyond end
    ],
)
def test_person_filmography_response_page_boundaries(
    monkeypatch, total, rows, page, limit, expected_count, expected_has_more
):
    monkeypatch.setattr(main, "_person_intelligence_page", lambda *args: (total, rows))
    payload = main.search_intelligence(
        q=None, person_id="1061913", provider=None, domain="all",
        language=None, genre=None, year=None, page=page, limit=limit,
    )
    assert payload["total"] == total
    assert payload["page"] == page
    assert payload["page_size"] == limit
    assert len(payload["items"]) == expected_count
    assert payload["has_more"] is expected_has_more


def test_person_filmography_duplicate_edges_and_multiple_roles_are_projected_once(monkeypatch):
    captured = {"page_sql": ""}
    monkeypatch.setattr(main, "_rows", lambda sql, params=(): (captured.update(page_sql=sql) or [{
        "total": 1, "canonical_movie_id": "TMDB:1", "title": "Example", "rating": None,
        "poster": None, "matched_person_roles": ["actor", "director"],
    }]))
    total, rows = main._person_intelligence_page(1061913, "all", 1, 10, None, None, None, None)
    assert total == 1
    assert [row["canonical_movie_id"] for row in rows] == ["TMDB:1"]
    assert rows[0]["matched_person_roles"] == ["actor", "director"]
    assert rows[0]["rating"] is None and rows[0]["poster"] is None
    assert "COUNT(*)::bigint AS total FROM matched_keys" in captured["page_sql"]
    assert "DISTINCT ON (i.canonical_movie_id)" in captured["page_sql"]
    assert "SELECT DISTINCT mp_roles.role" in captured["page_sql"]


@pytest.mark.parametrize(
    ("value", "expected"),
    (("te", "telugu"), ("Telugu", "telugu"), ("hi", "hindi"),
     ("ta", "tamil"), ("ml", "malayalam"), ("kn", "kannada")),
)
def test_language_codes_and_names_share_primary_language_bucket(value, expected):
    assert expected in main._language_match_values(value)


def test_movie_card_availability_overrides_stale_search_document_flags():
    item = main._normalise_movie({
        "has_ott": False,
        "has_youtube": False,
        "provider_count": 0,
        "youtube_count": 0,
        "runtime_has_ott": True,
        "runtime_has_youtube": True,
        "runtime_provider_count": 2,
        "runtime_youtube_count": 3,
    })
    assert item["has_ott"] is True
    assert item["has_youtube"] is True
    assert item["provider_count"] == 2
    assert item["availability_count"] == 2
    assert item["youtube_count"] == 3


def test_movie_search_queries_project_live_accepted_availability(monkeypatch):
    captured = []
    monkeypatch.setattr(main, "_rows", lambda sql, params=(): (captured.append(sql) or []))
    monkeypatch.setattr(main, "_require_canonical_release_dates", lambda *args, **kwargs: None)
    main._search_rows("Jigris", "current", 24)
    assert captured
    assert "runtime_has_ott" in captured[0]
    assert "runtime_has_youtube" in captured[0]
    assert "provider_ott_availability_v3" in captured[0]
    assert "youtube_video_master_v3" in captured[0]


def test_youtube_serving_is_distinct_master_backed_and_capped_at_three(monkeypatch):
    captured = {}
    monkeypatch.setattr(main, "_rows", lambda sql, params=(): (captured.update(sql=sql, params=params) or []))
    main._youtube_rows("TMDB:123")
    sql = captured["sql"]
    assert captured["params"] == ("TMDB:123",)
    assert "DISTINCT ON (y.canonical_movie_id, y.video_id)" in sql
    assert "JOIN public.\"youtube_video_master_v3\" v" in sql
    assert "COALESCE(v.is_available, FALSE) IS TRUE" not in sql
    assert "serving_rank <= 3" in sql
    assert "to_jsonb(y)->>'selection_rank'" in sql
    assert "to_jsonb(edges)->>'confidence_score'" in sql
    assert "LEFT JOIN" not in sql


def test_movie_detail_slug_collision_fails_closed_and_identifier_priority_is_explicit(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        main,
        "_rows",
        lambda sql, params=(): (captured.update(sql=sql, params=params) or [
            {"canonical_movie_id": "TMDB:10", "tmdb_id": 10, "normalized_title": "aaya toofan"},
            {"canonical_movie_id": "TMDB:20", "tmdb_id": 20, "normalized_title": "aaya toofan"},
        ]),
    )
    for _ in range(2):
        with pytest.raises(main.HTTPException) as error:
            main._resolve_movie("historical", "aaya-toofan")
        assert error.value.status_code == 409
    assert "ORDER BY CASE WHEN LOWER(i.canonical_movie_id)" in captured["sql"]
    assert "i.canonical_movie_id ASC LIMIT 2" in captured["sql"]
