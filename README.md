# validator-monitor

Hourly watchdog for 5 validators (4 ETH, 1 LUKSO).

- **GitHub Action** runs every hour, hits beaconcha.in + LUKSO explorer, checks status / balance / slashed.
- **Telegram alert** fires on any issue.
- **Dashboard** at `docs/index.html` (served via GitHub Pages) shows current state, refreshes every 5 min.
- **Dead-man's switch** via [healthchecks.io](https://healthchecks.io) — if the watchdog itself stops running (the failure mode that hid the 35-day outage), you get alerted within an hour.

That's the whole thing. ~150 lines of Python + 1 workflow + 1 HTML page.

---

## What it watches

| ID | Chain | Status check | Balance floor |
|---|---|---|---|
| `1998923`, `1165884`, `1998776`, `1998777` | Ethereum | `active_online` / `active_ongoing` | 31.85 ETH |
| `humbly-flying-dove` (pubkey `0xa2e5…f905cf`) | LUKSO | `active*` | 31.80 LYX |

Edit the constants at the top of `check.py` to change validator set, floors, or status whitelist.

---

## Deploy — 4 steps

### 1. Push to GitHub
```bash
cd /Users/lawgreg/consciousness_agent/code/validator-monitor
git remote add origin git@github.com:ecofinanceowner/validator-monitor.git
git push -u origin main
```
(Create the repo on GitHub first — public is fine if the repo name is non-guessable; private works too. The dashboard only requires GitHub Pages, which works on private repos with a paid Pro plan, or any public repo for free.)

### 2. Add 4 repo secrets

GitHub → repo → **Settings → Secrets and variables → Actions → New repository secret**:

| Secret | Where to get it |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Message [@BotFather](https://t.me/BotFather) → `/newbot` → he gives you the token |
| `TELEGRAM_CHAT_ID` | Message your bot once, then visit `https://api.telegram.org/bot<TOKEN>/getUpdates` — `chat.id` in the response |
| `BEACONCHAIN_API_KEY` | Free, mandatory. Sign up at [beaconcha.in](https://beaconcha.in/user/settings) → API Keys |
| `HEALTHCHECK_URL` | Free at [healthchecks.io](https://healthchecks.io) — create a check named "validator-watchdog", period 2 hours, grace 30 min. Add Telegram or email integration in the same UI. Paste the ping URL here. |

If you skip `HEALTHCHECK_URL`, you lose the dead-man's switch — the 35-day-silent-failure protection. Don't skip it.

### 3. Enable GitHub Pages
GitHub → repo → **Settings → Pages** → Source: **Deploy from branch** → Branch: `main` / folder: `/docs`. Save.

Your dashboard will live at `https://ecofinanceowner.github.io/validator-monitor/` within a few minutes.

### 4. Fire it once manually to confirm
GitHub → repo → **Actions → validator watchdog → Run workflow**. After it runs:
- `docs/state.json` should be updated (commit by `validator-watchdog`)
- Dashboard URL renders current state
- If there's a real issue, Telegram fires
- healthchecks.io shows a green tick

From here, the cron runs `:17` past every hour automatically.

---

## Local test
```bash
BEACONCHAIN_API_KEY=... python3 check.py
cat docs/state.json
```

Without the API key, ETH checks return 403 (the script flags this as an issue, doesn't crash). LUKSO works unauthenticated.

---

## What happens during a real outage

Watching for the specific failure mode you experienced (35-day silent outage):

1. **Validator goes offline** → status flips to `active_offline`. Next hour's cron catches it, fires Telegram. You see it within ~60 min.
2. **Validator gets slashed** → `slashed: true`. Critical Telegram alert with the beaconcha.in link.
3. **Balance drops below floor** → Telegram alert.
4. **beaconcha.in / LUKSO explorer is down** → the fetch failure becomes an alert. You're told the watchdog can't see (vs silently passing).
5. **GitHub Actions itself stops running** (the failure mode that hid your last outage) → healthchecks.io stops getting pings. Within ~30 min of grace, it alerts you. **This is the safety net.**

---

## Why not [the previous overbuilt thing]

The earlier scaffold under `../_validator-monitor-overbuild-archive/` was 67 files (Dockerfile, SQLite, Astro, 11 playbooks, CI matrix). For watching 5 validators, that's enterprise software for a personal job. This version is one Python file, one HTML file, one workflow. If it ever needs more, it's easier to grow this than to maintain that.

Archive can be deleted any time — nothing depends on it.
