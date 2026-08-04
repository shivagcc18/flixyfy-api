import pytest

import app.main as main


def test_person_query_forms_and_roles():
    assert main._person_query_forms("N. T. Rama Rao") == ("n t rama rao", "ntramarao")
    assert main._person_roles(["Cast", "Director", "cast"]) == ["actor", "director"]


def test_person_entity_payload_is_compact_and_stable():
    payload = main._person_entity_payload(
        {
            "person_id": 148037,
            "display_name": "N.T. Rama Rao Jr.",
            "aliases": ["N.T. Rama Rao Jr."],
            "roles": ["cast"],
            "movie_count": 35,
        },
        "NTR",
    )
    assert payload == {
        "entity_type": "person",
        "person_id": "148037",
        "display_name": "N.T. Rama Rao Jr.",
        "aliases": ["N.T. Rama Rao Jr."],
        "disambiguation": "actor; 35 serving movies",
        "roles": ["actor"],
    }


def test_entity_query_is_bounded_and_ranked(monkeypatch):
    captured = {}

    def fake_rows(sql, params=()):
        captured["sql"] = sql
        captured["params"] = params
        return []

    monkeypatch.setattr(main, "_rows", fake_rows)
    assert main._person_entity_rows("NTR", 100) == []
    assert "ORDER BY match_rank ASC" in captured["sql"]
    assert "LIMIT %s" in captured["sql"]
    assert captured["params"][-1] == 20


def test_canonical_person_search_uses_and_predicates_and_no_person_text_fallback(monkeypatch):
    captured = {}

    def fake_rows(sql, params=()):
        captured["sql"] = sql
        captured["params"] = params
        return []

    monkeypatch.setattr(main, "_rows", fake_rows)
    assert main._search_intelligence_rows(
        "Prabhas",
        "current",
        24,
        "netflix",
        237045,
        None,
        None,
        None,
    ) == []
    assert "mp.person_id = %s" in captured["sql"]
    assert "provider_ott_availability_v3" in captured["sql"]
    assert "s.search_text" not in captured["sql"]
    assert "s.people" not in captured["sql"]


def test_canonical_person_response_emits_integrity_evidence(monkeypatch):
    monkeypatch.setattr(
        main,
        "_search_intelligence_rows",
        lambda *args: [
            {
                "canonical_movie_id": "TMDB:1",
                "title": "Example",
                "matched_person_roles": ["Cast"],
            }
        ],
    )
    payload = main.search_intelligence(
        q="Prabhas",
        person_id="237045",
        provider="netflix",
        domain="current",
        language=None,
        genre=None,
        year=None,
        page=1,
        limit=24,
    )
    assert payload["items"][0]["match_evidence"] == {
        "person_id": "237045",
        "person_roles": ["actor"],
        "provider_key": "netflix",
        "provider_confirmed": True,
    }


def test_empty_canonical_result_is_valid(monkeypatch):
    monkeypatch.setattr(main, "_search_intelligence_rows", lambda *args: [])
    payload = main.search_intelligence(
        q=None,
        person_id="237045",
        provider="netflix",
        domain="current",
        language=None,
        genre=None,
        year=None,
        page=1,
        limit=24,
    )
    assert payload["total"] == 0
    assert payload["items"] == []


def test_missing_evidence_cannot_be_published(monkeypatch):
    monkeypatch.setattr(
        main,
        "_search_intelligence_rows",
        lambda *args: [{"canonical_movie_id": "TMDB:1", "title": "Invalid", "matched_person_roles": []}],
    )
    with pytest.raises(RuntimeError, match="person edge evidence"):
        main.search_intelligence(
            q=None,
            person_id="237045",
            provider="netflix",
            domain="current",
            language=None,
            genre=None,
            year=None,
            page=1,
            limit=24,
        )

