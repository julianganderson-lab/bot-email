"""Evening finance recap: sector scoreboard (1-day / 7-day / 30-day) + headlines.
Stdlib only. Optional: set ANTHROPIC_API_KEY to add a short 'why' explanation."""
import html
import json
import os
import re
import smtplib
import ssl
import sys
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from email.message import EmailMessage
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/New_York")  # handles EST/EDT automatically

# ---- Edit these to change what you get -------------------------------------
FEEDS = {
    "Markets (CNBC)": "https://www.cnbc.com/id/20910258/device/rss/rss.html",
    "Finance (CNBC)": "https://www.cnbc.com/id/10000664/device/rss/rss.html",
    "Top Stories (MarketWatch)": "https://feeds.content.dowjones.io/public/rss/mw_topstories",
    "Markets (WSJ)": "https://feeds.content.dowjones.io/public/rss/RSSMarketsMain",
    "Economy (NYT)": "https://rss.nytimes.com/services/xml/rss/nyt/Economy.xml",
    "Federal Reserve": "https://www.federalreserve.gov/feeds/press_all.xml",
}
ITEMS_PER_FEED = 5

SECTORS = {
    "Technology": "XLK",
    "Communication Services": "XLC",
    "Consumer Discretionary": "XLY",
    "Consumer Staples": "XLP",
    "Health Care": "XLV",
    "Financials": "XLF",
    "Industrials": "XLI",
    "Energy": "XLE",
    "Materials": "XLB",
    "Utilities": "XLU",
    "Real Estate": "XLRE",
}
BENCHMARK = ("S&P 500", "SPY")
# -----------------------------------------------------------------------------

UA = {"User-Agent": "Mozilla/5.0 (compatible; daily-news-bot/2.0)"}


def http_get(url, timeout=20):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def clean(text):
    return html.unescape(re.sub(r"<[^>]+>", "", text or "")).strip()


# ---------------- Market data ----------------
def parse_chart(payload):
    """Yahoo chart JSON -> sorted list of (date, close), skipping empty days."""
    res = payload["chart"]["result"][0]
    closes = res["indicators"]["quote"][0]["close"]
    out = []
    for t, c in zip(res["timestamp"], closes):
        if c is not None:
            out.append((datetime.fromtimestamp(t, TZ).date(), float(c)))
    return out


def fetch_series(symbol):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=3mo&interval=1d"
    return parse_chart(json.loads(http_get(url)))


def pct(series, calendar_days=None):
    """% change from the close `calendar_days` ago (or the prior close if None)."""
    last_date, last = series[-1]
    if calendar_days is None:
        base = series[-2][1]
    else:
        target = last_date - timedelta(days=calendar_days)
        eligible = [c for d, c in series if d <= target]
        if not eligible:
            return None
        base = eligible[-1]
    return (last / base - 1) * 100


def get_sector_rows():
    rows, as_of = [], None
    for name, sym in list(SECTORS.items()) + [BENCHMARK]:
        try:
            s = fetch_series(sym)
            as_of = s[-1][0]
            rows.append({"name": name, "sym": sym, "d1": pct(s), "d7": pct(s, 7), "d30": pct(s, 30)})
        except Exception as e:
            print(f"Failed {sym}: {e}", file=sys.stderr)
    return rows, as_of


def fmt(v):
    return "n/a" if v is None else f"{v:+.2f}%"


def cell(v):
    if v is None:
        return "<td style='padding:6px 10px;text-align:right'>n/a</td>"
    color = "#0a7d36" if v > 0 else "#c0262d" if v < 0 else "#555"
    return f"<td style='padding:6px 10px;text-align:right;color:{color};font-weight:600'>{v:+.2f}%</td>"


def sector_table_html(rows, as_of):
    sectors = sorted([r for r in rows if r["sym"] != BENCHMARK[1]], key=lambda r: -(r["d1"] or 0))
    bench = [r for r in rows if r["sym"] == BENCHMARK[1]]
    head = ("<tr style='background:#f0f0f0'><th style='padding:6px 10px;text-align:left'>Sector</th>"
            "<th style='padding:6px 10px'>1 day</th><th style='padding:6px 10px'>7 days</th>"
            "<th style='padding:6px 10px'>30 days</th></tr>")
    body = "".join(
        f"<tr style='border-top:1px solid #ddd'><td style='padding:6px 10px'>{html.escape(r['name'])} "
        f"<span style='color:#888'>({r['sym']})</span></td>{cell(r['d1'])}{cell(r['d7'])}{cell(r['d30'])}</tr>"
        for r in sectors + bench
    )
    return (f"<h3>Sector scoreboard <span style='font-weight:normal;color:#666;font-size:13px'>"
            f"(close {as_of:%a %b %d})</span></h3>"
            f"<table style='border-collapse:collapse;font-size:14px'>{head}{body}</table>")


