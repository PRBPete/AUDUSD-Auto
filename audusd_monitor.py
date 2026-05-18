"""
Multi-instrument hourly forecast monitor.
Covers FX pairs and global indices.

Fetches live market data + recent news, calls the Anthropic API with a
condensed COSTAR prompt, then emails a consolidated report.

Designed to run unattended on GitHub Actions, triggered hourly by cron-job.org.
"""

from __future__ import annotations

import os
import smtplib
import sys
import traceback
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import yfinance as yf
from anthropic import Anthropic

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 2000

# Instruments to monitor
PAIRS = {
    # FX pairs
    "AUDUSD": {"ticker": "AUDUSD=X", "name": "AUD/USD"},
    "USDJPY": {"ticker": "USDJPY=X", "name": "USD/JPY"},
    "EURUSD": {"ticker": "EURUSD=X", "name": "EUR/USD"},
    "GBPUSD": {"ticker": "GBPUSD=X", "name": "GBP/USD"},
    # Global indices
    "US500":  {"ticker": "^GSPC",    "name": "US500 (S&P 500)"},
    "NAS100": {"ticker": "^NDX",     "name": "NAS100 (NASDAQ 100)"},
    "UK100":  {"ticker": "^FTSE",    "name": "UK100 (FTSE 100)"},
    "GER40":  {"ticker": "^GDAXI",   "name": "GER40 (DAX 40)"},
    "HK50":   {"ticker": "^HSI",     "name": "HK50 (Hang Seng)"},
    "JPN225": {"ticker": "^N225",    "name": "JPN225 (Nikkei 225)"},
}

# Shared context tickers — any that match the main instrument are auto-excluded
CONTEXT_TICKERS = {
    "DXY":    "DX-Y.NYB",
    "US10Y":  "^TNX",
    "US2Y":   "^IRX",
    "GOLD":   "GC=F",
    "COPPER": "HG=F",
    "BRENT":  "BZ=F",
    "SP500":  "^GSPC",
    "VIX":    "^VIX",
    "NIKKEI": "^N225",
    "DAX":    "^GDAXI",
    "FTSE":   "^FTSE",
    "HSI":    "^HSI",
    "NAS100": "^NDX",
}

# News symbols per instrument
PAIR_NEWS_SYMBOLS = {
    "AUDUSD": ["AUDUSD=X", "DX-Y.NYB", "GC=F"],
    "USDJPY": ["USDJPY=X", "DX-Y.NYB", "^N225"],
    "EURUSD": ["EURUSD=X", "DX-Y.NYB", "^GDAXI"],
    "GBPUSD": ["GBPUSD=X", "DX-Y.NYB", "^FTSE"],
    "US500":  ["^GSPC", "^VIX", "^NDX"],
    "NAS100": ["^NDX", "^GSPC", "^VIX"],
    "UK100":  ["^FTSE", "GBPUSD=X", "^GDAXI"],
    "GER40":  ["^GDAXI", "EURUSD=X", "^FTSE"],
    "HK50":   ["^HSI", "^N225"],
    "JPN225": ["^N225", "USDJPY=X", "^HSI"],
}

# ---------------------------------------------------------------------------
# Market hours check
# ---------------------------------------------------------------------------

def is_fx_market_open(now_utc: datetime | None = None) -> bool:
    """FX market is open Sunday 22:00 UTC to Friday 22:00 UTC."""
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
    """Pull current quote and context for the instrument + shared context tickers."""
    filtered_context = {k: v for k, v in CONTEXT_TICKERS.items() if v != pair_ticker}
    tickers = {"PAIR": pair_ticker, **filtered_context}
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

