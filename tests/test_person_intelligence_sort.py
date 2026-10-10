import pytest
from fastapi import HTTPException

from app import main


@pytest.mark.parametrize(
    ("sort", "page_order", "final_order"),
    [
        ("popular", "rating DESC NULLS LAST, release_year DESC NULLS LAST", "page_rows.rating DESC NULLS LAST"),
        ("newest", "release_date DESC NULLS LAST, release_year DESC NULLS LAST", "page_rows.sort_release_date DESC NULLS LAST"),
        ("oldest", "release_year ASC NULLS LAST", "page_rows.sort_release_year ASC NULLS LAST"),
        ("title", "title ASC NULLS LAST", "page_rows.title ASC NULLS LAST"),
    ],
)
def test_person_intelligence_sort_uses_deterministic_key_and_projection_order(
    monkeypatch, sort, page_order, final_order
):
    captured = {}
    monkeypatch.setattr(main, "_movie_predicates", lambda *args: (["i.canonical_movie_id IS NOT NULL"], []))
    monkeypatch.setattr(main, "_rows", lambda sql, params: captured.update(sql=sql, params=params) or [])
    monkeypatch.setattr(main, "_require_canonical_release_dates", lambda *args, **kwargs: None)

    total, items = main._person_intelligence_page(
        person_id=123,
        domain="current",
        page=1,
        limit=24,
        provider=None,
        language=None,
        genre=None,
        year=None,
        sort=sort,
    )

    assert total == 0
    assert items == []
    assert f"ORDER BY {page_order}" in captured["sql"]
    assert f"ORDER BY {final_order}" in captured["sql"]


def test_person_intelligence_rejects_unknown_sort(monkeypatch):
    with pytest.raises(HTTPException) as error:
        main._person_intelligence_page(
            person_id=123,
            domain="current",
            page=1,
            limit=24,
            provider=None,
            language=None,
            genre=None,
            year=None,
            sort="random",
        )
    assert error.value.status_code == 400


def test_person_intelligence_projects_approved_provider_and_youtube_summaries(monkeypatch):
    captured = {}
    monkeypatch.setattr(main, "_movie_predicates", lambda *args: (["i.canonical_movie_id IS NOT NULL"], []))

    def rows(sql, params):
        captured["sql"] = sql
        return [{
            "total": 1,
            "canonical_movie_id": "TMDB:42",
            "matched_person_roles": ["actor"],
            "runtime_has_ott": True,
            "runtime_has_youtube": True,
            "runtime_provider_count": 2,
            "runtime_youtube_count": 3,
        }]

    monkeypatch.setattr(main, "_rows", rows)
    total, items = main._person_intelligence_page(
        person_id=123,
        domain="current",
        page=1,
        limit=24,
        provider=None,
        language=None,
        genre=None,
        year=None,
        sort="popular",
    )

    assert "runtime_has_ott" in captured["sql"]
    assert "runtime_has_youtube" in captured["sql"]
    assert "runtime_provider_count" in captured["sql"]
    assert "runtime_youtube_count" in captured["sql"]
    assert total == 1
    assert items[0]["has_ott"] is True
    assert items[0]["has_youtube"] is True
    assert items[0]["provider_count"] == 2
    assert items[0]["youtube_count"] == 3


def test_search_intelligence_forwards_sort_to_canonical_person_query(monkeypatch):
    captured = {}
    monkeypatch.setattr(main, "_parse_person_id", lambda value: int(value) if value else None)
    monkeypatch.setattr(
        main,
        "_person_intelligence_page",
        lambda *args, **kwargs: captured.update(args=args, kwargs=kwargs) or (0, []),
    )
    monkeypatch.setattr(main, "_items_payload", lambda *args: {})

    main.search_intelligence(person_id="123", page=1, limit=24, sort="newest")

    assert captured["args"][-1] == "newest"
