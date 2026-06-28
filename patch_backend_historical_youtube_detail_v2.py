from pathlib import Path
from datetime import datetime

TARGET = Path("app/main_v3.py")
text = TARGET.read_text(encoding="utf-8")

backup = TARGET.with_name("main_v3.py.backup_historical_youtube_detail_patch_v2_" + datetime.now().strftime("%Y%m%d_%H%M%S"))
backup.write_text(text, encoding="utf-8")

MARKER = "# FLIXYFY_HISTORICAL_YOUTUBE_DETAIL_PATCH_V2"

HELPER_CODE = r'''
# FLIXYFY_HISTORICAL_YOUTUBE_DETAIL_PATCH_V2
def _hist_yt_db_url():
    import os

    for key in ("DATABASE_URL", "POSTGRES_URL", "NEON_DATABASE_URL", "DATABASE_PUBLIC_URL"):
        value = os.getenv(key)
        if value:
            return value

    return None


def _hist_yt_rows(sql, params=None):
    params = params or []
    database_url = _hist_yt_db_url()

    if not database_url:
        return []

    try:
        import psycopg2
        from psycopg2.extras import RealDictCursor

        conn = psycopg2.connect(database_url)
        try:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(sql, params)
                return [dict(row) for row in cur.fetchall()]
        finally:
            conn.close()
    except Exception as exc:
        print("historical youtube patch query failed:", repr(exc))
        return []


def _hist_yt_table_exists(table_name):
    rows = _hist_yt_rows(
        "SELECT 1 FROM information_schema.tables WHERE table_schema='public' AND table_name=%s LIMIT 1",
        [table_name],
    )
    return bool(rows)


def _hist_yt_columns(table_name):
    rows = _hist_yt_rows(
        "SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name=%s ORDER BY ordinal_position",
        [table_name],
    )
    return [row.get("column_name") for row in rows if row.get("column_name")]


def _hist_yt_pick(row, *names):
    if not isinstance(row, dict):
        return None

    for name in names:
        value = row.get(name)
        if value is not None and str(value).strip() != "":
            return value

    return None


def _hist_yt_is_youtube_url(value):
    text = str(value or "").strip().lower()
    return bool(text and ("youtube.com" in text or "youtu.be" in text))


def _hist_yt_normalize_link(row, fallback_slug):
    if not isinstance(row, dict):
        return None

    youtube_url = _hist_yt_pick(row, "youtube_url", "final_url", "url")
    if not _hist_yt_is_youtube_url(youtube_url):
        return None

    slug = _hist_yt_pick(row, "slug") or fallback_slug
    youtube_title = _hist_yt_pick(row, "youtube_title", "title")
    youtube_video_id = _hist_yt_pick(row, "youtube_video_id", "video_id")

    return {
        "domain": "historical",
        "source": _hist_yt_pick(row, "youtube_source") or "historical_youtube_verified_links_v1",
        "provider": "YouTube",
        "provider_key": "youtube",
        "provider_display_name": "YouTube",
        "provider_type": "free",
        "button_label": "Watch on YouTube",
        "title": _hist_yt_pick(row, "title") or youtube_title,
        "slug": slug,
        "youtube_url": youtube_url,
        "youtube_title": youtube_title,
        "youtube_video_id": youtube_video_id,
        "youtube_language": _hist_yt_pick(row, "youtube_language", "language"),
        "youtube_duration_seconds": _hist_yt_pick(row, "youtube_duration_seconds"),
        "youtube_view_count": _hist_yt_pick(row, "youtube_view_count"),
        "youtube_match_score": _hist_yt_pick(row, "youtube_match_score"),
        "youtube_match_type": _hist_yt_pick(row, "youtube_match_type"),
        "youtube_confidence": _hist_yt_pick(row, "youtube_confidence"),
        "youtube_source": _hist_yt_pick(row, "youtube_source"),
        "final_url": youtube_url,
        "url": youtube_url,
        "active": True,
        "is_free": True,
        "has_youtube": True,
        "has_ott": True,
        "ott_primary": "YouTube",
        "ott_primary_key": "youtube",
    }


def _hist_yt_fetch_verified_links(slug):
    slug = (slug or "").strip()
    if not slug:
        return []

    links = []

    if _hist_yt_table_exists("historical_youtube_verified_links_v1"):
        rows = _hist_yt_rows(
            "SELECT * FROM historical_youtube_verified_links_v1 "
            "WHERE slug = %s AND COALESCE(active, TRUE) = TRUE "
            "ORDER BY COALESCE(is_primary, FALSE) DESC, COALESCE(link_rank, 999999) ASC, id ASC",
            [slug],
        )

        for row in rows:
            link = _hist_yt_normalize_link(row, slug)
            if link:
                links.append(link)

    if links:
        return links

    fallback_tables = [
        "historical_detail_serving_v1",
        "historical_serving_v1",
        "historical_availability_v2",
        "historical_card_serving_v1",
        "historical_search_serving_v1",
    ]

    for table in fallback_tables:
        if not _hist_yt_table_exists(table):
            continue

        cols = set(_hist_yt_columns(table))
        if "slug" not in cols or "youtube_url" not in cols:
            continue

        sql = (
            'SELECT * FROM "' + table + '" '
            "WHERE slug = %s "
            "AND youtube_url IS NOT NULL "
            "AND TRIM(CAST(youtube_url AS TEXT)) <> '' "
            "AND (LOWER(CAST(youtube_url AS TEXT)) LIKE %s OR LOWER(CAST(youtube_url AS TEXT)) LIKE %s) "
            "LIMIT 20"
        )

        rows = _hist_yt_rows(sql, [slug, "%youtube.com%", "%youtu.be%"])

        for row in rows:
            link = _hist_yt_normalize_link(row, slug)
            if link:
                links.append(link)

        if links:
            return links

    return []


def _hist_yt_is_historical(data, row=None):
    if not isinstance(data, dict):
        return False

    domain = str(data.get("domain") or data.get("source_domain") or "").lower()
    movie_url = str(data.get("movie_url") or "")
    source_label = str(data.get("source_label") or "").lower()

    if domain == "historical":
        return True

    if movie_url.startswith("/historical/"):
        return True

    if source_label.startswith("historical"):
        return True

    if isinstance(row, dict):
        row_url = str(row.get("movie_url") or "")
        if row_url.startswith("/historical/"):
            return True

    return False


def _hist_yt_unique_links(links):
    out = []
    seen = set()

    for link in links:
        if not isinstance(link, dict):
            continue

        url = str(link.get("youtube_url") or link.get("final_url") or link.get("url") or "").strip()
        if not _hist_yt_is_youtube_url(url):
            continue

        if url in seen:
            continue

        seen.add(url)
        out.append(link)

    return out


def enrich_historical_youtube_detail_patch_v2(data, row=None):
    try:
        if not isinstance(data, dict):
            return data

        if not _hist_yt_is_historical(data, row):
            return data

        slug = str(data.get("slug") or "").strip()
        if not slug and isinstance(row, dict):
            slug = str(row.get("slug") or "").strip()

        if not slug:
            return data

        verified_links = _hist_yt_fetch_verified_links(slug)
        verified_links = _hist_yt_unique_links(verified_links)

        # Critical fix:
        # Historical detail route was showing wrong availability rows because old logic joined by numeric id.
        # For historical detail, keep only same-slug verified YouTube links here.
        data["availability"] = verified_links
        data["ott_all"] = verified_links
        data["watch_providers"] = verified_links
        data["youtube_full_movies"] = verified_links
        data["youtube_variants"] = verified_links
        data["youtube_count"] = len(verified_links)

        if verified_links:
            primary = verified_links[0]

            data["youtube_url"] = primary.get("youtube_url")
            data["youtube_title"] = primary.get("youtube_title")
            data["youtube_video_id"] = primary.get("youtube_video_id")
            data["youtube_language"] = primary.get("youtube_language")
            data["has_youtube"] = True
            data["has_ott"] = True
            data["ott_primary"] = "YouTube"
            data["ott_primary_key"] = "youtube"
            data["ott_count"] = max(int(data.get("ott_count") or 0), len(verified_links))
            data["is_free"] = True

            raw = data.get("raw")
            if isinstance(raw, dict):
                raw["youtube_url"] = primary.get("youtube_url")
                raw["youtube_title"] = primary.get("youtube_title")
                raw["youtube_video_id"] = primary.get("youtube_video_id")
                raw["youtube_language"] = primary.get("youtube_language")
                raw["has_youtube"] = 1
                raw["has_ott"] = 1
                raw["ott_primary"] = "YouTube"
                raw["ott_primary_key"] = "youtube"
        else:
            data["youtube_url"] = data.get("youtube_url")
            data["youtube_title"] = data.get("youtube_title")
            data["youtube_video_id"] = data.get("youtube_video_id")
            data["youtube_count"] = 0

        return data

    except Exception as exc:
        print("enrich_historical_youtube_detail_patch_v2 failed:", repr(exc))
        return data
# /FLIXYFY_HISTORICAL_YOUTUBE_DETAIL_PATCH_V2
'''

