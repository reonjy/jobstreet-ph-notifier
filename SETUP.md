# JobStreet PH → Telegram setup

Default search:

| Setting | Value |
|---------|--------|
| Keywords | non-voice |
| Location | Cebu |
| URL | `https://ph.jobstreet.com/non-voice-jobs/in-cebu?pos=1&workarrangement=0&worktype=0` |

## Local

```powershell
cd C:\Users\Peppa\Documents\Programs\jobstreet-ph-scraper
pip install -r requirements.txt

# Scrape only (no Telegram)
python scrape.py

# Telegram (reuse same bot as OnlineJobs / CSC)
$env:TELEGRAM_BOT_TOKEN = "YOUR_BOT_TOKEN"
$env:TELEGRAM_CHAT_ID = "YOUR_CHAT_ID"

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
| `JOBSTREET_URL` | No | Override search URL |
| `RESEND_ALL` | No | `true` once to dump all current matches |

3. **Actions → JobStreet Telegram Notify → Run workflow**

4. For reliable 15‑minute polls, follow **EXTERNAL_CRON.md** (cron-job.org + your GitHub PAT).

### First run vs later runs

| Situation | What Telegram gets |
|-----------|--------------------|
| First run (default) | Jobs are **seeded**, not sent |
| Later runs | Only **new** jobs |
| `RESEND_ALL=true` | All current matches (up to 40) |

## Notes

- HTTP scrape with `curl_cffi` (no Selenium)
- Typical run: under 1 minute
- Seen job IDs: `state/seen_jobs.json` (cache + `state` branch)
