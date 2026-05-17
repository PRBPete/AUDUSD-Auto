"""
AUD/USD hourly forecast monitor.

Fetches live market data + recent news, calls the Anthropic API with a
condensed COSTAR prompt, and appends the result to logs/updates.md plus a
structured row to logs/data.csv.

Designed to run unattended on GitHub Actions during FX market hours.
"""

from __future__ import annotations

import csv
import json
import os
import sys
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yfinance as yf
from anthropic import Anthropic

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 2000

REPO_ROOT = Path(__file__).resolve().parent
LOG_DIR = REPO_ROOT / "logs"
UPDATES_MD = LOG_DIR / "updates.md"
DATA_CSV = LOG_DIR / "data.csv"

# Tickers we pull from Yahoo Finance.
# These are all free and don't require an API key.
TICKERS = {
    "AUDUSD": "AUDUSD=X",
    "DXY": "DX-Y.NYB",
    "US10Y": "^TNX",            # 10-year Treasury yield (×10, so divide by 10 later? No - quoted in %)
    "US2Y": "^IRX",             # 13-week T-bill — closest free proxy for short-end
    "GOLD": "GC=F",             # Gold futures
    "COPPER": "HG=F",           # Copper futures
    "BRENT": "BZ=F",            # Brent crude
    "SP500": "^GSPC",
    "VIX": "^VIX",
}

# ---------------------------------------------------------------------------
# Market hours check
# ---------------------------------------------------------------------------

def is_fx_market_open(now_utc: datetime | None = None) -> bool:
    """
    FX market is open from Sunday 22:00 UTC to Friday 22:00 UTC.
    Closed: Friday 22:00 UTC through Sunday 22:00 UTC.

    Note: this is a conservative check using standard time.
    During DST, the actual close shifts to 21:00 UTC, but we accept the
    extra hour of "open" classification — Claude will see thin liquidity
    in the data and flag it.
    """
    now_utc = now_utc or datetime.now(timezone.utc)
    weekday = now_utc.weekday()   # Mon=0 ... Sun=6
    hour = now_utc.hour

    if weekday == 5:                       # Saturday — always closed
        return False
    if weekday == 6 and hour < 22:         # Sunday before 22:00 UTC
        return False
    if weekday == 4 and hour >= 22:        # Friday after 22:00 UTC
        return False
    return True


# ---------------------------------------------------------------------------
# Data fetching
# ---------------------------------------------------------------------------

def fetch_market_data() -> dict:
    """Pull current quote and recent context for each ticker."""
    data: dict[str, dict] = {}

    for name, symbol in TICKERS.items():
        try:
            ticker = yf.Ticker(symbol)
            # 2 days of hourly bars gives us session context + prior close
            hist = ticker.history(period="2d", interval="1h")
            if hist.empty:
                # Fall back to daily if hourly isn't available for this symbol
                hist = ticker.history(period="5d", interval="1d")
            if hist.empty:
                data[name] = {"error": "no data"}
                continue

            last_close = float(hist["Close"].iloc[-1])
            prev_close = float(hist["Close"].iloc[0])
            high_24h = float(hist["High"].iloc[-min(24, len(hist)):].max())
            low_24h = float(hist["Low"].iloc[-min(24, len(hist)):].min())
            change_pct = (last_close - prev_close) / prev_close * 100

            data[name] = {
                "symbol": symbol,
                "last": round(last_close, 4),
                "prev_close": round(prev_close, 4),
                "high_24h": round(high_24h, 4),
                "low_24h": round(low_24h, 4),
                "change_pct": round(change_pct, 2),
            }
        except Exception as e:
            data[name] = {"error": f"{type(e).__name__}: {e}"}

    return data


def fetch_news() -> list[dict]:
    """Pull recent news headlines for AUD/USD and DXY tickers."""
    headlines: list[dict] = []
    seen_titles: set[str] = set()
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)

    for symbol in ("AUDUSD=X", "DX-Y.NYB", "GC=F"):
        try:
            items = yf.Ticker(symbol).news or []
        except Exception:
            continue

        for item in items[:10]:
            # yfinance news shape varies; handle both old + new schemas
            content = item.get("content", item)
            title = content.get("title") or item.get("title")
            if not title or title in seen_titles:
                continue

            pub_raw = content.get("pubDate") or item.get("providerPublishTime")
            try:
                if isinstance(pub_raw, str):
                    pub_dt = datetime.fromisoformat(pub_raw.replace("Z", "+00:00"))
                elif isinstance(pub_raw, (int, float)):
                    pub_dt = datetime.fromtimestamp(pub_raw, tz=timezone.utc)
                else:
                    continue
            except Exception:
                continue

            if pub_dt < cutoff:
                continue

            seen_titles.add(title)
            publisher = (
                content.get("provider", {}).get("displayName")
                if isinstance(content.get("provider"), dict)
                else item.get("publisher", "")
            )
            headlines.append({
                "title": title,
                "publisher": publisher or "",
                "time_utc": pub_dt.strftime("%Y-%m-%d %H:%M"),
            })

    # Newest first, capped
    headlines.sort(key=lambda h: h["time_utc"], reverse=True)
    return headlines[:12]


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------

