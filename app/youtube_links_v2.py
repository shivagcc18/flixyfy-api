"""
FLIXYFY YOUTUBE LINKS DISPLAY HELPERS V2

Safe helper only. No DB writes.
Adds a stable response shape for YouTube free-watch links.

Expected movie detail response additions:
- youtube_links
- best_youtube_link
- has_youtube
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional


LANGUAGE_LABELS = {
    "hi": "Hindi",
    "te": "Telugu",
    "ta": "Tamil",
    "ml": "Malayalam",
    "kn": "Kannada",
    "bn": "Bengali",
    "mr": "Marathi",
    "pa": "Punjabi",
    "gu": "Gujarati",
    "or": "Odia",
    "as": "Assamese",
    "en": "English",
}


def _get(row: Any, key: str, default: Any = None) -> Any:
    if row is None:
        return default
    if isinstance(row, dict):
        return row.get(key, default)
    try:
        return row[key]
    except Exception:
        return getattr(row, key, default)


def _int_or_none(value: Any) -> Optional[int]:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except Exception:
        return None


def _float_or_none(value: Any) -> Optional[float]:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except Exception:
        return None


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def language_label(code: Optional[str]) -> Optional[str]:
    if not code:
        return None
    code = str(code).strip().lower()
    return LANGUAGE_LABELS.get(code, code.upper())


def duration_label(seconds: Optional[int]) -> Optional[str]:
    seconds = _int_or_none(seconds)
    if not seconds:
        return None

    minutes = seconds // 60
    hours = minutes // 60
    mins = minutes % 60

    if hours and mins:
        return f"{hours}h {mins}m"
    if hours:
        return f"{hours}h"
    return f"{mins}m"


def build_youtube_label(link: Dict[str, Any]) -> str:
    audio = language_label(link.get("audio_language"))
    is_dubbed = bool(link.get("is_dubbed"))

    if audio and is_dubbed:
        return f"{audio} dubbed full movie"
    if audio:
        return f"{audio} full movie"
    return "Full movie"


def normalize_youtube_link(row: Any) -> Dict[str, Any]:
    video_id = _get(row, "youtube_video_id") or _get(row, "video_id")
    url = (
        _get(row, "youtube_url")
        or _get(row, "video_url")
        or (f"https://www.youtube.com/watch?v={video_id}" if video_id else None)
    )

    link = {
        "video_id": video_id,
        "url": url,
        "title": _get(row, "youtube_title") or _get(row, "title"),
        "channel": _get(row, "youtube_channel") or _get(row, "channel_name") or _get(row, "video_channel_title"),
        "duration_seconds": _int_or_none(_get(row, "duration_seconds")),
        "duration_label": duration_label(_get(row, "duration_seconds")),
        "view_count": _int_or_none(_get(row, "view_count")),
        "audio_language": _get(row, "audio_language") or _get(row, "youtube_language"),
        "audio_language_label": language_label(_get(row, "audio_language") or _get(row, "youtube_language")),
        "is_dubbed": _bool(_get(row, "is_dubbed")),
        "match_score": _float_or_none(_get(row, "match_score")),
        "match_type": _get(row, "match_type"),
        "source": _get(row, "source") or "youtube",
    }

    link["label"] = build_youtube_label(link)
    return link


def normalize_youtube_links(rows: Iterable[Any], limit: int = 5) -> List[Dict[str, Any]]:
    links: List[Dict[str, Any]] = []

    for row in rows or []:
        link = normalize_youtube_link(row)
        if not link.get("video_id") or not link.get("url"):
            continue
        links.append(link)

    links.sort(
        key=lambda x: (
            float(x.get("match_score") or 0),
            int(x.get("view_count") or 0),
        ),
        reverse=True,
    )

    seen = set()
    deduped: List[Dict[str, Any]] = []

    for link in links:
        video_id = link.get("video_id")
        if video_id in seen:
            continue
        seen.add(video_id)
        deduped.append(link)

    return deduped[:limit]


def attach_youtube_links(movie: Dict[str, Any], youtube_rows: Iterable[Any], limit: int = 5) -> Dict[str, Any]:
    payload = dict(movie or {})
    links = normalize_youtube_links(youtube_rows, limit=limit)

    payload["youtube_links"] = links
    payload["best_youtube_link"] = links[0] if links else None
    payload["has_youtube"] = bool(links)

    return payload


YOUTUBE_LINKS_SQLITE = """
SELECT
    youtube_video_id,
    youtube_url,
    youtube_title,
    youtube_channel,
    duration_seconds,
    view_count,
    match_score,
    match_type,
    audio_language,
    is_dubbed,
    source
FROM youtube_full_movies_v2_stage
WHERE
    (movie_slug = :slug OR movie_id = :movie_id)
ORDER BY
    COALESCE(match_score, 0) DESC,
    COALESCE(view_count, 0) DESC
LIMIT :limit
"""


YOUTUBE_LINKS_POSTGRES = """
SELECT
    youtube_video_id,
    youtube_url,
    youtube_title,
    youtube_channel,
    duration_seconds,
    view_count,
    match_score,
    match_type,
    audio_language,
    is_dubbed,
    source
FROM youtube_full_movies_v2
WHERE
    (movie_slug = %(slug)s OR movie_id = %(movie_id)s)
ORDER BY
    COALESCE(match_score, 0) DESC,
    COALESCE(view_count, 0) DESC
LIMIT %(limit)s
"""
