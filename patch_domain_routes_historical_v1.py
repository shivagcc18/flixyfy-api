from pathlib import Path
from datetime import datetime

TARGET = Path("app/domain_routes_v1.py")
text = TARGET.read_text(encoding="utf-8")

backup = TARGET.with_name("domain_routes_v1.py.backup_historical_routes_patch_v1_" + datetime.now().strftime("%Y%m%d_%H%M%S"))
backup.write_text(text, encoding="utf-8")

MARKER = "# FLIXYFY_DOMAIN_HISTORICAL_ROUTES_PATCH_V1"

PATCH = r'''
# FLIXYFY_DOMAIN_HISTORICAL_ROUTES_PATCH_V1
def _fhp_db_url():
    import os
    for key in ("DATABASE_URL", "POSTGRES_URL", "NEON_DATABASE_URL", "DATABASE_PUBLIC_URL"):
        value = os.getenv(key)
        if value:
            return value
    return None


def _fhp_rows(sql, params=None):
    params = params or []
    database_url = _fhp_db_url()
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
        print("historical domain patch query failed:", repr(exc))
        return []


def _fhp_table_exists(table_name):
    rows = _fhp_rows(
        "SELECT 1 FROM information_schema.tables WHERE table_schema='public' AND table_name=%s LIMIT 1",
        [table_name],
    )
    return bool(rows)


def _fhp_columns(table_name):
    rows = _fhp_rows(
        "SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name=%s ORDER BY ordinal_position",
        [table_name],
    )
    return [row.get("column_name") for row in rows if row.get("column_name")]


def _fhp_pick(row, *names):
    if not isinstance(row, dict):
        return None

    for name in names:
        value = row.get(name)
        if value is not None and str(value).strip() != "":
            return value

    return None


def _fhp_bool(value):
    if value is True:
        return True
    if value is False or value is None:
        return False
    return str(value).strip().lower() in ("1", "true", "yes", "y")


def _fhp_int(value, default=0):
    try:
        if value is None or str(value).strip() == "":
            return default
        return int(float(str(value)))
    except Exception:
        return default


def _fhp_has_real_poster(row):
    poster = str(_fhp_pick(row, "poster_url", "poster", "image_url") or "").strip().lower()
    if not poster:
        return False
    if "placeholder" in poster:
        return False
    if "classic-indian" in poster:
        return False
    if "no-poster" in poster or "no_poster" in poster:
        return False
    if "default" in poster:
        return False
    return poster.startswith("http://") or poster.startswith("https://") or poster.startswith("/")


def _fhp_is_youtube_url(value):
    text = str(value or "").strip().lower()
    return bool(text and ("youtube.com" in text or "youtu.be" in text))


def _fhp_bad_person_row(row):
    title = str(_fhp_pick(row, "title", "name") or "").strip().lower()
    slug = str(_fhp_pick(row, "slug") or "").strip().lower()

    title_norm = title.replace(".", " ").replace("_", " ").replace("-", " ")
    title_norm = " ".join(title_norm.split())

    blocked_titles = {
        "a r rahman",
        "ar rahman",
        "a venkatesh",
        "ajith kumar",
    }

    blocked_slug_parts = (
        "a-r-rahman",
        "ar-rahman",
        "a-venkatesh",
        "ajith-kumar",
    )

    if title_norm in blocked_titles:
        return True

    if any(part in slug for part in blocked_slug_parts):
        return True

    return False


def _fhp_route_for(slug):
    return f"/historical/{slug}" if slug else None


def _fhp_verified_links(slug):
    slug = (slug or "").strip()
    if not slug:
        return []

    out = []

    if _fhp_table_exists("historical_youtube_verified_links_v1"):
        rows = _fhp_rows(
            "SELECT * FROM historical_youtube_verified_links_v1 "
            "WHERE slug=%s AND COALESCE(active, TRUE)=TRUE "
            "ORDER BY COALESCE(is_primary, FALSE) DESC, COALESCE(link_rank, 999999) ASC, id ASC",
            [slug],
        )

        for row in rows:
            url = _fhp_pick(row, "youtube_url", "final_url", "url")
            if not _fhp_is_youtube_url(url):
                continue

            out.append(
                {
                    "domain": "historical",
                    "slug": slug,
                    "title": _fhp_pick(row, "title", "youtube_title"),
                    "provider": "YouTube",
                    "provider_key": "youtube",
                    "provider_display_name": "YouTube",
                    "provider_type": "free",
                    "button_label": "Watch on YouTube",
                    "final_url": url,
                    "url": url,
                    "youtube_url": url,
                    "youtube_title": _fhp_pick(row, "youtube_title", "title"),
                    "youtube_video_id": _fhp_pick(row, "youtube_video_id", "video_id"),
                    "youtube_language": _fhp_pick(row, "youtube_language", "language"),
                    "youtube_view_count": _fhp_pick(row, "youtube_view_count"),
                    "youtube_duration_seconds": _fhp_pick(row, "youtube_duration_seconds"),
                    "youtube_match_score": _fhp_pick(row, "youtube_match_score"),
                    "youtube_match_type": _fhp_pick(row, "youtube_match_type"),
                    "youtube_confidence": _fhp_pick(row, "youtube_confidence"),
                    "youtube_source": _fhp_pick(row, "youtube_source") or "historical_youtube_verified_links_v1",
                    "active": True,
                    "is_free": True,
                    "has_youtube": True,
                    "has_ott": True,
                    "ott_primary": "YouTube",
                    "ott_primary_key": "youtube",
                }
            )

    if out:
        return out

    fallback_tables = [
        "historical_detail_serving_v1",
        "historical_serving_v1",
        "historical_availability_v2",
        "historical_card_serving_v1",
        "historical_search_serving_v1",
    ]

    for table in fallback_tables:
        if not _fhp_table_exists(table):
            continue

        cols = set(_fhp_columns(table))
        if "slug" not in cols or "youtube_url" not in cols:
            continue

        rows = _fhp_rows(
            'SELECT * FROM "' + table + '" '
            "WHERE slug=%s "
            "AND youtube_url IS NOT NULL "
            "AND TRIM(CAST(youtube_url AS TEXT)) <> '' "
            "AND (LOWER(CAST(youtube_url AS TEXT)) LIKE %s OR LOWER(CAST(youtube_url AS TEXT)) LIKE %s) "
            "LIMIT 20",
            [slug, "%youtube.com%", "%youtu.be%"],
        )

        for row in rows:
            url = _fhp_pick(row, "youtube_url", "final_url", "url")
            if not _fhp_is_youtube_url(url):
                continue

            out.append(
                {
                    "domain": "historical",
                    "slug": slug,
                    "title": _fhp_pick(row, "title", "youtube_title"),
                    "provider": "YouTube",
                    "provider_key": "youtube",
                    "provider_display_name": "YouTube",
                    "provider_type": "free",
                    "button_label": "Watch on YouTube",
                    "final_url": url,
                    "url": url,
                    "youtube_url": url,
                    "youtube_title": _fhp_pick(row, "youtube_title", "title"),
                    "youtube_video_id": _fhp_pick(row, "youtube_video_id", "video_id"),
                    "youtube_language": _fhp_pick(row, "youtube_language", "language"),
                    "youtube_view_count": _fhp_pick(row, "youtube_view_count"),
                    "youtube_duration_seconds": _fhp_pick(row, "youtube_duration_seconds"),
                    "youtube_match_score": _fhp_pick(row, "youtube_match_score"),
                    "youtube_match_type": _fhp_pick(row, "youtube_match_type"),
                    "youtube_confidence": _fhp_pick(row, "youtube_confidence"),
                    "youtube_source": _fhp_pick(row, "youtube_source"),
                    "active": True,
                    "is_free": True,
                    "has_youtube": True,
                    "has_ott": True,
                    "ott_primary": "YouTube",
                    "ott_primary_key": "youtube",
                }
            )

        if out:
            return out

    return []


def _fhp_card(row):
    slug = _fhp_pick(row, "slug")
    links = _fhp_verified_links(slug)
    primary = links[0] if links else None

    has_youtube = bool(primary)
    has_ott = has_youtube or _fhp_bool(_fhp_pick(row, "has_ott"))

    return {
        "domain": "historical",
        "source_domain": "historical",
        "source_label": "Historical Indian",
        "id": _fhp_pick(row, "id"),
        "tmdb_id": _fhp_pick(row, "tmdb_id"),
        "imdb_id": _fhp_pick(row, "imdb_id"),
        "title": _fhp_pick(row, "title"),
        "original_title": _fhp_pick(row, "original_title"),
        "slug": slug,
        "movie_url": _fhp_pick(row, "movie_url") or _fhp_route_for(slug),
        "release_year": _fhp_pick(row, "release_year", "year"),
        "year": _fhp_pick(row, "release_year", "year"),
        "primary_language": _fhp_pick(row, "primary_language", "language_name"),
        "language_name": _fhp_pick(row, "language_name", "primary_language"),
        "language_slug": _fhp_pick(row, "language_slug", "language"),
        "poster_url": _fhp_pick(row, "poster_url"),
        "backdrop_url": _fhp_pick(row, "backdrop_url"),
        "rating": _fhp_pick(row, "rating"),
        "vote_count": _fhp_pick(row, "vote_count"),
        "popularity": _fhp_pick(row, "popularity"),
        "quality_score": _fhp_pick(row, "quality_score"),
        "ott_primary": "YouTube" if has_youtube else _fhp_pick(row, "ott_primary"),
        "ott_primary_key": "youtube" if has_youtube else (_fhp_pick(row, "ott_primary_key") or ""),
        "ott_count": len(links) if has_youtube else _fhp_pick(row, "ott_count"),
        "has_ott": has_ott,
        "has_free_ott": has_youtube or _fhp_bool(_fhp_pick(row, "has_free_ott")),
        "has_subscription_ott": _fhp_bool(_fhp_pick(row, "has_subscription_ott")),
        "has_rent_ott": _fhp_bool(_fhp_pick(row, "has_rent_ott")),
        "has_buy_ott": _fhp_bool(_fhp_pick(row, "has_buy_ott")),
        "is_free": has_youtube or _fhp_bool(_fhp_pick(row, "is_free")),
        "youtube_url": primary.get("youtube_url") if primary else _fhp_pick(row, "youtube_url"),
        "youtube_title": primary.get("youtube_title") if primary else _fhp_pick(row, "youtube_title"),
        "youtube_video_id": primary.get("youtube_video_id") if primary else _fhp_pick(row, "youtube_video_id"),
        "youtube_count": len(links),
    }


def _fhp_detail(row):
    data = _fhp_card(row)
    slug = data.get("slug")
    links = _fhp_verified_links(slug)
    primary = links[0] if links else None

    data.update(
        {
            "overview": _fhp_pick(row, "overview"),
            "runtime": _fhp_pick(row, "runtime"),
            "genres": [],
            "director": _fhp_pick(row, "director"),
            "writers": _fhp_pick(row, "writers", "writer"),
            "actors": _fhp_pick(row, "actors", "cast"),
            "cast": _fhp_pick(row, "cast", "actors"),
            "certification": _fhp_pick(row, "certification"),
            "trailer_url": _fhp_pick(row, "trailer_url"),
            "production_companies": _fhp_pick(row, "production_companies", "production_company"),
            "availability": links,
            "ott_all": links,
            "watch_providers": links,
            "youtube_full_movies": links,
            "youtube_variants": links,
            "youtube_count": len(links),
            "raw": dict(row),
        }
    )

    if primary:
        data["youtube_url"] = primary.get("youtube_url")
        data["youtube_title"] = primary.get("youtube_title")
        data["youtube_video_id"] = primary.get("youtube_video_id")
        data["youtube_language"] = primary.get("youtube_language")
        data["has_youtube"] = True
        data["has_ott"] = True
        data["ott_primary"] = "YouTube"
        data["ott_primary_key"] = "youtube"
        data["ott_count"] = len(links)
        data["is_free"] = True
        data["raw"]["youtube_url"] = primary.get("youtube_url")
        data["raw"]["youtube_title"] = primary.get("youtube_title")
        data["raw"]["youtube_video_id"] = primary.get("youtube_video_id")
        data["raw"]["youtube_language"] = primary.get("youtube_language")
        data["raw"]["has_youtube"] = 1
        data["raw"]["has_ott"] = 1

    return data


def _fhp_fetch_historical_row(slug):
    for table in ("historical_detail_serving_v1", "historical_serving_v1", "historical_card_serving_v1"):
        if not _fhp_table_exists(table):
            continue

        cols = set(_fhp_columns(table))
        if "slug" not in cols:
            continue

        rows = _fhp_rows('SELECT * FROM "' + table + '" WHERE slug=%s LIMIT 1', [slug])
        if rows:
            return rows[0]

    return None


@router.get("/api/v3/historical")
def historical_movies_patched_v1(
    page: int = 1,
    limit: int = 24,
    q: str = None,
    year: str = None,
    language: str = None,
    sort: str = "popular",
    provider: str = None,
    has_ott: str = None,
    availability: str = None,
):
    page = max(1, int(page or 1))
    limit = max(1, min(int(limit or 24), 100))
    offset = (page - 1) * limit

    table = "historical_card_serving_v1"
    if not _fhp_table_exists(table):
        table = "historical_serving_v1"

    where = []
    params = []

    if q:
        where.append("(LOWER(title) LIKE %s OR LOWER(slug) LIKE %s)")
        params.extend([f"%{str(q).lower()}%", f"%{str(q).lower()}%"])

    if year:
        where.append("CAST(release_year AS TEXT) = %s")
        params.append(str(year))

    if language:
        where.append("(LOWER(CAST(language_slug AS TEXT)) = %s OR LOWER(CAST(language_name AS TEXT)) = %s)")
        params.extend([str(language).lower(), str(language).lower()])

    blocked_slugs = ["a-r-rahman-1999-ta", "a-venkatesh-1999-ta", "ajith-kumar-1999-ta"]
    where.append("slug <> ALL(%s)")
    params.append(blocked_slugs)

    provider_text = str(provider or "").strip().lower()
    availability_text = str(availability or has_ott or "").strip().lower()

    youtube_only = provider_text in ("youtube", "youTube") or availability_text in ("youtube", "true", "ott", "1")

    join_sql = ""
    if youtube_only and _fhp_table_exists("historical_youtube_verified_links_v1"):
        join_sql = (
            " JOIN ("
            "   SELECT DISTINCT slug FROM historical_youtube_verified_links_v1 "
            "   WHERE COALESCE(active, TRUE)=TRUE"
            " ) ytv ON ytv.slug = h.slug "
        )
    elif youtube_only:
        where.append(
            "youtube_url IS NOT NULL AND TRIM(CAST(youtube_url AS TEXT)) <> '' "
            "AND (LOWER(CAST(youtube_url AS TEXT)) LIKE %s OR LOWER(CAST(youtube_url AS TEXT)) LIKE %s)"
        )
        params.extend(["%youtube.com%", "%youtu.be%"])

    where_sql = "WHERE " + " AND ".join(where) if where else ""

    rows = _fhp_rows(
        f'SELECT h.* FROM "{table}" h {join_sql} {where_sql}',
        params,
    )

    items = [_fhp_card(row) for row in rows if not _fhp_bad_person_row(row)]

    if provider_text == "youtube" or availability_text in ("youtube", "true", "ott", "1"):
        items = [item for item in items if item.get("youtube_count", 0) > 0 or item.get("has_ott") is True]

    def sort_key(item):
        poster_rank = 0 if _fhp_has_real_poster(item) else 1
        ott_rank = 0 if item.get("has_ott") else 1
        year_value = _fhp_int(item.get("release_year"), 0)
        title_value = str(item.get("title") or "")

        if sort == "latest":
            return (poster_rank, ott_rank, -year_value, title_value)
        if sort == "title":
            return (poster_rank, ott_rank, title_value)
        if sort == "rating":
            return (poster_rank, ott_rank, -float(item.get("rating") or 0), title_value)

        return (poster_rank, ott_rank, -_fhp_int(item.get("youtube_count"), 0), -year_value, title_value)

    items.sort(key=sort_key)

    total = len(items)
    paged = items[offset: offset + limit]
    pages = (total + limit - 1) // limit if limit else 0

    return {
        "domain": "historical",
        "source_domain": "historical",
        "page": page,
        "limit": limit,
        "total": total,
        "pages": pages,
        "items": paged,
    }


@router.get("/api/v3/historical/{slug}")
def historical_detail_patched_v1(slug: str):
    row = _fhp_fetch_historical_row(slug)

    if not row:
        raise HTTPException(status_code=404, detail="Historical movie not found")

    return _fhp_detail(row)


@router.get("/api/v3/historical/movie/{slug}")
def historical_detail_movie_alias_patched_v1(slug: str):
    return historical_detail_patched_v1(slug)


@router.get("/api/v3/historical/movies/{slug}")
def historical_detail_movies_alias_patched_v1(slug: str):
    return historical_detail_patched_v1(slug)
# /FLIXYFY_DOMAIN_HISTORICAL_ROUTES_PATCH_V1
'''

if MARKER in text:
    print("Patch marker already present; no insert needed.")
else:
    insert_marker = '@router.get("/api/v3/historical")'
    idx = text.find(insert_marker)
    if idx == -1:
        raise SystemExit("Could not find @router.get(\"/api/v3/historical\") insertion point")
    text = text[:idx] + "\n\n" + PATCH + "\n\n" + text[idx:]

TARGET.write_text(text, encoding="utf-8")

print("=" * 100)
print("PATCH_DOMAIN_ROUTES_HISTORICAL_V1")
print("=" * 100)
print("BACKUP:", backup)
print("PATCHED:", TARGET)
print("PATCH_PASS")
