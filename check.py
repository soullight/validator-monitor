#!/usr/bin/env python3
"""Validator watchdog. Runs every hour via GitHub Actions.

Watches 4 Ethereum + 1 LUKSO validator. No API key needed for any source.

Sources (all free, no auth):
- ethereum-beacon-api.publicnode.com  →  ETH validator state (status, balance, slashed)
- explorer.consensus.mainnet.lukso.network  →  LUKSO validator state
- api.coingecko.com  →  ETH + LYX spot price for USD valuation

Fires Telegram alert on:
- slashed
- status not active_ongoing / active_online
- balance below baseline floor
- explorer fetch failure (so we never silently pass)

Always writes docs/state.json (the dashboard reads it).
Pings HEALTHCHECK_URL on success — if pings stop, healthchecks.io alerts you.
"""

import json
import os
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone

# ── Config ────────────────────────────────────────────────────────────────────
VALIDATORS_ETH = [1998923, 1165884, 1998776, 1998777]
ETH_ACTIVATION_BALANCE = 32.0   # ETH at activation; anything above is rewards

LUKSO_PUBKEY = "0xa2e5a4723655a8baf3411300d3ff3622903b6a7f79f85fc41193bd83f51afa284a2c8d055e2b0c54c7b46871a8f905cf"
LUKSO_NICKNAME = "humbly-flying-dove"
LUKSO_ACTIVATION_BALANCE = 32.0

# Below these = real regression, not a transient.
ETH_BALANCE_FLOOR_GWEI   = 31_850_000_000      # 31.85 ETH
LUKSO_BALANCE_FLOOR_GWEI = 31_800_000_000      # 31.80 LYX

# Endpoints (no auth on any of these)
ETH_BEACON  = "https://ethereum-beacon-api.publicnode.com"
LUKSO_EXPL  = "https://explorer.consensus.mainnet.lukso.network/api/v1"
PRICE_FEED  = "https://api.coingecko.com/api/v3/simple/price?ids=ethereum,lukso-token-2&vs_currencies=usd"

# Env
TELEGRAM_TOKEN  = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT   = os.environ.get("TELEGRAM_CHAT_ID", "")
HEALTHCHECK_URL = os.environ.get("HEALTHCHECK_URL", "")

USER_AGENT = "validator-monitor (soullight/validator-monitor)"


# ── HTTP ──────────────────────────────────────────────────────────────────────
def http_get_json(url, headers=None, timeout=20):
    hdrs = {"User-Agent": USER_AGENT}
    hdrs.update(headers or {})
    req = urllib.request.Request(url, headers=hdrs)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def telegram(msg: str) -> None:
    if not TELEGRAM_TOKEN:
        print(f"[telegram-skip-no-token] {msg}", file=sys.stderr)
        return
    if not TELEGRAM_CHAT:
        print(f"[telegram-skip-no-chat-id] {msg}", file=sys.stderr)
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


