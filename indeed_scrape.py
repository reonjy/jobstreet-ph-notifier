"""
Indeed PH search scraper
========================
Scrapes public job cards from ph.indeed.com.

Default search:
  https://ph.indeed.com/jobs?q=non+voice&l=Cebu

Uses curl_cffi Chrome impersonation; falls back to Selenium if blocked
(same env flags as JobStreet: FORCE_SELENIUM, HEADLESS).
"""

from __future__ import annotations

import os
import re
import time
from typing import Iterable
from urllib.parse import parse_qs, quote_plus, urlencode, urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup

from scrape import (
    DEFAULT_DELAY,
    DEFAULT_MAX_PAGES,
    USER_AGENT,
    fetch_with_selenium,
    make_session,
)

BASE_URL = "https://ph.indeed.com"
DEFAULT_INDEED_URL = "https://ph.indeed.com/jobs?q=non+voice&l=Cebu"
DEFAULT_INDEED_LOCATION = "Cebu"
DEFAULT_INDEED_QUERY = "non voice"
# Indeed pages typically show ~15 jobs; start=0,10,20...
PAGE_SIZE = 10


def _strip_tracking_params(url: str) -> str:
    """Drop noisy tracking params (vjk, from, ...) for a stable default URL."""
    parsed = urlparse(url)
    qs = parse_qs(parsed.query, keep_blank_values=True)
    keep = {}
    for key in ("q", "l", "start", "radius", "sort", "fromage", "jt", "explvl"):
        if key in qs:
            keep[key] = qs[key]
    return urlunparse(parsed._replace(query=urlencode(keep, doseq=True)))


def keyword_to_indeed_url(
    keyword: str,
    location: str | None = None,
) -> str:
    """Build Indeed search URL for one keyword + location."""
    q = (keyword or DEFAULT_INDEED_QUERY).strip() or DEFAULT_INDEED_QUERY
    loc = (location or DEFAULT_INDEED_LOCATION).strip() or DEFAULT_INDEED_LOCATION
    return f"{BASE_URL}/jobs?q={quote_plus(q)}&l={quote_plus(loc)}"


def parse_keywords(raw: str | None) -> list[str]:
    if raw is None:
        return []
    text = str(raw).strip()
    if not text or text.lower() in {"all", "*", "(all)", "any", "default"}:
        return []
    return [k.strip() for k in text.split(",") if k.strip()]


def resolve_indeed_urls(
    keywords: list[str] | None = None,
    search_url: str | None = None,
    location: str | None = None,
) -> list[tuple[str, str]]:
    """Return list of (label, url) for Indeed searches."""
    kws = [k for k in (keywords or []) if str(k).strip()]
    if kws:
        return [(k, keyword_to_indeed_url(k, location=location)) for k in kws]
    url = (search_url or "").strip() or DEFAULT_INDEED_URL
    return [("default", _strip_tracking_params(url))]


def page_url(base_url: str, start: int) -> str:
    """Add or replace start=N (0, 10, 20, ...)."""
    parsed = urlparse(base_url)
    qs = parse_qs(parsed.query, keep_blank_values=True)
    if start <= 0:
        qs.pop("start", None)
    else:
        qs["start"] = [str(start)]
    flat = {k: v[0] if len(v) == 1 else v for k, v in qs.items()}
    return urlunparse(parsed._replace(query=urlencode(flat, doseq=True)))


def _text(el) -> str:
    if not el:
        return ""
    return re.sub(r"\s+", " ", el.get_text(" ", strip=True)).strip()


