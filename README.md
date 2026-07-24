# JobStreet PH → Telegram notifier

Scrapes **non-voice jobs in Cebu** on [JobStreet PH](https://ph.jobstreet.com/non-voice-jobs/in-cebu?pos=1&workarrangement=0&worktype=0) and sends **new** listings to your Telegram bot.

Built the same way as your OnlineJobs / CSC notifiers:

- GitHub Actions workflow (`workflow_dispatch` + optional schedule)
- Durable seen-ID state (cache + `state` branch)
- External cron (cron-job.org + GitHub PAT) every **15 minutes**

## Quick start

```bash
pip install -r requirements.txt
python scrape.py
python notify.py --once   # needs TELEGRAM_* env vars
```

## Docs

- [SETUP.md](SETUP.md) — secrets + local test
- [EXTERNAL_CRON.md](EXTERNAL_CRON.md) — reliable 15‑minute polls

## Config

| Env / secret | Purpose |
|--------------|---------|
| `TELEGRAM_BOT_TOKEN` | Bot token from @BotFather |
| `TELEGRAM_CHAT_ID` | Your chat id |
| `JOBSTREET_URL` | Optional full search URL override |
| `RESEND_ALL` | Send all current matches once |

Default search URL is hard-coded in `scrape.py` / used when `JOBSTREET_URL` is empty.
