import pytest
from fastapi import HTTPException

import app.main as main


def test_combination_rejects_noncanonical_duplicate_and_out_of_range_ids():
    for values in (["1"], ["1", "1"], ["01", "2"], ["0", "2"], ["x", "2"],
                   ["9223372036854775808", "2"], [str(i) for i in range(1, 10)]):
        with pytest.raises(HTTPException) as exc:
            main._parse_combination_person_ids(list(values))
        assert exc.value.status_code == 400


def test_combination_roles_must_be_exact_and_aligned():
    with pytest.raises(HTTPException, match="align one-to-one"):
        main._parse_combination_roles(["actor"], 2)
    with pytest.raises(HTTPException, match="exact non-empty"):
        main._parse_combination_roles(["actor", ""], 2)
    assert main._parse_combination_roles([" Actor ", "director"], 2) == ["actor", "director"]


def test_combination_query_requires_all_people_and_exact_roles_and_returns_paged_evidence(monkeypatch):
    captured = {}

    def fake_rows(sql, params=()):
        captured["sql"] = sql
        captured["params"] = params
        return [{
            "total": 3,
            "canonical_movie_id": "TMDB:42",
            "title": "Shared film",
            "person_roles_by_id": {"148037": ["actor"], "115440": ["director"]},
        }]

    monkeypatch.setattr(main, "_rows", fake_rows)
    payload = main.search_intelligence_combination(
        person_id=["148037", "115440"],
        role=["actor", "director"],
        provider="netflix",
        domain="current",
        language="te",
        genre="drama",
        year=2020,
        page=2,
        limit=1,
    )

    assert "HAVING COUNT(DISTINCT pe.person_id) = %s" in captured["sql"]
    assert "sp.required_role IS NULL OR LOWER(BTRIM(e.role)) = sp.required_role" in captured["sql"]
    assert "provider_ott_availability_v3" in captured["sql"]
    assert captured["params"][:4] == (148037, "actor", 115440, "director")
    assert captured["params"][-3:] == (2, 1, 1)
    assert payload["total"] == 3
    assert payload["page"] == 2
    assert payload["has_more"] is True
    assert payload["person_ids"] == ["148037", "115440"]
    assert payload["items"][0]["canonical_movie_id"] == "TMDB:42"
    assert payload["items"][0]["match_evidence"]["people"] == {
        "148037": ["actor"],
        "115440": ["director"],
    }
    assert payload["items"][0]["match_evidence"]["provider_confirmed"] is True


def test_combination_fails_closed_when_any_selected_person_evidence_is_missing(monkeypatch):
    monkeypatch.setattr(main, "_rows", lambda *args: [{
        "total": 1,
        "canonical_movie_id": "TMDB:42",
        "person_roles_by_id": {"148037": ["actor"]},
    }])
    with pytest.raises(RuntimeError, match="missing selected person edge evidence"):
        main._person_combination_page(
            [148037, 115440], ["actor", "director"], "all", 1, 24,
            None, None, None, None,
        )


def test_combination_evidence_preserves_exact_serving_role_labels(monkeypatch):
    monkeypatch.setattr(main, "_rows", lambda *args: [{
        "total": 1,
        "canonical_movie_id": "TMDB:42",
        "person_roles_by_id": {"148037": ["cast"], "115440": ["director"]},
    }])
    _, items = main._person_combination_page(
        [148037, 115440], ["cast", "director"], "all", 1, 24,
        None, None, None, None,
    )
    assert items[0]["match_evidence"]["people"] == {
        "148037": ["cast"],
        "115440": ["director"],
    }


def test_combination_page_projects_runtime_availability_summaries(monkeypatch):
    captured = {}

    def rows(sql, params=()):
        captured["sql"] = sql
        return [{
            "total": 1,
            "canonical_movie_id": "TMDB:42",
            "person_roles_by_id": {"148037": ["cast"], "115440": ["director"]},
            "runtime_has_ott": True,
            "runtime_has_youtube": True,
            "runtime_provider_count": 2,
            "runtime_youtube_count": 3,
        }]

    monkeypatch.setattr(main, "_rows", rows)
    total, items = main._person_combination_page(
        [148037, 115440], ["cast", "director"], "current", 1, 24,
        None, None, None, None,
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