def parse_listing_page(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    jobs: list[dict] = []
    seen: set[str] = set()

    cards = soup.select("div.job_seen_beacon")
    if not cards:
        # Fallback: any element with data-jk
        cards = soup.select("[data-jk]")

    for card in cards:
        jk = (card.get("data-jk") or "").strip()
        title_a = card.select_one("a.jcs-JobTitle") or card.select_one("h2 a")
        if not jk and title_a:
            jk = (title_a.get("data-jk") or "").strip()
            if not jk:
                href = title_a.get("href") or ""
                m = re.search(r"[?&]jk=([a-f0-9]+)", href)
                if m:
                    jk = m.group(1)
        if not jk or jk in seen:
            continue
        seen.add(jk)

        title = _text(title_a) if title_a else ""
        href = (title_a.get("href") if title_a else "") or ""
        if href.startswith("/"):
            link = urljoin(BASE_URL, href.split("&bb=")[0])
        elif href:
            link = href
        else:
            link = f"{BASE_URL}/viewjob?jk={jk}"
        # Prefer clean viewjob link
        link = f"{BASE_URL}/viewjob?jk={jk}"

        company = _text(
            card.select_one("[data-testid='company-name']")
            or card.select_one(".companyName")
        )
        location = _text(
            card.select_one("[data-testid='text-location']")
            or card.select_one(".companyLocation")
        )
        salary = _text(
            card.select_one(".salary-snippet-container")
            or card.select_one(".estimated-salary")
            or card.select_one("[data-testid='attribute_snippet_testid']")
        )
        # Optional date / metadata
        listed = _text(
            card.select_one("[data-testid='myJobsStateDate']")
            or card.select_one("span.date")
            or card.select_one(".date")
        )
        # Snippet / description
        desc = _text(
            card.select_one(".job-snippet")
            or card.select_one("[data-testid='job-snippet']")
        )

        jobs.append(
            {
                "job_id": jk,
                "source": "indeed",
                "title": title,
                "company": company,
                "location": location,
                "salary": salary,
                "description": desc,
                "listed": listed,
                "employment_type": "",
                "classification": "",
                "sub_classification": "",
                "link": link,
            }
        )

    return jobs


def _indeed_headers() -> dict[str, str]:
    return {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-PH,en;q=0.9",
        "Referer": f"{BASE_URL}/",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "same-origin",
        "Upgrade-Insecure-Requests": "1",
    }


def make_indeed_session():
    """curl_cffi session with Indeed Referer (not JobStreet)."""
    session = make_session()
    if session is not None:
        try:
            session.headers.update(_indeed_headers())
        except Exception:
            pass
    return session


def fetch_indeed_selenium(url: str, headless: bool = True) -> str | None:
    """Selenium load tuned for Indeed: wait for job cards, scroll, diagnostics."""
    print(f"  [selenium/indeed] Loading {url}")
    driver = None
    try:
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.webdriver.support.ui import WebDriverWait

        from scrape import _create_chrome_driver

        driver = _create_chrome_driver(headless=headless)
        driver.get(url)
        try:
            WebDriverWait(driver, 18).until(
                EC.presence_of_element_located(
                    (By.CSS_SELECTOR, "div.job_seen_beacon, a.jcs-JobTitle, [data-jk]")
                )
            )
        except Exception:
            time.sleep(6)
        try:
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight/3);")
            time.sleep(2)
        except Exception:
            pass
        html = driver.page_source or ""
        if "job_seen_beacon" not in html and "data-jk" not in html:
            title = ""
            try:
                title = driver.title or ""
            except Exception:
                pass
            print(
                f"  [warn] Indeed Selenium: no job cards "
                f"(title={title!r}, html_len={len(html)})"
            )
        return html
    except Exception as exc:
        print(f"  [warn] Indeed Selenium failed: {exc}")
        try:
            return fetch_with_selenium(url, headless=headless, wait_seconds=15.0)
        except Exception as exc2:
            print(f"  [warn] Indeed Selenium fallback failed: {exc2}")
            return None
    finally:
        if driver is not None:
            try:
                driver.quit()
            except Exception:
                pass


def fetch_html(session, url: str, delay: float, force_selenium: bool) -> str | None:
    headless = (os.environ.get("HEADLESS") or "true").lower() in (
        "1",
        "true",
        "yes",
        "y",
    )
    if force_selenium:
        return fetch_indeed_selenium(url, headless=headless)

    if delay > 0:
        time.sleep(delay)
    try:
        if session is None:
            print("  [warn] No HTTP session - Selenium for Indeed...")
            return fetch_indeed_selenium(url, headless=headless)
        try:
            session.headers.update(_indeed_headers())
        except Exception:
            pass
        resp = session.get(url, timeout=45)
        if resp.status_code != 200:
            print(f"  [warn] Indeed HTTP {resp.status_code} for {url}")
            print("  Falling back to Selenium for Indeed...")
            return fetch_indeed_selenium(url, headless=headless)
        text = resp.text or ""
        if "Just a moment" in text or "captcha" in text.lower()[:2000]:
            print("  [warn] Indeed blocked/challenge - Selenium fallback...")
            return fetch_indeed_selenium(url, headless=headless)
        if "job_seen_beacon" not in text and "data-jk" not in text:
            print("  [warn] Indeed HTML has no job cards - Selenium fallback...")
            return fetch_indeed_selenium(url, headless=headless)
        return text
    except Exception as exc:
        print(f"  [warn] Indeed fetch failed: {exc}")
        return fetch_indeed_selenium(url, headless=headless)