def sector_table_text(rows, as_of):
    lines = [f"SECTOR SCOREBOARD (close {as_of:%a %b %d})", f"{'Sector':26}{'1d':>9}{'7d':>9}{'30d':>9}"]
    sectors = sorted([r for r in rows if r["sym"] != BENCHMARK[1]], key=lambda r: -(r["d1"] or 0))
    for r in sectors + [r for r in rows if r["sym"] == BENCHMARK[1]]:
        lines.append(f"{r['name'][:25]:26}{fmt(r['d1']):>9}{fmt(r['d7']):>9}{fmt(r['d30']):>9}")
    return "\n".join(lines)


# ---------------- Optional AI commentary ----------------
def ai_commentary(rows, sections):
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key or not rows:
        return ""
    table = "\n".join(f"{r['name']}: 1d {fmt(r['d1'])}, 7d {fmt(r['d7'])}, 30d {fmt(r['d30'])}" for r in rows)
    heads = "\n".join(f"- {i['title']}" for items in sections.values() for i in items[:4])
    prompt = (
        "Using ONLY the data and headlines below, write 4-5 plain sentences for a morning email: "
        "which sectors are rising and falling across 1-day, 7-day and 30-day, and the likely reasons "
        "according to the headlines. If the headlines don't explain a move, say so rather than guessing. "
        "No bullet points, no advice.\n\nSECTOR DATA:\n" + table + "\n\nHEADLINES:\n" + heads
    )
    body = json.dumps({"model": "claude-sonnet-4-6", "max_tokens": 400,
                       "messages": [{"role": "user", "content": prompt}]}).encode()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages", data=body,
        headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            data = json.loads(r.read())
        return "".join(b.get("text", "") for b in data["content"] if b.get("type") == "text").strip()
    except Exception as e:
        print(f"Commentary failed: {e}", file=sys.stderr)
        return ""


# ---------------- News ----------------
def fetch_items(url):
    root = ET.fromstring(http_get(url))
    items = []
    for it in root.iter("item"):
        items.append({
            "title": clean(it.findtext("title")),
            "link": (it.findtext("link") or "").strip(),
            "summary": clean(it.findtext("description")),
        })
        if len(items) >= ITEMS_PER_FEED:
            break
    return items


def build_email(now):
    rows, as_of = get_sector_rows()
    sections = {}
    for name, url in FEEDS.items():
        try:
            items = fetch_items(url)
            if items:
                sections[name] = items
        except Exception as e:
            print(f"Failed {name}: {e}", file=sys.stderr)

    h = [f"<h2>Your markets recap for {now:%A, %B %d, %Y}</h2>"]
    t = [f"Your markets recap for {now:%A, %B %d, %Y}\n"]

    if rows:
        h.append(sector_table_html(rows, as_of))
        t.append(sector_table_text(rows, as_of))
        why = ai_commentary(rows, sections)
        if why:
            h.append(f"<p style='max-width:640px'>{html.escape(why)}</p>")
            t.append("\n" + why)

    for section, items in sections.items():
        h.append(f"<h3>{html.escape(section)}</h3><ul>")
        t.append(f"\n== {section} ==")
        for i in items:
            ti, li, su = html.escape(i["title"]), html.escape(i["link"]), html.escape(i["summary"])
            h.append(f'<li style="margin-bottom:10px"><a href="{li}"><b>{ti}</b></a>'
                     + (f"<br><span style='color:#555'>{su}</span>" if su else "") + "</li>")
            t.append(f"- {i['title']}\n  {i['link']}")
        h.append("</ul>")
    h.append("<p style='color:#888;font-size:12px'>Information only, not investment advice. "
             "Sector figures are from sector ETFs and may differ slightly from other sources.</p>")
    return "\n".join(t), "\n".join(h), as_of


def main():
    now = datetime.now(TZ)
    forced = os.environ.get("FORCE_SEND") == "1"
    # The scheduler fires at two UTC times to cover EST/EDT; only send at 5pm hour Eastern, weekdays.
    if not forced and (now.hour != 17 or now.weekday() >= 5):
        print(f"It's {now:%a %H:%M} Eastern, not a weekday 5:30pm. Skipping.")
        return

    user = os.environ["GMAIL_USER"]
    password = os.environ["GMAIL_APP_PASSWORD"]
    to = os.environ.get("TO_EMAIL", user)

    text, body_html, as_of = build_email(now)
    # If the latest market data isn't from today, the market was closed (holiday). Skip.
    if not forced and as_of is not None and as_of != now.date():
        print(f"Latest market data is {as_of}, not today. Market closed? Skipping.")
        return
    msg = EmailMessage()
    msg["Subject"] = f"Evening Markets Recap – {now:%b %d}"
    msg["From"] = user
    msg["To"] = to
    msg.set_content(text)
    msg.add_alternative(f"<html><body style='font-family:sans-serif'>{body_html}</body></html>", subtype="html")

    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ssl.create_default_context()) as s:
        s.login(user, password)
        s.send_message(msg)
    print("Sent.")


if __name__ == "__main__":
    main()
