# JobStreet + Indeed PH → Telegram setup

## Defaults (no KEYWORDS secret)

Both sites are scraped and sent to the **same** Telegram bot.

| Site | Default search |
|------|----------------|
| JobStreet | non-voice jobs in Cebu — `https://ph.jobstreet.com/non-voice-jobs/in-cebu?pos=1&workarrangement=0&worktype=0` |
| Indeed | non voice + Cebu — `https://ph.indeed.com/jobs?q=non+voice&l=Cebu` |

## Keywords mode

Set secret / env `KEYWORDS` to a **comma-separated** list (shared by both sites):

```text
non voice, data entry, back office, content moderator
```

| Site | How each keyword is used |
|------|--------------------------|
| JobStreet | `https://ph.jobstreet.com/data-entry-jobs/in-cebu?...` |
| Indeed | `https://ph.indeed.com/jobs?q=data+entry&l=Cebu` |

| Secret | Meaning |
|--------|---------|
| `KEYWORDS` empty / missing | Default non-voice Cebu on **both** sites |
| `KEYWORDS` set | Scrape each keyword on each enabled site (deduped) |
| `JOBSTREET_LOCATION` | JobStreet path (default `cebu`) |
| `INDEED_LOCATION` | Indeed `l=` param (default `Cebu`) |
| `JOBSTREET_URL` | Only when KEYWORDS empty (JobStreet override) |
| `INDEED_URL` | Only when KEYWORDS empty (Indeed override) |
| `ENABLE_JOBSTREET` | Default `true` — set `false` to skip JobStreet |
| `ENABLE_INDEED` | Default `true` — set `false` to skip Indeed |

## Local

```powershell
cd C:\Users\Peppa\Documents\Programs\jobstreet-ph-scraper
pip install -r requirements.txt

# JobStreet only scrape
python scrape.py

# Indeed only smoke test
python -c "from indeed_scrape import scrape_indeed_search; print(len(scrape_indeed_search(max_pages=1)))"

# Telegram (both sources)
$env:TELEGRAM_BOT_TOKEN = "YOUR_BOT_TOKEN"
$env:TELEGRAM_CHAT_ID = "YOUR_CHAT_ID"
# optional: $env:KEYWORDS = "non voice, data entry"

python notify.py --test
python notify.py --once
```

## GitHub Actions (always-on)

1. Repo: https://github.com/reonjy/jobstreet-ph-notifier  
2. **Settings → Secrets and variables → Actions**:

| Secret | Required | Notes |
|--------|----------|--------|
| `TELEGRAM_BOT_TOKEN` | Yes | Same bot as other notifiers |
| `TELEGRAM_CHAT_ID` | Yes | Same chat id |
| `KEYWORDS` | No | Shared by JobStreet + Indeed |
| `JOBSTREET_LOCATION` | No | Default `cebu` |
| `INDEED_LOCATION` | No | Default `Cebu` |
| `JOBSTREET_URL` | No | Only if KEYWORDS empty |
| `INDEED_URL` | No | Only if KEYWORDS empty |
| `ENABLE_JOBSTREET` | No | Default on; set `false` to disable |
| `ENABLE_INDEED` | No | Default on; set `false` to disable |
| `RESEND_ALL` | No | `true` once to dump all current matches |

3. **Actions → JobStreet + Indeed Telegram Notify → Run workflow**

4. For reliable 15‑minute polls, follow **EXTERNAL_CRON.md**.

### First run vs later runs

| Situation | What Telegram gets |
|-----------|--------------------|
| First run (default) | Jobs are **seeded**, not sent (one “connected” status) |
| Later runs | Only **new** jobs from either site |
| `RESEND_ALL=true` | All current matches (up to 40) |

Messages show the source in the title, e.g. `JobStreet: …` or `Indeed: …`, with an **Open on Jobstreet** / **Open on Indeed** link.

## Notes

- Selenium + Chrome on GitHub (Cloudflare / blocks)
- Seen IDs are prefixed (`js-…` / `ind-…`) so the two sites never collide
- Multiple keywords = more pages / longer runs; keep the list focused
- Seen job IDs: `state/seen_jobs.json` (cache + `state` branch)
