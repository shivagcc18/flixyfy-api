from __future__ import annotations

import re
from urllib.parse import quote_plus, unquote, urlparse

PROVIDER_DISPLAY_POLICY_VERSION = "FLIXYFY_PROVIDER_TRUST_SAFE_DISPLAY_POLICY_V1_TRUTH_FIREWALL"
POLICY_VERSION = PROVIDER_DISPLAY_POLICY_VERSION

AGGREGATOR_HOSTS = ("themoviedb.org", "tmdb.org", "justwatch.com")

PROVIDER_HOME = {
    "netflix": "https://www.netflix.com/in/",
    "prime_video": "https://www.primevideo.com/",
    "amazon_prime_video": "https://www.primevideo.com/",
    "amazon_video": "https://www.primevideo.com/",
    "jiohotstar": "https://www.hotstar.com/in/",
    "hotstar": "https://www.hotstar.com/in/",
    "zee5": "https://www.zee5.com/",
    "sonyliv": "https://www.sonyliv.com/",
    "sony_liv": "https://www.sonyliv.com/",
    "sun_nxt": "https://www.sunnxt.com/",
    "sunnxt": "https://www.sunnxt.com/",
    "aha": "https://www.aha.video/",
    "shemaroome": "https://www.shemaroome.com/",
    "youtube": "https://www.youtube.com/",
    "vi_movies_and_tv": "https://www.myvi.in/vi-movies-and-tv",
    "mxplayer": "https://www.mxplayer.in/",
    "mx_player": "https://www.mxplayer.in/",
    "googleplay": "https://play.google.com/store/movies",
    "google_tv": "https://play.google.com/store/movies",
    "apple_tv_store": "https://tv.apple.com/",
    "appletvstore": "https://tv.apple.com/",
}

PROVIDER_SEARCH = {
    "netflix": "https://www.netflix.com/search?q={q}",
    "prime_video": "https://www.primevideo.com/search/ref=atv_nb_sr?phrase={q}",
    "amazon_prime_video": "https://www.primevideo.com/search/ref=atv_nb_sr?phrase={q}",
    "amazon_video": "https://www.primevideo.com/search/ref=atv_nb_sr?phrase={q}",
    "jiohotstar": "https://www.hotstar.com/in/search?q={q}",
    "hotstar": "https://www.hotstar.com/in/search?q={q}",
    "zee5": "https://www.zee5.com/search?q={q}",
    "sonyliv": "https://www.sonyliv.com/search?q={q}",
    "sony_liv": "https://www.sonyliv.com/search?q={q}",
    "sun_nxt": "https://www.sunnxt.com/search?q={q}",
    "sunnxt": "https://www.sunnxt.com/search?q={q}",
    "aha": "https://www.aha.video/search?q={q}",
    "shemaroome": "https://www.shemaroome.com/search?q={q}",
    "youtube": "https://www.youtube.com/results?search_query={q}",
    "vi_movies_and_tv": "https://www.myvi.in/vi-movies-and-tv/search?q={q}",
    "mxplayer": "https://www.mxplayer.in/search/{q}",
    "mx_player": "https://www.mxplayer.in/search/{q}",
    "googleplay": "https://play.google.com/store/search?q={q}&c=movies",
    "google_tv": "https://play.google.com/store/search?q={q}&c=movies",
    "apple_tv_store": "https://tv.apple.com/search?term={q}",
    "appletvstore": "https://tv.apple.com/search?term={q}",
}

PROVIDER_HOSTS = {
    "netflix": ("netflix.com",),
    "prime_video": ("primevideo.com", "amazon.com", "amazonvideo.com"),
    "amazon_prime_video": ("primevideo.com", "amazon.com", "amazonvideo.com"),
    "amazon_video": ("primevideo.com", "amazon.com", "amazonvideo.com"),
    "jiohotstar": ("hotstar.com", "jiohotstar.com", "jiostar.com"),
    "hotstar": ("hotstar.com", "jiohotstar.com", "jiostar.com"),
    "zee5": ("zee5.com",),
    "sonyliv": ("sonyliv.com",),
    "sony_liv": ("sonyliv.com",),
    "sun_nxt": ("sunnxt.com",),
    "sunnxt": ("sunnxt.com",),
    "aha": ("aha.video", "ahatamil.com"),
    "shemaroome": ("shemaroome.com",),
    "youtube": ("youtube.com", "youtu.be"),
    "vi_movies_and_tv": ("myvi.in",),
    "mxplayer": ("mxplayer.in", "mxplayer.com"),
    "mx_player": ("mxplayer.in", "mxplayer.com"),
    "googleplay": ("play.google.com",),
    "google_tv": ("play.google.com",),
    "apple_tv_store": ("tv.apple.com", "itunes.apple.com"),
    "appletvstore": ("tv.apple.com", "itunes.apple.com"),
}

