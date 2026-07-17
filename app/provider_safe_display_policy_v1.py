"""Trust-safe provider display shaping for the fresh v4 API.

This module is deliberately response-only. It never changes provider data or
chooses a replacement provider; it classifies the URL/provider evidence that
is already present in a response and hides unsafe rows from public arrays.
"""

from __future__ import annotations

import json
import re
from urllib.parse import parse_qs, urlparse
from typing import Any


POLICY_VERSION = "FLIXYFY_PROVIDER_TRUST_SAFE_DISPLAY_POLICY_V1"

_HOSTS = {
    "netflix": ("netflix.com",),
    "primevideo": ("primevideo.com", "amazon.com", "amazonvideo.com"),
    "amazon": ("primevideo.com", "amazon.com", "amazonvideo.com"),
    "jiohotstar": ("jiohotstar.com", "hotstar.com", "jiostar.com"),
    "hotstar": ("jiohotstar.com", "hotstar.com", "jiostar.com"),
    "youtube": ("youtube.com", "youtu.be", "googlevideo.com"),
    "zee5": ("zee5.com",),
    "sonyliv": ("sonyliv.com",),
    "sunnxt": ("sunnxt.com",),
    "mxplayer": ("mxplayer.in", "mxplayer.com"),
    "aha": ("aha.video", "ahatamil.com"),
    "disneyplus": ("disneyplus.com",),
    "hulu": ("hulu.com",),
    "max": ("max.com", "hbomax.com"),
    "paramount": ("paramountplus.com",),
    "peacock": ("peacocktv.com",),
    "mubi": ("mubi.com",),
    "apple": ("tv.apple.com",),
    "google": ("play.google.com",),
    "tubi": ("tubitv.com", "tubi.tv"),
    "hoichoi": ("hoichoi.tv",),
    "manorama": ("manoramamax.com",),
}

_SEARCH_MARKERS = ("/search", "/find", "?q=", "&q=", "query=", "searchterm=")
_HOME_PATHS = {"", "/", "/home", "/in", "/us", "/gb"}


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", _text(value).lower())


def _first(row: dict[str, Any], *names: str) -> str:
    for name in names:
        value = _text(row.get(name))
        if value:
            return value
    return ""


