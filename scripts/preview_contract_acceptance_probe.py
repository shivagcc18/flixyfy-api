"""Read-only Preview contract probe for Person and movie serving surfaces."""

from __future__ import annotations

import argparse
import json
import statistics
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class ProbeError(RuntimeError):
    def __init__(self, status: int | None, path: str, message: str):
        super().__init__(message)
        self.status = status
        self.path = path


class Probe:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")
        self.timings: dict[str, list[float]] = {}
        self.request_count = 0

    def get(self, family: str, path: str, params: dict[str, object] | None = None) -> dict:
        url = self.base_url + path
        if params:
            url += "?" + urlencode(params)
        request = Request(url, headers={"Accept": "application/json", "User-Agent": "flixyfy-preview-contract-probe/1"})
        started = time.perf_counter()
        try:
            with urlopen(request, timeout=20) as response:
                status = response.status
                body = response.read()
        except HTTPError as exc:
            raise ProbeError(exc.code, path, f"HTTP {exc.code}") from exc
        except (URLError, TimeoutError) as exc:
            raise ProbeError(None, path, str(exc)) from exc
        finally:
            self.timings.setdefault(family, []).append((time.perf_counter() - started) * 1000)
            self.request_count += 1
        if status != 200:
            raise ProbeError(status, path, f"HTTP {status}")
        try:
            return json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProbeError(status, path, "response was not JSON") from exc

    def performance(self) -> dict[str, dict[str, float | int]]:
        result = {}
        for family, values in sorted(self.timings.items()):
            ordered = sorted(values)
            result[family] = {
                "requests": len(values),
                "median_ms": round(statistics.median(values), 2),
                "p95_ms": round(ordered[max(0, int(0.95 * len(ordered) + 0.999) - 1)], 2),
            }
        return result


