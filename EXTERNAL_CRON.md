# Reliable every-15-minute polls (external cron)

Same approach as OnlineJobs / CSC: **cron-job.org** triggers GitHub Actions via `workflow_dispatch`.

JobStreet scrape is HTTP-only (`curl_cffi`, no Selenium), so **every 15 minutes** is fine.

## cron-job.org settings

Create a **new** cron job (keep OnlineJobs + CSC as separate jobs).

| Field | Value |
|-------|--------|
| **Title** | `JobStreet poll 15m` |
| **URL** | `https://api.github.com/repos/reonjy/jobstreet-ph-notifier/actions/workflows/jobstreet-notify.yml/dispatches` |
| **Method** | **POST** (not GET — GET causes 422) |
| **Schedule** | Every **15 minutes** |
| **Body** | `{"ref":"main"}` |

### Headers (same PAT as OnlineJobs)

```text
Accept: application/vnd.github+json
Authorization: Bearer ghp_YOUR_TOKEN
X-GitHub-Api-Version: 2022-11-28
Content-Type: application/json
```

Reuse the **same classic PAT** (`repo` scope) that already works for OnlineJobs / CSC.

### Success

| Status | Meaning |
|--------|--------|
| **204** | OK — Actions will run |
| **422** | Method still GET or body empty/wrong |
| **401/403** | Token/header issue |

Then check: https://github.com/reonjy/jobstreet-ph-notifier/actions  
You should see **JobStreet Telegram Notify** with event `workflow_dispatch`.

---

## Side-by-side (all scrapers)

| | OnlineJobs | CSC | JobStreet |
|--|------------|-----|-----------|
| Repo | `onlinejobs-ph-notifier` | `csc-career-notifier` | `jobstreet-ph-notifier` |
| Workflow file | `onlinejobs-notify.yml` | `csc-notify.yml` | `jobstreet-notify.yml` |
| Body | `{"ref":"main"}` | `{"ref":"main"}` | `{"ref":"main"}` |
| Interval | **15 min** | **30 min** (Selenium) | **15 min** |
| PAT | same | same | same |
| Telegram secrets | on that repo | on that repo | on that repo |

---

## Optional: resend all current matches

1. Repo → Settings → Secrets → `RESEND_ALL` = `true`
2. Trigger cron once (or **Run workflow**)
3. Delete secret or set `false`

---

## Optional: custom search URL

Secret `JOBSTREET_URL` = full JobStreet search URL  
(default is non-voice jobs in Cebu with your query params)

---

## Notes

- First successful poll **seeds** seen IDs (no flood); later polls send **new** jobs only
- Seen IDs: Actions cache + durable `state` git branch
- Uses Chrome TLS impersonation so Cloudflare does not block GitHub runners
