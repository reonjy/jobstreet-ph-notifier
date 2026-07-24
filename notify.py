"""
JobStreet PH + Indeed PH → Telegram Notifier
============================================
Polls JobStreet and/or Indeed search URLs and sends NEW job cards to
the same Telegram bot.

Defaults (when KEYWORDS empty):
  JobStreet: non-voice jobs in Cebu
  Indeed:    https://ph.indeed.com/jobs?q=non+voice&l=Cebu

Usage:
    python notify.py --once
    python notify.py --test
    python notify.py --resend-all --once

Env / GitHub Actions secrets:
  TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID  (required)
  KEYWORDS            (optional) comma list shared by both sites
                      empty / unset = default non-voice Cebu on each site
  JOBSTREET_LOCATION  (optional) default "cebu"
  JOBSTREET_URL       (optional; only when KEYWORDS empty)
  INDEED_URL          (optional; only when KEYWORDS empty)
  INDEED_LOCATION     (optional) default "Cebu"
  ENABLE_JOBSTREET    (optional) default true
  ENABLE_INDEED       (optional) default true
  MAX_PAGES, REQUEST_DELAY_SECONDS
  SEND_ON_FIRST_RUN, RESEND_ALL, MAX_SEND_PER_RUN, STATE_FILE
"""

from __future__ import annotations

import argparse
import html
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

from indeed_scrape import (
    DEFAULT_INDEED_LOCATION,
    DEFAULT_INDEED_URL,
    scrape_indeed_keywords,
)
from keywords import (
    DEFAULT_LOCATION,
    parse_keywords,
    scrape_keywords,
)
from scrape import (
    DEFAULT_DELAY,
    DEFAULT_MAX_PAGES,
    DEFAULT_SEARCH_URL,
    make_session,
    sort_jobs,
)

ROOT = Path(__file__).resolve().parent
STATE_FILE = ROOT / "state" / "seen_jobs.json"
TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"


def _env(name: str, default: str | None = None) -> str | None:
    val = os.environ.get(name)
    if val is None or str(val).strip() == "":
        return default
    return str(val).strip()


def _truthy(name: str, default: str = "false") -> bool:
    return (_env(name, default) or default).lower() in ("1", "true", "yes", "y")


def load_settings() -> dict:
    token = (_env("TELEGRAM_BOT_TOKEN") or "").strip()
    chat_id = str(_env("TELEGRAM_CHAT_ID") or "").strip()
    if not token:
        print("ERROR: Set TELEGRAM_BOT_TOKEN env (or GitHub secret).")
        sys.exit(1)
    if not chat_id:
        print("ERROR: Set TELEGRAM_CHAT_ID env (or GitHub secret).")
        sys.exit(1)

    chat_id = chat_id.strip().strip('"').strip("'")
    if chat_id.lower().startswith("chat_id="):
        chat_id = chat_id.split("=", 1)[1].strip()

    # JOBSTREET_URL / INDEED_URL only used when KEYWORDS is empty
    search_url = _env("JOBSTREET_URL") or DEFAULT_SEARCH_URL
    indeed_url = _env("INDEED_URL") or DEFAULT_INDEED_URL
    # KEYWORDS empty / unset / "all" -> default non-voice Cebu on each site
    keywords = parse_keywords(
        os.environ.get("KEYWORDS") if "KEYWORDS" in os.environ else None
    )
    location = _env("JOBSTREET_LOCATION", DEFAULT_LOCATION) or DEFAULT_LOCATION
    indeed_location = (
        _env("INDEED_LOCATION", DEFAULT_INDEED_LOCATION) or DEFAULT_INDEED_LOCATION
    )
    enable_jobstreet = _truthy("ENABLE_JOBSTREET", "true")
    enable_indeed = _truthy("ENABLE_INDEED", "true")
    if not enable_jobstreet and not enable_indeed:
        print("ERROR: Both ENABLE_JOBSTREET and ENABLE_INDEED are false.")
        sys.exit(1)

    max_pages = int(_env("MAX_PAGES", str(DEFAULT_MAX_PAGES)) or DEFAULT_MAX_PAGES)
    delay = float(_env("REQUEST_DELAY_SECONDS", str(DEFAULT_DELAY)) or DEFAULT_DELAY)
    interval = float(_env("POLL_INTERVAL_MINUTES", "15") or "15")
    send_first = _truthy("SEND_ON_FIRST_RUN", "false")
    resend_all = _truthy("RESEND_ALL", "false")
    max_send = int(_env("MAX_SEND_PER_RUN", "40") or "40")

    state = _env("STATE_FILE")
    if state:
        state_path = Path(state)
    else:
        data_dir = Path("/data")
        if data_dir.is_dir() and os.access(data_dir, os.W_OK):
            state_path = data_dir / "jobstreet_seen_jobs.json"
        else:
            state_path = STATE_FILE

    return {
        "telegram_bot_token": token,
        "telegram_chat_id": chat_id,
        "search_url": search_url,
        "indeed_url": indeed_url,
        "keywords": keywords,
        "location": location,
        "indeed_location": indeed_location,
        "enable_jobstreet": enable_jobstreet,
        "enable_indeed": enable_indeed,
        "max_pages": max_pages,
        "request_delay_seconds": delay,
        "poll_interval_minutes": interval,
        "send_on_first_run": send_first,
        "resend_all": resend_all,
        "max_send_per_run": max_send,
        "state_file": state_path,
    }


