"""
Multi-instrument hourly forecast monitor.
Covers FX pairs and global indices.

Fetches live market data + recent news, calls the Anthropic API with a
condensed COSTAR prompt, publishes a full HTML report to GitHub Pages,
and emails a summary table with a link to the report.

Designed to run unattended on GitHub Actions, triggered hourly by cron-job.org.
"""

from __future__ import annotations

import os
import re
import smtplib
import sys
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import yfinance as yf
from anthropic import Anthropic

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

MODEL       = "claude-sonnet-4-6"
MAX_TOKENS  = 3000
PAGES_URL   = "https://prbpete.github.io/AUDUSD-Auto"

REPO_ROOT = Path(__file__).resolve().parent
DOCS_DIR  = REPO_ROOT / "docs"

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

BIAS_COLOUR = {
    "RISE":  "#1a7f37",  # green
    "FALL":  "#cf222e",  # red
    "DRIFT": "#9a6700",  # amber
    "—":     "#57606a",  # grey
}

# ---------------------------------------------------------------------------
# Market hours check
# ---------------------------------------------------------------------------

def is_fx_market_open(now_utc: datetime | None = None) -> bool:
    """FX market is open Sunday 22:00 UTC to Friday 22:00 UTC."""
    now_utc = now_utc or datetime.now(timezone.utc)
    weekday = now_utc.weekday()  # Mon=0 ... Sun=6
    hour    = now_utc.hour

    if weekday == 5:                 # Saturday — always closed
        return False
    if weekday == 6 and hour < 22:   # Sunday before 22:00 UTC
        return False
    if weekday == 4 and hour >= 22:  # Friday after 22:00 UTC
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
            hist = ticker.history(period="2d", interval="1h", timeout=10)
            if hist.empty:
                hist = ticker.history(period="5d", interval="1d", timeout=10)
            if hist.empty:
                data[name] = {"error": "no data"}
                continue

            last_close = float(hist["Close"].iloc[-1])
            prev_close = float(hist["Close"].iloc[0])
            high_24h   = float(hist["High"].iloc[-min(24, len(hist)):].max())
            low_24h    = float(hist["Low"].iloc[-min(24, len(hist)):].min())
            change_pct = (last_close - prev_close) / prev_close * 100

            data[name] = {
                "symbol":     symbol,
                "last":       round(last_close, 4),
                "prev_close": round(prev_close, 4),
                "high_24h":   round(high_24h, 4),
                "low_24h":    round(low_24h, 4),
                "change_pct": round(change_pct, 2),
            }
        except Exception as e:
            data[name] = {"error": f"{type(e).__name__}: {e}"}

    return data


def fetch_news(news_symbols: list[str]) -> list[dict]:
    """Pull recent headlines for the given yfinance symbols."""
    headlines: list[dict] = []
    seen_titles: set[str] = set()
    cutoff = datetime.now(timezone.utc) - timedelta(hours=48)

    for symbol in news_symbols:
        try:
            items = yf.Ticker(symbol).news or []
        except Exception:
            continue

        for item in items[:10]:
            content = item.get("content", item)
            title   = content.get("title") or item.get("title")
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
                "title":     title,
                "publisher": publisher or "",
                "time_utc":  pub_dt.strftime("%Y-%m-%d %H:%M"),
            })

    headlines.sort(key=lambda h: h["time_utc"], reverse=True)
    return headlines[:20]


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------

