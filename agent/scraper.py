"""Scrapers for Tel Aviv weekend event sources — multi-source, Hebrew + English, with prices."""

from __future__ import annotations

import logging
import json
import re
from html import unescape
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urljoin, urlparse, unquote
from dataclasses import dataclass
from datetime import datetime

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger("tel-aviv-agent")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "he-IL,he;q=0.9,en-US,en;q=0.8",
}

REQUEST_TIMEOUT = 20

# Anchor text that shows up on every listing card or in nav links but is not an event.
_JUNK_LINK_TITLES = {
    "read more", "read more...", "more info", "more information", "buy tickets",
    "get tickets", "tickets", "details", "see more", "view event", "book now",
    "calendar", "parties", "live music", "submit an event", "read post",
}


@dataclass
class Event:
    title: str
    when: str
    where: str
    category: str
    price: str = "see listing"
    link: str = ""
    source: str = ""
    description: str = ""
    sort_dt: datetime | None = None
    day_part: str = ""
    verified: bool = False  # True if found on multiple sources
    genres: str = ""
    source_link: str = ""
    aliases: str = ""

    def sort_key(self) -> tuple:
        dp_order = {
            "eve_thu": 0, "morning_fri": 1, "day_fri": 2, "eve_fri": 3,
            "morning_sat": 4, "day_sat": 5, "eve_sat": 6,
        }
        return (dp_order.get(self.day_part, 99), self.sort_dt or datetime.max)


def _clean(text: str) -> str:
    if not text:
        return ""
    text = unescape(text)
    text = "".join(ch for ch in text if not (0xD800 <= ord(ch) <= 0xDFFF))
    text = "".join(ch for ch in text if 0x20 <= ord(ch) <= 0x10FFFF or ch in "\n\t")
    return " ".join(text.split())


def _get(url: str) -> str | None:
    try:
        r = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        r.encoding = "utf-8" if "charset=" not in r.headers.get("Content-Type", "").lower() else r.encoding
        return r.text
    except Exception as exc:  # noqa: BLE001
        logger.warning("fetch failed %s: %s", url, exc)
        return None


def _parse_dt(text: str, reference: datetime | None = None) -> datetime | None:
    # English format: DD/MM/YYYY
    m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", text)
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            return datetime(y, mo, d)
        except ValueError:
            return None
    # Hebrew format: DD.MM
    m = re.search(r"(\d{1,2})\.(\d{1,2})(?:\.(\d{4}|\d{2}))?", text)
    if m:
        d, mo = int(m.group(1)), int(m.group(2))
        try:
            year = int(m.group(3)) if m.group(3) else (reference or datetime.now()).year
            if year < 100:
                year += 2000
            if not m.group(3) and reference:
                candidates = []
                for candidate_year in (year - 1, year, year + 1):
                    try:
                        candidates.append(datetime(candidate_year, mo, d))
                    except ValueError:
                        continue
                return min(candidates, key=lambda dt: abs((dt.date() - reference.date()).days)) if candidates else None
            return datetime(year, mo, d)
        except ValueError:
            return None
    return None


def _extract_venue(title: str) -> str:
    if "@" in title:
        parts = title.split("@", 1)
        if len(parts) == 2:
            venue = _clean(parts[1])
            venue = venue.replace("Read More", "").strip()
            if venue:
                return venue
    return ""


def _extract_price(cell_text: str) -> str:
    cell_text = re.sub(r"~~.*?~~", "", cell_text)
    m = re.search(r'\d+(?:[.,]\d{1,2})?(?:\s*[-–]\s*\d+(?:[.,]\d{1,2})?)?\s*(?:₪|ש״ח|ש"ח|שח)(?![א-תA-Za-z])', cell_text)
    if m:
        return m.group(0).strip()
    # NIS pattern
    m = re.search(r"(NIS\s*\d+(?:\s*-\s*\d+)?)", cell_text, re.IGNORECASE)
    if m:
        return m.group(1).strip()
    # Shekel pattern
    m = re.search(r"(₪\s*\d+(?:\s*-\s*\d+)?)", cell_text)
    if m:
        return m.group(1).strip()
    # Hebrew shekel pattern (e.g., 85₪)
    m = re.search(r"(\d+\s*₪)", cell_text)
    if m:
        return m.group(1).strip()
    # Range pattern (e.g., ~~240₪~~ 85₪)
    m = re.search(r"~~\d+₪~~\s*(\d+₪)", cell_text)
    if m:
        return m.group(1).strip()
    # Dollar pattern
    m = re.search(r"(\$\s*\d+(?:\s*-\s*\d+)?)", cell_text)
    if m:
        return m.group(1).strip()
    if re.search(r'\bfree\b|חינם|כניסה חופשית', cell_text, re.IGNORECASE):
        return "Free"
    return ""


def _extract_description(cell_text: str, title: str) -> str:
    text = cell_text
    text = text.replace("Read More", "")
    title_clean = title.replace("Read More", "").strip()
    if title_clean in text:
        text = text.replace(title_clean, "", 1)
    text = _clean(text)
    text = re.sub(r"\d{1,2}/\d{1,2}/\d{4}", "", text)
    text = re.sub(r"\d{1,2}\.\d{1,2}", "", text)
    text = re.sub(r"\d{1,2}:\d{2}\s*(?:am|pm)", "", text, flags=re.IGNORECASE)
    text = _clean(text)
    if len(text) > 150:
        text = text[:147] + "..."
    return text


def _extract_time(cell_text: str) -> str:
    m = re.search(r"((?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)[a-z]*\s+\d{1,2}/\d{1,2}/\d{4}\s+\d{1,2}:\d{2}\s*(?:am|pm)\s*-\s*\d{1,2}:\d{2}\s*(?:am|pm))", cell_text, re.IGNORECASE)
    if m:
        return m.group(1).strip()
    m = re.search(r"(\d{1,2}/\d{1,2}/\d{4}\s+\d{1,2}:\d{2}\s*(?:am|pm)\s*-\s*\d{1,2}:\d{2}\s*(?:am|pm))", cell_text, re.IGNORECASE)
    if m:
        return m.group(1).strip()
    # Keep full numeric dates on long cards. Truncating before the year can
    # otherwise turn a future "Friday" party into this Friday's listing.
    m = re.search(r"(\d{1,2}/\d{1,2}/\d{4}\s+\d{1,2}:\d{2})", cell_text)
    if m:
        return m.group(1).strip()
    # Hebrew date format: DD.MM HH:MM
    m = re.search(r"(\d{1,2}\.\d{1,2}\s+\d{1,2}:\d{2})", cell_text)
    if m:
        return m.group(1).strip()
    return cell_text[:60]


