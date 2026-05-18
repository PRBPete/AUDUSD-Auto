"""
Multi-pair FX hourly forecast monitor.
Covers AUD/USD, USD/JPY, EUR/USD.

Fetches live market data + recent news, calls the Anthropic API with a
condensed COSTAR prompt, and appends the result to per-pair log files.

Designed to run unattended on GitHub Actions, triggered hourly by cron-job.org.
"""

from __future__ import annotations

import csv
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

# Pairs to monitor
PAIRS = {
    "AUDUSD": {"ticker": "AUDUSD=X", "name": "AUD/USD"},
    "USDJPY": {"ticker": "USDJPY=X", "name": "USD/JPY"},
    "EURUSD": {"ticker": "EURUSD=X", "name": "EUR/USD"},
}

# Shared context tickers (correlated assets)
CONTEXT_TICKERS = {
    "DXY":    "DX-Y.NYB",
    "US10Y":  "^TNX",
    "US2Y":   "^IRX",
    "GOLD":   "GC=F",
    "COPPER": "HG=F",
    "BRENT":  "BZ=F",
    "SP500":  "^GSPC",
    "VIX":    "^VIX",
    "NIKKEI": "^N225",   # Relevant for USD/JPY
    "DAX":    "^GDAXI",  # Relevant for EUR/USD
}

# News sources per pair (yfinance symbols to pull headlines from)
PAIR_NEWS_SYMBOLS = {
    "AUDUSD": ["AUDUSD=X", "DX-Y.NYB", "GC=F"],
    "USDJPY": ["USDJPY=X", "DX-Y.NYB", "^N225"],
    "EURUSD": ["EURUSD=X", "DX-Y.NYB", "^GDAXI"],
}

# ---------------------------------------------------------------------------
# Market hours check
# ---------------------------------------------------------------------------

def is_fx_market_open(now_utc: datetime | None = None) -> bool:
    """
    FX market is open from Sunday 22:00 UTC to Friday 22:00 UTC.
    Closed: Friday 22:00 UTC through Sunday 22:00 UTC.
    """
    now_utc = now_utc or datetime.now(timezone.utc)
    weekday = now_utc.weekday()  # Mon=0 ... Sun=6
    hour = now_utc.hour

    if weekday == 5:                    # Saturday — always closed
        return False
    if weekday == 6 and hour < 22:      # Sunday before 22:00 UTC
        return False
    if weekday == 4 and hour >= 22:     # Friday after 22:00 UTC
        return False
    return True


# ---------------------------------------------------------------------------
# Data fetching
# ---------------------------------------------------------------------------

def fetch_market_data(pair_ticker: str) -> dict:
    """Pull current quote and context for the pair + shared context tickers."""
    tickers = {"PAIR": pair_ticker, **CONTEXT_TICKERS}
    data: dict[str, dict] = {}

    for name, symbol in tickers.items():
        try:
            ticker = yf.Ticker(symbol)
            hist = ticker.history(period="2d", interval="1h")
            if hist.empty:
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


def fetch_news(news_symbols: list[str]) -> list[dict]:
    """Pull recent headlines for the given yfinance symbols."""
    headlines: list[dict] = []
    seen_titles: set[str] = set()
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)

    for symbol in news_symbols:
        try:
            items = yf.Ticker(symbol).news or []
        except Exception:
            continue

        for item in items[:10]:
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

    headlines.sort(key=lambda h: h["time_utc"], reverse=True)
    return headlines[:12]


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------

