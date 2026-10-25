import unittest
from datetime import datetime
from unittest.mock import patch
from agent.scraper import Event, _fits_audience, _is_excluded, scrape_restaurants
from agent.nightlife_sources import parse_bonire_cards, _bonire_date, scrape_happytlv, scrape_airdrop
from agent.nightlife_sources import _organizer_link, _genres
from agent.scraper import _same_event
from bs4 import BeautifulSoup
from tests.test_agent import THU_EVE, FRI, SAT, SUN

WINDOW = (THU_EVE, FRI, SAT, SUN)


class NightlifeAudienceTests(unittest.TestCase):
    def test_entrance_and_discovery_are_not_music_genres(self):
        text = 'Entrance by guest list. Discover the party.'
        self.assertEqual(_genres(text), '')
        self.assertFalse(_fits_audience(Event('Mainstream night', '', 'Cappella', 'party', description=text)))
        self.assertFalse(_fits_audience(Event('Mainstream Friday', '', 'Unknown club', 'party', genres='Trance', description='Related DJs play trance')))
        self.assertFalse(_fits_audience(Event('אלכוהול חופשי בהאלווין', '', 'Unknown club', 'party', genres='Electronic')))
        self.assertFalse(_fits_audience(Event('Halloween', '', 'Unknown club', 'party', genres='Electronic')))

    def test_event_link_does_not_lead_to_whatsapp_promo_or_shortener(self):
        soup = BeautifulSoup('<a href="https://chat.whatsapp.com/group">Join</a><a href="https://tinyurl.com/broken">Table</a><a href="https://go-out.co/event/123">Tickets</a>', 'html.parser')
        self.assertEqual(_organizer_link(soup, 'https://bonire.live/event/1'), 'https://go-out.co/event/123')

    def test_promoter_aliases_do_not_duplicate_one_festival(self):
        official = Event('Decision Festival', '', 'Gagarin, Tel Aviv', 'party', sort_dt=datetime(2026,10,29,23))
        promoter = Event('Decision x Colorful', '', 'גגרין', 'party', sort_dt=datetime(2026,10,29,22,30))
        self.assertTrue(_same_event(official, promoter))
        official.aliases = promoter.title
        self.assertTrue(_same_event(official, Event('Colorful Festival', '', 'Gagarin', 'party', sort_dt=datetime(2026,10,29,23))))
        self.assertFalse(_same_event(official, Event('Different Halloween Festival', '', 'Gagarin', 'party', sort_dt=datetime(2026,10,29,23))))
        first = Event('DALI ב-ROOM 48', '', 'ROOM 48', 'party', sort_dt=datetime(2026,10,30,23,59))
        second = Event('ROOM 48 · DALI', '', 'Ozen', 'party', sort_dt=datetime(2026,10,30,23,59))
        self.assertTrue(_same_event(first, second))

    def test_free_municipal_does_not_imply_relevant(self):
        for title in ('הרקדה שבועית בחוף גורדון', 'צועדים עם קאנטרי ארלוזורוב 97', 'Generic free activity'):
            self.assertFalse(_fits_audience(Event(title, 'Friday', 'Tel Aviv', 'free-municipal', price='Free')))
        self.assertTrue(_fits_audience(Event('סאונד סיסטם תחת כיפת השמיים', 'Friday', 'פארק התחנה', 'free-municipal', price='Free')))
        self.assertTrue(_fits_audience(Event('הופעה של אמנים צעירים', 'Friday', 'Tel Aviv', 'free-municipal', price='Free')))

    def test_local_bar_and_genres_not_general_mainstream(self):
        for ev in (Event('Techno D&B', '', 'FOMA', 'party'), Event('Disco night', '', 'New venue', 'party'),
                   Event('Horrorave', '', 'Haoman 17', 'party'), Event('DJ night', '', 'Nuweiba', 'party')):
            self.assertTrue(_fits_audience(ev))
        self.assertFalse(_fits_audience(Event('Mainstream reggaeton party', '', 'Unknown club', 'party')))

    def test_dj_name_not_mistaken_for_children(self):
        self.assertFalse(_is_excluded(Event('Kidnappa DJ set', '', 'Rafi', 'party')))

    def test_restaurant_requires_actual_dated_event(self):
        self.assertFalse(_fits_audience(Event('New restaurants roundup', '', 'Tel Aviv', 'restaurant')))
        self.assertTrue(_fits_audience(Event('Opening party', '', 'Restaurant', 'restaurant', sort_dt=FRI, day_part='eve_fri')))
        with patch('agent.scraper._get', return_value='<html>Monthly restaurant roundup</html>'):
            self.assertEqual(scrape_restaurants(), [])

    def test_bonire_weekend_and_start_time(self):
        html = '''<a href="/he/event/friday-techno"><h3>Techno D&amp;B</h3><p>יום ו׳, 30 באוק׳ · 23:00</p><p>FOMA</p></a>
                  <a href="/he/event/future"><h3>Future rave</h3><p>יום ו׳, 6 בנוב׳ · 23:00</p><p>FOMA</p></a>'''
        events = parse_bonire_cards(html, WINDOW)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].day_part, 'eve_fri')
        self.assertEqual(events[0].sort_dt, datetime(2026, 10, 30, 23))
        self.assertEqual(events[0].source_link, 'https://bonire.live/he/event/friday-techno')
        self.assertEqual(_bonire_date('יום ו׳, 1 בינו׳', datetime(2026, 12, 31)), datetime(2027, 1, 1))

    def test_happy_tlv_keeps_direct_tickets_and_date(self):
        html = '''<section id="d-2026-10-30"><li class="en-item"><span class="t">Night of Veles</span>
                  <span class="m">19:00 · Menachem Begin Rd 37 · disco</span>
                  <a class="en-go" href="https://selector.org.il/e/123">Tickets from ₪80</a>
                  <a class="en-link" href="/tel-aviv-parties/callas-2026-10-30">Details</a></li></section>'''
        with patch('agent.nightlife_sources._get', return_value=html):
            events = scrape_happytlv(*WINDOW)
        self.assertEqual(events[0].link, 'https://selector.org.il/e/123')
        self.assertEqual(events[0].genres, 'Disco')
        self.assertEqual(events[0].day_part, 'eve_fri')

    def test_airdrop_rejects_out_of_city_and_future_listings(self):
        import json
        def data(city, date, name):
            return {'@type':'MusicEvent', 'name':name, 'startDate':date,
                    'description':'Techno', 'location':{'name':'Club', 'address':{'addressLocality':city}},
                    'offers':{'url':'https://go-out.co/event/123','price':107,'priceCurrency':'ILS'}}
        html = '<script type="application/ld+json">' + json.dumps([
            data('תל אביב', '2026-10-30T23:59:00+02:00', 'Dali'),
            data('חיפה', '2026-10-30T23:59:00+02:00', 'Haifa'),
            data('תל אביב', '2026-11-06T23:59:00+02:00', 'Next week')]) + '</script>'
        with patch('agent.nightlife_sources._get', return_value=html):
            events = scrape_airdrop(*WINDOW)
        self.assertEqual([e.title for e in events], ['Dali'])
        self.assertEqual(events[0].price, 'From ILS 107')
