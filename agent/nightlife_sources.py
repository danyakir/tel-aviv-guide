"""Independent nightlife discovery feeds; dates and links come from live cards."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from .scraper import (Event, _get, _clean, _parse_dt, _assign_day_part,
                      _extract_price, _structured_events, _fits_audience, _local_scene)


def _genres(text):
    text = text.lower()
    return ' / '.join(label for label, pattern in (
        ('Techno', r'\btechno\b|טכנו'), ('Disco', r'\bdisco\b|דיסקו'), ('Rave', r'\b(?:rave|horrorave)\b|רייב'),
        ('House', r'\b(?:deep|tech|afro) house\b|house music|האוס'),
        ('Trance', r'\b(?:psy)?trance\b|טראנס'), ('Electronic', r'\belectronic\b|אלקטרוני')) if re.search(pattern, text))


def _organizer_link(soup, fallback):
    candidates = []
    ticket_hosts = ('go-out.co', 'eventer.co.il', 'partiq.co', 'selector.org.il', 'chillz.com', 'easytix.co.il', 'eventbuzz.co.il')
    for a in soup.select('a[href]'):
        link = a['href']
        parsed = urlparse(link)
        host = parsed.hostname or ''
        if not link.startswith('https://') or not host or host.endswith('bonire.live'):
            continue
        if any(x in host for x in ('whatsapp', 'wa.me', 'tinyurl', 'bit.ly', 'google', 'maps', 'twitter', 'facebook')):
            continue
        if 'instagram.com' in host and not parsed.path.startswith(('/p/', '/reel/')):
            continue
        priority = 0 if any(host.endswith(x) for x in ticket_hosts) else 2 if 'instagram.com' in host else 1
        candidates.append((priority, link))
    return min(candidates, key=lambda item: item[0])[1] if candidates else fallback


def _bonire_date(text, reference):
    month_names = ('ינו', 'פבר', 'מרץ', 'אפר', 'מאי', 'יונ', 'יול', 'אוג', 'ספט', 'אוק', 'נוב', 'דצמ')
    match = re.search(r'(\d{1,2})\s+ב([א-ת]+)', text)
    if not match:
        return None
    month = next((i for i, name in enumerate(month_names, 1) if match[2].startswith(name)), None)
    return _parse_dt(f'{match[1]}.{month}', reference) if month else None


def parse_bonire_cards(html, window):
    thu, fri, sat, sun = window
    events = []
    for card in BeautifulSoup(html, 'html.parser').select('a[href*="/event/"]'):
        heading, paragraphs = card.find('h3'), card.find_all('p')
        if not heading or len(paragraphs) < 2:
            continue
        date_text = _clean(paragraphs[0].get_text(' ', strip=True))
        dt = _bonire_date(date_text, thu)
        clock = re.search(r'\b(\d{1,2}):(\d{2})\b', date_text)
        if not dt or not clock or not thu.date() <= dt.date() < sun.date():
            continue
        dt = dt.replace(hour=int(clock[1]), minute=int(clock[2]))
        listing = urljoin('https://bonire.live/', card['href'])
        ev = Event(title=_clean(heading.get_text(' ', strip=True)), when=f'{dt:%d/%m/%Y %H:%M}',
                   where=_clean(paragraphs[1].get_text(' ', strip=True)), category='party',
                   source='Bonire', link=listing, source_link=listing, sort_dt=dt,
                   price=_extract_price(card.get_text(' ', strip=True)) or 'see listing')
        ev.genres = _genres(ev.title)
        ev.day_part = _assign_day_part(ev, *window)
        if ev.day_part:
            events.append(ev)
    return events


def scrape_bonire(*window):
    html = _get('https://bonire.live/?category=parties&city=tel-aviv-yafo')
    if not html:
        return []
    events = parse_bonire_cards(html, window)
    # Friday and local alternative venues get detail coverage first, rather
    # than allowing Thursday's listing order to consume the entire budget.
    events.sort(key=lambda e: (e.day_part != 'eve_fri', not _local_scene(e), e.sort_key()))
    def detail(ev):
        html = _get(ev.source_link)
        if not html:
            return ev
        soup = BeautifulSoup(html, 'html.parser')
        for tag in soup.select('script, style, nav, header, footer'):
            tag.decompose()
        text = _clean(soup.get_text(' ', strip=True))
        description = text.split("ז'אנרים", 1)[-1] if "ז'אנרים" in text else text.split('פרטים:', 1)[-1]
        if 'אזל מהמלאי' in description or 'sold out' in description.lower():
            return None
        ev.genres = _genres(description + ' ' + ev.title)
        # The event detail exposes the original organizer/ticket link.
        ev.link = _organizer_link(soup, ev.source_link)
        # The labelled price block carries ranges; later promotional copy may
        # mention unrelated VIP/door amounts.
        price_block = description.split('יש לבדוק מחיר עדכני', 1)[0]
        ev.price = _extract_price(price_block) or ev.price
        description = re.sub(r'מחיר\s+[\d.,–\-\s₪]+(?:·\s*)?(?:יש לבדוק מחיר עדכני)?', '', description)
        description = re.sub(r'הצטרפו.{0,500}?https?\s*:\s*//\s*\S+', '', description)
        description = re.sub(r'https?\s*:\s*//\s*\S+', '', description)
        ev.description = _clean(description)[:220]
        return ev
    with ThreadPoolExecutor(max_workers=4) as pool:
        events = list(pool.map(detail, events[:45]))
    return [e for e in events if e and _fits_audience(e)]


def scrape_happytlv(*window):
    html = _get('https://www.happytlv.com/tel-aviv-parties-this-week')
    if not html:
        return []
    thu, fri, sat, sun = window
    events = []
    for section in BeautifulSoup(html, 'html.parser').select('section[id^="d-"]'):
        try:
            date = datetime.fromisoformat(section['id'][2:])
        except ValueError:
            continue
        if not thu.date() <= date.date() < sun.date():
            continue
        for card in section.select('li.en-item'):
            title, meta, link = card.select_one('.t'), card.select_one('.m'), card.select_one('a.en-go[href]')
            if not title or not meta or not link:
                continue
            text = _clean(meta.get_text(' ', strip=True))
            if any(x in text.lower() for x in ('haifa', 'petah tikva', 'rishon', 'herzliya', 'נתניה', 'חיפה', 'ראשון לציון')):
                continue
            clock = re.search(r'(\d{1,2}):(\d{2})', text)
            if not clock:
                continue
            dt = date.replace(hour=int(clock[1]), minute=int(clock[2]))
            listing = card.select_one('a.en-link[href]')
            ev = Event(title=_clean(title.get_text(' ', strip=True)), when=f'{dt:%d/%m/%Y %H:%M}',
                       where=text.split('·')[1].strip() if '·' in text else 'Tel Aviv',
                       category='party', source='HappyTLV', link=link['href'], sort_dt=dt,
                       source_link=urljoin('https://www.happytlv.com', listing['href']) if listing else '',
                       description=text, genres=_genres(text), price=_extract_price(card.get_text(' ', strip=True)) or 'see listing')
            ev.day_part = _assign_day_part(ev, *window)
            if ev.day_part and _fits_audience(ev):
                events.append(ev)
    return events


def scrape_airdrop(*window):
    html = _get('https://airdrop.co.il/')
    if not html:
        return []
    thu, fri, sat, sun = window
    events = []
    for data in _structured_events(BeautifulSoup(html, 'html.parser')):
        location = data.get('location', {})
        address = location.get('address', {}) if isinstance(location, dict) else {}
        city = address.get('addressLocality', '') if isinstance(address, dict) else str(address)
        if not any(x in city.lower() for x in ('tel aviv', 'tel-aviv', 'תל אביב', 'תל-אביב')):
            continue
        try:
            dt = datetime.fromisoformat(data['startDate'].replace('Z', '+00:00')).replace(tzinfo=None)
        except (ValueError, TypeError):
            continue
        if not thu.date() <= dt.date() < sun.date():
            continue
        offers = data.get('offers', {})
        if isinstance(offers, list):
            offers = offers[0] if offers else {}
        price = offers.get('price', offers.get('lowPrice'))
        ev = Event(title=_clean(data.get('name', '')), when=f'{dt:%d/%m/%Y %H:%M}',
                   where=f"{location.get('name', '')}, {city}", category='party', source='Airdrop',
                   link=offers.get('url') or data.get('url', ''), source_link=data.get('url', ''), sort_dt=dt,
                   description=_clean(data.get('description', ''))[:350],
                   price=f"From {offers.get('priceCurrency', '')} {price}" if price is not None else 'see listing')
        ev.genres = _genres(ev.title + ' ' + ev.description)
        ev.day_part = _assign_day_part(ev, *window)
        if ev.day_part and _fits_audience(ev):
            events.append(ev)
    return events
