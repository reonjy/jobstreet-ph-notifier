# JobStreet PH -> Telegram setup

## Default (no KEYWORDS secret)

| Setting | Value |
|---------|--------|
| Search | non-voice jobs in Cebu |
| URL | `https://ph.jobstreet.com/non-voice-jobs/in-cebu?pos=1&workarrangement=0&worktype=0` |

## Keywords mode

Set secret / env `KEYWORDS` to a **comma-separated** list:

```text
non voice, data entry, back office, content moderator
```

Each keyword becomes a JobStreet search like:

```text
https://ph.jobstreet.com/data-entry-jobs/in-cebu?pos=1&workarrangement=0&worktype=0
```

| Secret | Meaning |
|--------|--------|
| `KEYWORDS` empty / missing | Default non-voice Cebu only |
| `KEYWORDS` set | Scrape each keyword (deduped) |
| `JOBSTREET_LOCATION` | Location path (default `cebu`) |
| `JOBSTREET_URL` | Only used when KEYWORDS is empty |

## Local

```powershell
cd C:\Users\Peppa\Documents\Programs\jobstreet-ph-scraper
pip install -r requirements.txt

# Default scrape
python scrape.py

# Multi-keyword scrape
python scrape.py --keywords "non voice, data entry, back office, content moderator"

# Telegram
$env:TELEGRAM_BOT_TOKEN = "YOUR_BOT_TOKEN"
$env:TELEGRAM_CHAT_ID = "YOUR_CHAT_ID"
$env:KEYWORDS = "non voice, data entry, back office, content moderator"

python notify.py --test
python notify.py --once
```

## GitHub Actions (always-on)

1. Repo: https://github.com/reonjy/jobstreet-ph-notifier  
2. **Settings -> Secrets and variables -> Actions**:

| Secret | Required | Notes |
|--------|----------|--------|
| `TELEGRAM_BOT_TOKEN` | Yes | Same bot as other notifiers |
| `TELEGRAM_CHAT_ID` | Yes | Same chat id |
| `KEYWORDS` | No | e.g. `non voice, data entry, back office, content moderator` |
| `JOBSTREET_LOCATION` | No | Default `cebu` |
| `JOBSTREET_URL` | No | Only if KEYWORDS empty |
| `RESEND_ALL` | No | `true` once to dump all current matches |

3. **Actions -> JobStreet Telegram Notify -> Run workflow**

4. For reliable 15-minute polls, follow **EXTERNAL_CRON.md**.

### First run vs later runs

| Situation | What Telegram gets |
|-----------|--------------------|
| First run (default) | Jobs are **seeded**, not sent |
| Later runs | Only **new** jobs |
| `RESEND_ALL=true` | All current matches (up to 40) |

## Notes

- Selenium + Chrome on GitHub (Cloudflare)
- Multiple keywords = more pages / longer runs; keep the list focused
- Seen job IDs: `state/seen_jobs.json` (cache + `state` branch)
