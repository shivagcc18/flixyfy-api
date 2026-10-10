import pytest

import app.main as main
from app.person_alias_crosswalk_v1 import AliasResolution, resolve_person_alias, resolve_preview_person_alias


def test_generic_alias_resolver_requires_evidence_for_unique_mapping():
    rows = [
        {"alias": "Sample Stage Name", "outcome": "UNIQUE_CANONICAL", "canonical_person_id": "42", "evidence": ["wikidata:Q1"]},
    ]
    result = resolve_person_alias("sample-stage name", rows)
    assert result is not None
    assert result.outcome == "UNIQUE_CANONICAL"
    assert result.canonical_person_id == "42"

    unsupported = resolve_person_alias(
        "Sample Stage Name",
        [{"alias": "Sample Stage Name", "outcome": "UNIQUE_CANONICAL", "canonical_person_id": "42", "evidence": []}],
    )
    assert unsupported is not None and unsupported.outcome == "UNSUPPORTED"


@pytest.mark.parametrize(
    ("rows", "expected"),
    [
        ([{"alias": "One", "outcome": "AMBIGUOUS", "evidence": ["two_candidates"]}], "AMBIGUOUS"),
        ([
            {"alias": "One", "outcome": "UNIQUE_CANONICAL", "canonical_person_id": "1", "evidence": ["a"]},
            {"alias": "One", "outcome": "UNIQUE_CANONICAL", "canonical_person_id": "2", "evidence": ["b"]},
        ], "AMBIGUOUS"),
        ([{"alias": "One", "outcome": "UNSUPPORTED", "evidence": ["no_match"]}], "UNSUPPORTED"),
    ],
)
def test_alias_resolver_fails_closed_for_ambiguous_or_unsupported(rows, expected):
    result = resolve_person_alias("ONE", rows)
    assert result is not None and result.outcome == expected
    assert result.canonical_person_id is None


def test_bundled_crosswalk_is_inactive_outside_preview(monkeypatch):
    monkeypatch.delenv("VERCEL_ENV", raising=False)
    monkeypatch.delenv("FLIXYFY_RUNTIME_ENV", raising=False)
    assert resolve_preview_person_alias("ANR") is None
    monkeypatch.setenv("VERCEL_ENV", "production")
    assert resolve_preview_person_alias("ANR") is None


def test_preview_crosswalk_data_has_evidence_and_blocks_short_name_guesses(monkeypatch):
    monkeypatch.setenv("VERCEL_ENV", "preview")
    for alias in ("ANR", "Akkineni Nageswara Rao", "A. Nageswara Rao", "A Nageswara Rao"):
        result = resolve_preview_person_alias(alias)
        assert result is not None
        assert (result.outcome, result.canonical_person_id) == ("UNIQUE_CANONICAL", "237254")
    nithiin = resolve_preview_person_alias("Nithiin")
    assert nithiin is not None and (nithiin.outcome, nithiin.canonical_person_id) == ("UNIQUE_CANONICAL", "1001828")
    long_form = resolve_preview_person_alias("Nithin Kumar Reddy")
    assert long_form is not None and (long_form.outcome, long_form.canonical_person_id) == ("UNIQUE_CANONICAL", "1001828")
    ambiguous = resolve_preview_person_alias("Nithin")
    assert ambiguous is not None and ambiguous.outcome == "AMBIGUOUS"
    unsupported = resolve_preview_person_alias("Nithin Reddy")
    assert unsupported is not None and unsupported.outcome == "UNSUPPORTED"


def test_search_entities_returns_canonical_id_for_crosswalk_alias(monkeypatch):
    monkeypatch.setattr(
        main,
        "resolve_preview_person_alias",
        lambda _q: AliasResolution("UNIQUE_CANONICAL", "anr", "237254", ("wikidata:Q278980",), ("237254",)),
    )
    monkeypatch.setattr(main, "_person_entity_rows_by_id", lambda person_id: [{
        "person_id": person_id, "display_name": "Akkineni Nageswara Rao",
        "aliases": ["Akkineni Nageswara Rao"], "roles": ["actor"], "movie_count": 81,
    }])
    payload = main.search_entities("ANR", "person", 8)
    assert payload["items"][0]["person_id"] == "237254"
    assert payload["person_resolution"]["outcome"] == "UNIQUE_CANONICAL"


def test_v4_alias_search_uses_canonical_serving_graph_and_blocks_sidecar(monkeypatch):
    monkeypatch.setattr(
        main,
        "resolve_preview_person_alias",
        lambda _q: AliasResolution("UNIQUE_CANONICAL", "anr", "237254", ("accepted_seed:237254",), ("237254",)),
    )
    captured = {}

    def page(person_id, domain, page, limit, provider, language, genre, year, *args):
        captured.update(person_id=person_id, domain=domain, page=page, limit=limit)
        return 81, [{"canonical_movie_id": "TMDB:1", "title": "Example", "matched_person_roles": ["actor"]}]

    monkeypatch.setattr(main, "_person_intelligence_page", page)
    monkeypatch.setattr(main, "resolve_person_search", lambda *_args: pytest.fail("crosswalk must precede the stale sidecar"))
    payload = main._v4_search("ANR", 1, 24, None, None, None, None)
    assert captured["person_id"] == 237254
    assert payload["person_resolution"]["canonical_person_id"] == "237254"
    assert payload["items"][0]["match_evidence"]["person_id"] == "237254"


def test_v4_ambiguous_crosswalk_does_not_select_stale_sidecar(monkeypatch):
    monkeypatch.setattr(
        main,
        "resolve_preview_person_alias",
        lambda _q: AliasResolution("AMBIGUOUS", "nitin", None, ("collision",), ("1", "2")),
    )
    monkeypatch.setattr(main, "resolve_person_search", lambda *_args: pytest.fail("ambiguous alias must block sidecar"))
    monkeypatch.setattr(main, "_search_rows", lambda *_args: [])
    payload = main._v4_search("Nitin", 1, 24, None, None, None, None)
    assert payload["person_resolution"]["outcome"] == "AMBIGUOUS"
    assert payload["person_resolution"]["candidate_person_ids"] == ["1", "2"]