PROVIDER_LABELS = {
    "netflix": "Netflix",
    "prime_video": "Prime Video",
    "amazon_prime_video": "Prime Video",
    "amazon_video": "Amazon Video",
    "jiohotstar": "JioHotstar",
    "hotstar": "JioHotstar",
    "zee5": "ZEE5",
    "sonyliv": "SonyLIV",
    "sony_liv": "SonyLIV",
    "sun_nxt": "Sun NXT",
    "sunnxt": "Sun NXT",
    "aha": "Aha",
    "shemaroome": "ShemarooMe",
    "youtube": "YouTube",
    "vi_movies_and_tv": "VI Movies and TV",
    "mxplayer": "MX Player",
    "mx_player": "MX Player",
    "googleplay": "Google Play",
    "google_tv": "Google TV",
    "apple_tv_store": "Apple TV",
    "appletvstore": "Apple TV",
}

TITLE_STOP = {
    "movie", "movies", "watch", "online", "stream", "streaming", "rent", "buy",
    "india", "in", "title", "details", "detail", "video", "videos", "search",
    "results", "ref", "phrase", "play", "store", "tv", "www", "com"
}

# API-only correction seeds. No Neon mutation.
PROVIDER_TRUTH_OVERLAYS = {
    "thaai-kizhavi-2026": {
        "title": "Thaai Kizhavi",
        "provider_key": "jiohotstar",
        "provider_name": "JioHotstar",
        "provider_display_name": "JioHotstar",
        "provider_type": "flatrate",
        "monetization_type": "subscription",
        "availability_type": "ott",
        "availability_status": "available",
        "homepage_url": "https://www.hotstar.com/in/",
        "search_url": "https://www.hotstar.com/in/search?q=Thaai%20Kizhavi",
        "source": "provider_truth_firewall_overlay_v1",
        "confidence": "PRESERVE_EXISTING_THAI_KIZHAVI_JIOHOTSTAR_CORRECTION",
    },
    "karuppu-2026": {
        "provider_key": "prime_video",
        "provider_name": "Prime Video",
        "provider_display_name": "Prime Video",
        "provider_type": "flatrate",
        "monetization_type": "subscription",
        "availability_type": "ott",
        "availability_status": "available",
        "homepage_url": "https://www.primevideo.com/",
        "search_url": "https://www.primevideo.com/search/ref=atv_nb_sr?phrase=Karuppu",
        "source": "provider_truth_firewall_overlay_v1",
        "confidence": "USER_REPORTED_WRONG_PROVIDER_FIREWALL_SEED",
    }
}


