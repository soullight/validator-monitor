# validator-monitor

Hourly watchdog for 5 validators (4 Ethereum, 1 LUKSO) with a btc-rings-style live dashboard showing **status, balance, and P&L in USD**.

- **GitHub Action** runs every hour, hits public beacon APIs, checks status / balance / slashed.
- **Telegram alert** fires on any issue.
- **Dashboard** at `docs/index.html` (served via GitHub Pages) shows current state + per-validator rewards in USD, auto-refreshes every 5 min.
- **Dead-man's switch** via [healthchecks.io](https://healthchecks.io) — if the watchdog itself stops running (the failure mode that hid the 35-day outage), you get alerted within an hour.

~250 lines of Python + 1 workflow + 1 dashboard. No API keys for the data sources.

---

## Data sources (all free, no auth)

| Source | Used for |
|---|---|
| `ethereum-beacon-api.publicnode.com` | ETH validator status, balance, slashed |
| `explorer.consensus.mainnet.lukso.network` | LUKSO validator status, balance, slashed |
| `api.coingecko.com` | ETH + LYX spot price (for USD valuation + P&L) |

If any source goes down, that becomes an alert — never a silent pass.

---

## What it watches

| ID | Chain | Status check | Balance floor |
|---|---|---|---|
| `1998923`, `1165884`, `1998776`, `1998777` | Ethereum | `active_ongoing` / `active_online` | 31.85 ETH |
| `humbly-flying-dove` (pubkey `0xa2e5…f905cf`) | LUKSO | `active*` | 31.80 LYX |

Edit the constants at the top of `check.py` to change the validator set, floors, or status whitelist.

---

## Deploy — 4 steps

### 1. Push to GitHub

```bash
cd /Users/lawgreg/consciousness_agent/code/validator-monitor
git init
git remote add origin git@github.com:soullight/validator-monitor.git
git add -A
git commit -m "Initial commit"
git branch -M main
git push -u origin main
```

(Or use the gh CLI: `gh repo create soullight/validator-monitor --public --source=. --push`.)

### 2. Add 3 repo secrets

GitHub → repo → **Settings → Secrets and variables → Actions → New repository secret**:

| Secret | Where to get it |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Message [@BotFather](https://t.me/BotFather) → `/newbot` → he gives you the token |
| `TELEGRAM_CHAT_ID` | Message your bot once, then visit `https://api.telegram.org/bot<TOKEN>/getUpdates` — `chat.id` in the response |
| `HEALTHCHECK_URL` | Free at [healthchecks.io](https://healthchecks.io) — create a check named "validator-watchdog", period 2h, grace 30 min. Add Telegram or email integration in the same UI. Paste the ping URL here. |

**Don't skip `HEALTHCHECK_URL`** — that's the 35-day-silent-failure safety net. Without it, if the watchdog itself dies, you find out the next time you remember to check.

### 3. Enable GitHub Pages

GitHub → repo → **Settings → Pages** → Source: **Deploy from branch** → Branch: `main` / folder: `/docs`. Save.

Dashboard lives at `https://soullight.github.io/validator-monitor/` within a few minutes.

### 4. Fire it once manually to confirm

GitHub → repo → **Actions → validator watchdog → Run workflow**. After it runs:

- `docs/state.json` is updated (commit by `validator-watchdog`)
- Dashboard URL renders current state with P&L
- If there's a real issue, Telegram fires
- healthchecks.io shows a green tick

From here, the cron runs `:17` past every hour automatically.

---

## Local test

```bash
python3 check.py
cat docs/state.json
```

No API keys needed. The script writes `docs/state.json` and prints any issues to stdout. Open `docs/index.html` in a browser to see the dashboard against your local state file.

---

## What happens during a real outage

The exact failure modes covered:

1. **Validator goes offline** → status flips to `active_offline`. Next hour's cron catches it, fires Telegram. You see it within ~60 min.
2. **Validator gets slashed** → `slashed: true`. Critical Telegram alert with the explorer link.
3. **Balance drops below floor** → Telegram alert.
4. **Public beacon API / LUKSO explorer is down** → the fetch failure becomes an alert. You're told the watchdog can't see (vs silently passing).
5. **GitHub Actions itself stops running** (the failure mode that hid the 35-day outage) → healthchecks.io stops getting pings. Within ~30 min of grace, it alerts you. **This is the safety net.**

---

## Dashboard

`docs/index.html` reads `docs/state.json` and renders:

- **TOTAL VALUE** — combined USD value of all validators
- **REWARDS** — combined rewards since activation, in USD (green positive, red negative)
- **ETH STAKED / LYX STAKED** — totals per chain with live price
- **Per-validator card** — balance, USD value, rewards in native + USD, status, explorer link

Visual register: btc-rings (dark, monospaced, glanceable). Pulse indicator at top is green when all-clear, red on alert.

---

## Why not [the previous overbuilt thing]

The earlier scaffold under `../_validator-monitor-overbuild-archive/` was 67 files (Dockerfile, SQLite, Astro, 11 playbooks, CI matrix). For watching 5 validators, that's enterprise software for a personal job. This version is one Python file, one HTML file, one workflow. If it ever needs more, it's easier to grow this than to maintain that.

Archive can be deleted any time — nothing depends on it.