def load_seen(path: Path) -> set[str]:
    if not path.exists():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return set(data.get("seen_ids", []))
    except (json.JSONDecodeError, OSError):
        return set()


def save_seen(path: Path, seen: set[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ids = sorted(seen)[-8000:]
    payload = {
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "count": len(ids),
        "seen_ids": ids,
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def job_id(job: dict) -> str:
    """Prefix IDs so JobStreet and Indeed never collide in the seen cache."""
    source = (job.get("source") or "jobstreet").strip().lower()
    prefix = "ind" if source == "indeed" else "js"
    jid = str(job.get("job_id") or "").strip()
    if jid:
        return f"{prefix}-{jid}"
    link = (job.get("link") or "").strip()
    return f"{prefix}-{link}" if link else f"{prefix}-unknown"


def source_label(job: dict) -> str:
    source = (job.get("source") or "jobstreet").strip().lower()
    return "Indeed" if source == "indeed" else "JobStreet"


def open_link_label(job: dict) -> str:
    source = (job.get("source") or "jobstreet").strip().lower()
    return "Open on Indeed" if source == "indeed" else "Open on Jobstreet"


def telegram_call(token: str, method: str, payload: dict) -> dict:
    url = TELEGRAM_API.format(token=token, method=method)
    resp = requests.post(url, json=payload, timeout=30)
    data = resp.json()
    if not data.get("ok"):
        desc = data.get("description") or data
        hint = ""
        if "chat not found" in str(desc).lower():
            hint = (
                "\n\nCHAT NOT FOUND — fix TELEGRAM_CHAT_ID:\n"
                "  1. Open your bot and press Start / send a message\n"
                "  2. Open getUpdates and copy chat.id (number only)\n"
            )
        raise RuntimeError(f"Telegram API error: {data}{hint}")
    return data


def send_telegram_message(token: str, chat_id: str, text: str) -> None:
    telegram_call(
        token,
        "sendMessage",
        {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": False,
        },
    )


def format_job_message(job: dict) -> str:
    """Simple one-line fields + emojis + Open on Jobstreet/Indeed text link."""
    title = html.escape(job.get("title") or "Untitled job")
    company = html.escape(job.get("company") or "—")
    location = html.escape(job.get("location") or "—")
    salary = html.escape(job.get("salary") or "Not stated")
    etype = html.escape(job.get("employment_type") or "—")
    listed = html.escape(job.get("listed") or "—")
    desc = html.escape(job.get("description") or "")
    if len(desc) > 280:
        desc = desc[:277] + "…"
    sub = html.escape(job.get("sub_classification") or "")
    link = (job.get("link") or "").strip()
    src = source_label(job)

    lines = [
        f"💼 <b>{src}: {title}</b>",
        f"🏢 <b>Company:</b> {company}",
        f"📍 <b>Location:</b> {location}",
        f"💰 <b>Salary:</b> {salary}",
        f"⏱ <b>Type:</b> {etype}",
        f"📅 <b>Listed:</b> {listed}",
    ]
    if sub:
        lines.append(f"🏷 <b>Class:</b> {sub}")
    kw = html.escape(job.get("matched_keyword") or "")
    if kw:
        lines.append(f"🔑 <b>Keyword:</b> {kw}")
    if desc:
        lines.append(f"📝 {desc}")
    if link:
        lines.append(
            f'<a href="{html.escape(link)}">🔗 {html.escape(open_link_label(job))}</a>'
        )
    return "\n".join(lines)


def verify_telegram(token: str, chat_id: str) -> None:
    me = telegram_call(token, "getMe", {})
    username = (me.get("result") or {}).get("username") or "?"
    telegram_call(token, "getChat", {"chat_id": chat_id})
    print(f"Telegram OK (bot @{username}, chat {chat_id}).")


def test_telegram(token: str, chat_id: str) -> None:
    verify_telegram(token, chat_id)
    send_telegram_message(
        token,
        chat_id,
        "✅ JobStreet + Indeed PH notifier is connected.\n"
        "You will get messages when new matching jobs appear on either site.",
    )


def _tag_source(jobs: list[dict], source: str) -> list[dict]:
    out: list[dict] = []
    for j in jobs:
        item = dict(j)
        item["source"] = source
        out.append(item)
    return out


def scrape_all(settings: dict) -> list[dict]:
    """Scrape enabled sources and merge into one list."""
    keywords = settings.get("keywords") or []
    max_pages = int(settings["max_pages"])
    delay = float(settings["request_delay_seconds"])
    all_jobs: list[dict] = []
    session = make_session()
    try:
        if settings.get("enable_jobstreet", True):
            print("\n--- JobStreet ---")
            if keywords:
                print(f"  Keywords: {', '.join(keywords)}")
                print(f"  Location: {settings.get('location') or DEFAULT_LOCATION}")
            else:
                print("  Keywords: (default non-voice)")
                print(f"  URL: {settings['search_url']}")
            try:
                js_jobs = scrape_keywords(
                    keywords=keywords,
                    search_url=settings["search_url"],
                    location=settings.get("location") or DEFAULT_LOCATION,
                    max_pages=max_pages,
                    delay=delay,
                    session=session,
                )
                js_jobs = _tag_source(js_jobs, "jobstreet")
                print(f"  JobStreet: {len(js_jobs)} job(s)")
                all_jobs.extend(js_jobs)
            except Exception as exc:
                print(f"  [warn] JobStreet scrape failed: {exc}")
                import traceback

                traceback.print_exc()

        if settings.get("enable_indeed", True):
            print("\n--- Indeed ---")
            if keywords:
                print(f"  Keywords: {', '.join(keywords)}")
                print(
                    f"  Location: "
                    f"{settings.get('indeed_location') or DEFAULT_INDEED_LOCATION}"
                )
            else:
                print("  Keywords: (default non-voice)")
                print(f"  URL: {settings['indeed_url']}")
            try:
                ind_jobs = scrape_indeed_keywords(
                    keywords=keywords,
                    search_url=settings["indeed_url"],
                    location=settings.get("indeed_location") or DEFAULT_INDEED_LOCATION,
                    max_pages=max_pages,
                    delay=delay,
                    session=session,
                )
                ind_jobs = _tag_source(ind_jobs, "indeed")
                print(f"  Indeed: {len(ind_jobs)} job(s)")
                all_jobs.extend(ind_jobs)
            except Exception as exc:
                print(f"  [warn] Indeed scrape failed: {exc}")
                import traceback

                traceback.print_exc()
    finally:
        try:
            session.close()
        except Exception:
            pass

    return all_jobs


def run_once(settings: dict, seen: set[str]) -> set[str]:
    state_path = Path(settings["state_file"])
    resend_all = bool(settings.get("resend_all"))
    send_on_first = bool(settings.get("send_on_first_run"))
    keywords = settings.get("keywords") or []

    sources = []
    if settings.get("enable_jobstreet", True):
        sources.append("JobStreet")
    if settings.get("enable_indeed", True):
        sources.append("Indeed")

    print(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Polling {', '.join(sources)}…")
    if keywords:
        print(f"  Keywords: {', '.join(keywords)}")
    else:
        print("  Keywords: (default non-voice Cebu on each site)")
    print(
        f"  Options: send_on_first_run={send_on_first} resend_all={resend_all} "
        f"already_seen={len(seen)}"
    )

    jobs = scrape_all(settings)
    jobs = sort_jobs(jobs)
    js_n = sum(1 for j in jobs if (j.get("source") or "") == "jobstreet")
    ind_n = sum(1 for j in jobs if (j.get("source") or "") == "indeed")
    print(f"  Found {len(jobs)} job(s) total (JobStreet={js_n}, Indeed={ind_n})")

    if not jobs:
        print("  No jobs found — check URLs / Cloudflare / network.")
        save_seen(state_path, seen)
        return seen

    for j in jobs:
        j["_id"] = job_id(j)

    # jobs is newest-first (sort_jobs). Prefer newest when capping, then reverse
    # so Telegram shows oldest first and latest at the bottom (no scroll up).
    if resend_all:
        to_send = list(jobs)
        print(f"  RESEND_ALL: will send all {len(to_send)} current match(es)")
    else:
        to_send = [j for j in jobs if j["_id"] not in seen]

    # True first run (empty cache) OR first time a source appears (e.g. adding
    # Indeed after JobStreet was already seeded) — seed without spam unless
    # SEND_ON_FIRST_RUN / RESEND_ALL.
    first_run = len(seen) == 0
    seed_prefixes: list[str] = []
    if not first_run and not send_on_first and not resend_all and to_send:
        for prefix, label in (("js-", "JobStreet"), ("ind-", "Indeed")):
            if any(j["_id"].startswith(prefix) for j in jobs) and not any(
                sid.startswith(prefix) for sid in seen
            ):
                seed_prefixes.append(prefix)
                print(
                    f"  First time seeing {label} IDs — will seed that source "
                    f"(no spam). Set SEND_ON_FIRST_RUN=true to send instead."
                )
        if seed_prefixes:
            seeded_n = 0
            remaining: list[dict] = []
            for j in to_send:
                if any(j["_id"].startswith(p) for p in seed_prefixes):
                    seen.add(j["_id"])
                    seeded_n += 1
                else:
                    remaining.append(j)
            to_send = remaining
            print(f"  Seeded {seeded_n} new-source job ID(s); remaining new={len(to_send)}")
            save_seen(state_path, seen)
            if not to_send:
                try:
                    labels = ", ".join(
                        "Indeed" if p == "ind-" else "JobStreet" for p in seed_prefixes
                    )
                    send_telegram_message(
                        settings["telegram_bot_token"],
                        settings["telegram_chat_id"],
                        (
                            f"✅ <b>Added source(s): {html.escape(labels)}</b>\n"
                            f"Seeded <b>{seeded_n}</b> current listing(s) "
                            f"(not spammed).\n"
                            f"You will get messages when <b>new</b> jobs appear "
                            f"on that site."
                        ),
                    )
                    print("  Sent new-source seed status to Telegram.")
                except Exception as exc:
                    print(f"  [warn] could not send new-source status: {exc}")
                return seen

    if first_run and not send_on_first and not resend_all:
        print(
            f"  First run: seeding {len(jobs)} job IDs (no Telegram spam). "
            "Set SEND_ON_FIRST_RUN=true or RESEND_ALL=true to send listings, "
            "or wait for new posts on later runs."
        )
        for j in jobs:
            seen.add(j["_id"])
        save_seen(state_path, seen)
        try:
            send_telegram_message(
                settings["telegram_bot_token"],
                settings["telegram_chat_id"],
                (
                    f"✅ <b>JobStreet + Indeed notifier is connected</b>\n"
                    f"Seeded <b>{len(jobs)}</b> current listings "
                    f"(JobStreet={js_n}, Indeed={ind_n}) — not spammed.\n"
                    f"You will get a message when <b>new</b> matching jobs appear.\n"
                    f"To dump all current matches once, set secret "
                    f"<code>RESEND_ALL=true</code> and re-run."
                ),
            )
            print("  Sent first-run connection status to Telegram.")
        except Exception as exc:
            print(f"  [warn] could not send first-run status: {exc}")
        return seen

    if not to_send:
        print(
            f"  No new jobs ({len(jobs)} match, all already seen). "
            "Use RESEND_ALL=true once to re-send current matches."
        )
        for j in jobs:
            seen.add(j["_id"])
        save_seen(state_path, seen)
        return seen

    max_send = int(settings.get("max_send_per_run") or 40)
    if len(to_send) > max_send:
        print(
            f"  Capping send list from {len(to_send)} to {max_send} "
            f"(keep newest; set MAX_SEND_PER_RUN to raise)"
        )
        to_send = to_send[:max_send]

    # Oldest first → newest last = latest post at bottom of Telegram chat
    to_send = list(reversed(to_send))
    if to_send:
        first_listed = to_send[0].get("listed") or "?"
        last_listed = to_send[-1].get("listed") or "?"
        print(
            f"  Send order: oldest first → newest last "
            f"(first listed={first_listed!r}, last listed={last_listed!r})"
        )

    print(f"  Sending {len(to_send)} job(s) to Telegram…")
    token = settings["telegram_bot_token"]
    chat_id = settings["telegram_chat_id"]
    sent = 0
    for job in to_send:
        try:
            send_telegram_message(token, chat_id, format_job_message(job))
            sent += 1
            seen.add(job["_id"])
            time.sleep(0.4)
        except Exception as exc:
            print(f"  [warn] failed to send {job['_id']}: {exc}")

    for j in jobs:
        seen.add(j["_id"])
    save_seen(state_path, seen)
    print(f"  Sent {sent} Telegram message(s). Seen IDs: {len(seen)}")
    return seen


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Notify Telegram of new JobStreet + Indeed PH jobs."
    )
    p.add_argument("--once", action="store_true", help="Single poll then exit")
    p.add_argument("--test", action="store_true", help="Send a test Telegram message")
    p.add_argument(
        "--send-existing",
        action="store_true",
        help="On first run, send current matches instead of only seeding",
    )
    p.add_argument(
        "--resend-all",
        action="store_true",
        help="Send all current matches this run (ignore seen cache)",
    )
    p.add_argument(
        "--interval",
        type=float,
        default=None,
        help="Minutes between polls (long-running mode)",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    settings = load_settings()
    if args.send_existing:
        settings["send_on_first_run"] = True
    if args.resend_all:
        settings["resend_all"] = True
    if args.interval is not None:
        settings["poll_interval_minutes"] = args.interval

    token = settings["telegram_bot_token"]
    chat_id = settings["telegram_chat_id"]

    if args.test:
        print("Sending test message…")
        test_telegram(token, chat_id)
        print("OK — check Telegram.")
        return 0

    state_path = Path(settings["state_file"])
    seen = load_seen(state_path)
    interval = float(settings["poll_interval_minutes"])

    sources = []
    if settings.get("enable_jobstreet", True):
        sources.append("JobStreet")
    if settings.get("enable_indeed", True):
        sources.append("Indeed")

    kws = settings.get("keywords") or []
    print("JobStreet + Indeed PH → Telegram notifier")
    print(f"  Sources  : {', '.join(sources)}")
    if kws:
        print(f"  Keywords : {', '.join(kws)}")
        print(f"  JS loc   : {settings.get('location') or DEFAULT_LOCATION}")
        print(
            f"  Indeed loc: {settings.get('indeed_location') or DEFAULT_INDEED_LOCATION}"
        )
    else:
        print("  Keywords : (default non-voice Cebu on each site)")
        if settings.get("enable_jobstreet", True):
            print(f"  JobStreet: {settings['search_url']}")
        if settings.get("enable_indeed", True):
            print(f"  Indeed   : {settings['indeed_url']}")
    print(f"  Max pages: {settings['max_pages']} (per source / keyword)")
    print(f"  Interval : {interval} min" + (" (single run)" if args.once else ""))
    print(f"  State    : {state_path}")
    print(f"  Seen IDs : {len(seen)}")
    print("  Press Ctrl+C to stop.\n")

    try:
        verify_telegram(token, chat_id)
        print()
    except Exception as exc:
        print(f"ERROR: Could not reach Telegram: {exc}")
        return 1

    try:
        while True:
            try:
                seen = run_once(settings, seen)
            except KeyboardInterrupt:
                raise
            except Exception as exc:
                print(f"  [error] poll failed: {exc}")
                import traceback

                traceback.print_exc()

            if args.once:
                break

            print(f"  Sleeping {interval} minute(s)…")
            time.sleep(interval * 60)
    except KeyboardInterrupt:
        print("\nStopped.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