def _clean_key(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")


def _host(url: object) -> str:
    try:
        return urlparse(str(url or "")).netloc.lower().replace("www.", "")
    except Exception:
        return ""


def _norm_text(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def _tokens(value: object) -> set[str]:
    return {t for t in _norm_text(value).split() if len(t) >= 3 and t not in TITLE_STOP}


def _url_tokens(url: object) -> set[str]:
    try:
        parsed = urlparse(str(url or ""))
    except Exception:
        return set()
    text = f"{parsed.netloc} {unquote(parsed.path or '')} {parsed.query}"
    text = re.sub(r"\d+", " ", text)
    return _tokens(text)


def _title_url_mismatch(title: object, url: object) -> bool:
    title_set = _tokens(title)
    url_set = _url_tokens(url)
    if not title_set or not url_set:
        return False
    return not bool(title_set.intersection(url_set))


def _is_aggregator_url(url: object) -> bool:
    h = _host(url)
    return any(host in h for host in AGGREGATOR_HOSTS)


def _is_search_url(url: object) -> bool:
    u = str(url or "").lower()
    return any(x in u for x in ("/search", "search?", "results?search_query", "phrase=", "q=", "term="))


def _is_youtube_watch(url: object) -> bool:
    u = str(url or "").lower()
    return "youtube.com/watch" in u or "youtu.be/" in u


def _provider_key(row: dict) -> str:
    return _clean_key(
        row.get("provider_key")
        or row.get("provider")
        or row.get("provider_slug")
        or row.get("normalized_provider")
        or row.get("provider_name")
        or row.get("provider_display_name")
        or ""
    )


def _provider_name(row: dict, key: str) -> str:
    raw = (
        row.get("provider_display_name")
        or row.get("provider_name")
        or row.get("provider")
        or row.get("name")
        or ""
    )
    return str(raw or PROVIDER_LABELS.get(key) or key.replace("_", " ").title()).strip()


def _first_url(row: dict, names: tuple[str, ...]) -> str:
    for name in names:
        value = row.get(name)
        if value and str(value).strip():
            return str(value).strip()
    return ""


def _build_search_url(provider_key: str, title: str) -> str:
    template = PROVIDER_SEARCH.get(provider_key)
    return template.format(q=quote_plus(title or "")) if template else ""


def _build_homepage_url(provider_key: str) -> str:
    return PROVIDER_HOME.get(provider_key, "")


def _host_matches_provider(provider_key: str, url: str) -> bool:
    if not url:
        return False
    expected = PROVIDER_HOSTS.get(provider_key)
    if not expected:
        return False
    h = _host(url)
    return any(x in h for x in expected)


def _sanitize_provider(row: dict, title: str = "", content_slug: str = "") -> dict | None:
    src = dict(row or {})
    key = _provider_key(src)
    name = _provider_name(src, key)

    final_url = _first_url(src, ("final_url", "watch_url", "deep_link", "deeplink", "url", "web_url", "link", "href"))
    search_url = _first_url(src, ("search_url", "provider_search_url")) or _build_search_url(key, title)
    homepage_url = _first_url(src, ("homepage_url", "provider_homepage_url")) or _build_homepage_url(key)

    if not key:
        return None

    # YouTube is strict.
    if key == "youtube":
        video_id = src.get("video_id") or src.get("youtube_video_id") or ""
        if final_url and (_is_youtube_watch(final_url) or video_id):
            src.update(
                provider_key=key,
                provider_display_name=name,
                final_url=final_url,
                provider_trust_label="YOUTUBE_DIRECT",
                provider_display_action="watch",
                provider_display_rank=0,
                provider_is_public_safe=True,
                provider_public_label="Watch on YouTube",
                provider_display_reason="youtube_valid_watch_url",
                provider_policy_version=PROVIDER_DISPLAY_POLICY_VERSION,
            )
            return src
        return None

    # If final URL is aggregator, it is never direct watch.
    if final_url and _is_aggregator_url(final_url):
        if _title_url_mismatch(title, final_url):
            # Example: Karuppu row pointing to Dhuandhaar TMDB URL.
            return None

        final_url = ""

    # If final URL host does not match provider, do not use as direct watch.
    if final_url and not _host_matches_provider(key, final_url):
        final_url = ""

    # Direct deeplink only when host is provider host and not a search page.
    if final_url and _host_matches_provider(key, final_url) and not _is_search_url(final_url):
        src.update(
            provider_key=key,
            provider_display_name=name,
            final_url=final_url,
            provider_trust_label="DIRECT_DEEPLINK",
            provider_display_action="watch",
            provider_display_rank=1,
            provider_is_public_safe=True,
            provider_public_label=f"Watch on {name}",
            provider_display_reason="provider_host_direct_deeplink",
            provider_policy_version=PROVIDER_DISPLAY_POLICY_VERSION,
        )
        return src

    # Search fallback is accepted as product-safe navigation.
    if search_url:
        src.update(
            provider_key=key,
            provider_display_name=name,
            final_url=search_url,
            search_url=search_url,
            provider_trust_label="PROVIDER_SEARCH_FALLBACK",
            provider_display_action="search",
            provider_display_rank=5,
            provider_is_public_safe=True,
            provider_public_label=f"Search on {name}",
            provider_display_reason="provider_search_fallback",
            provider_policy_version=PROVIDER_DISPLAY_POLICY_VERSION,
        )
        return src

    # Homepage fallback is accepted as product-safe navigation.
    if homepage_url:
        src.update(
            provider_key=key,
            provider_display_name=name,
            final_url=homepage_url,
            homepage_url=homepage_url,
            provider_trust_label="PROVIDER_HOMEPAGE_FALLBACK",
            provider_display_action="open",
            provider_display_rank=9,
            provider_is_public_safe=True,
            provider_public_label=f"Open {name}",
            provider_display_reason="provider_homepage_fallback",
            provider_policy_version=PROVIDER_DISPLAY_POLICY_VERSION,
        )
        return src

    return None


def _provider_list_from_item(item: dict) -> list:
    for key in ("providers", "availability", "ott_all", "watch_providers"):
        value = item.get(key)
        if isinstance(value, list):
            return value
    return []


def _apply_overlay_to_item(item: dict) -> dict:
    slug = str(item.get("slug") or item.get("content_slug") or "").strip().lower()
    overlay = PROVIDER_TRUTH_OVERLAYS.get(slug)
    if not overlay:
        return item

    title = str(item.get("title") or overlay.get("title") or "").strip()
    row = _sanitize_provider(dict(overlay), title=title, content_slug=slug)
    if not row:
        return item

    item["providers"] = [row]
    item["availability"] = [row]
    item["ott_all"] = [row]
    item["watch_providers"] = [row]
    item["ott_primary"] = row.get("provider_display_name") or row.get("provider_name")
    item["ott_primary_key"] = row.get("provider_key")
    item["provider_display_primary_key"] = row.get("provider_key")
    item["provider_display_primary_name"] = row.get("provider_display_name") or row.get("provider_name")
    item["provider_display_primary_label"] = row.get("provider_public_label")
    item["provider_public_count"] = 1
    item["provider_hidden_count"] = 0
    item["provider_policy_version"] = PROVIDER_DISPLAY_POLICY_VERSION
    item["provider_correction_source"] = overlay.get("source")
    return item


def apply_provider_safe_display_policy(providers, title: str = "", content_slug: str = "", *args, **kwargs):
    if isinstance(providers, dict):
        return apply_provider_safe_display_policy_to_item(providers, *args, **kwargs)

    out = []
    hidden = 0
    for row in providers or []:
        clean = _sanitize_provider(row, title=title, content_slug=content_slug)
        if clean:
            out.append(clean)
        else:
            hidden += 1

    out.sort(key=lambda r: int(r.get("provider_display_rank") or 99))
    return out


def apply_provider_safe_display_policy_to_item(item: dict, *args, **kwargs) -> dict:
    if not isinstance(item, dict):
        return item

    item = dict(item)
    item = _apply_overlay_to_item(item)

    title = str(item.get("title") or item.get("name") or "").strip()
    slug = str(item.get("slug") or item.get("content_slug") or "").strip().lower()

    raw = _provider_list_from_item(item)
    public = []
    hidden = 0

    for row in raw:
        clean = _sanitize_provider(row, title=title, content_slug=slug)
        if clean:
            public.append(clean)
        else:
            hidden += 1

    public.sort(key=lambda r: int(r.get("provider_display_rank") or 99))

    item["providers"] = public
    item["availability"] = public
    item["ott_all"] = public
    item["watch_providers"] = public
    item["provider_public_count"] = len(public)
    item["provider_hidden_count"] = hidden
    item["provider_policy_version"] = PROVIDER_DISPLAY_POLICY_VERSION

    if public:
        primary = public[0]
        item["ott_primary"] = primary.get("provider_display_name") or primary.get("provider_name")
        item["ott_primary_key"] = primary.get("provider_key")
        item["provider_display_primary_key"] = primary.get("provider_key")
        item["provider_display_primary_name"] = primary.get("provider_display_name") or primary.get("provider_name")
        item["provider_display_primary_label"] = primary.get("provider_public_label")
    else:
        item["provider_display_primary_key"] = None
        item["provider_display_primary_name"] = None
        item["provider_display_primary_label"] = None

    return item