EXCLUDE_KEYWORDS = [
    "kid", "kids", "children", "family", "toddler", "baby", "mother",
    "mizrahi", "מזרחי", "dizengoff", "דיזנגוף", "sarona", "סארונה",
    "read more", "members only", "members-only",
    "shittim", "rishon lezion", "rishon le-zion", "rishon-lezion",
    "children", "kids", "תינוק", "ילדים", "משפחה",
    "ילדות", "קטנטנים", "לגילי", "לכיתות",
    "הרקדה", "ריקודי עם", "folk dancing", "folk dance", "גיל הזהב", "senior activities",
    "קאנטרי ארלוזורוב", "זמר עברי", "שירה בציבור", "singalong",
]

INCLUDE_KEYWORDS = [
    "techno", "disco", "house", "electronic", "rock", "indie", "punk",
    "movie", "cinema", "film", "screening", "community", "market", "fair",
    "workshop", "free", "young", "alternative", "underground", "rave",
    "party", "gig", "concert", "live", "dj", "bar", "club", "festival",
    "halloween", "record", "vintage", "antique", "cupping", "olive",
    "mesiba", "מסיבה", "הופעה", "show", "music", "-party",
]


def _is_excluded(ev: Event) -> bool:
    text = f"{ev.title} {ev.where} {ev.description}".lower()
    return any((re.search(r'\b' + re.escape(k) + r'\b', text) is not None)
               if k.isascii() else k in text for k in EXCLUDE_KEYWORDS)


def _is_included(ev: Event) -> bool:
    text = f"{ev.title} {ev.where} {ev.description}".lower()
    return any(k in text for k in INCLUDE_KEYWORDS)


LOCAL_SCENE_VENUES = (
    'teder', 'תדר', 'beit romano', 'בית רומנו', 'rafi', 'רפי', 'phi garden',
    'פיי', 'kuli alma', 'כולי עלמא', 'foma', 'nuweiba', 'נואיבה', 'hive', 'הייב',
    'audiophil', 'אודיופיל', 'radio epgb', 'radio e.p.g.b', 'mozambique',
    'early bar', 'levontin', 'לבונטין', 'room 48', 'חדר 48', 'haoman', 'האומן',
    'gagarin', 'גגרין', 'schalter', 'שאלטר', 'ozen', 'אוזן', 'callas', 'קאלאס',
    'k bar', 'azazel', 'עזאזל', 'fortress', 'førtress',
)


def _local_scene(ev: Event) -> bool:
    text = f'{ev.title} {ev.where} {ev.description}'.lower()
    return any(venue in text for venue in LOCAL_SCENE_VENUES)


def _fits_audience(ev: Event) -> bool:
    if _is_excluded(ev):
        return False
    text = f'{ev.title} {ev.where} {ev.description} {ev.genres}'.lower()
    if ev.category == 'party':
        styles = r'\b(?:techno|disco|rave|horrorave|(?:psy)?trance|house music|tech house|deep house|afro house)\b|טכנו|דיסקו|רייב|האוס|טראנס'
        title_style = re.search(styles, ev.title.lower())
        if any(term in ev.title.lower() for term in ('mainstream', 'מיינסטרים', 'אלכוהול חופשי', 'free alcohol')) and not title_style:
            return False
        return bool(re.search(styles, text)) or _local_scene(ev) or (ev.source == 'Airdrop' and bool(ev.genres))
    if ev.category in ('community', 'free-municipal'):
        return _local_scene(ev) or any(term in text for term in (
            'dj', 'music', 'vinyl', 'record fair', 'vintage', 'street art', 'design', 'swap',
            'creative', 'art fair', 'open studio', 'sound system', 'רגאיי', 'דאב', 'סאונד',
            'וינטג', 'תקליטים', 'אמנות', 'אומנות', 'סטודיו', 'עיצוב', 'קחי תני', 'החלפת בגדים',
            'מוזיקה', 'הופעה', 'קולנוע', 'הקרנה', 'אמנים צעירים'))
    if ev.category == 'restaurant':
        return bool(ev.day_part and ev.sort_dt) and any(term in text for term in (
            'opening party', 'launch', 'pop-up', 'popup', 'tasting', 'opening event',
            'מסיבת פתיחה', 'אירוע פתיחה', 'השקה', 'פופ אפ', 'פופ-אפ', 'טעימות'))
    if any(term in text for term in ('shlomo artzi', 'שלמה ארצי', 'eden golan', 'עדן גולן', 'nostalgia gala', 'concert by the light of hundreds of candles')):
        return False
    return True


def _assign_day_part(ev: Event, thu_eve: datetime, fri: datetime, sat: datetime, sun: datetime) -> str:
    dt = ev.sort_dt or _parse_dt(ev.when, thu_eve) or datetime.min
    # Yearless card dates must follow the requested weekend, not the host year.
    if re.search(r'\b\d{1,2}\.\d{1,2}(?![\d.])', ev.when) and not re.search(r'\d{1,2}[./]\d{1,2}[./]\d{2,4}', ev.when):
        dt = _parse_dt(ev.when, thu_eve) or dt
    text = f"{ev.when} {ev.title}".lower()

    if dt == datetime.min:
        # Hebrew day names: Sunday is (yom) rishon, Thursday is chamishi,
        # Friday is shishi, Saturday is shabbat.
        if "thu" in text or "thursday" in text or "חמישי" in text:
            dt = thu_eve
        elif "fri" in text or "friday" in text or "שישי" in text:
            dt = fri
        elif "sat" in text or "saturday" in text or "שבת" in text:
            dt = sat
        elif "sun" in text or "sunday" in text or "ראשון" in text:
            dt = sun
        else:
            return ""

    # Prefer parsing the actual start hour over substring heuristics:
    # naive '20:'/'am'/'ערב' matching misclassifies e.g. noon ("12:00 pm").
    m = re.search(r"(\d{1,2}):(\d{2})\s*(am|pm)", text)
    if m:
        h = int(m.group(1)) % 12
        if m.group(3) == "pm":
            h += 12
        is_evening = h >= 17 or h < 2   # 5pm-2am counts as evening
        is_morning = 6 <= h < 12
        ev.sort_dt = dt.replace(hour=h, minute=int(m.group(2)), tzinfo=None)
    else:
        clock = re.search(r"\b(\d{1,2}):\d{2}\b", text)
        if clock:
            hour = int(clock.group(1))
            is_evening = hour >= 17 or hour < 2
            is_morning = 6 <= hour < 12
            if hour < 24:
                ev.sort_dt = dt.replace(hour=hour, minute=int(clock.group(0).split(":")[1]), tzinfo=None)
        else:
            is_evening = any(k in text for k in ["night", "eve", "evening", "ערב", "לילה"])
            is_morning = any(k in text for k in ["morning", "בוקר"])

    if dt.date() == thu_eve.date():
        return "eve_thu" if is_evening else ""
    if dt.date() == fri.date():
        if is_morning:
            return "morning_fri"
        if is_evening:
            return "eve_fri"
        return "day_fri"
    if dt.date() == sat.date():
        if is_morning:
            return "morning_sat"
        if is_evening:
            return "eve_sat"
        return "day_sat"
    if dt.date() == sun.date():
        return ""
    return ""