def _host(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower().removeprefix("www.")
    except Exception:
        return ""


def _host_match(provider_key: str, host: str) -> bool | None:
    if not provider_key or not host:
        return None
    compact = _key(provider_key)
    for family, hosts in _HOSTS.items():
        if family in compact or compact in family:
            return any(host == allowed or host.endswith("." + allowed) for allowed in hosts)
    return None


def _youtube_video_id(row: dict[str, Any]) -> str:
    explicit = _first(row, "youtube_video_id", "video_id", "provider_video_id")
    if explicit:
        return explicit
    for value in (_first(row, "final_url"), _first(row, "deep_link"), _first(row, "youtube_url"), _first(row, "watch_url")):
        try:
            parsed = urlparse(value)
            host = (parsed.hostname or "").lower()
            if host.endswith("youtu.be"):
                return parsed.path.strip("/").split("/")[0]
            if "youtube.com" in host:
                if parsed.path == "/watch":
                    return parse_qs(parsed.query).get("v", [""])[0]
                for prefix in ("/shorts/", "/embed/", "/live/"):
                    if parsed.path.startswith(prefix):
                        return parsed.path[len(prefix):].split("/")[0]
        except Exception:
            continue
    return ""


def classify_provider_display(row: dict[str, Any]) -> dict[str, Any]:
    """Return additive policy fields for one provider row."""

    provider_key = _first(row, "provider_key", "provider", "platform")
    provider_name = _first(row, "provider_display_name", "provider_name", "provider", "platform_name") or provider_key
    final_url = _first(row, "final_url")
    deep_link = _first(row, "deep_link", "provider_deep_link", "watch_url")
    search_url = _first(row, "search_url", "provider_search_url")
    homepage_url = _first(row, "homepage_url", "provider_homepage_url")
    key_blob = f"{provider_key} {provider_name}".lower()
    youtube = "youtube" in key_blob or "youtu.be" in final_url.lower() or "youtube.com" in final_url.lower() or "youtube.com" in deep_link.lower()
    video_id = _youtube_video_id(row) if youtube else ""
    host = _host(final_url or deep_link or search_url or homepage_url)
    match = _host_match(provider_key, host)
    path_query = ((urlparse(final_url or deep_link).path or "") + "?" + (urlparse(final_url or deep_link).query or "")).lower()

    unsafe_reason = ""
    if not provider_key:
        label, action, rank, public_safe = "UNKNOWN_PROVIDER", "HIDE", 99, False
        unsafe_reason = "provider_key_missing"
    elif youtube and not video_id:
        label, action, rank, public_safe = "YOUTUBE_WITHOUT_VIDEO_ID", "HIDE", 99, False
        unsafe_reason = "youtube_video_id_or_watch_url_missing"
    elif not (final_url or deep_link or search_url or homepage_url):
        label, action, rank, public_safe = "EMPTY_URL", "HIDE", 99, False
        unsafe_reason = "no_public_url"
    elif match is False:
        label, action, rank, public_safe = "HOST_PROVIDER_MISMATCH", "HIDE", 99, False
        unsafe_reason = f"host_not_allowed:{host}"
    elif final_url or deep_link:
        if youtube:
            label, action, rank, public_safe = "YOUTUBE_DIRECT", "WATCH", 2, True
        elif any(marker in path_query for marker in _SEARCH_MARKERS):
            label, action, rank, public_safe = "PROVIDER_SEARCH_FALLBACK", "SEARCH", 5, True
        elif (urlparse(final_url or deep_link).path or "").rstrip("/").lower() in _HOME_PATHS:
            if search_url:
                label, action, rank, public_safe = "PROVIDER_SEARCH_FALLBACK", "SEARCH", 5, True
            else:
                label, action, rank, public_safe = "PROVIDER_HOMEPAGE_FALLBACK", "OPEN_PROVIDER", 6, True
        else:
            label, action, rank, public_safe = "DIRECT_DEEPLINK", "WATCH", 1, True
    elif search_url:
        label, action, rank, public_safe = "PROVIDER_SEARCH_FALLBACK", "SEARCH", 5, True
    else:
        label, action, rank, public_safe = "PROVIDER_HOMEPAGE_FALLBACK", "OPEN_PROVIDER", 6, True

    if label == "YOUTUBE_DIRECT":
        public_label = "Watch on YouTube"
    elif label == "DIRECT_DEEPLINK":
        public_label = f"Watch on {provider_name}" if provider_name else "Watch on Provider"
    elif label == "PROVIDER_SEARCH_FALLBACK":
        public_label = f"Search on {provider_name}" if provider_name else "Search on Provider"
    elif label == "PROVIDER_HOMEPAGE_FALLBACK":
        public_label = f"Open {provider_name}" if provider_name else "Open Provider"
    else:
        public_label = None

    return {
        "provider_trust_label": label,
        "provider_display_action": action,
        "provider_display_rank": rank,
        "provider_is_public_safe": public_safe,
        "provider_public_label": public_label,
        "provider_display_reason": unsafe_reason or label.lower(),
        "provider_video_id": video_id or None,
        "provider_policy_version": POLICY_VERSION,
    }


def apply_provider_safe_display_policy(rows: list[dict[str, Any]] | None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Classify, sort, and split public-safe versus hidden provider rows."""

    public: list[dict[str, Any]] = []
    hidden: list[dict[str, Any]] = []
    for raw in rows or []:
        if not isinstance(raw, dict):
            continue
        row = dict(raw)
        row.update(classify_provider_display(row))
        if row["provider_is_public_safe"]:
            public.append(row)
        else:
            hidden.append(row)

    def sort_key(row: dict[str, Any]) -> tuple[int, int, str]:
        # Preserve an explicitly approved API overlay as the displayed primary
        # while keeping its honest Open Provider label/rank.
        overlay_first = 0 if row.get("overlay_status") == "primary" else 1
        return overlay_first, int(row.get("provider_display_rank") or 99), _text(row.get("provider_display_name") or row.get("provider_name"))

    public.sort(key=sort_key)
    return public, hidden


def _parse_rows(value: Any) -> tuple[list[dict[str, Any]], bool]:
    if isinstance(value, list):
        return [dict(item) for item in value if isinstance(item, dict)], False
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                return [dict(item) for item in parsed if isinstance(item, dict)], True
        except (TypeError, ValueError):
            pass
    return [], isinstance(value, str)


def apply_provider_safe_display_policy_to_item(item: dict[str, Any]) -> dict[str, Any]:
    """Apply policy to a v4 card/search item when provider rows are present."""

    if not isinstance(item, dict):
        return item
    out = dict(item)
    source_rows: list[dict[str, Any]] = []
    source_field = ""
    for field in ("providers", "watch_providers", "availability", "ott_all"):
        rows, _ = _parse_rows(out.get(field))
        if rows:
            source_rows, source_field = rows, field
            break
    if not source_rows:
        for field in ("availability_json", "provider_summary"):
            rows, _ = _parse_rows(out.get(field))
            if rows:
                source_rows, source_field = rows, field
                break
    if not source_rows and any(out.get(field) for field in ("provider_key", "provider_name", "provider_display_name")):
        source_rows = [out]
        source_field = "item"
    if not source_rows:
        return out

    public, hidden = apply_provider_safe_display_policy(source_rows)
    for field in ("providers", "watch_providers", "availability", "ott_all"):
        if field in out or field == source_field:
            out[field] = public
    for field in ("availability_json", "provider_summary"):
        if field in out and isinstance(out.get(field), str):
            out[field] = json.dumps(public, ensure_ascii=False)
    out["provider_hidden_rows"] = hidden
    out["provider_public_count"] = len(public)
    out["provider_hidden_count"] = len(hidden)
    out["provider_policy_version"] = POLICY_VERSION
    if public:
        primary = public[0]
        out["provider_display_primary_key"] = primary.get("provider_key")
        out["provider_display_primary_name"] = primary.get("provider_display_name") or primary.get("provider_name")
        out["provider_display_primary_label"] = primary.get("provider_public_label")
    return out
