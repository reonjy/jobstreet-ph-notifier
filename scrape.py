"""
JobStreet PH (SEEK) search scraper
==================================
Scrapes public job cards from ph.jobstreet.com search result pages.

Default search (overridable via JOBSTREET_URL / config):
  https://ph.jobstreet.com/non-voice-jobs/in-cebu?pos=1&workarrangement=0&worktype=0

Strategy:
  1) curl_cffi Chrome impersonation (fast, works on home IPs)
  2) Selenium + real Chrome fallback (needed on GitHub Actions - Cloudflare
     blocks many datacenter IPs even with TLS impersonation)

Personal / research use only. Respect site ToS and rate limits.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Iterable
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup

try:
    from curl_cffi import requests as curl_requests
except ImportError:
    curl_requests = None  # type: ignore

BASE_URL = "https://ph.jobstreet.com"
DEFAULT_SEARCH_URL = (
    "https://ph.jobstreet.com/non-voice-jobs/in-cebu"
    "?pos=1&workarrangement=0&worktype=0"
)
DEFAULT_DELAY = 2.0
DEFAULT_MAX_PAGES = 5
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)


def make_session():
    """Return a curl_cffi session that impersonates Chrome (or None if unavailable)."""
    if curl_requests is None:
        return None
    for profile in ("chrome131", "chrome124", "chrome120"):
        try:
            session = curl_requests.Session(impersonate=profile)
            session.headers.update(
                {
                    "User-Agent": USER_AGENT,
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "en-PH,en;q=0.9",
                    "Referer": f"{BASE_URL}/",
                }
            )
            session._impersonate = profile  # type: ignore[attr-defined]
            return session
        except Exception:
            continue
    return None


def fetch(session, url: str, delay: float) -> str | None:
    if session is None:
        return None
    if delay > 0:
        time.sleep(delay)
    try:
        resp = session.get(url, timeout=45)
        if resp.status_code != 200:
            print(f"  [warn] HTTP {resp.status_code} for {url}")
            return None
        text = resp.text or ""
        if "Just a moment" in text and (
            "cf-" in text.lower() or "cloudflare" in text.lower()
        ):
            print(f"  [warn] Cloudflare challenge page for {url}")
            return None
        return text
    except Exception as exc:
        print(f"  [warn] Failed to fetch {url}: {exc}")
        return None


def clean_text(value: str | None) -> str:
    if not value:
        return ""
    text = BeautifulSoup(value, "lxml").get_text(" ", strip=True)
    return re.sub(r"\s+", " ", text).strip()


def page_url(base_url: str, page: int) -> str:
    parsed = urlparse(base_url)
    qs = parse_qs(parsed.query, keep_blank_values=True)
    if page <= 1:
        qs.pop("page", None)
    else:
        qs["page"] = [str(page)]
    flat = {k: v[0] if len(v) == 1 else v for k, v in qs.items()}
    new_query = urlencode(flat, doseq=True)
    return urlunparse(parsed._replace(query=new_query))


def _auto(card, name: str) -> str:
    el = card.select_one(f'[data-automation="{name}"]')
    return clean_text(el.get_text()) if el else ""


def parse_listing_page(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    jobs: list[dict] = []
    seen_ids: set[str] = set()

    for card in soup.select("article"):
        link_el = card.select_one('a[href*="/job/"]')
        if not link_el:
            continue
        href = (link_el.get("href") or "").strip()
        m = re.search(r"/job/(\d+)", href)
        if not m:
            continue
        job_id = m.group(1)
        if job_id in seen_ids:
            continue
        seen_ids.add(job_id)

        link = urljoin(BASE_URL, href.split("?")[0])
        title = _auto(card, "jobTitle") or clean_text(link_el.get_text())
        company = _auto(card, "jobCompany")
        if not company:
            company_el = card.select_one('a[href*="-jobs"], a[href*="advertiserid"]')
            if company_el:
                company = clean_text(company_el.get_text())
        location = _auto(card, "jobLocation") or _auto(card, "jobCardLocation")
        salary = _auto(card, "jobSalary")
        description = _auto(card, "jobShortDescription")
        listed = _auto(card, "jobListingDate")
        classification = _auto(card, "jobClassification")
        sub_class = _auto(card, "jobSubClassification")

        etype = ""
        blob = card.get_text(" ", strip=True)
        m_type = re.search(r"This is a\s+(.+?)\s+job", blob, re.I)
        if m_type:
            etype = clean_text(m_type.group(1))

        jobs.append(
            {
                "job_id": job_id,
                "title": title,
                "company": company,
                "location": location,
                "salary": salary,
                "description": description,
                "listed": listed,
                "employment_type": etype,
                "classification": classification,
                "sub_classification": sub_class,
                "link": link,
            }
        )

    return jobs


def parse_total_jobs(html: str) -> int | None:
    m = re.search(r">\s*([\d,]+)\s+jobs?\s*<", html, re.I)
    if m:
        return int(m.group(1).replace(",", ""))
    m = re.search(r"\b([\d,]+)\s+jobs?\b", html, re.I)
    if m:
        return int(m.group(1).replace(",", ""))
    return None


def _create_chrome_driver(headless: bool = True):
    try:
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
        from selenium.webdriver.chrome.service import Service
    except ImportError as exc:
        raise RuntimeError(
            "Selenium not installed. Run: pip install selenium webdriver-manager"
        ) from exc

    options = Options()
    if headless:
        options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    options.add_argument(f"--user-agent={USER_AGENT}")
    options.add_argument("--lang=en-PH,en")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    options.add_argument("--log-level=3")

    chrome_bin = os.environ.get("CHROME_BIN") or os.environ.get("CHROME_PATH")
    if chrome_bin and os.path.isfile(chrome_bin):
        options.binary_location = chrome_bin

    try:
        from webdriver_manager.chrome import ChromeDriverManager

        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=options)
    except Exception:
        driver = webdriver.Chrome(options=options)

    driver.set_page_load_timeout(60)
    try:
        driver.execute_cdp_cmd(
            "Page.addScriptToEvaluateOnNewDocument",
            {
                "source": (
                    "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
                )
            },
        )
    except Exception:
        pass
    return driver


def fetch_with_selenium(url: str, headless: bool = True, wait_seconds: float = 8.0) -> str | None:
    print(f"  [selenium] Loading {url}")
    driver = None
    try:
        driver = _create_chrome_driver(headless=headless)
        driver.get(url)
        time.sleep(wait_seconds)
        html = driver.page_source or ""
        if "Just a moment" in html:
            time.sleep(10)
            html = driver.page_source or ""
        if "Just a moment" in html and len(html) < 20000:
            print("  [warn] Selenium still on Cloudflare challenge page")
            return None
        if not re.search(r"/job/\d+", html) and "article" not in html.lower():
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight/2);")
            time.sleep(3)
            html = driver.page_source or ""
        return html
    except Exception as exc:
        print(f"  [warn] Selenium fetch failed: {exc}")
        return None
    finally:
        if driver is not None:
            try:
                driver.quit()
            except Exception:
                pass


def scrape_search(
    search_url: str | None = None,
    max_pages: int = DEFAULT_MAX_PAGES,
    delay: float = DEFAULT_DELAY,
    session=None,
    force_selenium: bool | None = None,
) -> list[dict]:
    """Scrape one or more result pages for the given search URL."""
    url = (search_url or DEFAULT_SEARCH_URL).strip()
    owns_session = session is None
    if session is None:
        session = make_session()

    if force_selenium is None:
        force_selenium = (os.environ.get("FORCE_SELENIUM") or "").lower() in (
            "1",
            "true",
            "yes",
            "y",
        )

    all_jobs: list[dict] = []
    print(f"  Search URL: {url}")

    use_selenium = force_selenium
    for page in range(1, max(1, max_pages) + 1):
        page_link = page_url(url, page)
        print(f"  Page {page}: {page_link}")

        html = None
        if not use_selenium:
            html = fetch(session, page_link, delay if page > 1 else min(delay, 0.5))
            if html is None and page == 1:
                print("  HTTP scrape blocked/failed - falling back to Selenium Chrome...")
                use_selenium = True

        if use_selenium:
            headless = (os.environ.get("HEADLESS") or "true").lower() in (
                "1",
                "true",
                "yes",
                "y",
            )
            html = fetch_with_selenium(page_link, headless=headless)
            if delay > 0 and page > 1:
                time.sleep(delay)

        if not html:
            break

        if page == 1:
            total = parse_total_jobs(html)
            if total is not None:
                print(f"  Site reports ~{total} jobs for this search")

        page_jobs = parse_listing_page(html)
        print(f"  Found {len(page_jobs)} job card(s)")
        if not page_jobs:
            if not use_selenium and page == 1:
                print("  No cards via HTTP - retrying page 1 with Selenium...")
                use_selenium = True
                headless = (os.environ.get("HEADLESS") or "true").lower() in (
                    "1",
                    "true",
                    "yes",
                    "y",
                )
                html = fetch_with_selenium(page_link, headless=headless)
                if html:
                    page_jobs = parse_listing_page(html)
                    print(f"  Found {len(page_jobs)} job card(s) via Selenium")
            if not page_jobs:
                break
        all_jobs.extend(page_jobs)

        if len(page_jobs) < 20:
            break

    if owns_session and session is not None:
        try:
            session.close()
        except Exception:
            pass

    return dedupe_jobs(all_jobs)


def dedupe_jobs(jobs: Iterable[dict]) -> list[dict]:
    by_id: dict[str, dict] = {}
    for job in jobs:
        jid = str(job.get("job_id") or job.get("link") or "")
        if not jid:
            continue
        if jid not in by_id:
            by_id[jid] = dict(job)
    return list(by_id.values())


def sort_jobs(jobs: list[dict]) -> list[dict]:
    def score(j: dict) -> tuple:
        listed = (j.get("listed") or "").lower().strip()
        m_h = re.search(r"(\d+)\s*h", listed)
        if m_h:
            return (0, int(m_h.group(1)))
        m_d = re.search(r"(\d+)\s*d", listed)
        if m_d:
            return (1, int(m_d.group(1)))
        if "just" in listed or listed in {"new", "today"}:
            return (0, 0)
        if "30d" in listed or "+" in listed:
            return (2, 999)
        return (2, 500)

    return sorted(jobs, key=score)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Scrape JobStreet PH search results.")
    p.add_argument("--url", default=DEFAULT_SEARCH_URL, help="Full JobStreet search URL")
    p.add_argument("--max-pages", type=int, default=DEFAULT_MAX_PAGES)
    p.add_argument("--delay", type=float, default=DEFAULT_DELAY)
    p.add_argument("--selenium", action="store_true", help="Force Selenium Chrome")
    p.add_argument("--json", default=None, help="Optional path to write JSON output")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    print("=" * 56)
    print("  JobStreet PH scraper")
    print("=" * 56)
    jobs = scrape_search(
        args.url,
        max_pages=args.max_pages,
        delay=args.delay,
        force_selenium=args.selenium or None,
    )
    jobs = sort_jobs(jobs)
    print(f"\nTotal unique jobs: {len(jobs)}")
    for j in jobs[:10]:
        print(
            f"  - [{j.get('job_id')}] {j.get('title', '')[:55]} "
            f"| {j.get('company', '')[:30]} | {j.get('salary') or '-'} | {j.get('listed')}"
        )
    if args.json:
        import json

        path = Path(args.json)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "scraped_at": datetime.now().isoformat(timespec="seconds"),
                    "count": len(jobs),
                    "jobs": jobs,
                },
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        print(f"Wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