def discover_chat_ids() -> None:
    """Bootstrap helper: when TELEGRAM_BOT_TOKEN is set but TELEGRAM_CHAT_ID is
    not, hit getUpdates and print discovered chat IDs to stdout so the operator
    can set the missing secret. No-op when both or neither are set.
    """
    if not TELEGRAM_TOKEN or TELEGRAM_CHAT:
        return
    try:
        updates = http_get_json(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates")
    except Exception as e:
        print(f"[discovery-error] {e}", file=sys.stderr)
        return
    chats = {}
    for u in updates.get("result", []):
        m = u.get("message") or u.get("edited_message") or u.get("channel_post") or {}
        c = m.get("chat") or {}
        cid = c.get("id")
        if cid is not None:
            label = c.get("title") or c.get("first_name") or c.get("username") or "?"
            chats[cid] = label
    print("=" * 60)
    print("TELEGRAM_CHAT_ID DISCOVERY")
    print("=" * 60)
    if not chats:
        print("No chats found.")
        print("Fix: in Telegram, open your bot (the username you set with")
        print("BotFather, e.g. @Validatorwatchdogbot), send any message (like")
        print("'hi'), then re-run this workflow.")
    else:
        print("Found these chats your bot can see:")
        for cid, label in chats.items():
            print(f"  chat_id={cid}  ({label})")
        print()
        print("Set TELEGRAM_CHAT_ID to the number above and re-run.")
    print("=" * 60)


# ── Price ─────────────────────────────────────────────────────────────────────
def fetch_prices():
    """Returns {'eth_usd': float, 'lyx_usd': float} or {} on failure."""
    try:
        d = http_get_json(PRICE_FEED)
        return {
            "eth_usd": float(d["ethereum"]["usd"]),
            "lyx_usd": float(d["lukso-token-2"]["usd"]),
        }
    except Exception as e:
        print(f"[price-fetch-error] {e}", file=sys.stderr)
        return {}


# ── ETH check ─────────────────────────────────────────────────────────────────
def check_eth(eth_price_usd):
    """Hit publicnode beacon API for all ETH validators in one call."""
    ids = ",".join(str(i) for i in VALIDATORS_ETH)
    url = f"{ETH_BEACON}/eth/v1/beacon/states/head/validators?id={ids}"
    data = http_get_json(url)
    rows = data.get("data") or []
    results, issues = [], []
    seen = set()
    for r in rows:
        idx = int(r["index"])
        seen.add(idx)
        v = r.get("validator", {})
        status = r.get("status", "unknown")
        balance_gwei = int(r["balance"])
        balance_eth = balance_gwei / 1e9
        slashed = bool(v.get("slashed", False))
        rewards_eth = balance_eth - ETH_ACTIVATION_BALANCE
        results.append({
            "chain": "ethereum",
            "label": f"eth-{idx}",
            "index": idx,
            "status": status,
            "balance": round(balance_eth, 6),
            "balance_unit": "ETH",
            "rewards": round(rewards_eth, 6),
            "value_usd": round(balance_eth * eth_price_usd, 2) if eth_price_usd else None,
            "rewards_usd": round(rewards_eth * eth_price_usd, 2) if eth_price_usd else None,
            "slashed": slashed,
            "explorer_url": f"https://beaconcha.in/validator/{idx}",
        })
        if slashed:
            issues.append(f"🚨 *SLASHED* — eth-{idx} https://beaconcha.in/validator/{idx}")
        elif status not in ("active_online", "active_ongoing"):
            issues.append(f"⚠️ eth-{idx} status `{status}` https://beaconcha.in/validator/{idx}")
        elif balance_gwei < ETH_BALANCE_FLOOR_GWEI:
            issues.append(
                f"⚠️ eth-{idx} balance `{balance_eth:.3f} ETH` "
                f"below floor `{ETH_BALANCE_FLOOR_GWEI/1e9} ETH`"
            )
    for missing_idx in VALIDATORS_ETH:
        if missing_idx not in seen:
            issues.append(f"❌ eth-{missing_idx} not returned by beacon API (possible source issue)")
    return results, issues


# ── LUKSO check ───────────────────────────────────────────────────────────────
def check_lukso(lyx_price_usd):
    url = f"{LUKSO_EXPL}/validator/{LUKSO_PUBKEY}"
    data = http_get_json(url)
    row = data.get("data")
    if isinstance(row, list):
        row = row[0] if row else None
    if not row:
        return [], [f"❌ LUKSO `{LUKSO_NICKNAME}` not returned by explorer (possible source issue)"]
    status = row.get("status", "unknown")
    balance_gwei = int(row.get("balance", 0))
    balance_lyx = balance_gwei / 1e9
    slashed = bool(row.get("slashed", False))
    rewards_lyx = balance_lyx - LUKSO_ACTIVATION_BALANCE
    result = {
        "chain": "lukso",
        "label": LUKSO_NICKNAME,
        "pubkey": LUKSO_PUBKEY,
        "status": status,
        "balance": round(balance_lyx, 6),
        "balance_unit": "LYX",
        "rewards": round(rewards_lyx, 6),
        "value_usd": round(balance_lyx * lyx_price_usd, 2) if lyx_price_usd else None,
        "rewards_usd": round(rewards_lyx * lyx_price_usd, 2) if lyx_price_usd else None,
        "slashed": slashed,
        "explorer_url": f"https://explorer.consensus.mainnet.lukso.network/validator/{LUKSO_PUBKEY}",
    }
    issues = []
    if slashed:
        issues.append(f"🚨 *SLASHED* — LUKSO `{LUKSO_NICKNAME}`")
    elif "active" not in status.lower():
        issues.append(f"⚠️ LUKSO `{LUKSO_NICKNAME}` status `{status}`")
    elif balance_gwei < LUKSO_BALANCE_FLOOR_GWEI:
        issues.append(
            f"⚠️ LUKSO `{LUKSO_NICKNAME}` balance `{balance_lyx:.3f} LYX` "
            f"below floor `{LUKSO_BALANCE_FLOOR_GWEI/1e9} LYX`"
        )
    return [result], issues


# ── Main ──────────────────────────────────────────────────────────────────────
def main() -> int:
    # Bootstrap path: print discovered chat IDs and bail when token-set/chat-unset.
    discover_chat_ids()

    now = datetime.now(timezone.utc)
    prices = fetch_prices()
    state = {
        "checked_at": now.isoformat(),
        "checked_at_human": now.strftime("%Y-%m-%d %H:%M UTC"),
        "prices": prices,
        "validators": [],
        "issues": [],
    }

    try:
        eth_results, eth_issues = check_eth(prices.get("eth_usd", 0.0))
        state["validators"].extend(eth_results)
        state["issues"].extend(eth_issues)
    except Exception as e:
        state["issues"].append(f"❌ ETH beacon API fetch failed: `{e}`")

    try:
        lukso_results, lukso_issues = check_lukso(prices.get("lyx_usd", 0.0))
        state["validators"].extend(lukso_results)
        state["issues"].extend(lukso_issues)
    except Exception as e:
        state["issues"].append(f"❌ LUKSO explorer fetch failed: `{e}`")

    # Roll-up totals for the dashboard.
    total_value = sum((v.get("value_usd") or 0) for v in state["validators"])
    total_rewards_usd = sum((v.get("rewards_usd") or 0) for v in state["validators"])
    eth_validators = [v for v in state["validators"] if v["chain"] == "ethereum"]
    lukso_validators = [v for v in state["validators"] if v["chain"] == "lukso"]
    state["totals"] = {
        "validators": len(state["validators"]),
        "eth_count": len(eth_validators),
        "lukso_count": len(lukso_validators),
        "eth_balance": round(sum(v["balance"] for v in eth_validators), 4),
        "lyx_balance": round(sum(v["balance"] for v in lukso_validators), 4),
        "eth_rewards": round(sum(v["rewards"] for v in eth_validators), 4),
        "lyx_rewards": round(sum(v["rewards"] for v in lukso_validators), 4),
        "value_usd": round(total_value, 2),
        "rewards_usd": round(total_rewards_usd, 2),
    }

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

    # Dead-man ping. If check.py crashes, GH Actions crashes, or the network is
    # down, we never reach this line. healthchecks.io stops getting pings and
    # YOU get the alert — the safety net against the 35-day silent failure.
    if HEALTHCHECK_URL:
        try:
            urllib.request.urlopen(HEALTHCHECK_URL, timeout=10).read()
        except Exception as e:
            print(f"[healthcheck-ping-error] {e}", file=sys.stderr)

    if state["issues"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
