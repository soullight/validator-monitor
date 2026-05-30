#!/usr/bin/env python3
"""Validator watchdog. Runs every hour via GitHub Actions.

Checks 4 ETH + 1 LUKSO validator. Fires Telegram alert on:
  - slashed
  - status not active_online
  - balance below baseline floor
  - explorer API down (so we don't silently skip)

Always writes docs/state.json (the dashboard reads it).
Pings HEALTHCHECK_URL on success — if pings stop, healthchecks.io alerts you
(this is what catches "the watchdog itself died" failures, like the 35-day outage
where nothing fired).
"""

import json
import os
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone

# ── Config ────────────────────────────────────────────────────────────────────
VALIDATORS_ETH = [1998923, 1165884, 1998776, 1998777]
LUKSO_PUBKEY = "0xa2e5a4723655a8baf3411300d3ff3622903b6a7f79f85fc41193bd83f51afa284a2c8d055e2b0c54c7b46871a8f905cf"
LUKSO_NICKNAME = "humbly-flying-dove"

# Baselines: balances below these = real regression, not the May 2026 outage
ETH_BALANCE_FLOOR_GWEI = 31_850_000_000      # 31.85 ETH
LUKSO_BALANCE_FLOOR_GWEI = 31_800_000_000    # 31.80 LYX

BEACONCHAIN = "https://beaconcha.in/api/v1"
LUKSO_EXPLORER = "https://explorer.consensus.mainnet.lukso.network/api/v1"

# Env (all optional except TELEGRAM_* — without those, alerts just print)
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT = os.environ.get("TELEGRAM_CHAT_ID", "")
BEACONCHAIN_KEY = os.environ.get("BEACONCHAIN_API_KEY", "")
HEALTHCHECK_URL = os.environ.get("HEALTHCHECK_URL", "")

# ── HTTP ──────────────────────────────────────────────────────────────────────
def http_get_json(url, headers=None, timeout=20):
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())

def telegram(msg: str) -> None:
    if not (TELEGRAM_TOKEN and TELEGRAM_CHAT):
        print(f"[telegram-skip] {msg}", file=sys.stderr)
        return
    payload = json.dumps({
        "chat_id": TELEGRAM_CHAT,
        "text": msg,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
    }).encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
        data=payload, headers={"Content-Type": "application/json"},
    )
    try:
        urllib.request.urlopen(req, timeout=15).read()
    except Exception as e:
        print(f"[telegram-error] {e}", file=sys.stderr)

# ── Checks ────────────────────────────────────────────────────────────────────
def check_eth() -> tuple[list[dict], list[str]]:
    ids = ",".join(str(i) for i in VALIDATORS_ETH)
    headers = {"apikey": BEACONCHAIN_KEY} if BEACONCHAIN_KEY else {}
    data = http_get_json(f"{BEACONCHAIN}/validator/{ids}", headers=headers)
    rows = data.get("data") or []
    if isinstance(rows, dict):
        rows = [rows]
    results, issues = [], []
    seen = set()
    for r in rows:
        idx = r.get("validatorindex")
        status = r.get("status", "unknown")
        balance_gwei = int(r.get("balance", 0))
        slashed = bool(r.get("slashed", False))
        results.append({
            "chain": "ethereum",
            "label": f"eth-{idx}",
            "index": idx,
            "status": status,
            "balance_eth": round(balance_gwei / 1e9, 4),
            "slashed": slashed,
        })
        seen.add(idx)
        if slashed:
            issues.append(f"🚨 *SLASHED* — eth-{idx} https://beaconcha.in/validator/{idx}")
        elif status not in ("active_online", "active_ongoing"):
            # active_online = beaconcha.in convention; active_ongoing = consensus-layer spec.
            # Both mean "healthy and attesting." active_offline / exiting / pending = real issue.
            issues.append(f"⚠️ eth-{idx} status: `{status}` https://beaconcha.in/validator/{idx}")
        elif balance_gwei < ETH_BALANCE_FLOOR_GWEI:
            issues.append(f"⚠️ eth-{idx} balance `{balance_gwei/1e9:.3f} ETH` below floor `{ETH_BALANCE_FLOOR_GWEI/1e9} ETH`")
    missing = [i for i in VALIDATORS_ETH if i not in seen]
    for m in missing:
        issues.append(f"❌ eth-{m} not returned by beaconcha.in (possible API issue)")
    return results, issues

def check_lukso() -> tuple[list[dict], list[str]]:
    url = f"{LUKSO_EXPLORER}/validator/{LUKSO_PUBKEY}"
    data = http_get_json(url)
    row = data.get("data")
    if isinstance(row, list):
        row = row[0] if row else None
    if not row:
        return [], [f"❌ LUKSO `{LUKSO_NICKNAME}` not returned by explorer (possible API issue)"]
    status = row.get("status", "unknown")
    balance_gwei = int(row.get("balance", 0))
    slashed = bool(row.get("slashed", False))
    result = {
        "chain": "lukso",
        "label": LUKSO_NICKNAME,
        "pubkey": LUKSO_PUBKEY,
        "status": status,
        "balance_lyx": round(balance_gwei / 1e9, 4),
        "slashed": slashed,
    }
    issues = []
    if slashed:
        issues.append(f"🚨 *SLASHED* — LUKSO `{LUKSO_NICKNAME}`")
    elif "active" not in status.lower():
        issues.append(f"⚠️ LUKSO `{LUKSO_NICKNAME}` status: `{status}`")
    elif balance_gwei < LUKSO_BALANCE_FLOOR_GWEI:
        issues.append(f"⚠️ LUKSO `{LUKSO_NICKNAME}` balance `{balance_gwei/1e9:.3f} LYX` below floor")
    return [result], issues

# ── Main ──────────────────────────────────────────────────────────────────────
def main() -> int:
    now = datetime.now(timezone.utc)
    state = {
        "checked_at": now.isoformat(),
        "checked_at_human": now.strftime("%Y-%m-%d %H:%M UTC"),
        "validators": [],
        "issues": [],
    }

    try:
        eth_results, eth_issues = check_eth()
        state["validators"].extend(eth_results)
        state["issues"].extend(eth_issues)
    except Exception as e:
        state["issues"].append(f"❌ ETH explorer fetch failed: `{e}`")

    try:
        lukso_results, lukso_issues = check_lukso()
        state["validators"].extend(lukso_results)
        state["issues"].extend(lukso_issues)
    except Exception as e:
        state["issues"].append(f"❌ LUKSO explorer fetch failed: `{e}`")

    state["status"] = "ok" if not state["issues"] else "alert"

    os.makedirs("docs", exist_ok=True)
    with open("docs/state.json", "w") as f:
        json.dump(state, f, indent=2)

    if state["issues"]:
        msg_lines = [
            f"*Validator watchdog — {state['checked_at_human']}*",
            "",
            *state["issues"],
        ]
        telegram("\n".join(msg_lines))

    # Dead-man ping. If the check itself crashes (network down, GH Actions broken,
    # config wrong), we never reach this line, healthchecks.io stops getting pings,
    # and YOU get an alert that the watchdog is silent. This is the safety net
    # against repeats of the 35-day silent failure.
    if HEALTHCHECK_URL:
        try:
            urllib.request.urlopen(HEALTHCHECK_URL, timeout=10).read()
        except Exception as e:
            print(f"[healthcheck-ping-error] {e}", file=sys.stderr)

    if state["issues"]:
        # Non-zero exit also surfaces in the GH Actions failed-runs view + emails.
        return 1
    return 0

if __name__ == "__main__":
    sys.exit(main())
