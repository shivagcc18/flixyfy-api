import pytest

import app.main as main


def test_person_query_forms_and_roles():
    assert main._person_query_forms("N. T. Rama Rao") == ("n t rama rao", "ntramarao")
    assert main._person_query_forms("Jr NTR") == ("n t rama rao jr", "ntramaraojr")
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
        "_person_intelligence_page",
        lambda *args: (1, [
            {
                "canonical_movie_id": "TMDB:1",
                "title": "Example",
                "matched_person_roles": ["Cast"],
            }
        ]),
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
    monkeypatch.setattr(main, "_person_intelligence_page", lambda *args: (0, []))
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
        "_person_intelligence_page",
        lambda *args: (1, [{"canonical_movie_id": "TMDB:1", "title": "Invalid", "matched_person_roles": []}]),
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



def test_person_search_sidecar_fails_closed_for_duplicate_current_canonical_identities(monkeypatch):
    from app.search_sidecar_v1 import PersonSearch

    result = PersonSearch(
        query="ANR", normalized_alias="anr", provider=None, person_ids=("6192119",),
        person_names=("Akkineni Nageswara Rao",), canonical_movie_ids=("TMDB:1",), ambiguous=False,
    )
    monkeypatch.setattr(main, "resolve_person_search", lambda *args: result)
    monkeypatch.setattr(main, "_person_entity_rows", lambda query, limit: [
        {"person_id": "237254", "display_name": "Akkineni Nageswara Rao"},
        {"person_id": "6192119", "display_name": "Akkineni Nageswara Rao"},
    ])
    monkeypatch.setattr(main, "_search_rows", lambda *args: [])
    monkeypatch.setattr(main, "_person_search_rows", lambda *args: pytest.fail("stale sidecar must not select a duplicate identity"))

    payload = main._v4_search("ANR", 1, 5, None, None, None, None)

    assert "person_resolution" not in payload


def test_person_search_sidecar_uses_unique_current_canonical_identity(monkeypatch):
    from app.search_sidecar_v1 import PersonSearch

    result = PersonSearch(
        query="Nithiin", normalized_alias="nithiin", provider=None, person_ids=("1001828",),
        person_names=("Nithiin",), canonical_movie_ids=("TMDB:226837",), ambiguous=False,
    )
    monkeypatch.setattr(main, "resolve_person_search", lambda *args: result)
    monkeypatch.setattr(main, "_person_entity_rows", lambda query, limit: [
        {"person_id": "1001828", "display_name": "Nithiin"},
    ])
    captured = {}

    def page(person_id, page, limit, domain, provider, language, year):
        captured.update(person_id=person_id, page=page, limit=limit, domain=domain)
        return {"total": 133, "items": [{"canonical_movie_id": "TMDB:226837", "match_evidence": {"person_id": person_id}}]}

    monkeypatch.setattr(main, "_canonical_person_alias_page", page)

    payload = main._v4_search("Nithiin", 1, 5, None, None, None, None)

    assert payload["person_resolution"]["resolved_person_ids"] == ["1001828"]
    assert captured == {"person_id": "1001828", "page": 1, "limit": 5, "domain": "all"}
    assert payload["total"] == 133


def test_person_search_sidecar_uses_live_graph_pagination_after_identity_resolution(monkeypatch):
    from app.search_sidecar_v1 import PersonSearch

    result = PersonSearch(
        query="Nithiin", normalized_alias="nithiin", provider=None, person_ids=("1001828",),
        person_names=("Nithiin",), canonical_movie_ids=tuple(f"TMDB:{i}" for i in range(133)), ambiguous=False,
    )
    monkeypatch.setattr(main, "resolve_person_search", lambda *args: result)
    monkeypatch.setattr(main, "_person_entity_rows", lambda query, limit: [
        {"person_id": "1001828", "display_name": "Nithiin"},
    ])
    captured = {}

    def page(person_id, page, limit, domain, provider, language, year):
        captured.update(person_id=person_id, page=page, limit=limit, domain=domain)
        return {"total": 133, "items": [{"canonical_movie_id": "TMDB:101"}]}

    monkeypatch.setattr(main, "_canonical_person_alias_page", page)
    monkeypatch.setattr(main, "_person_search_rows", lambda *args: pytest.fail("bounded sidecar rows must not serve paged filmography"))

    payload = main._v4_search("Nithiin", 2, 100, None, None, None, None)

    assert captured == {"person_id": "1001828", "page": 2, "limit": 100, "domain": "all"}
    assert payload["total"] == 133
    assert payload["person_resolution"]["resolved_person_ids"] == ["1001828"]