# ---------------------------------------------------------------------------
# Scrapers
# ---------------------------------------------------------------------------

def scrape_secrettelaviv(thu_eve, fri, sat, sun) -> list[Event]:
    events: list[Event] = []
    html = _get("https://www.secrettelaviv.com/tickets/")
    if not html:
        return events
    soup = BeautifulSoup(html, "html.parser")

    rows = soup.select("table tr")
    for row in rows:
        cells = row.find_all("td")
        if len(cells) < 3:
            continue
        date_cell = ""
        event_cell = None
        price_cell = ""
        for cell in cells:
            ct = _clean(cell.get_text(" ", strip=True))
            if re.search(r"\d{1,2}/\d{1,2}/\d{4}", ct):
                date_cell = ct
            elif cell.find("a") and not event_cell:
                event_cell = cell
            elif not price_cell and re.search(r"NIS|₪|\$|shekels?\b|free", ct, re.IGNORECASE):
                price_cell = ct

        if not event_cell:
            event_cell = max(cells, key=lambda c: len(c.get_text(" ", strip=True)), default=None)
        if not event_cell:
            continue

        title_el = event_cell.find("a")
        if not title_el:
            continue
        title = _clean(title_el.get_text(" ", strip=True))
        if not title or len(title) < 5 or title.lower() in _JUNK_LINK_TITLES:
            continue
        link = title_el.get("href", "")
        if link.startswith("/"):
            link = "https://www.secrettelaviv.com" + link
        where = _extract_venue(title) or _extract_venue(_clean(event_cell.get_text(" ", strip=True))) or "Tel Aviv"
        cell_text = _clean(event_cell.get_text(" ", strip=True))
        desc = _extract_description(cell_text, title)
        price = price_cell or _extract_price(cell_text) or "see listing"
        dt = _parse_dt(date_cell)

        ev = Event(
            title=title, when=date_cell, where=where, category=_categorize(title + " " + where),
            price=price, link=link, source="Secret Tel Aviv",
            description=desc, sort_dt=dt,
        )
        ev.day_part = _assign_day_part(ev, thu_eve, fri, sat, sun)
        if ev.day_part and not _is_excluded(ev) and _is_included(ev):
            events.append(ev)
    return events


def scrape_secrettelaviv_parties(thu_eve, fri, sat, sun) -> list[Event]:
    events: list[Event] = []
    html = _get("https://www.secrettelaviv.com/tickets/categories/parties/")
    if not html:
        return events
    soup = BeautifulSoup(html, "html.parser")
    for a in soup.select("a[href*='/tickets/']"):
        title = _clean(a.get_text(" ", strip=True))
        if not title or len(title) < 5 or title.lower() in _JUNK_LINK_TITLES:
            continue
        link = a.get("href", "")
        if link.startswith("/"):
            link = "https://www.secrettelaviv.com" + link
        parent = a.find_parent("tr") or a.find_parent("div") or a
        cell_text = _clean(parent.get_text(" ", strip=True))
        dt = _parse_dt(cell_text)
        where = _extract_venue(title) or _extract_venue(cell_text) or "Tel Aviv"
        desc = _extract_description(cell_text, title)
        price = _extract_price(cell_text) or "see listing"
        ev = Event(
            title=title, when=_extract_time(cell_text), where=where, category="party",
            price=price, link=link, source="Secret Tel Aviv Parties",
            description=desc, sort_dt=dt,
        )
        ev.day_part = _assign_day_part(ev, thu_eve, fri, sat, sun)
        if ev.day_part and not _is_excluded(ev) and _is_included(ev):
            events.append(ev)
    return events


def scrape_secrettelaviv_livemusic(thu_eve, fri, sat, sun) -> list[Event]:
    events: list[Event] = []
    html = _get("https://www.secrettelaviv.com/tickets/categories/live-music/")
    if not html:
        return events
    soup = BeautifulSoup(html, "html.parser")
    for a in soup.select("a[href*='/tickets/']"):
        title = _clean(a.get_text(" ", strip=True))
        if not title or len(title) < 5 or title.lower() in _JUNK_LINK_TITLES:
            continue
        link = a.get("href", "")
        if link.startswith("/"):
            link = "https://www.secrettelaviv.com" + link
        parent = a.find_parent("tr") or a.find_parent("div") or a
        cell_text = _clean(parent.get_text(" ", strip=True))
        dt = _parse_dt(cell_text)
        where = _extract_venue(title) or _extract_venue(cell_text) or "Tel Aviv"
        desc = _extract_description(cell_text, title)
        price = _extract_price(cell_text) or "see listing"
        ev = Event(
            title=title, when=_extract_time(cell_text), where=where, category="show",
            price=price, link=link, source="Secret Tel Aviv Live Music",
            description=desc, sort_dt=dt,
        )
        ev.day_part = _assign_day_part(ev, thu_eve, fri, sat, sun)
        if ev.day_part and not _is_excluded(ev) and _is_included(ev):
            events.append(ev)
    return events


def scrape_diy(thu_eve, fri, sat, sun) -> list[Event]:
    events: list[Event] = []
    html = _get("https://www.diytelavivguide.com/blog/parties-in-tel-aviv-tonight")
    if not html:
        return events
    soup = BeautifulSoup(html, "html.parser")
    for anchor in soup.select("a[href]"):
        parent = anchor.find_parent("li") or anchor.find_parent("p") or anchor
        text = _clean(parent.get_text(" ", strip=True))
        dt = _parse_dt(text)
        if not dt:
            continue
        ev = Event(title=_clean(anchor.get_text(" ", strip=True)), when=_extract_time(text),
                   where="Tel Aviv", category=_categorize(text), price=_extract_price(text) or "see listing",
                   link=urljoin("https://www.diytelavivguide.com/", anchor["href"]),
                   source="DIY Tel Aviv", sort_dt=dt)
        ev.day_part = _assign_day_part(ev, thu_eve, fri, sat, sun)
        if ev.day_part and not _is_excluded(ev) and _is_included(ev):
            events.append(ev)
    return events


