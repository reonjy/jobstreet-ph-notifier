"""Keyword multi-search helpers for JobStreet PH notifier."""

from __future__ import annotations

import os
import re
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from scrape import (
    DEFAULT_DELAY,
    DEFAULT_MAX_PAGES,
    DEFAULT_SEARCH_URL,
    make_session,
    scrape_search,
)

BASE_URL = "https://ph.jobstreet.com"
DEFAULT_LOCATION = "cebu"
# workarrangement: 0=any, 1=on-site, 2=hybrid, 3=remote
DEFAULT_QUERY = "pos=1&workarrangement=0&worktype=0"
REMOTE_QUERY = "pos=1&workarrangement=3&worktype=0"
# Remote jobs are often listed nationwide, not under a single city
DEFAULT_REMOTE_LOCATION = "philippines"
DEFAULT_REMOTE_URL = (
    f"{BASE_URL}/non-voice-jobs/in-{DEFAULT_REMOTE_LOCATION}?{REMOTE_QUERY}"
)


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


def keyword_to_remote_url(
    keyword: str,
    remote_location: str | None = None,
) -> str:
    """Remote-only search (workarrangement=3), usually Philippines-wide."""
    slug = keyword_slug(keyword)
    loc = keyword_slug(remote_location or DEFAULT_REMOTE_LOCATION) or DEFAULT_REMOTE_LOCATION
    if not slug:
        return DEFAULT_REMOTE_URL
    return f"{BASE_URL}/{slug}-jobs/in-{loc}?{REMOTE_QUERY}"


def parse_keywords(raw: str | None) -> list[str]:
    """Empty / all / * -> []; otherwise comma-separated list."""
    if raw is None:
        return []
    text = str(raw).strip()
    if not text or text.lower() in {"all", "*", "(all)", "any", "default"}:
        return []
    return [k.strip() for k in text.split(",") if k.strip()]


def _env_truthy(name: str, default: str = "true") -> bool:
    val = os.environ.get(name)
    if val is None or str(val).strip() == "":
        val = default
    return str(val).strip().lower() in ("1", "true", "yes", "y")


def with_workarrangement(url: str, arrangement: int) -> str:
    """Set or replace workarrangement=N on a JobStreet search URL."""
    parsed = urlparse(url)
    qs = parse_qs(parsed.query, keep_blank_values=True)
    qs["workarrangement"] = [str(arrangement)]
    flat = {k: v[0] if len(v) == 1 else v for k, v in qs.items()}
    return urlunparse(parsed._replace(query=urlencode(flat, doseq=True)))


def resolve_search_urls(
    keywords: list[str] | None = None,
    search_url: str | None = None,
    location: str | None = None,
    include_remote: bool | None = None,
    remote_location: str | None = None,
) -> list[tuple[str, str]]:
    """
    Return list of (label, url) to scrape.

    By default also adds a Remote (workarrangement=3) pass so WFH roles
    (often listed under Philippines, not only Cebu) are included.
    """
    if include_remote is None:
        include_remote = _env_truthy("INCLUDE_REMOTE", "true")
    rloc = (remote_location or os.environ.get("REMOTE_LOCATION") or DEFAULT_REMOTE_LOCATION).strip()

    kws = [k for k in (keywords or []) if str(k).strip()]
    targets: list[tuple[str, str]] = []
    if kws:
        for k in kws:
            targets.append((k, keyword_to_search_url(k, location=location)))
            if include_remote:
                targets.append(
                    (f"{k} [remote]", keyword_to_remote_url(k, remote_location=rloc))
                )
        return targets

    url = (search_url or "").strip() or DEFAULT_SEARCH_URL
    targets.append(("default", url))
    if include_remote:
        # Prefer dedicated remote URL; if JOBSTREET_URL already forces
        # workarrangement, still add a clear remote PH pass for non-voice default.
        targets.append(("default [remote]", DEFAULT_REMOTE_URL))
    return targets


def scrape_keywords(
    keywords: list[str] | None = None,
    search_url: str | None = None,
    location: str | None = None,
    max_pages: int = DEFAULT_MAX_PAGES,
    delay: float = DEFAULT_DELAY,
    session=None,
    force_selenium: bool | None = None,
    include_remote: bool | None = None,
    remote_location: str | None = None,
) -> list[dict]:
    """
    Scrape one or more searches and merge/dedupe results.
    keywords empty -> default non-voice Cebu (or JOBSTREET_URL),
    plus remote PH pass when INCLUDE_REMOTE is on (default).
    """
    targets = resolve_search_urls(
        keywords,
        search_url=search_url,
        location=location,
        include_remote=include_remote,
        remote_location=remote_location,
    )
    owns = session is None
    if session is None:
        session = make_session()
    collected: list[dict] = []
    try:
        for label, url in targets:
            print(f"\n=== Search: {label!r} ===")
            print(f"  URL: {url}")
            try:
                jobs = scrape_search(
                    search_url=url,
                    max_pages=max_pages,
                    delay=delay,
                    session=session,
                    force_selenium=force_selenium,
                )
                is_remote = "[remote]" in label.lower() or "workarrangement=3" in url
                for j in jobs:
                    if label not in ("default",) and not label.endswith(" [remote]"):
                        j["matched_keyword"] = label
                    elif label.endswith(" [remote]"):
                        base_kw = label[: -len(" [remote]")].strip()
                        if base_kw and base_kw != "default":
                            j["matched_keyword"] = base_kw
                        j["work_arrangement"] = "Remote"
                    if is_remote:
                        j["work_arrangement"] = j.get("work_arrangement") or "Remote"
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
        if not existing.get("work_arrangement") and job.get("work_arrangement"):
            existing["work_arrangement"] = job["work_arrangement"]
        for field in (
            "title",
            "company",
            "location",
            "salary",
            "description",
            "listed",
            "link",
            "employment_type",
        ):
            if not existing.get(field) and job.get(field):
                existing[field] = job[field]
    return list(by_id.values())
