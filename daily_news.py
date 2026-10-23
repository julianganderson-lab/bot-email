"""Sends a daily news digest by email. Stdlib only, no API keys needed for news."""
import html
import os
import re
import smtplib
import ssl
import sys
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime
from email.message import EmailMessage
from zoneinfo import ZoneInfo

# Edit these to change your sources
FEEDS = {
    "Top Stories (NPR)": "https://feeds.npr.org/1001/rss.xml",
    "World (BBC)": "http://feeds.bbci.co.uk/news/world/rss.xml",
    "US (NYT)": "https://rss.nytimes.com/services/xml/rss/nyt/US.xml",
    "Technology (BBC)": "http://feeds.bbci.co.uk/news/technology/rss.xml",
    "Business (BBC)": "http://feeds.bbci.co.uk/news/business/rss.xml",
}
ITEMS_PER_FEED = 5
TZ = ZoneInfo("America/New_York")  # handles EST/EDT automatically


def clean(text):
    return html.unescape(re.sub(r"<[^>]+>", "", text or "")).strip()


def fetch_items(url):
    req = urllib.request.Request(url, headers={"User-Agent": "daily-news-bot/1.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        root = ET.fromstring(r.read())
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
    html_parts = [
        f"<h2>Your news for {now:%A, %B %d, %Y}</h2>"
    ]
    text_parts = [f"Your news for {now:%A, %B %d, %Y}\n"]
    for section, url in FEEDS.items():
        try:
            items = fetch_items(url)
        except Exception as e:
            print(f"Failed {section}: {e}", file=sys.stderr)
            continue
        if not items:
            continue
        html_parts.append(f"<h3>{html.escape(section)}</h3><ul>")
        text_parts.append(f"\n== {section} ==")
        for i in items:
            t, l, s = html.escape(i["title"]), html.escape(i["link"]), html.escape(i["summary"])
            html_parts.append(
                f'<li style="margin-bottom:10px"><a href="{l}"><b>{t}</b></a>'
                + (f"<br><span style='color:#555'>{s}</span>" if s else "")
                + "</li>"
            )
            text_parts.append(f"- {i['title']}\n  {i['link']}")
        html_parts.append("</ul>")
    return "\n".join(text_parts), "\n".join(html_parts)


def main():
    now = datetime.now(TZ)
    # The scheduler fires at two UTC times to cover EST/EDT; only send at 8am Eastern.
    if os.environ.get("FORCE_SEND") != "1" and now.hour != 8:
        print(f"It's {now:%H:%M} Eastern, not 8am. Skipping.")
        return

    user = os.environ["GMAIL_USER"]
    password = os.environ["GMAIL_APP_PASSWORD"]
    to = os.environ.get("TO_EMAIL", user)

    text, body_html = build_email(now)
    msg = EmailMessage()
    msg["Subject"] = f"Daily News – {now:%b %d}"
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