PROMPT_TEMPLATE = """You are a senior market strategist producing a 36-hour forward
outlook on {pair_name} for professional traders.

Your analysis must weight inputs as follows:
  - 2/3 (≈66%): macro fundamentals, geopolitical developments, central bank
    posture, risk sentiment, and news flow from the last 48 hours.
  - 1/3 (≈34%): technical analysis — price structure, key levels, momentum.

## Current market data (as of {timestamp_utc} UTC)

{market_data_block}

## News & macro headlines (last 48h)

{news_block}

## Required output format

Produce a structured report with EXACTLY these sections, in order.
Use prose unless a section explicitly calls for a table.

### Snapshot
One line each: current spot, 48h change, and where price sits relative to
the key technical levels below.

### Macro & Geopolitical Drivers
Write 3–5 sentences identifying the dominant fundamental forces likely
to move price over the next 36 hours: central bank tone, economic data releases
due, geopolitical risk, commodity linkages, cross-asset flows, or risk-on/off
shifts. Be specific — name the event, the expected impact, and the direction.

### Technical Setup
Write 2–3 sentences describing the prevailing price structure,
any pattern or momentum signal, and how technicals either confirm or conflict
with the macro view.

### Bias
Synthesise the two sections above using the stated weighting (66% macro,
34% technical) to produce a single combined directional call. State it on
its own line in bold, exactly one of:
**RISE** | **DRIFT** | **FALL**
Then give a 36-hour scenario probability table:

| Scenario | Probability |
|---|---|
| Rise | ...% |
| Drift | ...% |
| Fall | ...% |

### Triggers
One sentence: the specific event or price level that would invalidate the bias
(e.g. "a break above 1.0950 or a hawkish Fed speaker tonight flips bias to RISE").

### Confidence
Write your score on its own line in this exact format: **CONFIDENCE: [0-100]**
Then one sentence justifying the score (data gaps, conflicting signals, upcoming
risk events that increase uncertainty).

### Trade Setup
Include this section ONLY if bias is RISE or FALL AND confidence is 58 or above.
If bias is DRIFT, or confidence is 57 or below, omit this section entirely.

When included, silently identify the key support and resistance levels from the
price data (recent swing highs/lows, round numbers, 24h range extremes) — do NOT
output a levels table. Use those levels internally to set:
- Entry: current spot or a clean break beyond the nearest S/R level
- Stop Loss: just beyond the nearest invalidating S/R level (support for RISE,
  resistance for FALL), plus a small instrument-appropriate buffer
- Take Profit: the next significant S/R level in the direction of the bias,
  adjusted for realistic 36h momentum

| | Price |
|---|---|
| Entry | [derived from spot and nearest S/R] |
| Stop Loss | [just beyond invalidating S/R level + buffer] |
| Take Profit | [next significant S/R in bias direction] |
| Risk : Reward | [calculated ratio, e.g. 1 : 2.1] |

Then one sentence explaining the entry and level rationale.

## Rules
- Lead with macro and geopolitical factors; technical levels support, not lead.
- Be specific about upcoming scheduled events (data releases, speeches, votes).
- Evidence-driven. No hype, no retail-trader language.
- If data is missing or stale, flag it and reduce confidence accordingly.
- Keep total length under ~550 words.
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
    news_block = (
        "\n".join(f"- [{h['time_utc']} UTC | {h['publisher']}] {h['title']}" for h in news)
        if news else "(no headlines retrieved in the last 48 hours)"
    )
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
# Bias extraction
# ---------------------------------------------------------------------------

def extract_bias(response: str) -> str:
    """
    Isolate the ### Bias section then look for the bolded direction Claude writes.
    Falls back progressively to avoid false matches from the probability table.
    """
    section_match = re.search(r"###\s*Bias(.*?)(?=###|\Z)", response, re.IGNORECASE | re.DOTALL)
    section = section_match.group(1) if section_match else response

    bold = re.search(r"\*\*(RISE|DRIFT|FALL)\*\*", section, re.IGNORECASE)
    if bold:
        return bold.group(1).upper()

    bare = re.search(r"\b(RISE|DRIFT|FALL)\b", section, re.IGNORECASE)
    if bare:
        return bare.group(1).upper()

    bold_any = re.search(r"\*\*(RISE|DRIFT|FALL)\*\*", response, re.IGNORECASE)
    if bold_any:
        return bold_any.group(1).upper()

    return "—"


def extract_confidence(response: str) -> int:
    """
    Parse the confidence score from Claude's **CONFIDENCE: N** line.
    Falls back to any number in the ### Confidence section.
    Returns 0 if nothing found (treated as low confidence).
    """
    # Primary: **CONFIDENCE: 72**
    match = re.search(r"\*\*CONFIDENCE:\s*(\d{1,3})\*\*", response, re.IGNORECASE)
    if match:
        return min(100, int(match.group(1)))

    # Fallback: first number in the ### Confidence section
    section = re.search(r"###\s*Confidence(.*?)(?=###|\Z)", response, re.IGNORECASE | re.DOTALL)
    if section:
        num = re.search(r"\b(\d{1,3})\b", section.group(1))
        if num:
            return min(100, int(num.group(1)))

    return 0


# ---------------------------------------------------------------------------
# Shared markdown → HTML converter
# ---------------------------------------------------------------------------

def markdown_to_html(text: str) -> str:
    """Convert the subset of markdown Claude produces into basic HTML."""
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    for level, tag in [("### ", "h4"), ("## ", "h3"), ("# ", "h2")]:
        text = "\n".join(
            f"<{tag}>{line[len(level):]}</{tag}>" if line.startswith(level) else line
            for line in text.split("\n")
        )
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    # Colour-code bold RISE / DRIFT / FALL wherever they appear
    _bias_colours = {"RISE": "#1a7f37", "DRIFT": "#9a6700", "FALL": "#cf222e"}
    for word, colour in _bias_colours.items():
        text = re.sub(
            rf"<b>({word})</b>",
            rf"<b><span style='color:{colour}'>\1</span></b>",
            text, flags=re.IGNORECASE,
        )
    text = re.sub(r"\|(.+)\|", lambda m: "<tr>" + "".join(
        f"<td style='padding:4px 8px;border:1px solid #d0d7de'>{c.strip()}</td>"
        for c in m.group(1).split("|")
    ) + "</tr>", text)
    text = re.sub(
        r"(<tr>.*?</tr>\n?)+",
        lambda m: f"<table style='border-collapse:collapse;margin:8px 0'>{m.group()}</table>",
        text, flags=re.DOTALL,
    )
    return text.replace("\n", "<br>")


# ---------------------------------------------------------------------------
# Summary table (shared by both the email and the pages report)
# ---------------------------------------------------------------------------

CONFIDENCE_THRESHOLD = 58  # below this → "Not enough confidence", no trade setup

def build_summary_table(results: list[dict], link_prefix: str = "#") -> str:
    """
    link_prefix="#"           → anchors within the same page (GitHub Pages report)
    link_prefix="https://..." → full URL to GitHub Pages report with anchor (email)
    """
    rows = ""
    for r in results:
        anchor     = r["key"].lower()
        bias       = r["bias"]
        confidence = r.get("confidence", 0)
        colour     = BIAS_COLOUR.get(bias, "#57606a")
        spot       = r["market_data"].get("PAIR", {}).get("last", "n/a")
        chg        = r["market_data"].get("PAIR", {}).get("change_pct", 0)
        chg_str    = f"{chg:+.2f}%" if isinstance(chg, float) else "n/a"
        href       = f"{link_prefix}{anchor}"

        has_trade  = confidence >= CONFIDENCE_THRESHOLD and bias in ("RISE", "FALL")
        trade_cell = (
            f"<span style='color:#1a7f37;font-weight:bold'>&#10003; Trade</span>"
            if has_trade else
            "<span style='color:#57606a'>—</span>"
        )
        conf_colour = "#1a7f37" if confidence >= CONFIDENCE_THRESHOLD else "#cf222e"
        conf_cell   = f"<span style='color:{conf_colour};font-weight:bold'>{confidence}</span>"

        rows += (
            f"<tr>"
            f"<td style='padding:6px 12px;border-bottom:1px solid #e1e4e8'>"
            f"<a href='{href}' style='color:#0969da;text-decoration:none;font-weight:bold'>{r['name']}</a></td>"
            f"<td style='padding:6px 12px;border-bottom:1px solid #e1e4e8'>{spot}</td>"
            f"<td style='padding:6px 12px;border-bottom:1px solid #e1e4e8'>{chg_str}</td>"
            f"<td style='padding:6px 12px;border-bottom:1px solid #e1e4e8;"
            f"color:{colour};font-weight:bold'>{bias}</td>"
            f"<td style='padding:6px 12px;border-bottom:1px solid #e1e4e8;text-align:center'>{conf_cell}</td>"
            f"<td style='padding:6px 12px;border-bottom:1px solid #e1e4e8;text-align:center'>{trade_cell}</td>"
            f"</tr>"
        )
    return f"""
    <table style='border-collapse:collapse;width:100%;margin-bottom:32px;font-size:14px'>
      <thead>
        <tr style='background:#f6f8fa'>
          <th style='padding:8px 12px;text-align:left;border-bottom:2px solid #d0d7de'>Instrument</th>
          <th style='padding:8px 12px;text-align:left;border-bottom:2px solid #d0d7de'>Spot</th>
          <th style='padding:8px 12px;text-align:left;border-bottom:2px solid #d0d7de'>24h Chg</th>
          <th style='padding:8px 12px;text-align:left;border-bottom:2px solid #d0d7de'>Bias</th>
          <th style='padding:8px 12px;text-align:center;border-bottom:2px solid #d0d7de'>Confidence</th>
          <th style='padding:8px 12px;text-align:center;border-bottom:2px solid #d0d7de'>Trade</th>
        </tr>
      </thead>
      <tbody>{rows}</tbody>
    </table>"""


# ---------------------------------------------------------------------------
# GitHub Pages full report builder
# ---------------------------------------------------------------------------

def build_report_html(timestamp: str, results: list[dict]) -> str:
    """Full standalone HTML page published to GitHub Pages."""
    summary = build_summary_table(results, link_prefix="#")

    forecasts = ""
    for r in results:
        anchor     = r["key"].lower()
        bias       = r["bias"]
        confidence = r.get("confidence", 0)
        colour     = BIAS_COLOUR.get(bias, "#57606a")

        if confidence < CONFIDENCE_THRESHOLD:
            # Short notice — no detailed report
            body = (
                f"<p style='color:#57606a;font-style:italic;margin:0'>"
                f"Not enough confidence to produce a forecast "
                f"(score: {confidence}/100 — minimum required: {CONFIDENCE_THRESHOLD})."
                f"</p>"
            )
        else:
            body = markdown_to_html(r["response"])

        forecasts += f"""
        <section id='{anchor}' style='margin-bottom:40px;border-left:4px solid {colour};padding-left:16px'>
          <h3 style='margin:0 0 8px;color:#24292f'>
            {r['name']}
            <span style='font-size:13px;font-weight:normal;color:{colour};margin-left:8px'>{bias}</span>
            <span style='font-size:12px;font-weight:normal;color:#57606a;margin-left:8px'>confidence: {confidence}</span>
          </h3>
          <div style='font-size:14px;line-height:1.7;color:#24292f'>{body}</div>
          <p style='margin:14px 0 0'>
            <a href='#top' style='font-size:12px;color:#57606a;text-decoration:none'>&#8593; Back to top</a>
          </p>
        </section>
        <hr style='border:none;border-top:1px solid #e1e4e8;margin:0 0 40px'>"""

    return f"""<!DOCTYPE html>
