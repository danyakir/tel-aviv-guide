"""Build the weekend-guide message (HTML email + plain text preview)."""

from __future__ import annotations

from datetime import datetime
from urllib.parse import urlparse

from .scraper import Event

DAY_PART_LABELS = {
    "eve_thu": "Thursday Evening",
    "morning_fri": "Friday Morning",
    "day_fri": "Friday Daytime",
    "eve_fri": "Friday Evening",
    "morning_sat": "Saturday Morning",
    "day_sat": "Saturday Daytime",
    "eve_sat": "Saturday Evening",
}

DAY_PART_ORDER = ["eve_thu", "morning_fri", "day_fri", "eve_fri", "morning_sat", "day_sat", "eve_sat"]

HEADER_PHOTO_CID = "cid:header-photo"


def _esc(text: str) -> str:
    return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', '&quot;')


def _format_price(price: str) -> str:
    # Unknown-price sentinels (and empties) are hidden from the guide.
    p = (price or "").strip()
    if not p or p.lower() in ("free / tbd", "see listing", "see venue", "see venue page"):
        return ""
    return p


def build_message(events: list[Event], thu_eve: datetime, fri: datetime, sat: datetime, sun: datetime) -> str:
    """Return a classy HTML email body."""
    date_range = f"{thu_eve:%d %b} – {sat:%d %b %Y}"

    by_part: dict[str, list[Event]] = {}
    for ev in events:
        by_part.setdefault(ev.day_part, []).append(ev)

    sections_html = []
    any_shown = False
    for part in DAY_PART_ORDER:
        items = by_part.get(part, [])
        if not items:
            continue
        any_shown = True
        items_html = ""
        for ev in items:
            location = ev.where if ev.where and ev.where != "Tel Aviv" else "Tel Aviv"
            verified_badge = ' <span style="color: #28a745; font-size: 11px;">✓ verified</span>' if ev.verified else ""
            category = {"party": "Party / nightlife", "show": "Live show", "movie": "Film", "free-municipal": "Daytime / culture", "community": "Daytime / community"}.get(ev.category, "Event")
            if ev.source == "Tel Aviv Municipality" and ev.price == "Free":
                category = "Free municipal event"
            if ev.category == "party":
                text = f"{ev.title} {ev.description} {ev.genres}".lower()
                genres = [label for label, words in (("Techno", ("techno", "טכנו")),
                          ("Disco", ("disco", "דיסקו")), ("House", ("house music", "deep house", "tech house", "האוס")),
                          ("Rave", ("rave", "רייב")), ("Trance", ("trance", "טראנס")))
                          if any(word in text for word in words)]
                if genres:
                    category += " · " + " / ".join(genres)
                elif ev.genres:
                    category += " · " + ev.genres
            items_html += f"""
                <div class="event" style="border:1px solid #dce3eb;border-radius:12px;background:#ffffff;padding:20px;margin:0 0 16px;">
                    <div style="font-size:11px;color:#52718d;margin-bottom:8px;">{category}</div>
                    <div class="event-title">📅 {_esc(ev.title)}{verified_badge}</div>
                    <div class="event-meta">
                        <span class="label">📍</span> <span class="value">{_esc(location)}</span>
                        &nbsp;&nbsp;
                        <span class="label">🕒</span> <span class="value">{_esc(ev.when)}</span>
                    </div>
            """
            price = _format_price(ev.price) or "Price not published — check event page"
            if price:
                items_html += f'<div class="event-meta"><span class="label">💰</span> <span class="value">{_esc(price)}</span></div>'
            if ev.description:
                items_html += f'<div class="event-desc">⁉ {_esc(ev.description)}</div>'
            if ev.link:
                domain = urlparse(ev.link).hostname or ev.source
                items_html += f'<div class="event-link"><a href="{_esc(ev.link)}" style="color: #2a6496; text-decoration: none;">Event page → {_esc(domain)}</a></div>'
            source_label = (f'<a href="{_esc(ev.source_link)}">{_esc(ev.source)}</a>' if ev.source_link else _esc(ev.source))
            items_html += f'<div class="event-meta">Source: {source_label}</div>'
            items_html += "</div>"
        sections_html.append(
            f'<div class="day-part"><h2>{DAY_PART_LABELS[part]}</h2>{items_html}</div>'
        )

    if not any_shown:
        sections_html.append(
            '<div class="day-part"><p>No matching events found this weekend — check back next Wednesday!</p></div>'
        )

    sources = " &middot; ".join(_esc(s) for s in sorted({e.source for e in events if e.day_part in DAY_PART_ORDER and e.source}))
    domains = {urlparse(e.link).hostname for e in events if e.day_part in DAY_PART_ORDER and e.link}
    coverage = (f"Limited source coverage: {len(domains)} independent websites returned matching events; target: at least 3."
                if len(domains) < 3 else f"Events from {len(domains)} independent websites.")
    html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  body {{
    font-family: Arial, Helvetica, sans-serif;
    line-height: 1.7;
    color: #1a1a1a;
    max-width: 600px;
    margin: 0 auto;
    padding: 0 24px;
    background: #f3f6fa;
  }}
  .header {{
    text-align: center;
    padding: 36px 0 24px 0;
    border-bottom: 1px solid #e0e0e0;
    margin-bottom: 32px;
  }}
  .header img {{
    width: 100%;
    max-width: 600px;
    border-radius: 10px;
    display: block;
    margin: 0 auto;
  }}
  .header h1 {{
    font-size: 26px;
    margin: 18px 0 6px 0;
    font-weight: normal;
    letter-spacing: 0.5px;
    color: #111;
  }}
  .header .date {{
    font-size: 14px;
    color: #666;
    font-style: italic;
  }}
  .day-part {{
    margin-bottom: 36px;
  }}
  .day-part h2 {{
    font-size: 13px;
    margin: 0 0 16px 0;
    padding-bottom: 8px;
    border-bottom: 1px solid #e0e0e0;
    color: #111;
    font-weight: normal;
    text-transform: uppercase;
    letter-spacing: 1.5px;
  }}
  .event {{
    margin-bottom: 22px;
    padding-bottom: 18px;
    border-bottom: 1px solid #f0f0f0;
  }}
  .event-title {{
    font-size: 16px;
    font-weight: bold;
    margin: 0 0 8px 0;
    color: #111;
    line-height: 1.4;
  }}
  .event-meta {{
    font-size: 13px;
    color: #444;
    margin: 3px 0;
    line-height: 1.5;
  }}
  .event-meta .value {{
    color: #333;
  }}
  .event-desc {{
    font-size: 13px;
    color: #666;
    font-style: italic;
    margin-top: 8px;
    line-height: 1.5;
  }}
  .event-link {{
    font-size: 12px;
    margin-top: 8px;
  }}
  .event-link a {{
    color: #2a6496;
    text-decoration: none;
  }}
  .footer {{
    margin-top: 44px;
    padding-top: 20px;
    border-top: 1px solid #e0e0e0;
    font-size: 12px;
    color: #888;
    text-align: center;
    line-height: 1.6;
  }}