PROMPT_TEMPLATE = """You are a professional FX strategist producing a condensed
hourly delta-update on {pair_name} for active traders. This is NOT the full COSTAR
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


def build_prompt(pair_name: str, market_data: dict, news: list[dict], timestamp_utc: str) -> str:
    md_lines = []
    for name, d in market_data.items():
        label = pair_name if name == "PAIR" else name
        if "error" in d:
            md_lines.append(f"- {label}: ERROR ({d['error']})")
            continue
        md_lines.append(
            f"- {label} ({d['symbol']}): last={d['last']}, "
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
        pair_name=pair_name,
        timestamp_utc=timestamp_utc,
        market_data_block=market_block,
        news_block=news_block,
    )


# ---------------------------------------------------------------------------
# Claude call
# ---------------------------------------------------------------------------

def call_claude(prompt: str) -> str:
    client = Anthropic()
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

def append_markdown_log(pair_key: str, pair_name: str, timestamp_utc: str,
                        market_data: dict, response: str) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_file = LOG_DIR / f"{pair_key.lower()}_updates.md"

    if not log_file.exists():
        log_file.write_text(
            f"# {pair_name} Hourly Forecast Log\n\n"
            f"Automated hourly updates during FX market hours.\n\n"
            f"---\n\n"
        )

    pair_data = market_data.get("PAIR", {})
    dxy = market_data.get("DXY", {})
    spot_line = (
        f"{pair_name} {pair_data.get('last', 'n/a')} "
        f"({pair_data.get('change_pct', 0):+.2f}%) · "
        f"DXY {dxy.get('last', 'n/a')}"
    )

    block = (
        f"## {timestamp_utc} UTC\n\n"
        f"**Snapshot:** {spot_line}\n\n"
        f"{response.strip()}\n\n"
        f"---\n\n"
    )

    with log_file.open("a", encoding="utf-8") as fh:
        fh.write(block)


def append_csv_row(pair_key: str, timestamp_utc: str,
                   market_data: dict, response: str) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    csv_file = LOG_DIR / f"{pair_key.lower()}_data.csv"

    header = [
        "timestamp_utc", "pair", "spot", "change_pct",
        "dxy", "us10y", "gold", "vix", "sp500",
        "bias", "raw_response_chars",
    ]

    upper = response.upper()
    bias = "UNKNOWN"
    if "BIAS" in upper:
        idx = upper.find("BIAS")
        window = upper[idx:idx + 400]
        if "FALL" in window:
            bias = "FALL"
        elif "RISE" in window:
            bias = "RISE"
        elif "DRIFT" in window:
            bias = "DRIFT"

    def g(k: str, field: str = "last") -> str:
        return str(market_data.get(k, {}).get(field, ""))

    row = [
        timestamp_utc, pair_key,
        g("PAIR"), g("PAIR", "change_pct"),
        g("DXY"), g("US10Y"), g("GOLD"), g("VIX"), g("SP500"),
        bias, len(response),
    ]

    write_header = not csv_file.exists()
    with csv_file.open("a", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        if write_header:
            writer.writerow(header)
        writer.writerow(row)


# ---------------------------------------------------------------------------
# Per-pair forecast runner
# ---------------------------------------------------------------------------

def run_pair(pair_key: str, pair_info: dict, timestamp: str) -> None:
    pair_name = pair_info["name"]
    pair_ticker = pair_info["ticker"]
    news_symbols = PAIR_NEWS_SYMBOLS[pair_key]

    print(f"\n[{timestamp}] === {pair_name} ===")

    print(f"[{timestamp}] Fetching market data...")
    market_data = fetch_market_data(pair_ticker)

    print(f"[{timestamp}] Fetching news...")
    news = fetch_news(news_symbols)
    print(f"[{timestamp}] Got {len(news)} headlines.")

    print(f"[{timestamp}] Calling Claude ({MODEL})...")
    prompt = build_prompt(pair_name, market_data, news, timestamp)
    try:
        response = call_claude(prompt)
    except Exception as e:
        print(f"ERROR calling Claude for {pair_name}: {e}", file=sys.stderr)
        traceback.print_exc()
        response = f"_API call failed: {type(e).__name__}: {e}_"

    print(f"[{timestamp}] Writing logs...")
    append_markdown_log(pair_key, pair_name, timestamp, market_data, response)
    append_csv_row(pair_key, timestamp, market_data, response)
    print(f"[{timestamp}] {pair_name} done.")


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

    for pair_key, pair_info in PAIRS.items():
        run_pair(pair_key, pair_info, timestamp)

    print(f"\n[{timestamp}] All pairs complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