def scrape_indeed_search(
    search_url: str | None = None,
    max_pages: int = DEFAULT_MAX_PAGES,
    delay: float = DEFAULT_DELAY,
    session=None,
    force_selenium: bool | None = None,
    matched_keyword: str | None = None,
) -> list[dict]:
    url = (search_url or DEFAULT_INDEED_URL).strip()
    url = _strip_tracking_params(url)
    owns = session is None
    if session is None:
        session = make_indeed_session()
    elif session is not None:
        try:
            session.headers.update(_indeed_headers())
        except Exception:
            pass

    # Prefer curl_cffi for Indeed even when JobStreet uses FORCE_SELENIUM
    # (GHA Selenium often gets empty Indeed pages). Force only via
    # FORCE_SELENIUM_INDEED or an explicit force_selenium=True argument.
    if force_selenium is None:
        force_selenium = (os.environ.get("FORCE_SELENIUM_INDEED") or "").lower() in (
            "1",
            "true",
            "yes",
            "y",
        )

    all_jobs: list[dict] = []
    print(f"  [Indeed] Search URL: {url}")
    if matched_keyword:
        print(f"  [Indeed] Keyword: {matched_keyword!r}")

    try:
        for page in range(max(1, max_pages)):
            start = page * PAGE_SIZE
            page_link = page_url(url, start)
            print(f"  [Indeed] Page {page + 1} start={start}: {page_link}")
            html = fetch_html(
                session,
                page_link,
                delay if page > 0 else min(delay, 0.5),
                force_selenium,
            )
            if not html:
                break
            page_jobs = parse_listing_page(html)
            print(f"  [Indeed] Found {len(page_jobs)} job card(s)")
            if not page_jobs:
                break
            if matched_keyword:
                for j in page_jobs:
                    j["matched_keyword"] = matched_keyword
            all_jobs.extend(page_jobs)
            if len(page_jobs) < 5:
                break
    finally:
        if owns and session is not None:
            try:
                session.close()
            except Exception:
                pass

    return dedupe_jobs(all_jobs)


def scrape_indeed_keywords(
    keywords: list[str] | None = None,
    search_url: str | None = None,
    location: str | None = None,
    max_pages: int = DEFAULT_MAX_PAGES,
    delay: float = DEFAULT_DELAY,
    session=None,
    force_selenium: bool | None = None,
) -> list[dict]:
    targets = resolve_indeed_urls(keywords, search_url=search_url, location=location)
    owns = session is None
    if session is None:
        session = make_indeed_session()
    else:
        try:
            session.headers.update(_indeed_headers())
        except Exception:
            pass
    collected: list[dict] = []
    try:
        for label, url in targets:
            print(f"\n=== Indeed search: {label!r} ===")
            try:
                collected.extend(
                    scrape_indeed_search(
                        search_url=url,
                        max_pages=max_pages,
                        delay=delay,
                        session=session,
                        force_selenium=force_selenium,
                        matched_keyword=None if label == "default" else label,
                    )
                )
            except Exception as exc:
                print(f"  [warn] Indeed search {label!r} failed: {exc}")
    finally:
        if owns and session is not None:
            try:
                session.close()
            except Exception:
                pass
    return dedupe_jobs(collected)


def dedupe_jobs(jobs: Iterable[dict]) -> list[dict]:
    by_id: dict[str, dict] = {}
    for job in jobs:
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
        for field in (
            "title",
            "company",
            "location",
            "salary",
            "description",
            "listed",
            "link",
        ):
            if not existing.get(field) and job.get(field):
                existing[field] = job[field]
    return list(by_id.values())