<html lang='en'>
<head>
  <meta charset='utf-8'>
  <meta name='viewport' content='width=device-width,initial-scale=1'>
  <title>Market Forecast — {timestamp} UTC</title>
  <style>
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
      max-width: 860px; margin: 0 auto; padding: 24px 16px; color: #24292f;
    }}
    h4 {{ margin: 18px 0 6px; color: #24292f; }}
    table {{ border-collapse: collapse; margin: 8px 0; }}
  </style>
</head>
<body>
  <div id='top'>
    <h2 style='margin:0 0 4px'>Hourly Market Forecast</h2>
    <p style='margin:0 0 24px;color:#57606a;font-size:14px'>{timestamp} UTC &nbsp;·&nbsp; 10 instruments</p>
  </div>
  {summary}
  {forecasts}
  <p style='font-size:12px;color:#57606a;margin-top:32px'>
    Automated by Claude {MODEL} via GitHub Actions
  </p>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Email builder (summary table + link only)
# ---------------------------------------------------------------------------

def build_email_html(timestamp: str, results: list[dict], report_url: str) -> str:
    """Slim email: summary table with links into the GitHub Pages report + one CTA button."""
    summary = build_summary_table(results, link_prefix=f"{report_url}#")

    return f"""<!DOCTYPE html>
<html>
<body style='font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif;
             max-width:800px;margin:0 auto;padding:24px;color:#24292f'>
  <h2 style='margin:0 0 4px'>Hourly Market Forecast</h2>
  <p style='margin:0 0 24px;color:#57606a;font-size:14px'>{timestamp} UTC &nbsp;·&nbsp; 10 instruments</p>
  {summary}
  <p style='margin:24px 0 0'>
    <a href='{report_url}' style='
      display:inline-block;padding:10px 20px;background:#0969da;color:#fff;
      text-decoration:none;border-radius:6px;font-size:14px;font-weight:600'>
      View full report &#8594;
    </a>
  </p>
  <p style='font-size:12px;color:#57606a;margin-top:32px'>
    Automated by Claude {MODEL} via GitHub Actions &nbsp;·&nbsp;
    Report available for 4 hours
  </p>
</body>
</html>"""


# ---------------------------------------------------------------------------
# GitHub Pages file management
# ---------------------------------------------------------------------------

def save_report_and_cleanup(timestamp: str, html: str) -> str:
    """
    Save the full report to docs/ and delete files older than 4 hours.
    Returns the URL of the timestamped report.
    """
    DOCS_DIR.mkdir(exist_ok=True)

    # Timestamped file: report_2026-05-19_14-00.html
    filename    = f"report_{timestamp.replace(':', '-').replace(' ', '_')}.html"
    report_path = DOCS_DIR / filename
    report_path.write_text(html, encoding="utf-8")

    # index.html always points to the latest report
    (DOCS_DIR / "index.html").write_text(html, encoding="utf-8")

    # Delete timestamped reports older than 72 hours
    cutoff = datetime.now(timezone.utc) - timedelta(hours=72)
    for f in DOCS_DIR.glob("report_*.html"):
        try:
            dt_str = f.stem[len("report_"):]          # e.g. 2026-05-19_14-00
            dt = datetime.strptime(dt_str, "%Y-%m-%d_%H-%M").replace(tzinfo=timezone.utc)
            if dt < cutoff:
                f.unlink()
                print(f"Deleted old report: {f.name}")
        except Exception:
            pass  # skip files that don't match the naming pattern

    return f"{PAGES_URL}/{filename}"


# ---------------------------------------------------------------------------
# Email sending
# ---------------------------------------------------------------------------

def send_email(subject: str, html_body: str) -> None:
    gmail_user     = os.environ["GMAIL_USER"]
    gmail_password = os.environ["GMAIL_APP_PASSWORD"]
    email_to       = os.environ["EMAIL_TO"]

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = gmail_user
    msg["To"]      = email_to
    msg.attach(MIMEText(html_body, "html"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(gmail_user, gmail_password)
        server.sendmail(gmail_user, email_to, msg.as_string())


# ---------------------------------------------------------------------------
# Per-instrument forecast runner
# ---------------------------------------------------------------------------

def run_pair(pair_key: str, pair_info: dict, timestamp: str) -> dict:
    pair_name   = pair_info["name"]
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

    bias       = extract_bias(response)
    confidence = extract_confidence(response)
    print(f"[{timestamp}] {pair_name}: bias={bias}  confidence={confidence} ✓")

    return {
        "key":         pair_key,
        "name":        pair_name,
        "market_data": market_data,
        "response":    response,
        "bias":        bias,
        "confidence":  confidence,
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> int:
    now_utc   = datetime.now(timezone.utc)
    timestamp = now_utc.strftime("%Y-%m-%d %H:%M")

    if not is_fx_market_open(now_utc):
        print(f"[{timestamp}] Market closed — skipping run.")
        return 0

    for var in ("ANTHROPIC_API_KEY", "GMAIL_USER", "GMAIL_APP_PASSWORD", "EMAIL_TO"):
        if not os.environ.get(var):
            print(f"ERROR: {var} not set.", file=sys.stderr)
            return 1

    # --- Run all forecasts in parallel (5 workers) ---
    print(f"[{timestamp}] Running {len(PAIRS)} instruments in parallel...")
    results_dict: dict = {}

    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {
            executor.submit(run_pair, pair_key, pair_info, timestamp): pair_key
            for pair_key, pair_info in PAIRS.items()
        }
        for future in as_completed(futures):
            pair_key = futures[future]
            try:
                results_dict[pair_key] = future.result(timeout=180)
            except Exception as e:
                print(f"[{timestamp}] ERROR: {pair_key} failed — {e}", file=sys.stderr)
                traceback.print_exc()
                # Store a placeholder so one failure doesn't kill the whole report
                results_dict[pair_key] = {
                    "key":         pair_key,
                    "name":        PAIRS[pair_key]["name"],
                    "market_data": {},
                    "response":    f"_Run failed: {type(e).__name__}: {e}_",
                    "bias":        "—",
                    "confidence":  0,
                }

    # Restore original PAIRS ordering
    results = [results_dict[k] for k in PAIRS if k in results_dict]
    print(f"[{timestamp}] All instruments complete ({len(results)}/{len(PAIRS)} succeeded).")

    # --- Build and save full HTML report to docs/ ---
    print(f"\n[{timestamp}] Building GitHub Pages report...")
    report_html = build_report_html(timestamp, results)
    report_url  = save_report_and_cleanup(timestamp, report_html)
    print(f"[{timestamp}] Report saved → {report_url}")

    # --- Build and send slim email ---
    print(f"[{timestamp}] Sending email...")
    subject    = f"Market Forecast | {timestamp} UTC"
    email_html = build_email_html(timestamp, results, report_url)
    try:
        send_email(subject, email_html)
        print(f"[{timestamp}] Email sent ✓")
    except Exception as e:
        print(f"ERROR sending email: {e}", file=sys.stderr)
        traceback.print_exc()
        return 1

    print(f"[{timestamp}] All done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