PROMPT_TEMPLATE = """You are a professional market strategist producing a condensed
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
# Bias extraction helper
# ---------------------------------------------------------------------------

def extract_bias(response: str) -> str:
    upper = response.upper()
    if "BIAS" in upper:
        idx = upper.find("BIAS")
        window = upper[idx:idx + 400]
        if "FALL" in window:
            return "FALL"
        if "RISE" in window:
            return "RISE"
        if "DRIFT" in window:
            return "DRIFT"
    return "—"


BIAS_COLOUR = {
    "RISE":  "#1a7f37",  # green
    "FALL":  "#cf222e",  # red
    "DRIFT": "#9a6700",  # amber
    "—":     "#57606a",  # grey
}


# ---------------------------------------------------------------------------
# Email builder
# ---------------------------------------------------------------------------

def build_email_html(timestamp: str, results: list[dict]) -> str:
    """Build a clean HTML email with a summary table + full forecasts."""

    # --- Summary table ---
    rows = ""
    for r in results:
        bias = r["bias"]
        colour = BIAS_COLOUR.get(bias, "#57606a")
        spot = r["market_data"].get("PAIR", {}).get("last", "n/a")
        chg  = r["market_data"].get("PAIR", {}).get("change_pct", 0)
        chg_str = f"{chg:+.2f}%" if isinstance(chg, float) else "n/a"
        rows += (
            f"<tr>"
            f"<td style='padding:6px 12px;border-bottom:1px solid #e1e4e8'><b>{r['name']}</b></td>"
            f"<td style='padding:6px 12px;border-bottom:1px solid #e1e4e8'>{spot}</td>"
            f"<td style='padding:6px 12px;border-bottom:1px solid #e1e4e8'>{chg_str}</td>"
            f"<td style='padding:6px 12px;border-bottom:1px solid #e1e4e8;"
            f"color:{colour};font-weight:bold'>{bias}</td>"
            f"</tr>"
        )

    summary_table = f"""
    <table style='border-collapse:collapse;width:100%;margin-bottom:32px;font-size:14px'>
      <thead>
        <tr style='background:#f6f8fa'>
          <th style='padding:8px 12px;text-align:left;border-bottom:2px solid #d0d7de'>Instrument</th>
          <th style='padding:8px 12px;text-align:left;border-bottom:2px solid #d0d7de'>Spot</th>
          <th style='padding:8px 12px;text-align:left;border-bottom:2px solid #d0d7de'>24h Chg</th>
          <th style='padding:8px 12px;text-align:left;border-bottom:2px solid #d0d7de'>Bias</th>
        </tr>
      </thead>
      <tbody>{rows}</tbody>
    </table>
    """

    # --- Full forecasts ---
    forecasts_html = ""
    for r in results:
        # Convert markdown-ish text to basic HTML
        body = r["response"]
        body = body.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        # Headers
        for level, tag in [("### ", "h4"), ("## ", "h3"), ("# ", "h2")]:
            lines = body.split("\n")
            body = "\n".join(
                f"<{tag}>{line[len(level):]}</{tag}>" if line.startswith(level) else line
                for line in lines
            )
        # Bold
        import re
        body = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", body)
        # Tables (basic)
        body = re.sub(r"\|(.+)\|", lambda m: "<tr>" + "".join(
            f"<td style='padding:4px 8px;border:1px solid #d0d7de'>{c.strip()}</td>"
            for c in m.group(1).split("|")
        ) + "</tr>", body)
        body = re.sub(r"(<tr>.*?</tr>\n?)+", lambda m: f"<table style='border-collapse:collapse;margin:8px 0'>{m.group()}</table>", body, flags=re.DOTALL)
        body = body.replace("\n", "<br>")

        bias = r["bias"]
        colour = BIAS_COLOUR.get(bias, "#57606a")
        forecasts_html += f"""
        <div style='margin-bottom:40px;border-left:4px solid {colour};padding-left:16px'>
          <h3 style='margin:0 0 8px;color:#24292f'>{r['name']}
            <span style='font-size:13px;font-weight:normal;color:{colour};margin-left:8px'>{bias}</span>
          </h3>
          <div style='font-size:14px;line-height:1.6;color:#24292f'>{body}</div>
        </div>
        <hr style='border:none;border-top:1px solid #e1e4e8;margin:0 0 40px'>
        """

    return f"""
    <!DOCTYPE html>
    <html>
    <body style='font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif;
                 max-width:800px;margin:0 auto;padding:24px;color:#24292f'>
      <h2 style='margin:0 0 4px'>Hourly Market Forecast</h2>
      <p style='margin:0 0 24px;color:#57606a;font-size:14px'>{timestamp} UTC &nbsp;·&nbsp; 10 instruments</p>
      {summary_table}
      {forecasts_html}
      <p style='font-size:12px;color:#57606a;margin-top:32px'>
        Automated by Claude {MODEL} via GitHub Actions
      </p>
    </body>
    </html>
    """


# ---------------------------------------------------------------------------
# Email sending
# ---------------------------------------------------------------------------

def send_email(subject: str, html_body: str) -> None:
    gmail_user = os.environ["GMAIL_USER"]
    gmail_password = os.environ["GMAIL_APP_PASSWORD"]
    email_to = os.environ["EMAIL_TO"]

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = gmail_user
    msg["To"] = email_to
    msg.attach(MIMEText(html_body, "html"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(gmail_user, gmail_password)
        server.sendmail(gmail_user, email_to, msg.as_string())


# ---------------------------------------------------------------------------
# Per-instrument forecast runner
# ---------------------------------------------------------------------------

def run_pair(pair_key: str, pair_info: dict, timestamp: str) -> dict:
    pair_name = pair_info["name"]
    pair_ticker = pair_info["ticker"]
    news_symbols = PAIR_NEWS_SYMBOLS[pair_key]

    print(f"[{timestamp}] {pair_name}: fetching data...")
    market_data = fetch_market_data(pair_ticker)

    print(f"[{timestamp}] {pair_name}: fetching news...")
    news = fetch_news(news_symbols)

    print(f"[{timestamp}] {pair_name}: calling Claude...")
    prompt = build_prompt(pair_name, market_data, news, timestamp)
    try:
        response = call_claude(prompt)
    except Exception as e:
        print(f"ERROR calling Claude for {pair_name}: {e}", file=sys.stderr)
        traceback.print_exc()
        response = f"API call failed: {type(e).__name__}: {e}"

    bias = extract_bias(response)
    print(f"[{timestamp}] {pair_name}: bias={bias} ✓")

    return {
        "key": pair_key,
        "name": pair_name,
        "market_data": market_data,
        "response": response,
        "bias": bias,
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> int:
    now_utc = datetime.now(timezone.utc)
    timestamp = now_utc.strftime("%Y-%m-%d %H:%M")

    if not is_fx_market_open(now_utc):
        print(f"[{timestamp}] Market closed — skipping run.")
        return 0

    for var in ("ANTHROPIC_API_KEY", "GMAIL_USER", "GMAIL_APP_PASSWORD", "EMAIL_TO"):
        if not os.environ.get(var):
            print(f"ERROR: {var} not set.", file=sys.stderr)
            return 1

    results = []
    for pair_key, pair_info in PAIRS.items():
        results.append(run_pair(pair_key, pair_info, timestamp))

    print(f"\n[{timestamp}] Building and sending email...")
    subject = f"Market Forecast | {timestamp} UTC"
    html = build_email_html(timestamp, results)
    try:
        send_email(subject, html)
        print(f"[{timestamp}] Email sent ✓")
    except Exception as e:
        print(f"ERROR sending email: {e}", file=sys.stderr)
        traceback.print_exc()
        return 1

    print(f"[{timestamp}] All done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