</style>
</head>
<body>

<div class="header">
  <img src="{HEADER_PHOTO_CID}" alt="Tel Aviv">
  <h1>Weekend in Tel Aviv</h1>
  <div class="date">{date_range} &middot; Thursday &ndash; Saturday</div>
</div>

{"".join(sections_html)}

<div class="footer">
  <p>Sources: {sources}</p>
  <p>{coverage} Prices may change; confirm before booking.</p>
  <p>Next update: Wednesday 20:00 Israel time</p>
</div>

</body>
</html>"""
    return html


def build_sample_message(thu_eve: datetime, fri: datetime, sat: datetime, sun: datetime) -> str:
    date_range = f"{thu_eve:%d %b} – {sat:%d %b %Y}"
    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  body {{ font-family: Georgia, serif; line-height: 1.7; color: #1a1a1a; max-width: 600px; margin: 0 auto; padding: 0 24px; background: #fdfdfd; }}
  .header {{ text-align: center; padding: 36px 0 24px 0; border-bottom: 1px solid #e0e0e0; margin-bottom: 32px; }}
  .header img {{ width: 100%; max-width: 600px; border-radius: 10px; display: block; margin: 0 auto; }}
  .header h1 {{ font-size: 26px; margin: 18px 0 6px 0; font-weight: normal; color: #111; }}
  .header .date {{ font-size: 14px; color: #666; font-style: italic; }}
  .day-part {{ margin-bottom: 36px; }}
  .day-part h2 {{ font-size: 13px; margin: 0 0 16px 0; padding-bottom: 8px; border-bottom: 1px solid #e0e0e0; color: #111; font-weight: normal; text-transform: uppercase; letter-spacing: 1.5px; }}
  .event {{ margin-bottom: 22px; padding-bottom: 18px; border-bottom: 1px solid #f0f0f0; }}
  .event-title {{ font-size: 16px; font-weight: bold; margin: 0 0 8px 0; color: #111; }}
  .event-meta {{ font-size: 13px; color: #444; margin: 3px 0; }}
  .event-desc {{ font-size: 13px; color: #666; font-style: italic; margin-top: 8px; }}
  .event-link {{ font-size: 12px; margin-top: 8px; }}
  .event-link a {{ color: #2a6496; text-decoration: none; }}
  .footer {{ margin-top: 44px; padding-top: 20px; border-top: 1px solid #e0e0e0; font-size: 12px; color: #888; text-align: center; }}
</style>
</head>
<body>

<div class="header">
  <img src="cid:header-photo" alt="Tel Aviv">
  <h1>Weekend in Tel Aviv</h1>
  <div class="date">{date_range} &middot; Thursday &ndash; Saturday</div>
</div>

<div class="day-part"><h2>Thursday Evening</h2>
<div class="event"><div class="event-title">📅 Check Secret Tel Aviv calendar</div>
<div class="event-meta">📍 Tel Aviv &nbsp;&nbsp; 🕒 Evening</div>
<div class="event-desc">⁉ Browse tonight&rsquo;s parties and gigs</div>
<div class="event-link"><a href="https://www.secrettelaviv.com/tickets/">🔗 Event page →</a></div></div></div>

<div class="day-part"><h2>Friday Morning</h2>
<div class="event"><div class="event-title">📅 Community market or cafe</div>
<div class="event-meta">📍 Tel Aviv &nbsp;&nbsp; 🕒 Morning</div>
<div class="event-desc">⁉ Local daytime community event</div>
<div class="event-link"><a href="https://www.secrettelaviv.com/tickets/">🔗 Event page →</a></div></div></div>

<div class="day-part"><h2>Friday Daytime</h2>
<div class="event"><div class="event-title">📅 Free municipal activity</div>
<div class="event-meta">📍 Tel Aviv &nbsp;&nbsp; 🕒 Daytime</div>
<div class="event-desc">⁉ Visit visit.tel-aviv.gov.il/see-do/whats-on</div>
<div class="event-link"><a href="https://visit.tel-aviv.gov.il/see-do/whats-on">🔗 Event page →</a></div></div></div>

<div class="day-part"><h2>Friday Evening</h2>
<div class="event"><div class="event-title">📅 Techno / disco party</div>
<div class="event-meta">📍 Teder.fm / Phi Garden / Kuli Alma &nbsp;&nbsp; 🕒 Evening</div>
<div class="event-desc">⁉ Electronic music, no Mizrahi</div>
<div class="event-link"><a href="https://www.secrettelaviv.com/tickets/categories/parties/">🔗 Event page →</a></div></div></div>

<div class="day-part"><h2>Saturday Morning</h2>
<div class="event"><div class="event-title">📅 Community daytime event</div>
<div class="event-meta">📍 Tel Aviv &nbsp;&nbsp; 🕒 Morning</div>
<div class="event-desc">⁉ Market, fair, or workshop</div>
<div class="event-link"><a href="https://www.secrettelaviv.com/tickets/categories/shopping/">🔗 Event page →</a></div></div></div>

<div class="day-part"><h2>Saturday Daytime</h2>
<div class="event"><div class="event-title">📅 Rock show or movie</div>
<div class="event-meta">📍 Barby / Levontin 7 / Cinema &nbsp;&nbsp; 🕒 Daytime</div>
<div class="event-desc">⁉ Live music or new film</div>
<div class="event-link"><a href="https://www.secrettelaviv.com/tickets/categories/live-music/">🔗 Event page →</a></div></div></div>

<div class="day-part"><h2>Saturday Evening</h2>
<div class="event"><div class="event-title">📅 Weekend party</div>
<div class="event-meta">📍 Tel Aviv &nbsp;&nbsp; 🕒 Evening</div>
<div class="event-desc">⁉ Nightlife pick</div>
<div class="event-link"><a href="https://www.secrettelaviv.com/tickets/categories/parties/">🔗 Event page →</a></div></div></div>

<div class="footer">
  <p>Sources: Secret Tel Aviv &middot; Mevalim (Hebrew) &middot; Zygo (Hebrew) &middot; DIY Tel Aviv &middot; Tel Aviv-Yafo Municipality &middot; Debbest Israel</p>
  <p>Cross-verified from multiple sources &middot; Excluded: Dizengoff &middot; Sarona &middot; Mizrahi &middot; Kid/family</p>
  <p>Next update: Wednesday 20:00 Israel time</p>
</div>

</body>
</html>"""


