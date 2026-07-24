"""Keyword multi-search helpers for JobStreet PH notifier."""

from __future__ import annotations

import re

from scrape import (
    DEFAULT_DELAY,
    DEFAULT_MAX_PAGES,
    DEFAULT_SEARCH_URL,
    make_session,
    scrape_search,
)

BASE_URL = "https://ph.jobstreet.com"
DEFAULT_LOCATION = "cebu"
DEFAULT_QUERY = "pos=1&workarrangement=0&worktype=0"


def keyword_slug(keyword: str) -> str:
    """Turn 'data entry' into JobStreet path slug 'data-entry'."""
    text = (keyword or "").strip().lower()
    text = re.sub(r"[_\s]+", "-", text)
    text = re.sub(r"[^a-z0-9\-]+", "", text)
    text = re.sub(r"-+", "-", text).strip("-")
    return text


def keyword_to_search_url(
    keyword: str,
    location: str | None = None,
    query: str | None = None,
) -> str:
    """
    Example: 'data entry' + cebu ->
      https://ph.jobstreet.com/data-entry-jobs/in-cebu?pos=1&workarrangement=0&worktype=0
    """
    slug = keyword_slug(keyword)
    if not slug:
        return DEFAULT_SEARCH_URL
    loc = keyword_slug(location or DEFAULT_LOCATION) or DEFAULT_LOCATION
    q = (query if query is not None else DEFAULT_QUERY).lstrip("?")
    base = f"{BASE_URL}/{slug}-jobs/in-{loc}"
    return f"{base}?{q}" if q else base


def parse_keywords(raw: str | None) -> list[str]:
    """Empty / all / * -> []; otherwise comma-separated list."""
    if raw is None:
        return []
    text = str(raw).strip()
    if not text or text.lower() in {"all", "*", "(all)", "any", "default"}:
        return []
    return [k.strip() for k in text.split(",") if k.strip()]


def resolve_search_urls(
    keywords: list[str] | None = None,
    search_url: str | None = None,
    location: str | None = None,
) -> list[tuple[str, str]]:
    kws = [k for k in (keywords or []) if str(k).strip()]
    if kws:
        return [(k, keyword_to_search_url(k, location=location)) for k in kws]
    url = (search_url or "").strip() or DEFAULT_SEARCH_URL
    return [("default", url)]


def scrape_keywords(
    keywords: list[str] | None = None,
    search_url: str | None = None,
    location: str | None = None,
    max_pages: int = DEFAULT_MAX_PAGES,
    delay: float = DEFAULT_DELAY,
    session=None,
    force_selenium: bool | None = None,
) -> list[dict]:
    """
    Scrape one or more searches and merge/dedupe results.
    keywords empty -> default non-voice Cebu (or JOBSTREET_URL).
    """
    targets = resolve_search_urls(keywords, search_url=search_url, location=location)
    owns = session is None
    if session is None:
        session = make_session()
    collected: list[dict] = []
    try:
        for label, url in targets:
            print(f"\n=== Search: {label!r} ===")
            try:
                jobs = scrape_search(
                    search_url=url,
                    max_pages=max_pages,
                    delay=delay,
                    session=session,
                    force_selenium=force_selenium,
                )
                if label != "default":
                    for j in jobs:
                        j["matched_keyword"] = label
                collected.extend(jobs)
            except Exception as exc:
                print(f"  [warn] search {label!r} failed: {exc}")
    finally:
        if owns and session is not None:
            try:
                session.close()
            except Exception:
                pass

    by_id: dict[str, dict] = {}
    for job in collected:
        jid = str(job.get("job_id") or job.get("link") or "")
        if not jid:
            continue
        if jid not in by_id:
            by_id[jid] = dict(job)
            continue
        existing = by_id[jid]
        a = (existing.get("matched_keyword") or "").strip()
        b = (job.get("matched_keyword") or "").strip()
        if b and b not in {k.strip() for k in a.split(",") if k.strip()}:
            existing["matched_keyword"] = f"{a}, {b}" if a else b
    return list(by_id.values())