PROMPT_TEMPLATE = """You are a professional FX strategist producing a condensed
hourly delta-update on AUD/USD for active traders. This is NOT the full COSTAR
report — it is a tight tactical refresh.

## Current market data (live, as of {timestamp_utc} UTC)

{market_data_block}

## News headlines (last 24h)

{news_block}

## Required output format

Produce a concise update with EXACTLY these sections. Use prose, not bullets,
unless the section is explicitly a table.

### Snapshot
- Spot, 24h change, distance from key levels (one line each).

### What changed in the last hour
2–3 sentences. Focus on price action and the most recent driver. If nothing
meaningfully changed, say so clearly.

### Bias
State directional bias: **RISE / DRIFT / FALL** for the next 1–4 hours.
Include scenario probabilities in a small table (Rise / Drift / Fall, summing
to 100%).

### Levels
| Type | Level |
|---|---|
| R2 | ... |
| R1 | ... |
| Spot | ... |
| S1 | ... |
| S2 | ... |

### Triggers
One sentence on what would invalidate the bias (e.g. "break above 0.7184
neutralises bearish view").

### Confidence
Score 0–100 with a one-line justification.

## Rules
- Evidence-driven, no hype, no retail-trader language.
- If data is missing or stale (e.g. weekend), flag it and reduce confidence.
- Keep total length under ~350 words.
- Do not invent prices not present in the data block.
"""


def build_prompt(market_data: dict, news: list[dict], timestamp_utc: str) -> str:
    # Format market data as a clean block
    md_lines = []
    for name, d in market_data.items():
        if "error" in d:
            md_lines.append(f"- {name}: ERROR ({d['error']})")
            continue
        md_lines.append(
            f"- {name} ({d['symbol']}): last={d['last']}, "
            f"24h range={d['low_24h']}–{d['high_24h']}, "
            f"change={d['change_pct']:+.2f}%"
        )
    market_block = "\n".join(md_lines)

    if news:
        news_lines = [
            f"- [{h['time_utc']} UTC | {h['publisher']}] {h['title']}"
            for h in news
        ]
        news_block = "\n".join(news_lines)
    else:
        news_block = "(no recent headlines retrieved)"

    return PROMPT_TEMPLATE.format(
        timestamp_utc=timestamp_utc,
        market_data_block=market_block,
        news_block=news_block,
    )


# ---------------------------------------------------------------------------
# Claude call
# ---------------------------------------------------------------------------

def call_claude(prompt: str) -> str:
    client = Anthropic()  # reads ANTHROPIC_API_KEY from env
    response = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        messages=[{"role": "user", "content": prompt}],
    )
    return "\n".join(
        block.text for block in response.content if getattr(block, "type", "") == "text"
    )


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def append_markdown_log(timestamp_utc: str, market_data: dict, response: str) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    if not UPDATES_MD.exists():
        UPDATES_MD.write_text(
            "# AUD/USD Hourly Forecast Log\n\n"
            "Automated hourly updates during FX market hours.\n\n"
            "---\n\n"
        )

    aud = market_data.get("AUDUSD", {})
    dxy = market_data.get("DXY", {})
    spot_line = (
        f"AUD/USD {aud.get('last', 'n/a')} "
        f"({aud.get('change_pct', 0):+.2f}%) · "
        f"DXY {dxy.get('last', 'n/a')}"
    )

    block = (
        f"## {timestamp_utc} UTC\n\n"
        f"**Snapshot:** {spot_line}\n\n"
        f"{response.strip()}\n\n"
        f"---\n\n"
    )

    with UPDATES_MD.open("a", encoding="utf-8") as fh:
        fh.write(block)


def append_csv_row(timestamp_utc: str, market_data: dict, response: str) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    header = [
        "timestamp_utc",
        "audusd", "audusd_change_pct",
        "dxy", "us10y", "gold", "copper", "brent", "vix", "sp500",
        "bias_guess", "raw_response_chars",
    ]

    # Crude regex-free bias extraction
    upper = response.upper()
    if "BIAS" in upper:
        # Look for RISE/DRIFT/FALL in the first 300 chars after "BIAS"
        idx = upper.find("BIAS")
        window = upper[idx:idx + 400]
        if "FALL" in window:
            bias = "FALL"
        elif "RISE" in window:
            bias = "RISE"
        elif "DRIFT" in window:
            bias = "DRIFT"
        else:
            bias = "UNKNOWN"
    else:
        bias = "UNKNOWN"

    def g(k: str, field: str = "last") -> str:
        return str(market_data.get(k, {}).get(field, ""))

    row = [
        timestamp_utc,
        g("AUDUSD"), g("AUDUSD", "change_pct"),
        g("DXY"), g("US10Y"), g("GOLD"),
        g("COPPER"), g("BRENT"), g("VIX"), g("SP500"),
        bias, len(response),
    ]

    write_header = not DATA_CSV.exists()
    with DATA_CSV.open("a", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        if write_header:
            writer.writerow(header)
        writer.writerow(row)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> int:
    now_utc = datetime.now(timezone.utc)
    timestamp = now_utc.strftime("%Y-%m-%d %H:%M")

    if not is_fx_market_open(now_utc):
        print(f"[{timestamp}] FX market closed — skipping run.")
        return 0

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERROR: ANTHROPIC_API_KEY not set.", file=sys.stderr)
        return 1

    print(f"[{timestamp}] Fetching market data...")
    market_data = fetch_market_data()

    print(f"[{timestamp}] Fetching news...")
    news = fetch_news()
    print(f"[{timestamp}] Got {len(news)} headlines.")

    print(f"[{timestamp}] Calling Claude ({MODEL})...")
    prompt = build_prompt(market_data, news, timestamp)
    try:
        response = call_claude(prompt)
    except Exception as e:
        print(f"ERROR calling Claude: {e}", file=sys.stderr)
        traceback.print_exc()
        # Still log the failure so we can see gaps in the log
        response = f"_API call failed: {type(e).__name__}: {e}_"

    print(f"[{timestamp}] Writing logs...")
    append_markdown_log(timestamp, market_data, response)
    append_csv_row(timestamp, market_data, response)

    print(f"[{timestamp}] Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
