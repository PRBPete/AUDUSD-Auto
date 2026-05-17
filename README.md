# AUD/USD Hourly Forecast Monitor

Unattended hourly tactical forecast for AUD/USD, running on GitHub Actions
during FX market hours. Each run:

1. Pulls live prices for AUD/USD, DXY, US 10Y, gold, copper, Brent, S&P 500, VIX.
2. Pulls recent news headlines from Yahoo Finance.
3. Calls the Anthropic API (Claude Sonnet 4.6) with a condensed COSTAR prompt.
4. Appends a Markdown update to `logs/updates.md`.
5. Appends a structured row to `logs/data.csv` for later analysis.
6. Commits both back to the repo.

No server, no laptop required. Free GitHub Actions minutes cover the workload.

---

## 1. Setup

### Prerequisites

- A GitHub account
- An Anthropic API key from [console.anthropic.com](https://console.anthropic.com)
  (pay-as-you-go billing, expect ~$15–25/month at this cadence — see *Costs* below)

### Steps

1. **Create a new repo on GitHub.** A private repo is fine — free Actions minutes
   are 2000/month for private, unlimited for public. This workload uses ~525/month.

2. **Push these files** to the repo:

   ```text
   .github/workflows/forecast.yml
   audusd_monitor.py
   requirements.txt
   .gitignore
   README.md
   ```

3. **Add your API key as a repo secret:**

   - Repo → Settings → Secrets and variables → Actions → New repository secret
   - Name: `ANTHROPIC_API_KEY`
   - Value: your `sk-ant-...` key

4. **Verify Actions has write permission:**

   - Repo → Settings → Actions → General → Workflow permissions
   - Select **Read and write permissions**
   - Save

5. **Trigger a test run:**

   - Repo → Actions tab → "AUD/USD Hourly Forecast" workflow → "Run workflow"
   - Wait ~1 minute, then check `logs/updates.md` for the first entry.

Once verified, the cron schedule takes over automatically.

---

## 2. Schedule

Runs hourly during FX market hours (Sunday 22:00 UTC → Friday 22:00 UTC):

| Day (UTC) | Schedule |
|---|---|
| Sunday | 22:00, 23:00 |
| Monday–Thursday | Every hour |
| Friday | 00:00 – 22:00 |
| Saturday | Skipped |

The script also performs its own market-hours check and silently exits if the
market is closed, so cron over-runs are safe.

**GitHub Actions cron caveat:** scheduled workflows can lag 5–15 minutes in
busy periods. If exact-on-the-hour timing matters, the cron settings inside
`forecast.yml` are the ones to adjust.

---

## 3. Reading the logs

- **`logs/updates.md`** — human-readable, newest entries appended at the bottom.
  Open it directly on GitHub for instant viewing.
- **`logs/data.csv`** — one row per run with timestamp, prices, and the
  detected directional bias (RISE / DRIFT / FALL). Open in Excel/Google Sheets
  to chart bias evolution over time.

To pull both locally for analysis:

```bash
git pull
```

---

## 4. Costs

Rough monthly estimate at ~525 runs/month:

| Item | Estimate |
|---|---|
| Anthropic API (Sonnet 4.6) | ~$15–25 / month |
| GitHub Actions minutes | $0 (within free tier) |
| Yahoo Finance data | $0 (free, no key) |

Per-call cost is roughly $0.03–0.05. To halve it, switch the `MODEL` constant
in `audusd_monitor.py` to `claude-haiku-4-5-20251001`. Quality drops noticeably
but the structure stays consistent.

---

## 5. Customising

Common tweaks, all in `audusd_monitor.py`:

- **Different pair** — change `"AUDUSD=X"` in `TICKERS` and the references
  in the prompt. Any Yahoo FX ticker works (`EURUSD=X`, `GBPJPY=X`, etc.).
- **More tickers** — add to the `TICKERS` dict. The script auto-includes them
  in the prompt.
- **Different model** — change the `MODEL` constant. Valid IDs:
  `claude-opus-4-7`, `claude-opus-4-6`, `claude-sonnet-4-6`,
  `claude-haiku-4-5-20251001`.
- **Different cadence** — edit the `cron:` lines in `.github/workflows/forecast.yml`.
  For 4×/day at key sessions, use:

  ```yaml
  - cron: "0 22 * * 0-4"   # Sydney/Asia open
  - cron: "0 0 * * 1-5"    # Tokyo
  - cron: "0 7 * * 1-5"    # London open
  - cron: "0 13 * * 1-5"   # NY open
  ```

- **Different output format** — edit `PROMPT_TEMPLATE` near the top of the file.

---

## 6. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Workflow doesn't run on schedule | GHA disables schedules on inactive repos (60 days no commits) | Push any commit to reactivate |
| `ANTHROPIC_API_KEY not set` | Secret missing or misnamed | Re-add the secret with the exact name |
| Git push fails with 403 | Workflow lacks write permission | Settings → Actions → enable read/write |
| `No data` for a ticker | Yahoo Finance throttling | Usually self-resolves next hour; check `data.csv` for gaps |
| Logs growing too large | Months of history accumulated | Periodically archive `updates.md` to a dated file and start fresh |

---

## 7. Disclaimer

This is an analytical tool, not financial advice. The forecasts are
probabilistic and frequently wrong. Don't trade off them without your own
analysis and risk management.