def build_plain_text(events: list[Event], thu_eve: datetime, fri: datetime, sat: datetime, sun: datetime) -> str:
    lines: list[str] = []
    date_range = f"{thu_eve:%d %b} – {sat:%d %b %Y}"
    lines.append(f"Weekend in Tel Aviv — {date_range} (Thu–Sat)\n")

    by_part: dict[str, list[Event]] = {}
    for ev in events:
        by_part.setdefault(ev.day_part, []).append(ev)

    for part in DAY_PART_ORDER:
        items = by_part.get(part, [])
        if not items:
            continue
        lines.append(f"\n{DAY_PART_LABELS[part]}")
        lines.append("-" * 40)
        for ev in items:
            location = ev.where if ev.where and ev.where != "Tel Aviv" else "Tel Aviv"
            lines.append(f"📅 {ev.title}")
            lines.append(f"📍 {location}")
            lines.append(f"🕒 {ev.when}")
            price = _format_price(ev.price) or "Price not published — check event page"
            if price:
                lines.append(f"💰 {price}")
            if ev.description:
                lines.append(f"⁉ {ev.description}")
            if ev.link:
                lines.append(f"🔗 {ev.link}")
            lines.append(f"Source: {ev.source}")
            if ev.verified:
                lines.append("✓ Verified on multiple sources")
            lines.append("")

    return "\n".join(lines)
