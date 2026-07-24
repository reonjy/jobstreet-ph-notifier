# JobStreet + Indeed PH → Telegram notifier

Scrapes **non-voice jobs in Cebu** on [JobStreet PH](https://ph.jobstreet.com/non-voice-jobs/in-cebu?pos=1&workarrangement=0&worktype=0) and [Indeed PH](https://ph.indeed.com/jobs?q=non+voice&l=Cebu), then sends **new** listings to your Telegram bot.

Built the same way as your OnlineJobs / CSC notifiers:

- GitHub Actions workflow (`workflow_dispatch` + optional schedule)
- Durable seen-ID state (cache + `state` branch)
- External cron (cron-job.org + GitHub PAT) every **15 minutes**
- **Two sources, one bot** (JobStreet + Indeed)

## Quick start

```bash
pip install -r requirements.txt
python scrape.py              # JobStreet only
python notify.py --once       # both sources → Telegram (needs TELEGRAM_* env)
```

## Docs

- [SETUP.md](SETUP.md) — secrets + local test
- [EXTERNAL_CRON.md](EXTERNAL_CRON.md) — reliable 15‑minute polls

## Config

| Env / secret | Purpose |
|--------------|---------|
| `TELEGRAM_BOT_TOKEN` | Bot token from @BotFather |
| `TELEGRAM_CHAT_ID` | Your chat id |
| `KEYWORDS` | Optional shared comma list for both sites |
| `JOBSTREET_URL` | Optional JobStreet URL when KEYWORDS empty |
| `INDEED_URL` | Optional Indeed URL when KEYWORDS empty |
| `ENABLE_JOBSTREET` | Default `true` |
| `ENABLE_INDEED` | Local default `true`; **GHA default `false`** (Indeed blocks runner IPs) |
| `INCLUDE_REMOTE` | Default `true` — also scrape JobStreet Remote (WFH) |
| `REMOTE_LOCATION` | Default `philippines` for remote-only pass |
| `RESEND_ALL` | Send all current matches once |

Default searches are hard-coded (non-voice + Cebu) when `KEYWORDS` and the URL secrets are empty.
