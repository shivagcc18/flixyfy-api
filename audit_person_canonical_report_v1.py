#!/usr/bin/env python3
"""
Build a read-only canonical person audit report.

Purpose:
  - Find duplicate/split person rows across historical + modern people.
  - Compare serving counts against historical, modern, Wiki, and YouTube evidence.
  - Produce reviewable AUTO_SAFE / REVIEW_HIGH / REVIEW_ONLY reports.

Does:
  - Reads local flixyfy.db by default.
  - Writes JSON, CSV, and Markdown reports under ./reports.

Does NOT:
  - Rebuild tables.
  - Update redirects.
  - Push prod.
  - Run enrichment.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


DEFAULT_DB = r"C:\Users\USER\Desktop\ott_project\data_factory\db\flixyfy.db"
REPORT_DIR = Path("reports")

PEOPLE_TABLE = "people_serving_v1"
REDIRECT_TABLE = "person_slug_redirect_v1"
YOUTUBE_TABLE = "youtube_link_serving_v1"

MAP_TABLES = {
    "historical_movie_people_seo_preprod_v1": "historical",
    "modern_movie_people_seo_preprod_v1": "modern",
    "wiki_filmography_source_v1": "wiki",
}

TIER1_LANGUAGES = {"te", "telugu", "ta", "tamil", "hi", "hindi", "kn", "kannada", "ml", "malayalam"}
TIER2_LANGUAGES = {"bn", "bengali", "mr", "marathi", "gu", "gujarati", "pa", "punjabi", "or", "odia", "as", "assamese"}

KNOWN_CANONICAL_ALIASES = {
    "ntr": "n-t-rama-rao",
    "n-t-rama-rao": "n-t-rama-rao",
    "anr": "akkineni-nageswara-rao",
    "a-nageswara-rao": "akkineni-nageswara-rao",
    "akkineni-nageswara-rao": "akkineni-nageswara-rao",
    "nbk": "nandamuri-balakrishna",
    "balakrishna": "nandamuri-balakrishna",
    "balakrishna-nandamuri": "nandamuri-balakrishna",
    "nandamuri-balakrishna": "nandamuri-balakrishna",
    "jayalalitha": "jayalalithaa",
    "jayalalithaa": "jayalalithaa",
    "shoban-babu": "sobhan-babu",
    "shobhan-babu": "sobhan-babu",
    "sobhan-babu": "sobhan-babu",
    "uppalapati-krishnam-raju": "krishnam-raju",
    "krishnam-raju": "krishnam-raju",
}


def configure_stdout() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass


def now_stamp() -> str:
    return dt.datetime.now().strftime("%Y%m%d_%H%M%S")


def now_iso() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def qident(value: str) -> str:
    return '"' + str(value).replace('"', '""') + '"'


def slugify(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(value or "").strip().lower()).strip("-")


def compact(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def normalize_name(value: Any) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", str(value or "").lower()))


def name_tokens(value: Any) -> list[str]:
    return re.findall(r"[a-z0-9]+", str(value or "").lower())


def parse_json_list(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, list):
        return [str(v) for v in value if v]
    try:
        data = json.loads(value)
    except Exception:
        return []
    if isinstance(data, list):
        return [str(v) for v in data if v]
    return []


def table_exists(cur: sqlite3.Cursor, table: str) -> bool:
    cur.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1", (table,))
    return cur.fetchone() is not None


def table_cols(cur: sqlite3.Cursor, table: str) -> set[str]:
    return {row["name"] for row in cur.execute(f"PRAGMA table_info({qident(table)})")}


def pick(row: dict[str, Any], *names: str) -> Any:
    for name in names:
        value = row.get(name)
        if value not in (None, ""):
            return value
    return None


def movie_identity(row: dict[str, Any]) -> str:
    title = pick(row, "title", "movie_title", "clean_title")
    year = pick(row, "release_year", "year")
    language = pick(row, "language_slug", "primary_language", "language")
    if title:
        return "|".join([slugify(title), str(year or ""), str(language or "").lower()])
    return str(pick(row, "movie_slug", "content_slug") or "").lower()


def levenshtein(a: str, b: str, max_distance: int = 3) -> int:
    if a == b:
        return 0
    if abs(len(a) - len(b)) > max_distance:
        return max_distance + 1
    if len(a) > len(b):
        a, b = b, a
    previous = list(range(len(a) + 1))
    for i, cb in enumerate(b, 1):
        current = [i]
        best = i
        for j, ca in enumerate(a, 1):
            value = min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ca != cb))
            current.append(value)
            best = min(best, value)
        if best > max_distance:
            return max_distance + 1
        previous = current
    return previous[-1]


def canonical_hint(slug: str) -> str:
    return KNOWN_CANONICAL_ALIASES.get(slug, slug)


def safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except Exception:
        return 0


def read_people(cur: sqlite3.Cursor) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for row in cur.execute(f"SELECT * FROM {qident(PEOPLE_TABLE)}"):
        item = dict(row)
        slug = str(item.get("person_slug") or "").strip()
        if not slug:
            continue
        aliases = set(parse_json_list(item.get("aliases_json")))
        aliases.add(item.get("display_name") or "")
        aliases.add(slug.replace("-", " "))
        aliases.add(slug)
        item["_aliases"] = sorted(a for a in aliases if a)
        item["_compact_aliases"] = sorted({compact(a) for a in aliases if compact(a)})
        item["_tokens"] = name_tokens(item.get("display_name") or slug)
        rows[slug] = item
    return rows


def read_redirects(cur: sqlite3.Cursor) -> dict[str, str]:
    if not table_exists(cur, REDIRECT_TABLE):
        return {}
    redirects = {}
    for row in cur.execute(f"SELECT old_slug, canonical_slug FROM {qident(REDIRECT_TABLE)} WHERE COALESCE(active, 1)=1"):
        redirects[str(row["old_slug"])] = str(row["canonical_slug"])
    return redirects


def read_movie_maps(cur: sqlite3.Cursor) -> dict[str, Any]:
    maps_by_person = defaultdict(lambda: defaultdict(dict))
    names_by_person = defaultdict(Counter)
    languages_by_person = defaultdict(Counter)
    roles_by_person = defaultdict(Counter)
    source_rows = Counter()

    for table, source in MAP_TABLES.items():
        if not table_exists(cur, table):
            continue
        cols = table_cols(cur, table)
        selected = [
            col
            for col in [
                "person_slug",
                "person_name",
                "movie_slug",
                "movie_title",
                "title",
                "release_year",
                "year",
                "language_slug",
                "primary_language",
                "role_type",
                "role",
                "has_youtube",
            ]
            if col in cols
        ]
        if not {"person_slug"}.issubset(selected):
            continue
        sql = f"SELECT {','.join(qident(c) for c in selected)} FROM {qident(table)}"
        for row in cur.execute(sql):
            item = dict(row)
            slug = str(item.get("person_slug") or "").strip()
            identity = movie_identity(item)
            if not slug or not identity:
                continue
            maps_by_person[slug][source][identity] = item
            source_rows[source] += 1
            if item.get("person_name"):
                names_by_person[slug][item["person_name"]] += 1
            language = str(pick(item, "language_slug", "primary_language") or "").lower()
            if language:
                languages_by_person[slug][language] += 1
            role = str(pick(item, "role_type", "role") or "actor").lower()
            roles_by_person[slug][role] += 1

    return {
        "maps_by_person": maps_by_person,
        "names_by_person": names_by_person,
        "languages_by_person": languages_by_person,
        "roles_by_person": roles_by_person,
        "source_rows": dict(source_rows),
    }


def read_youtube_slugs(cur: sqlite3.Cursor) -> set[str]:
    if not table_exists(cur, YOUTUBE_TABLE):
        return set()
    cols = table_cols(cur, YOUTUBE_TABLE)
    slug_col = "content_slug" if "content_slug" in cols else "movie_slug"
    where = "WHERE COALESCE(active, 1)=1" if "active" in cols else ""
    return {
        str(row[slug_col])
        for row in cur.execute(f"SELECT {qident(slug_col)} FROM {qident(YOUTUBE_TABLE)} {where}")
        if row[slug_col]
    }


def dominant_language(languages: Counter) -> str:
    return languages.most_common(1)[0][0] if languages else ""


def in_scope(person: dict[str, Any], languages: Counter, tier: str) -> bool:
    if tier == "all":
        return True
    lang = dominant_language(languages)
    lang_set = TIER1_LANGUAGES if tier == "tier1" else TIER2_LANGUAGES
    if lang in lang_set:
        return True
    domain = str(person.get("domain") or "").lower()
    return tier == "tier1" and domain in {"historical", "modern", "modern_historical_bridge"} and not lang


def person_strength(person: dict[str, Any]) -> tuple[int, int, int, str]:
    return (
        safe_int(person.get("indexable")),
        safe_int(person.get("movie_count")),
        safe_int(person.get("youtube_movie_count")),
        str(person.get("display_name") or ""),
    )


def overlap_stats(a_movies: set[str], b_movies: set[str]) -> tuple[int, float]:
    if not a_movies or not b_movies:
        return 0, 0.0
    overlap = len(a_movies & b_movies)
    denom = max(1, min(len(a_movies), len(b_movies)))
    return overlap, overlap / denom


def classify_pair(
    left: dict[str, Any],
    right: dict[str, Any],
    redirects: dict[str, str],
    movies_by_person: dict[str, Any],
) -> dict[str, Any] | None:
    left_slug = left["person_slug"]
    right_slug = right["person_slug"]
    if left_slug == right_slug:
        return None

    left_movies = set().union(*movies_by_person.get(left_slug, {}).values()) if movies_by_person.get(left_slug) else set()
    right_movies = set().union(*movies_by_person.get(right_slug, {}).values()) if movies_by_person.get(right_slug) else set()
    overlap_count, overlap_ratio = overlap_stats(left_movies, right_movies)

    left_name = normalize_name(left.get("display_name") or left_slug)
    right_name = normalize_name(right.get("display_name") or right_slug)
    left_compact = compact(left_name)
    right_compact = compact(right_name)
    left_tokens = name_tokens(left_name)
    right_tokens = name_tokens(right_name)
    same_last_token = bool(left_tokens and right_tokens and left_tokens[-1] == right_tokens[-1])
    distance = levenshtein(left_compact, right_compact, max_distance=3)
    exact_alias = bool(set(left.get("_compact_aliases", [])) & set(right.get("_compact_aliases", [])))

    reasons = []
    confidence = 0.0

    known_left = canonical_hint(left_slug)
    known_right = canonical_hint(right_slug)
    shared_known_target = known_left == known_right and left_slug != right_slug
    if shared_known_target and known_left not in {left_slug, right_slug}:
        return None

    if shared_known_target:
        confidence = max(confidence, 0.99)
        reasons.append("known_canonical_alias")

    if redirects.get(left_slug) == right_slug or redirects.get(right_slug) == left_slug:
        confidence = max(confidence, 0.99)
        reasons.append("existing_redirect")

    if exact_alias:
        confidence = max(confidence, 0.95)
        reasons.append("exact_compact_alias")

    if overlap_count >= 5 and overlap_ratio >= 0.50:
        confidence = max(confidence, 0.90)
        reasons.append("high_movie_overlap")
    elif overlap_count >= 3 and overlap_ratio >= 0.30:
        confidence = max(confidence, 0.88)
        reasons.append("medium_movie_overlap")

    if same_last_token and distance <= 2 and min(len(left_compact), len(right_compact)) >= 7:
        confidence = max(confidence, 0.90)
        reasons.append("near_name_same_last_token")

    if left_name == right_name:
        confidence = max(confidence, 0.92)
        reasons.append("same_display_name")

    if not reasons:
        return None

    canonical = max([left, right], key=person_strength)
    duplicate = right if canonical is left else left
    action = "AUTO_SAFE" if confidence >= 0.96 else ("REVIEW_HIGH" if confidence >= 0.86 else "REVIEW_ONLY")

    return {
        "action": action,
        "confidence": round(confidence, 3),
        "canonical_slug": canonical["person_slug"],
        "canonical_name": canonical.get("display_name"),
        "canonical_movie_count": safe_int(canonical.get("movie_count")),
        "canonical_youtube_count": safe_int(canonical.get("youtube_movie_count")),
        "duplicate_slug": duplicate["person_slug"],
        "duplicate_name": duplicate.get("display_name"),
        "duplicate_movie_count": safe_int(duplicate.get("movie_count")),
        "duplicate_youtube_count": safe_int(duplicate.get("youtube_movie_count")),
        "overlap_movies": overlap_count,
        "overlap_ratio": round(overlap_ratio, 3),
        "name_distance": distance,
        "reasons": "|".join(reasons),
    }


def build_duplicate_candidates(people: dict[str, dict[str, Any]], redirects: dict[str, str], movie_maps: dict[str, Any], max_candidates: int) -> list[dict[str, Any]]:
    candidate_pairs = set()
    by_alias = defaultdict(list)
    by_last_token = defaultdict(list)

    for slug, person in people.items():
        for alias in person.get("_compact_aliases", []):
            if len(alias) >= 4:
                by_alias[alias].append(slug)
        tokens = person.get("_tokens") or []
        if tokens:
            by_last_token[(tokens[-1], tokens[0][:1])].append(slug)

    for slugs in by_alias.values():
        if 1 < len(slugs) <= 25:
            for i, left in enumerate(slugs):
                for right in slugs[i + 1 :]:
                    candidate_pairs.add(tuple(sorted([left, right])))

    for slugs in by_last_token.values():
        if 1 < len(slugs) <= 60:
            for i, left in enumerate(slugs):
                for right in slugs[i + 1 :]:
                    candidate_pairs.add(tuple(sorted([left, right])))

    for old_slug, canonical_slug in redirects.items():
        if old_slug in people and canonical_slug in people:
            candidate_pairs.add(tuple(sorted([old_slug, canonical_slug])))

    for slug, canonical_slug in KNOWN_CANONICAL_ALIASES.items():
        if slug in people and canonical_slug in people and slug != canonical_slug:
            candidate_pairs.add(tuple(sorted([slug, canonical_slug])))

    rows = []
    for left_slug, right_slug in sorted(candidate_pairs):
        item = classify_pair(people[left_slug], people[right_slug], redirects, movie_maps["maps_by_person"])
        if item:
            rows.append(item)

    rows.sort(key=lambda r: (r["action"] != "AUTO_SAFE", -r["confidence"], -r["canonical_movie_count"], r["canonical_slug"]))
    return rows[:max_candidates]


def build_coverage_rows(people: dict[str, dict[str, Any]], movie_maps: dict[str, Any], youtube_slugs: set[str], tier: str, max_rows: int) -> list[dict[str, Any]]:
    rows = []
    maps_by_person = movie_maps["maps_by_person"]
    languages_by_person = movie_maps["languages_by_person"]
    roles_by_person = movie_maps["roles_by_person"]

    for slug, person in people.items():
        languages = languages_by_person.get(slug, Counter())
        if not in_scope(person, languages, tier):
            continue

        source_maps = maps_by_person.get(slug, {})
        historical_movies = set(source_maps.get("historical", {}))
        modern_movies = set(source_maps.get("modern", {}))
        wiki_movies = set(source_maps.get("wiki", {}))
        all_source_movies = historical_movies | modern_movies | wiki_movies
        person_youtube_movies = {
            identity
            for identity, row in {**source_maps.get("historical", {}), **source_maps.get("modern", {}), **source_maps.get("wiki", {})}.items()
            if str(row.get("movie_slug") or "") in youtube_slugs or slugify(row.get("title")) in youtube_slugs
        }
        wiki_missing_historical = wiki_movies - historical_movies - modern_movies
        serving_count = safe_int(person.get("movie_count"))
        evidence_max = max(len(historical_movies), len(modern_movies), len(wiki_movies))
        too_high = serving_count > 0 and evidence_max > 0 and serving_count >= evidence_max * 1.6 and serving_count - evidence_max >= 20
        too_low = evidence_max >= serving_count + 15 and evidence_max >= serving_count * 1.2
        wiki_gap = len(wiki_missing_historical) >= 10
        youtube_gap = len(person_youtube_movies) > safe_int(person.get("youtube_movie_count")) + 5

        if not (too_high or too_low or wiki_gap or youtube_gap):
            continue

        reasons = []
        if too_high:
            reasons.append("serving_count_too_high")
        if too_low:
            reasons.append("serving_count_too_low")
        if wiki_gap:
            reasons.append("wiki_movies_missing_from_serving_maps")
        if youtube_gap:
            reasons.append("youtube_links_more_than_serving_youtube_count")

        rows.append(
            {
                "person_slug": slug,
                "display_name": person.get("display_name"),
                "domain": person.get("domain"),
                "primary_role": person.get("primary_role"),
                "dominant_language": dominant_language(languages),
                "movie_count_serving": serving_count,
                "youtube_count_serving": safe_int(person.get("youtube_movie_count")),
                "historical_movies": len(historical_movies),
                "modern_movies": len(modern_movies),
                "wiki_movies": len(wiki_movies),
                "max_single_source_movies": evidence_max,
                "union_source_movies": len(all_source_movies),
                "youtube_linked_source_movies": len(person_youtube_movies),
                "wiki_missing_from_historical_or_modern": len(wiki_missing_historical),
                "top_roles": json.dumps(dict(roles_by_person.get(slug, Counter()).most_common(4)), ensure_ascii=False),
                "reasons": "|".join(reasons),
                "sample_wiki_missing": json.dumps(sorted(list(wiki_missing_historical))[:10], ensure_ascii=False),
            }
        )

    rows.sort(
        key=lambda r: (
            "serving_count_too_high" not in r["reasons"],
            "serving_count_too_low" not in r["reasons"],
            -abs(r["max_single_source_movies"] - r["movie_count_serving"]),
            r["display_name"] or "",
        )
    )
    return rows[:max_rows]


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def write_markdown(path: Path, report: dict[str, Any], duplicate_rows: list[dict[str, Any]], coverage_rows: list[dict[str, Any]]) -> None:
    action_counts = Counter(row["action"] for row in duplicate_rows)
    reason_counts = Counter()
    for row in coverage_rows:
        for reason in str(row.get("reasons") or "").split("|"):
            if reason:
                reason_counts[reason] += 1

    lines = [
        "# Person Canonical Audit Report",
        "",
        f"- Created: {report['created_at']}",
        f"- DB: `{report['db']}`",
        f"- Tier: `{report['tier']}`",
        f"- People rows: {report['counts']['people_rows']}",
        f"- Redirect rows: {report['counts']['redirect_rows']}",
        f"- YouTube link rows: {report['counts']['youtube_link_rows']}",
        f"- Duplicate candidates: {len(duplicate_rows)}",
        f"- Coverage issues: {len(coverage_rows)}",
        "",
        "## Duplicate Actions",
        "",
    ]
    for action in ["AUTO_SAFE", "REVIEW_HIGH", "REVIEW_ONLY"]:
        lines.append(f"- {action}: {action_counts.get(action, 0)}")
    lines.extend(["", "## Coverage Reasons", ""])
    for reason, count in reason_counts.most_common():
        lines.append(f"- {reason}: {count}")
    lines.extend(["", "## Top Duplicate Candidates", ""])
    for row in duplicate_rows[:25]:
        lines.append(
            f"- {row['action']} {row['confidence']}: {row['duplicate_slug']} -> {row['canonical_slug']} "
            f"({row['reasons']}, duplicate {row['duplicate_movie_count']}, canonical {row['canonical_movie_count']})"
        )
    lines.extend(["", "## Top Coverage Issues", ""])
    for row in coverage_rows[:25]:
        lines.append(
            f"- {row['person_slug']} / {row['display_name']}: serving={row['movie_count_serving']}, "
            f"historical={row['historical_movies']}, modern={row['modern_movies']}, wiki={row['wiki_movies']}, "
            f"union={row['union_source_movies']} ({row['reasons']})"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    configure_stdout()
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=DEFAULT_DB)
    parser.add_argument("--tier", choices=["tier1", "tier2", "all"], default="tier1")
    parser.add_argument("--max-duplicates", type=int, default=5000)
    parser.add_argument("--max-coverage", type=int, default=5000)
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        raise SystemExit(f"DB not found: {db_path}")

    stamp = now_stamp()
    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    try:
        cur = con.cursor()
        if not table_exists(cur, PEOPLE_TABLE):
            raise SystemExit(f"Missing required table: {PEOPLE_TABLE}")

        people = read_people(cur)
        redirects = read_redirects(cur)
        movie_maps = read_movie_maps(cur)
        youtube_slugs = read_youtube_slugs(cur)

        duplicate_rows = build_duplicate_candidates(people, redirects, movie_maps, args.max_duplicates)
        coverage_rows = build_coverage_rows(people, movie_maps, youtube_slugs, args.tier, args.max_coverage)

        duplicate_csv = REPORT_DIR / f"person_canonical_audit_duplicates_v1_{stamp}.csv"
        coverage_csv = REPORT_DIR / f"person_canonical_audit_coverage_v1_{stamp}.csv"
        json_path = REPORT_DIR / f"person_canonical_audit_v1_{stamp}.json"
        md_path = REPORT_DIR / f"person_canonical_audit_summary_v1_{stamp}.md"

        report = {
            "created_at": now_iso(),
            "db": str(db_path),
            "tier": args.tier,
            "counts": {
                "people_rows": len(people),
                "redirect_rows": len(redirects),
                "youtube_link_rows": len(youtube_slugs),
                "source_rows": movie_maps["source_rows"],
                "duplicate_candidates": len(duplicate_rows),
                "coverage_issues": len(coverage_rows),
            },
            "outputs": {
                "duplicates_csv": str(duplicate_csv),
                "coverage_csv": str(coverage_csv),
                "json": str(json_path),
                "markdown": str(md_path),
            },
            "duplicate_action_counts": dict(Counter(row["action"] for row in duplicate_rows)),
            "top_duplicate_candidates": duplicate_rows[:100],
            "top_coverage_issues": coverage_rows[:100],
        }

        write_csv(duplicate_csv, duplicate_rows)
        write_csv(coverage_csv, coverage_rows)
        json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        write_markdown(md_path, report, duplicate_rows, coverage_rows)

        print(json.dumps({"status": "PASS", **report["counts"], "outputs": report["outputs"]}, ensure_ascii=False, indent=2))
    finally:
        con.close()


if __name__ == "__main__":
    main()