def scrape_mevalim(thu_eve, fri, sat, sun) -> list[Event]:
    """Scrape mevalim.co.il — Hebrew event site with prices."""
    events: list[Event] = []
    html = _get("https://www.mevalim.co.il/tel-aviv/")
    if not html:
        return events
    soup = BeautifulSoup(html, "html.parser")

    # Parse event cards from the page
    # mevalim uses links with event info in text
    for link_el in soup.select("a[href*='tickets.mevalim.co.il/event/']"):
        parent = link_el.find_parent("div") or link_el.find_parent("td") or link_el
        text = _clean(parent.get_text(" ", strip=True))
        if not text or len(text) < 5:
            continue

        # Extract title from the link
        title = _clean(link_el.get_text(" ", strip=True))
        if not title or len(title) < 3 or title.lower() in _JUNK_LINK_TITLES:
            # Try to get title from parent
            title = _clean(parent.get_text(" ", strip=True))[:60]
        if not title or title.lower() in _JUNK_LINK_TITLES:
            continue

        # Extract price
        price = _extract_price(text)
        if not price:
            price = "see listing"

        # Extract date/time
        when = _extract_time(text)
        if not when or when == text[:60]:
            # Try to find date pattern
            m = re.search(r"(\d{1,2}\.\d{1,2})", text)
            if m:
                when = m.group(1)

        # Extract venue/location
        where = "Tel Aviv"
        # Look for venue name in text (after the event name)
        parts = text.split(title, 1)
        if len(parts) > 1:
            rest = parts[1].strip()
            # Remove price and date from rest
            rest = re.sub(r"~~\d+₪~~\s*\d+₪", "", rest)
            rest = re.sub(r"\d+₪", "", rest)
            rest = re.sub(r"\d{1,2}\.\d{1,2}", "", rest)
            rest = _clean(rest)
            if rest and len(rest) > 2 and len(rest) < 60:
                where = rest

        dt = _parse_dt(when)
        link = link_el.get("href", "")
        if link.startswith("/"):
            link = "https://www.mevalim.co.il" + link

        # Check if event is excluded
        ev = Event(
            title=title, when=when, where=where, category=_categorize(title + " " + where),
            price=price, link=link, source="Mevalim (Hebrew)",
            description="", sort_dt=dt,
        )
        ev.day_part = _assign_day_part(ev, thu_eve, fri, sat, sun)
        if ev.day_part and not _is_excluded(ev) and _is_included(ev):
            events.append(ev)

    # Also parse table rows (events with details)
    for table in soup.select("table"):
        rows = table.select("tr")
        for row in rows:
            cells = row.find_all("td")
            if len(cells) < 2:
                continue
            text = _clean(row.get_text(" ", strip=True))
            if not text or len(text) < 10:
                continue

            # Extract title from link in row
            link_el = row.find("a")
            if not link_el:
                continue
            title = _clean(link_el.get_text(" ", strip=True))
            if not title or len(title) < 3 or title.lower() in _JUNK_LINK_TITLES:
                continue

            link = link_el.get("href", "")
            if link.startswith("/"):
                link = "https://www.mevalim.co.il" + link

            price = _extract_price(text)
            if not price:
                price = "see listing"
            when = _extract_time(text)
            dt = _parse_dt(when)
            where = "Tel Aviv"

            # Try to find venue in text
            # Remove title, price, date from text to get venue
            rest = text.replace(title, "", 1)
            rest = re.sub(r"~~\d+₪~~\s*\d+₪", "", rest)
            rest = re.sub(r"\d+₪", "", rest)
            rest = re.sub(r"\d{1,2}\.\d{1,2}", "", rest)
            rest = _clean(rest)
            if rest and len(rest) > 2 and len(rest) < 60:
                where = rest

            ev = Event(
                title=title, when=when, where=where, category=_categorize(title + " " + where),
                price=price, link=link, source="Mevalim (Hebrew)",
                description="", sort_dt=dt,
            )
            ev.day_part = _assign_day_part(ev, thu_eve, fri, sat, sun)
            if ev.day_part and not _is_excluded(ev) and _is_included(ev):
                events.append(ev)

    return events


def scrape_zygo(thu_eve, fri, sat, sun) -> list[Event]:
    """Scrape zygo.co.il — Hebrew party/event site with prices."""
    events: list[Event] = []
    html = _get("https://zygo.co.il/tel-aviv/")
    if not html:
        return events
    soup = BeautifulSoup(html, "html.parser")

    # zygo shows event cards
    for card in soup.select("a[href^='/event/']"):
        title_el = card.find(["h2", "h3", "h4", "a", "span"])
        if not title_el:
            continue
        title = _clean(title_el.get_text(" ", strip=True))
        if not title or len(title) < 3 or title.lower() in _JUNK_LINK_TITLES:
            continue

        link_el = card
        link = link_el.get("href", "") if link_el else ""
        if link.startswith("/"):
            link = "https://zygo.co.il" + link

        text = _clean(card.get_text(" ", strip=True))
        price = _extract_price(text)
        if not price:
            price = "see listing"
        when = _extract_time(text)
        dt = _parse_dt(when)
        where = "Tel Aviv"

        # Try to find venue in text
        parts = text.split(title, 1)
        if len(parts) > 1:
            rest = parts[1].strip()
            rest = re.sub(r"~~\d+₪~~\s*\d+₪", "", rest)
            rest = re.sub(r"\d+₪", "", rest)
            rest = re.sub(r"\d{1,2}\.\d{1,2}", "", rest)
            rest = _clean(rest)
            if rest and len(rest) > 2 and len(rest) < 60:
                where = rest

        ev = Event(
            title=title, when=when, where=where, category="party",
            price=price, link=link, source="Zygo (Hebrew)",
            description="", sort_dt=dt,
        )
        ev.day_part = _assign_day_part(ev, thu_eve, fri, sat, sun)
        if ev.day_part and not _is_excluded(ev):
            events.append(ev)

    return events