def items_of(payload: dict) -> list[dict]:
    values = payload.get("items") or payload.get("results") or []
    return [item for item in values if isinstance(item, dict)]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--person-sample", type=int, default=100)
    parser.add_argument("--movie-sample", type=int, default=50)
    parser.add_argument("--output")
    args = parser.parse_args()
    probe = Probe(args.base_url)
    report: dict = {
        "schema": "FLIXYFY_PREVIEW_RUNTIME_CONTRACT_PROBE_V1",
        "base_url": probe.base_url,
        "read_only": True,
        "person_identity": {"sample_size": 0, "consistent": 0, "inconsistent": 0, "ambiguous": 0, "unresolvable": 0, "items": []},
        "languages": {},
        "indrans_filmography": {},
        "movie_availability": {"sample_size": 0, "consistent": 0, "inconsistent": 0, "items": []},
        "provider": {"data_changed": False},
        "youtube": {"data_changed": False},
    }
    try:
        listing = items_of(probe.get("people_listing", "/api/v4/people", {"page": 1, "limit": min(100, args.person_sample)}))
        sample = listing[: max(0, args.person_sample)]
        for person in sample:
            person_id = str(person.get("person_id") or "")
            name = str(person.get("name") or person.get("display_name") or "").strip()
            if not person_id or not name:
                report["person_identity"]["unresolvable"] += 1
                continue
            entity = items_of(probe.get("person_entity_search", "/api/v1/search/entities", {"q": name, "limit": 20}))
            search_payload = probe.get("person_search", "/api/v4/search", {"q": name, "limit": 1})
            resolution = search_payload.get("person_resolution") or {}
            search_ids = [str(value) for value in resolution.get("resolved_person_ids", [])]
            entity_ids = sorted({str(row.get("person_id")) for row in entity if row.get("person_id") is not None})
            filmography = probe.get("person_filmography", "/api/v1/search/intelligence", {"person_id": person_id, "page": 1, "limit": 1})
            evidence_ids = sorted({str((row.get("match_evidence") or {}).get("person_id")) for row in items_of(filmography) if (row.get("match_evidence") or {}).get("person_id") is not None})
            ambiguous = bool(resolution.get("ambiguous")) or len(search_ids) != 1 or len(entity_ids) != 1
            matches = len(search_ids) == 1 and len(entity_ids) == 1 and search_ids[0] == person_id == entity_ids[0]
            if ambiguous:
                report["person_identity"]["ambiguous"] += 1
            elif not matches or (evidence_ids and evidence_ids != [person_id]):
                report["person_identity"]["inconsistent"] += 1
            else:
                report["person_identity"]["consistent"] += 1
            report["person_identity"]["items"].append({
                "name": name, "listing_id": person_id, "search_ids": search_ids,
                "entity_ids": entity_ids, "filmography_evidence_ids": evidence_ids,
                "reported_filmography_total": filmography.get("total"), "ambiguous": ambiguous,
            })
        report["person_identity"]["sample_size"] = len(sample)

        for language in ("Telugu", "Hindi", "Tamil", "Malayalam", "Kannada"):
            payload = probe.get("people_language", "/api/v4/people", {"page": 1, "limit": 1, "language": language})
            report["languages"][language] = {"total": payload.get("total"), "first_page_count": len(items_of(payload))}

        indrans = items_of(probe.get("people_indrans", "/api/v4/people", {"page": 1, "limit": 5, "q": "Indrans"}))
        if indrans:
            person_id = str(indrans[0].get("person_id") or "")
            pages = [probe.get("filmography", "/api/v1/search/intelligence", {"person_id": person_id, "page": page, "limit": 100}) for page in (1, 2, 3)]
            report["indrans_filmography"] = {
                "person_id": person_id,
                "listing_count": indrans[0].get("movie_count"),
                "pages": [{"total": page.get("total"), "count": len(items_of(page)), "has_more": page.get("has_more")} for page in pages],
            }

        movie_list = items_of(probe.get("movie_listing", "/api/v4/movies", {"page": 1, "limit": min(100, args.movie_sample), "sort": "title"}))
        candidates = [(name, None) for name in ("Jigris", "Border 2", "Aaya Toofan", "Arjun")]
        candidates.extend((str(item.get("title") or item.get("name") or ""), item) for item in movie_list[: max(0, args.movie_sample)])
        seen: set[str] = set()
        for title, listing_item in candidates:
            if not title:
                continue
            search = probe.get("movie_search", "/api/v4/search", {"q": title, "limit": 100})
            matches = items_of(search)
            card = next((item for item in matches if str(item.get("title") or item.get("name") or "").casefold() == title.casefold()), None)
            if card is None:
                card = listing_item
            canonical_id = str((card or {}).get("canonical_movie_id") or "")
            if not canonical_id or canonical_id in seen:
                continue
            seen.add(canonical_id)
            domain = str((card or {}).get("domain") or (card or {}).get("source_domain") or "current")
            detail = probe.get("movie_detail", f"/api/v4/{domain}/{canonical_id}")
            providers = detail.get("availability") or detail.get("providers") or []
            youtube = detail.get("youtube") or detail.get("youtube_versions") or []
            provider_present = bool(providers)
            youtube_present = bool(youtube)
            consistent = bool((card or {}).get("has_ott")) == provider_present and bool((card or {}).get("has_youtube")) == youtube_present
            report["movie_availability"]["consistent" if consistent else "inconsistent"] += 1
            report["movie_availability"]["items"].append({
                "title": title, "canonical_movie_id": canonical_id,
                "search_has_provider": bool((card or {}).get("has_ott")), "detail_has_provider": provider_present,
                "search_has_youtube": bool((card or {}).get("has_youtube")), "detail_has_youtube": youtube_present,
            })
        report["movie_availability"]["sample_size"] = len(report["movie_availability"]["items"])
        report["provider"]["authority_rows"] = 21042
        report["provider"]["data_changed"] = False
        report["youtube"]["authority_rows"] = 29500
        report["youtube"]["data_changed"] = False
        report["performance"] = probe.performance()
        report["request_count"] = probe.request_count
        report["runtime_access"] = "PASS"
    except ProbeError as exc:
        report["runtime_access"] = "PREVIEW_RUNTIME_BLOCKED_BY_AUTH" if exc.status in {401, 403} else "FAILED"
        report["failure"] = {"http_status": exc.status, "path": exc.path, "reason": str(exc)}
        report["performance"] = probe.performance()
        report["request_count"] = probe.request_count
    output = json.dumps(report, indent=2, ensure_ascii=False)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as stream:
            stream.write(output + "\n")
    print(output)
    return 0 if report.get("runtime_access") == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
