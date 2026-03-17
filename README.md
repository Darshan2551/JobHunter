# Job Hunter: Telegram Bot + Web Dashboard

This project has two parts:

- `job_bot.py`: scrapes jobs, filters (including fresher mode), sends Telegram alerts, and stores sent alerts in `jobs.db`.
- `web_app.py`: Flask dashboard showing sent Telegram jobs by platform with Apply links.

## Local run

```powershell
pip install -r requirements.txt

# run bot (sends Telegram + stores sent alerts)
python job_bot.py

# run dashboard
python web_app.py
```

Open `http://127.0.0.1:5000`.

## Deploy (recommended: Render + GitHub Actions)

### 1) Push repo to GitHub

Ensure these files are in repo:

- `render.yaml`
- `Procfile`
- `.github/workflows/job_scraper.yml`

### 2) Configure GitHub Secrets

In GitHub repo settings:

- `BOT_TOKEN`
- `CHAT_ID`

### 3) Configure GitHub Variables (optional, recommended)

- `JOB_QUERY` (example: `python developer`)
- `JOB_LOCATION` (example: `India`)
- `MAX_JOBS_PER_SOURCE` (example: `25`)
- `FRESHER_ONLY` (`true` or `false`)
- `MAX_FRESHER_EXPERIENCE_YEARS` (example: `1`)

The action runs hourly, updates `jobs.db`, and commits it back to the repo.

### 4) Deploy dashboard on Render

1. Create new Render service from this repo.
2. Render auto-detects `render.yaml`.
3. Deploy the `job-hunter-dashboard` web service.
4. After deploy, open your Render URL.

Health check endpoint: `/health`

## Notes

- Dashboard shows jobs from `sent_alerts` table (jobs that were successfully sent to Telegram).
- If you change scheduler frequency, edit `.github/workflows/job_scraper.yml` cron expression.