def scrape_municipal(thu_eve, fri, sat, sun) -> list[Event]:
    events = scrape_municipal_feed(thu_eve, fri, sat, sun)
    if events:
        return events
    events: list[Event] = []
    html = _get("https://visit.tel-aviv.gov.il/see-do/whats-on")
    if not html:
        return events
    soup = BeautifulSoup(html, "html.parser")
    for card in soup.select(".event, .event-card, article, .whatson-item"):
        title_el = card.find(["h1", "h2", "h3", "h4", "a"])
        if not title_el:
            continue
        title = _clean(title_el.get_text(" ", strip=True))
        if not title or len(title) < 5 or title.lower() in _JUNK_LINK_TITLES:
            continue
        link = title_el.get("href", "") if title_el.name == "a" else ""
        if link.startswith("/"):
            link = "https://visit.tel-aviv.gov.il" + link
        where_el = card.select_one(".location, .address, .place")
        where = _clean(where_el.get_text(" ", strip=True)) if where_el else "Tel Aviv"
        text = _clean(card.get_text(" ", strip=True))
        dt = _parse_dt(text)
        desc = _extract_description(text, title)
        price = _extract_price(text) or "see listing"
        ev = Event(
            title=title, when=_extract_time(text), where=where, category="free-municipal",
            price=price, link=link, source="Tel Aviv Municipality",
            description=desc or "Free municipal activity", sort_dt=dt,
        )
        ev.day_part = _assign_day_part(ev, thu_eve, fri, sat, sun)
        if ev.day_part and not _is_excluded(ev) and _is_included(ev):
            events.append(ev)
    return events