if MARKER not in text:
    route_marker = '@app.get("/api/v3/historical/movie/{slug}")'
    insert_at = text.find(route_marker)

    if insert_at == -1:
        insert_at = text.find("@app.get")

    if insert_at == -1:
        raise SystemExit("Could not find insertion point for helper code")

    text = text[:insert_at] + "\n\n" + HELPER_CODE + "\n\n" + text[insert_at:]

call_line = "    data = enrich_historical_youtube_detail_patch_v2(data, row)\n    return data"

if "enrich_historical_youtube_detail_patch_v2(data, row)" not in text:
    start = text.find("def movie_detail(")

    if start == -1:
        raise SystemExit("Could not find def movie_detail(")

    end = text.find("\ndef ", start + 1)
    if end == -1:
        end = len(text)

    segment = text[start:end]
    needle = "\n    return data"

    idx = segment.rfind(needle)
    if idx == -1:
        raise SystemExit("Could not find `return data` inside movie_detail")

    patched_segment = segment[:idx] + "\n" + call_line + segment[idx + len(needle):]
    text = text[:start] + patched_segment + text[end:]

TARGET.write_text(text, encoding="utf-8")

print("=" * 100)
print("PATCH_BACKEND_HISTORICAL_YOUTUBE_DETAIL_V2")
print("=" * 100)
print("BACKUP:", backup)
print("PATCHED:", TARGET)
print("PATCH_PASS")