def test_preview_v10_identity_precedes_older_alias_crosswalk(monkeypatch):
    from app.search_sidecar_v1 import PersonSearch

    monkeypatch.setenv("VERCEL_ENV", "preview")
    result = PersonSearch(
        query="Mahesh Babu", normalized_alias="maheshbabu", provider=None, person_ids=("116924",),
        person_names=("Mahesh Babu",), canonical_movie_ids=(), ambiguous=False,
    )
    monkeypatch.setattr(main, "resolve_person_search", lambda *args: result)
    monkeypatch.setattr(main, "resolve_preview_person_alias", lambda *args: pytest.fail("V10 Preview authority must run first"))
    monkeypatch.setattr(main, "_person_entity_rows_by_id", lambda person_id, *args: [{
        "person_id": person_id, "display_name": "Mahesh Babu", "aliases": ["Mahesh Babu"],
    }])
    monkeypatch.setattr(main, "_canonical_person_alias_page", lambda person_id, *args: {
        "total": 45, "items": [{"canonical_movie_id": "TMDB:1", "match_evidence": {"person_id": person_id}}],
    })

    payload = main._v4_search("Mahesh Babu", 1, 100, None, None, None, None)

    assert payload["person_resolution"]["source"] == "v1.3_sidecar"
    assert payload["person_resolution"]["resolved_person_ids"] == ["116924"]
    assert payload["items"][0]["match_evidence"]["person_id"] == "116924"


def test_preview_v10_ambiguity_cannot_be_overridden_by_older_unique_crosswalk(monkeypatch):
    from app.search_sidecar_v1 import PersonSearch

    monkeypatch.setenv("VERCEL_ENV", "preview")
    result = PersonSearch(
        query="Venkatesh", normalized_alias="venkatesh", provider=None, person_ids=("8", "9"),
        person_names=("Venkatesh", "Venkatesh"), canonical_movie_ids=(), ambiguous=True,
    )
    monkeypatch.setattr(main, "resolve_person_search", lambda *args: result)
    monkeypatch.setattr(main, "resolve_preview_person_alias", lambda *args: pytest.fail("V10 ambiguity must remain authoritative"))
    monkeypatch.setattr(main, "_search_rows", lambda *args: [])
    monkeypatch.setattr(main, "_canonical_person_alias_page", lambda *args: pytest.fail("ambiguous identity must not be selected"))

    payload = main._v4_search("Venkatesh", 1, 100, None, None, None, None)

    assert payload["person_resolution"]["ambiguous"] is True
    assert payload["person_resolution"]["resolved_person_ids"] == ["8", "9"]


def test_preview_people_alias_listing_uses_exact_v10_person_id_and_all_domains(monkeypatch):
    from app.search_sidecar_v1 import PersonSearch

    monkeypatch.setenv("VERCEL_ENV", "preview")
    result = PersonSearch(
        query="N.T. Rama Rao", normalized_alias="ntramarao", provider=None, person_ids=("1003933",),
        person_names=("N.T. Rama Rao",), canonical_movie_ids=(), ambiguous=False,
    )
    monkeypatch.setattr(main, "resolve_person_search", lambda *args: result)
    monkeypatch.setattr(main, "_person_entity_rows_by_id", lambda person_id, domain, language, min_movies: [{
        "person_id": person_id, "display_name": "N.T. Rama Rao", "aliases": ["N.T. Rama Rao"],
        "roles": ["cast"], "movie_count": 109,
    }])
    monkeypatch.setattr(main, "_people_rows", lambda *args: pytest.fail("unique V10 alias must use exact ID listing"))

    payload = main._flixyfy_v4_people(page=1, limit=100, q="N.T. Rama Rao", domain="all")

    assert payload["total"] == 1
    assert payload["items"][0]["person_id"] == "1003933"
    assert main._normalise_domain("all") == "all"
    assert main._domain_values("all") == ("current", "historical", "hollywood", "webseries")