def scrape_municipal_feed(thu_eve, fri, sat, sun) -> list[Event]:
    """Read the same public free-event feed used by the city calendar."""
    payload = dict(srch_ctAll=['סוג תוכן לאירועים', 'סוג תוכן אירוע WITH'],
                   srch_orderByFields=['TlvOrder', 'TlvStartDate'], srch_audID=[], srch_locID=[],
                   srch_crmIntsID=[], srch_toIntsID=[], srch_digiOnly=False, srch_free=True,
                   srch_tickets=False, srch_dateStart=f'{thu_eve:%Y-%m-%d}', srch_dateEnd=f'{sun:%Y-%m-%d}',
                   srch_txt='', srch_Omp='', srch_radius=500, srch_myLat=0, srch_myLon=0,
                   srch_iccID='-1', srch_AcsID=[], srch_specialEvID=[],
                   srch_exceptions=['סוג תוכן להטבות', 'סוג תוכן הטבות עם רישום', 'סוג תוכן הטבות עם בתשלום',
                                    'סוג תוכן הטבות עסקיות', 'TlvInterestsID:x17o55x'],
                   srch_totalItemsLoad=3000, srch_paramsStrForCache=f'weekend-free-{thu_eve:%Y%m%d}',
                   srch_showBoundBenefits=False, srch_showInLobby=False, srch_delivery=False,
                   srch_morning=False, srch_noon=False, srch_evening=False, srch_lstTktCtgr='1,8',
                   srch_isBusiness=False, srch_bsCtgry=[], srch_BenChldDS=None)
    try:
        response = requests.post('https://www.tel-aviv.gov.il/_vti_bin/TlvSP2013PublicSite/TlvListUtils.svc/GetEvBenList',
                                 json=payload, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        rows = response.json()
    except (requests.RequestException, ValueError) as exc:
        logger.warning('Municipal public feed unavailable: %s', exc)
        return []
    events = []
    for row in rows if isinstance(rows, list) else []:
        fields = {f['InternalName']: f.get('Value') or '' for f in row.get('Fields', [])}
        when = fields.get('TlvStartDate', '')
        dt = _parse_dt(when)
        # Recurring ranges need occurrence evidence; do not invent a weekend date.
        if not dt or not thu_eve.date() <= dt.date() < sun.date():
            continue
        link = ('https://www.tel-aviv.gov.il/Pages/MainItemPage.aspx?'
                f"WebID={fields.get('WebID', '')}&ListID={fields.get('ListID', '')}&ItemID={fields.get('ListItemID', '')}")
        ev = Event(title=_clean(fields.get('Title', '')), when=when,
                   where=_clean(f"{fields.get('TlvCityLocation', '')}, {fields.get('TlvAddress1', '')}").strip(', '),
                   category='free-municipal', price='Free', link=link, source='Tel Aviv Municipality',
                   description=_clean(fields.get('TlvSummary', ''))[:220], sort_dt=dt)
        ev.day_part = _assign_day_part(ev, thu_eve, fri, sat, sun)
        if ev.day_part and not _is_excluded(ev):
            events.append(ev)
    return events


def scrape_community_daytime(thu_eve, fri, sat, sun) -> list[Event]:
    events: list[Event] = []
    for cat_url in ["https://www.secrettelaviv.com/tickets/categories/shopping/",
                    "https://www.secrettelaviv.com/tickets/categories/meetups/"]:
        html = _get(cat_url)
        if not html:
            continue
        soup = BeautifulSoup(html, "html.parser")
        for a in soup.select("a[href*='/tickets/']"):
            title = _clean(a.get_text(" ", strip=True))
            if not title or len(title) < 5 or title.lower() in _JUNK_LINK_TITLES:
                continue
            link = a.get("href", "")
            if link.startswith("/"):
                link = "https://www.secrettelaviv.com" + link
            parent = a.find_parent("tr") or a.find_parent("div") or a
            cell_text = _clean(parent.get_text(" ", strip=True))
            dt = _parse_dt(cell_text)
            where = _extract_venue(title) or _extract_venue(cell_text) or "Tel Aviv"
            desc = _extract_description(cell_text, title)
            price = _extract_price(cell_text) or "see listing"
            ev = Event(
                title=title, when=_extract_time(cell_text), where=where, category="community",
                price=price, link=link, source="Secret Tel Aviv Community",
                description=desc or "Daytime community event.", sort_dt=dt,
            )
            ev.day_part = _assign_day_part(ev, thu_eve, fri, sat, sun)
            if ev.day_part in ("day_fri", "day_sat", "morning_fri", "morning_sat") \
               and not _is_excluded(ev) and _is_included(ev):
                events.append(ev)
    return events


def scrape_restaurants() -> list[Event]:
    # A restaurant roundup is not an event. Only accept actual Event schema
    # with an explicit date; the aggregator checks its weekend and launch type.
    html = _get('https://debbestfood.com/')
    if not html:
        return []
    events = []
    for data in _structured_events(BeautifulSoup(html, 'html.parser')):
        try:
            dt = datetime.fromisoformat(data['startDate'].replace('Z', '+00:00')).replace(tzinfo=None)
        except (ValueError, TypeError):
            continue
        location = data.get('location', {})
        address = location.get('address', '') if isinstance(location, dict) else str(location)
        if not any(x in str(address).lower() for x in ('tel aviv', 'tel-aviv', 'תל אביב', 'תל-אביב')):
            continue
        events.append(Event(title=_clean(data.get('name', '')), when=f'{dt:%d/%m/%Y %H:%M}',
                            where=_clean(location.get('name', 'Tel Aviv')), category='restaurant',
                            sort_dt=dt, source='Debbest Israel', link=data.get('url', ''),
                            description=_clean(data.get('description', ''))[:220]))
    return events


def _categorize(text: str) -> str:
    t = text.lower()
    if any(k in t for k in ["party", "disco", "rave", "club", "dj", "electronic", "techno", "mesiba", "מסיבה", "טכנו", "טראנס", "דיסקו", "האוס", "רייב"]):
        return "party"
    if any(k in t for k in ["concert", "live", "music", "gig", "show", "theater", "theatre", "comedy", "rock", "הופעה"]):
        return "show"
    if any(k in t for k in ["movie", "cinema", "film", "screening", "סרט"]):
        return "movie"
    if any(k in t for k in ["restaurant", "food", "dinner", "brunch", "bar", "wine"]):
        return "restaurant"
    if any(k in t for k in ["free", "workshop", "tour", "walking", "museum", "gallery", "exhibition", "community", "market", "fair"]):
        return "free-municipal"
    return "show"


def _guide_group(ev: Event) -> str:
    if ev.category == "party":
        return "party"
    if ev.category in ("free-municipal", "community"):
        return "daytime"
    return "show"


def _nightlife_match(ev: Event) -> bool:
    text = f"{ev.title} {ev.description} {ev.genres}".lower()
    return bool(re.search(r'\b(?:techno|disco|rave|horrorave|electronic|(?:psy)?trance|house music|tech house|deep house|afro house)\b', text)
                or any(word in text for word in ('טכנו', 'דיסקו', 'רייב', 'האוס', 'טראנס', 'אלקטרונ')))


def _select_guide(events: list[Event], limit: int = 30) -> list[Event]:
    """Soft source balance plus flexible content targets; never prune for percentages."""
    remaining = [ev for ev in events if ev.day_part]
    extras = [ev for ev in events if not ev.day_part]
    picked = []
    domains, parts, groups = {}, {}, {}
    venues, styles = {}, {}

    def music_styles(ev):
        text = f'{ev.genres} {ev.title} {ev.description}'.lower()
        return [label for label, pattern in (
            ('techno', r'\btechno\b|טכנו'), ('disco', r'\bdisco\b|דיסקו'),
            ('house', r'\bhouse music\b|\b(?:deep|tech) house\b|האוס'),
            ('rave', r'\brave\b|רייב'), ('trance', r'\b(?:psy)?trance\b|טראנס')) if re.search(pattern, text)]

    def venue_key(ev):
        venue = _clean(ev.where).lower()
        # A generic city fallback is not evidence that two events share a venue.
        return venue if venue not in ('', 'tel aviv', 'tel aviv-yafo', 'תל אביב') else ev.link or ev.title

    def rank(ev):
        preferred = (_nightlife_match(ev) if _guide_group(ev) == "party" else
                     "municipal" in ev.source.lower() if _guide_group(ev) == "daytime" else True)
        variety = min((styles.get(style, 0) for style in music_styles(ev)), default=0) if ev.category == 'party' else 0
        evening = ev.day_part.startswith('eve_')
        # Prefer evening starts, with extra room for Friday night. These are
        # selection preferences, never a reason to relabel an afternoon event.
        friday_priority = evening and ev.day_part != 'eve_fri' and parts.get('eve_fri', 0) < min(8, limit)
        return (not evening, friday_priority,
                domains.get(_domain(ev), 0) >= max(1, round(limit * 0.4)), not preferred,
                venues.get(venue_key(ev), 0) >= 2, variety,
                domains.get(_domain(ev), 0), parts.get(ev.day_part, 0), ev.sort_key())

    def take(ev):
        remaining.remove(ev)
        picked.append(ev)
        domains[_domain(ev)] = domains.get(_domain(ev), 0) + 1
        parts[ev.day_part] = parts.get(ev.day_part, 0) + 1
        group = _guide_group(ev)
        groups[group] = groups.get(group, 0) + 1
        venues[venue_key(ev)] = venues.get(venue_key(ev), 0) + 1
        if ev.category == 'party':
            for style in music_styles(ev):
                styles[style] = styles.get(style, 0) + 1

    # Every independent website gets a representative before filling targets.
    for domain in dict.fromkeys(_domain(ev) for ev in remaining):
        if len(picked) == limit:
            break
        take(min((ev for ev in remaining if _domain(ev) == domain), key=rank))
    for group, target in (("party", 20), ("show", 7), ("daytime", 3)):
        while len(picked) < limit and groups.get(group, 0) < target:
            candidates = [ev for ev in remaining if _guide_group(ev) == group]
            if not candidates:
                break
            take(min(candidates, key=rank))
    while remaining and len(picked) < limit:
        take(min(remaining, key=rank))
    return sorted(picked + extras[:max(0, limit-len(picked))], key=lambda ev: ev.sort_key())


def _domain(ev: Event) -> str:
    host = (urlparse(ev.link).hostname or ev.source).lower()
    if host.startswith("www."):
        host = host[4:]
    return "secrettelaviv.com" if "secrettelaviv" in host else host


def _event_key(ev: Event) -> tuple:
    title = ev.title.split('@', 1)[0]
    title = re.sub(r'\s*//\s*\d{1,2}\.\d{1,2}.*$', '', title)
    title = re.sub(r'[^\w]+', ' ', _clean(title).lower()).strip()
    dt = ev.sort_dt or _parse_dt(ev.when)
    return title, dt.date() if dt else ev.day_part


def _same_event(a: Event, b: Event) -> bool:
    a_key, b_key = _event_key(a), _event_key(b)
    if a_key[1] != b_key[1]:
        return False
    if a_key == b_key or (a.link and a.link == b.link):
        return True
    def venue(ev):
        text = ev.where.lower()
        if re.search(r'room\s*48|רום\s*48', f'{ev.title} {text}', re.IGNORECASE):
            return 'room48'
        for key, names in (('gagarin', ('gagarin', 'גגרין')), ('phi', ('phi', 'פאי', 'פיי')),
                           ('ozen', ('ozen', 'אוזן')), ('hive', ('hive', 'הייב')),
                           ('haoman17', ('haoman', 'האומן'))):
            if any(name in text for name in names):
                return key
        return re.sub(r'\W+', '', text)
    # Promoters can publish the same collaboration under different names and
    # slightly different door times. Require venue, date and distinctive name
    # evidence; generic "Halloween"/genre words alone never merge two events.
    if a.category == b.category == 'party' and a.sort_dt and b.sort_dt and venue(a) == venue(b) and abs((a.sort_dt - b.sort_dt).total_seconds()) <= 3600:
        generic = {'the', 'and', 'party', 'festival', 'halloween', 'friday', 'saturday', 'thursday',
                   'music', 'night', 'event', 'live', 'tel', 'aviv', 'tlv', 'gagarin', 'phi', 'garden',
                   'open', 'air', 'mainstream', 'techno', 'disco', 'rave', 'house', 'edition', 'club', 'bar'}
        def names(ev):
            slug = unquote(urlparse(ev.source_link or ev.link).path).split('/')[-1]
            slug = re.sub(r'-[a-f0-9]{8}-[a-f0-9-]{27,}$', '', slug)
            return set(re.findall(r'[a-z]{2,}', f'{_event_key(ev)[0]} {ev.aliases} {slug}'.lower())) - generic
        shared = names(a) & names(b)
        if len(shared) >= 2 or any(len(word) >= 4 for word in shared):
            return True
    # Different-language titles can share artist/organizer names in their
    # descriptions. Require strong overlap plus matching dates and start times.
    if not a.sort_dt or not b.sort_dt or a.sort_dt.time() != b.sort_dt.time():
        return False
    ignored = {'the', 'and', 'party', 'music', 'night', 'event', 'live', 'tel', 'aviv'}
    def tokens(text):
        return set(re.findall(r'[a-z]{2,}', text.lower())) - ignored
    for title, other in ((a_key[0], b), (b_key[0], a)):
        names = tokens(title)
        if len(names) >= 3 and len(names & tokens(f'{other.title} {other.description}')) / len(names) >= 0.8:
            return True
    return False


def _enrich_nightlife(ev: Event) -> None:
    if ev.source in ('Bonire', 'HappyTLV', 'Airdrop'):
        return  # These collectors already read their dated event details.
    html = _get(ev.link) if ev.link.startswith('https://') else None
    if not html:
        return
    soup = BeautifulSoup(html, 'html.parser')
    content = soup.select_one('.em-event-single, .tribe-events-single-event-description, .elementor-widget-theme-post-content, .elementor-widget-text-editor, .entry-content')
    if content:
        text = _clean(content.get_text(' ', strip=True)).lower()
        tags = [label for label, pattern in (
            ('Techno', r'\btechno\b|טכנו'), ('Disco', r'\bdisco\b|דיסקו'),
            ('House', r'\b(?:deep |tech )?house music\b|\b(?:deep|tech) house\b|האוס'),
            ('Rave', r'\brave\b|רייב'), ('Trance', r'\b(?:psy)?trance\b|טראנס'),
            ('Electronic', r'\belectronic\b|אלקטרוני')) if re.search(pattern, text)]
        ev.genres = ' / '.join(tags)
    if ev.price.lower() in ('', 'see listing', 'see venue'):
        ev.price = _page_price(soup) or ev.price


def _page_price(soup: BeautifulSoup) -> str:
    """Read explicit ticket offers before falling back to visible price text."""
    def walk(obj):
        if isinstance(obj, list):
            for item in obj:
                yield from walk(item)
        elif isinstance(obj, dict):
            if obj.get("@type") in ("Offer", "AggregateOffer"):
                price = obj.get("price", obj.get("lowPrice"))
                if price is not None:
                    currency = obj.get("priceCurrency", "")
                    yield f"{currency} {price}".strip()
            for value in obj.values():
                if isinstance(value, (dict, list)):
                    yield from walk(value)
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            price = next(walk(json.loads(script.string or script.get_text())), "")
            if price:
                return price
        except (ValueError, TypeError):
            continue
    for tag in soup.select("del, s, nav, footer, header, script, style"):
        tag.decompose()
    text = soup.get_text(" ", strip=True)
    price = _extract_price(text)
    if price:
        return price
    labelled = re.search(r"(?:מחיר|Ticket price)\s*[:：]?\s*(\d+(?:\.\d{1,2})?)", text, re.IGNORECASE)
    if labelled:
        return f"₪{float(labelled.group(1)):g}"
    return ""


def scrape_levontin(thu_eve, fri, sat, sun) -> list[Event]:
    html = _get("https://levontin7.com/")
    if not html:
        return []
    events = []
    for card in BeautifulSoup(html, "html.parser").select(".fat-event-item"):
        anchor = card.select_one(".fat-event-title a[href]")
        when = card.get("data-time", "")
        if not anchor or not _parse_dt(when):
            continue
        ev = Event(title=_clean(anchor.get_text(" ", strip=True)), when=when,
                   where="Levontin 7, Tel Aviv", category="show", source="Levontin 7",
                   link=urljoin("https://levontin7.com/", anchor["href"]), sort_dt=_parse_dt(when))
        ev.day_part = _assign_day_part(ev, thu_eve, fri, sat, sun)
        if not ev.day_part:
            continue
        detail = _get(ev.link)
        if detail:
            soup = BeautifulSoup(detail, "html.parser")
            heading = soup.select_one("h1, .fat-event-single-title")
            if heading:
                ev.title = _clean(heading.get_text(" ", strip=True))
            elif soup.title:
                ev.title = _clean(soup.title.get_text().split(" – Levontin")[0])
            ev.price = _page_price(soup) or "see listing"
        if not _is_excluded(ev):
            events.append(ev)
    return events


def scrape_teder(thu_eve, fri, sat, sun) -> list[Event]:
    html = _get("https://www.teder.fm/")
    if not html:
        return []
    events = []
    seen = set()
    for anchor in BeautifulSoup(html, "html.parser").select("a[href*='/events/']"):
        text = _clean(anchor.get_text(" ", strip=True))
        dt = _parse_dt(text)
        link = urljoin("https://www.teder.fm/", anchor["href"])
        if not dt or link in seen:
            continue
        title = re.split(r"יום |\d{1,2}\.\d{1,2}", text)[0].strip()
        ev = Event(title=title, when=text[len(title):].strip(), where="Teder / Beit Romano, Derech Yafo 9",
                   category=_categorize(title), link=link, source="Teder", sort_dt=dt)
        ev.day_part = _assign_day_part(ev, thu_eve, fri, sat, sun)
        if ev.day_part and not _is_excluded(ev):
            seen.add(link)
            events.append(ev)
    return events


def _structured_events(soup):
    def walk(value):
        if isinstance(value, list):
            for child in value:
                yield from walk(child)
        elif isinstance(value, dict):
            kind = value.get('@type', '')
            if any(str(t).endswith('Event') for t in (kind if isinstance(kind, list) else [kind])) and value.get('startDate'):
                yield value
            for child in value.values():
                if isinstance(child, (dict, list)):
                    yield from walk(child)
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            yield from walk(json.loads(script.get_text()))
        except (ValueError, TypeError):
            continue


def scrape_gagarin(thu_eve, fri, sat, sun) -> list[Event]:
    html = _get('https://gagarin.co.il/')
    if not html:
        return []
    soup = BeautifulSoup(html, 'html.parser')
    # Upcoming calendar cards come first; cap detail requests and skip archives.
    links = list(dict.fromkeys(a['href'] for a in soup.select('a.bdt-event-title[href]')))[:6]
    events = []
    for link in links:
        detail = _get(link)
        if not detail:
            continue
        page = BeautifulSoup(detail, 'html.parser')
        for data in _structured_events(page):
            try:
                dt = datetime.fromisoformat(data['startDate'].replace('Z', '+00:00')).replace(tzinfo=None)
            except (ValueError, TypeError):
                continue
            if not thu_eve.date() <= dt.date() < sun.date():
                continue
            content = page.select_one('.tribe-events-single-event-description')
            description = _clean(BeautifulSoup(data.get('description', ''), 'html.parser').get_text(' ', strip=True))
            text = _clean(content.get_text(' ', strip=True)) if content else description
            title = _clean(data.get('name', ''))
            category = 'party' if _nightlife_match(Event(title, '', '', '', description=text)) or any(x in title.lower() for x in ['blackout', 'festival']) else 'show'
            ev = Event(title=title, when=f'{dt:%d/%m/%Y %H:%M}', where='Gagarin, Kibbutz Galuyot 13, Tel Aviv',
                       category=category, link=link, source='Gagarin', description=description[:220], sort_dt=dt)
            ev.price = _extract_price(text) or 'see listing'
            ev.day_part = _assign_day_part(ev, thu_eve, fri, sat, sun)
            if ev.day_part and not _is_excluded(ev):
                events.append(ev)
    return events


def fetch_weekend_events(thu_eve, fri, sat, sun) -> list[Event]:
    all_events: list[Event] = []

    scrapers = [
        scrape_secrettelaviv,
        scrape_secrettelaviv_parties,
        scrape_secrettelaviv_livemusic,
        scrape_diy,
        scrape_mevalim,
        scrape_zygo,
        scrape_municipal,
        scrape_community_daytime,
        scrape_levontin,
        scrape_teder,
        scrape_gagarin,
        scrape_bonire,
        scrape_happytlv,
        scrape_airdrop,
    ]
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [(scrape, pool.submit(scrape, thu_eve, fri, sat, sun)) for scrape in scrapers]
        for scrape, future in futures:
            try:
                found = future.result()
                logger.info("%s: %d matching events", getattr(scrape, "__name__", "collector"), len(found))
                all_events.extend(found)
            except Exception as exc:  # noqa: BLE001
                logger.warning("%s error: %s", getattr(scrape, "__name__", "collector"), exc)

    try:
        all_events.extend(scrape_restaurants())
    except Exception as exc:  # noqa: BLE001
        logger.warning("scrape_restaurants error: %s", exc)
    for ev in all_events:
        if ev.category == 'restaurant' and ev.sort_dt:
            ev.day_part = _assign_day_part(ev, thu_eve, fri, sat, sun)

    # Read party details before ranking, so genre preferences use actual music
    # descriptions rather than guessing from a Halloween title or venue name.
    parties = list({ev.link: ev for ev in all_events if ev.category == 'party' and ev.link}.values())[:45]
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(_enrich_nightlife, parties))
    party_details = {ev.link: ev for ev in parties}
    for ev in all_events:
        if ev.category == 'party' and ev.link in party_details:
            detail = party_details[ev.link]
            ev.genres, ev.price = detail.genres, detail.price

    all_events = [ev for ev in all_events if _fits_audience(ev)]

    # Prefer a direct/independent source over another aggregator copy.
    all_events.sort(key=lambda ev: (0 if ev.source in ('Teder', 'Gagarin', 'Levontin 7', 'Tel Aviv Municipality') else
                                    2 if _domain(ev) == 'secrettelaviv.com' else 1, ev.sort_key()))
    unique: list[Event] = []
    for ev in all_events:
        kept = next((item for item in unique if _same_event(item, ev)), None)
        if kept:
            if kept and kept.price in ("", "see listing", "see venue") and ev.price not in ("", "see listing", "see venue"):
                kept.price = ev.price
            kept.verified = kept.verified or _domain(kept) != _domain(ev)
            kept.aliases = f'{kept.aliases} {ev.title}'.strip()
            continue
        ev.title = _clean(ev.title)
        ev.when = _clean(ev.when)
        ev.where = _clean(ev.where)
        ev.price = _clean(ev.price)
        ev.description = _clean(ev.description)
        ev.source = _clean(ev.source)
        if ev.title and ev.title.lower() != "read more":
            unique.append(ev)

    unique = _select_guide(unique)

    # Fetch missing prices from each selected event page, with a bounded budget.
    def enrich_price(ev):
        if ev.source not in ('Bonire', 'HappyTLV', 'Airdrop') and ev.link not in party_details and ev.price.lower() in ("", "see listing", "see venue", "see venue page") and ev.link.startswith("https://"):
            html = _get(ev.link)
            if html:
                ev.price = _page_price(BeautifulSoup(html, "html.parser")) or ev.price
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(enrich_price, [ev for ev in unique[:30] if ev.day_part]))
    return unique[:30]


def scrape_bonire(*window):
    from .nightlife_sources import scrape_bonire as collect
    return collect(*window)


def scrape_happytlv(*window):
    from .nightlife_sources import scrape_happytlv as collect
    return collect(*window)


def scrape_airdrop(*window):
    from .nightlife_sources import scrape_airdrop as collect
    return collect(*window)
