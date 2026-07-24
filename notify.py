"""
JobStreet PH -> Telegram Notifier
================================
Polls a JobStreet search URL and sends NEW job cards to Telegram.

Default search:
  non-voice jobs in Cebu
  https://ph.jobstreet.com/non-voice-jobs/in-cebu?pos=1&workarrangement=0&worktype=0

Usage:
    python notify.py --once
    python notify.py --test
    python notify.py --resend-all --once

Env / GitHub Actions secrets:
  TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID  (required)
  JOBSTREET_URL                         (optional search URL)
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

from scrape import (
    DEFAULT_DELAY,
    DEFAULT_MAX_PAGES,
    DEFAULT_SEARCH_URL,
    make_session,
    scrape_search,
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

    search_url = _env("JOBSTREET_URL", DEFAULT_SEARCH_URL) or DEFAULT_SEARCH_URL
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
    jid = str(job.get("job_id") or "").strip()
    if jid:
        return f"js-{jid}"
    link = (job.get("link") or "").strip()
    return f"js-{link}" if link else "js-unknown"


def telegram_call(token: str, method: str, payload: dict) -> dict:
    url = TELEGRAM_API.format(token=token, method=method)
    resp = requests.post(url, json=payload, timeout=30)
    data = resp.json()
    if not data.get("ok"):
        desc = data.get("description") or data
        hint = ""
        if "chat not found" in str(desc).lower():
            hint = (
                "\n\nCHAT NOT FOUND - fix TELEGRAM_CHAT_ID:\n"
                "  1. Open your bot and press Start / send a message\n"
                "  2. Open getUpdates and copy chat.id (number only)\n"
            )
        raise RuntimeError(f"Telegram API error: {data}{hint}")
    return data


def send_telegram_message(
    token: str,
    chat_id: str,
    text: str,
    *,
    button_url: str | None = None,
    button_text: str = "Open on Jobstreet",
    disable_preview: bool = True,
) -> None:
    payload: dict = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": disable_preview,
    }
    if button_url:
        payload["reply_markup"] = {
            "inline_keyboard": [[{"text": button_text, "url": button_url}]]
        }
    telegram_call(token, "sendMessage", payload)


def format_job_message(job: dict) -> tuple[str, str | None]:
    """Rich HTML job card + optional JobStreet URL for Open on Jobstreet button."""
    title = html.escape(job.get("title") or "Untitled job")
    company = html.escape(job.get("company") or "Not listed")
    location = html.escape(job.get("location") or "Not listed")
    salary = html.escape(job.get("salary") or "Not stated")
    etype = html.escape(job.get("employment_type") or "Not stated")
    listed = html.escape(job.get("listed") or "-")
    desc = html.escape(job.get("description") or "")
    if len(desc) > 280:
        desc = desc[:277] + "..."
    sub = html.escape(job.get("sub_classification") or "")
    classification = html.escape(job.get("classification") or "")
    link = (job.get("link") or "").strip()

    lines = [
        "\U0001f195 <b>New JobStreet listing</b>",
        "\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501",
        f"\U0001f4bc <b>{title}</b>",
        "",
        f"\U0001f3e2 <b>Company</b>",
        f"   {company}",
        f"\U0001f4cd <b>Location</b>",
        f"   {location}",
        f"\U0001f4b0 <b>Salary</b>",
        f"   {salary}",
        f"\u23f1 <b>Type</b>  \u00b7  {etype}",
        f"\U0001f4c5 <b>Listed</b>  \u00b7  {listed}",
    ]
    if sub or classification:
        class_line = sub or classification
        if sub and classification and sub != classification:
            class_line = f"{sub} \u00b7 {classification}"
        lines.append(f"\U0001f3f7 <b>Class</b>  \u00b7  {class_line}")
    if desc:
        lines.extend(["", f"\U0001f4dd <i>{desc}</i>"])
    lines.extend(["", "Tap <b>Open on Jobstreet</b> below to view the full post."])
    return "\n".join(lines), (link or None)


def verify_telegram(token: str, chat_id: str) -> None:
    me = telegram_call(token, "getMe", {})
    username = (me.get("result") or {}).get("username") or "?"
    telegram_call(token, "getChat", {"chat_id": chat_id})
    print(f"Telegram OK (bot @{username}, chat {chat_id}).")


def test_telegram(token: str, chat_id: str) -> None:
    verify_telegram(token, chat_id)
    sample_url = "https://ph.jobstreet.com/non-voice-jobs/in-cebu"
    send_telegram_message(
        token,
        chat_id,
        (
            "\u2705 <b>JobStreet PH notifier is connected</b>\n"
            "\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
            "You will get rich job cards with an "
            "<b>Open on Jobstreet</b> button when new listings appear."
        ),
        button_url=sample_url,
        button_text="Open on Jobstreet",
    )


def run_once(settings: dict, seen: set[str]) -> set[str]:
    state_path = Path(settings["state_file"])
    resend_all = bool(settings.get("resend_all"))
    send_on_first = bool(settings.get("send_on_first_run"))
    search_url = settings["search_url"]

    print(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Polling JobStreet...")
    print(f"  URL: {search_url}")
    print(
        f"  Options: send_on_first_run={send_on_first} resend_all={resend_all} "
        f"already_seen={len(seen)}"
    )

    session = make_session()
    try:
        jobs = scrape_search(
            search_url=search_url,
            max_pages=int(settings["max_pages"]),
            delay=float(settings["request_delay_seconds"]),
            session=session,
        )
    finally:
        try:
            session.close()
        except Exception:
            pass

    jobs = sort_jobs(jobs)
    print(f"  Found {len(jobs)} job(s)")

    if not jobs:
        print("  No jobs found - check JOBSTREET_URL / Cloudflare / network.")
        save_seen(state_path, seen)
        return seen

    for j in jobs:
        j["_id"] = job_id(j)

    if resend_all:
        to_send = list(jobs)
        print(f"  RESEND_ALL: will send all {len(to_send)} current match(es)")
    else:
        to_send = [j for j in jobs if j["_id"] not in seen]

    first_run = len(seen) == 0
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
                    f"\u2705 <b>JobStreet notifier is connected</b>\n"
                    f"\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
                    f"\U0001f4cc Seeded <b>{len(jobs)}</b> current listings "
                    f"(not spammed as separate messages).\n"
                    f"\U0001f514 You will get a card + <b>Open on Jobstreet</b> button "
                    f"when <b>new</b> non-voice Cebu jobs appear.\n\n"
                    f"To dump all current matches once, set secret "
                    f"<code>RESEND_ALL=true</code> and re-run."
                ),
                button_url=settings.get("search_url") or DEFAULT_SEARCH_URL,
                button_text="Open on Jobstreet",
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
            f"(set MAX_SEND_PER_RUN to raise)"
        )
        to_send = to_send[:max_send]

    print(f"  Sending {len(to_send)} job(s) to Telegram...")
    token = settings["telegram_bot_token"]
    chat_id = settings["telegram_chat_id"]
    sent = 0
    for job in to_send:
        try:
            text, button_url = format_job_message(job)
            send_telegram_message(
                token,
                chat_id,
                text,
                button_url=button_url,
                button_text="Open on Jobstreet",
            )
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
    p = argparse.ArgumentParser(description="Notify Telegram of new JobStreet PH jobs.")
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
        print("Sending test message...")
        test_telegram(token, chat_id)
        print("OK - check Telegram.")
        return 0

    state_path = Path(settings["state_file"])
    seen = load_seen(state_path)
    interval = float(settings["poll_interval_minutes"])

    print("JobStreet PH -> Telegram notifier")
    print(f"  URL      : {settings['search_url']}")
    print(f"  Max pages: {settings['max_pages']}")
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

            print(f"  Sleeping {interval} minute(s)...")
            time.sleep(interval * 60)
    except KeyboardInterrupt:
        print("\nStopped.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
