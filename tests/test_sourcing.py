import unittest
from datetime import datetime, timedelta
from unittest.mock import patch
from bs4 import BeautifulSoup
from agent.scraper import Event, _page_price, scrape_teder, scrape_zygo, _select_guide, _guide_group, scrape_municipal_feed, _structured_events, _extract_price, _nightlife_match, _event_key, _enrich_nightlife
from agent.report import build_message
from tests import test_agent as legacy
from tests.test_agent import THU_EVE, FRI, SAT, SUN
from agent.scraper import _parse_dt, _assign_day_part, _same_event
from agent.main import _upcoming_weekend


class SourcingRulesTests(unittest.TestCase):
    _run_with = legacy.FetchWeekendEventsTests._run_with

    def test_evenings_take_priority_over_busy_friday_daytime(self):
        events = [Event(title=f'{part} {i}', when='Listed time', where=f'Venue {i}',
                        category='party', day_part=part, genres='Techno')
                  for part, count in [('day_fri', 25), ('eve_fri', 12), ('eve_sat', 12)]
                  for i in range(count)]
        selected = _select_guide(events)
        from collections import Counter
        counts = Counter(e.day_part for e in selected)
        self.assertEqual(len(selected), 30)
        self.assertEqual(counts['eve_fri'], 12)
        self.assertEqual(counts['eve_sat'], 12)
        self.assertEqual(counts['day_fri'], 6)

    def test_sparse_evenings_fill_from_real_daytime_events(self):
        events = [Event(title=f'{part} {i}', when='Listed time', where='Venue', category='party', day_part=part)
                  for part, count in [('day_fri', 32), ('eve_fri', 3)] for i in range(count)]
        selected = _select_guide(events)
        self.assertEqual(len(selected), 30)
        self.assertEqual(sum(e.day_part == 'eve_fri' for e in selected), 3)

    def test_long_friday_card_does_not_admit_next_week_party(self):
        html = '<a href="/event/future/zygo"><h3>FRIDAY ' + 'Electronic music ' * 8 + '</h3><p>Tel Aviv Port</p><p>06/11/2026 20:30</p></a>'
        with patch('agent.scraper._get', return_value=html):
            events = scrape_zygo(THU_EVE, FRI, SAT, SUN)
        self.assertEqual(events, [])

    def test_variety_applies_to_future_weekends_and_new_venues(self):
        from collections import Counter
        for now in (datetime(2026, 11, 9), datetime(2027, 1, 5), datetime(2028, 5, 2)):
            thu, fri, sat, sun = _upcoming_weekend(now)
            events = []
            for group, count in [('party', 22), ('show', 12), ('community', 12)]:
                for i in range(count):
                    day = fri if i % 2 else sat
                    events.append(Event(title=f'{group} {i}', when=f'{day:%d/%m/%Y} 21:00',
                                        where=f'New venue {i % 8}', category=group,
                                        source=f'Publisher {i % 4}', link=f'https://new-source-{i % 4}.example/{group}/{i}',
                                        sort_dt=day.replace(hour=21), day_part='eve_fri' if i % 2 else 'eve_sat',
                                        genres=('Techno' if i % 2 else 'Disco') if group == 'party' else ''))
            selected = _select_guide(events)
            self.assertEqual(len(selected), 30)
            self.assertEqual(Counter(_guide_group(e) for e in selected), {'party':20, 'show':7, 'daytime':3})
            self.assertEqual(len({e.source for e in selected}), 4)
            self.assertEqual({e.genres for e in selected if e.category == 'party'}, {'Techno', 'Disco'})
            self.assertTrue(all(thu.date() <= e.sort_dt.date() < sun.date() for e in selected))

    def test_yearless_dates_follow_weekend_across_new_year(self):
        thu = datetime(2026, 12, 31)
        fri, sat, sun = (thu + timedelta(days=n) for n in (1, 2, 3))
        self.assertEqual(_parse_dt('01.01 21:00', thu), datetime(2027, 1, 1))
        event = Event('Fresh party', '01.01 21:00', 'New venue', 'party', sort_dt=datetime(2026, 1, 1))
        self.assertEqual(_assign_day_part(event, thu, fri, sat, sun), 'eve_fri')
        self.assertEqual(event.sort_dt, datetime(2027, 1, 1, 21))
        old = Event('Old party', '01.01.26 21:00', 'Venue', 'party', sort_dt=datetime(2026, 1, 1))
        self.assertEqual(_assign_day_part(old, thu, fri, sat, sun), '')

    def test_duplicate_detection_is_not_tied_to_specific_artists(self):
        dt = datetime(2027, 2, 5, 12)
        english = Event('Northern Lights Sound System', '', 'Park', 'party', sort_dt=dt)
        hebrew = Event('מוזיקה בפארק', '', 'Park', 'community', description='Northern Lights Sound System מגיעים לפארק', sort_dt=dt)
        self.assertTrue(_same_event(english, hebrew))
        hebrew.sort_dt += timedelta(days=1)
        self.assertFalse(_same_event(english, hebrew))
    def test_balances_actual_domains(self):
        def events(domain, count):
            return [Event(title=f"{domain} {i}", when="Friday 21:00", where="Tel Aviv",
                          category="party", day_part="eve_fri", source=domain,
                          link=f"https://{domain}/event/{i}") for i in range(count)]
        result = self._run_with(scrape_secrettelaviv=events("secrettelaviv.com", 25),
                                scrape_zygo=events("zygo.co.il", 4),
                                scrape_levontin=events("levontin7.com", 4))
        from collections import Counter
        counts = Counter(e.source for e in result)
        self.assertEqual(len(counts), 3)
        self.assertEqual(len(result), 30, 'Source balance must not shrink a full guide')
        self.assertGreaterEqual(min(counts.values()), 4)

    def test_category_targets_and_techno_priority(self):
        events = []
        for category, count in [('party', 30), ('show', 15), ('free-municipal', 15)]:
            for i in range(count):
                events.append(Event(title=f'{"Techno" if category == "party" and i < 15 else "Event"} {category} {i}',
                                    when='Friday', where='Tel Aviv', category=category, day_part='eve_fri',
                                    source='Tel Aviv Municipality' if category == 'free-municipal' else 'Test'))
        chosen = _select_guide(events)
        from collections import Counter
        self.assertEqual(Counter(_guide_group(e) for e in chosen), {'party': 20, 'show': 7, 'daytime': 3})
        self.assertEqual(sum('Techno' in e.title for e in chosen if e.category == 'party'), 15)

    def test_unused_category_slots_fill_without_fabrication(self):
        events = [Event(title=f'Techno {i}', when='Friday', where='Tel Aviv', category='party', day_part='eve_fri') for i in range(35)]
        self.assertEqual(len(_select_guide(events)), 30)
        self.assertEqual(len(_select_guide(events[:5])), 5)

    def test_house_party_is_not_automatically_house_music(self):
        self.assertFalse(_nightlife_match(Event('Halloween house party', '', '', 'party')))
        self.assertTrue(_nightlife_match(Event('Halloween', '', '', 'party', genres='Techno')))

    def test_music_style_read_from_actual_event_body(self):
        event = Event('Halloween', '', '', 'party', link='https://example.com/event')
        with patch('agent.scraper._get', return_value='<div class="em-event-single"><p>Techno, disco and trance all night.</p></div>'):
            _enrich_nightlife(event)
        self.assertEqual(event.genres, 'Techno / Disco / Trance')
        self.assertTrue(_nightlife_match(event))

    def test_artist_name_and_freedom_are_not_ticket_prices(self):
        self.assertEqual(_extract_price('בשנת 2024 שחרר את האלבום'), '')
        self.assertEqual(_extract_price('A night of freedom'), '')

    def test_venue_suffix_does_not_duplicate_same_dated_event(self):
        self.assertEqual(_event_key(Event('Romano Record Fair @ Teder', '31.10.26', '', 'community')),
                         _event_key(Event('Romano Record Fair', '31.10.26', '', 'community')))

    def test_municipal_feed_filters_children_and_old_ranges(self):
        def row(title, start, summary):
            return {'Fields': [{'InternalName': k, 'Value': v} for k, v in
                              {'Title': title, 'TlvStartDate': start, 'TlvSummary': summary,
                               'WebID': 'web', 'ListID': 'list', 'ListItemID': '123'}.items()]}
        response = unittest.mock.Mock()
        response.json.return_value = [row('Open air music', '30.10.26, 12:00', 'Free music'),
                                      row('ילדים', '30.10.26, 12:00', 'Family activity'),
                                      row('Old range', '17.10.26, 12:00', 'Recurring')]
        with patch('agent.scraper.requests.post', return_value=response):
            events = scrape_municipal_feed(THU_EVE, FRI, SAT, SUN)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].price, 'Free')
        self.assertIn('ItemID=123', events[0].link)

    def test_official_venue_jsonld_has_real_date(self):
        soup = BeautifulSoup('<script type="application/ld+json">{"@graph":[{"@type":"MusicEvent","name":"Techno","startDate":"2026-10-29T23:00:00+02:00"}]}</script>', 'html.parser')
        self.assertEqual(list(_structured_events(soup))[0]['name'], 'Techno')

    def test_same_website_sections_are_not_cross_verification(self):
        def event(section):
            return Event(title="Same party", when="Friday", where="Tel Aviv", category="party",
                         source=section, link="https://www.secrettelaviv.com/tickets/party", day_part="eve_fri")
        result = self._run_with(scrape_secrettelaviv=[event("Calendar")],
                                scrape_secrettelaviv_parties=[event("Parties")])
        self.assertFalse(result[0].verified)

    def test_structured_offer_and_old_price(self):
        soup = BeautifulSoup('<script type="application/ld+json">{"@type":"Event","offers":{"@type":"Offer","price":85,"priceCurrency":"ILS"}}</script>', "html.parser")
        self.assertEqual(_page_price(soup), "ILS 85")
        self.assertEqual(_page_price(BeautifulSoup('<del>240₪</del>85₪', "html.parser")), "85₪")
        self.assertEqual(_page_price(BeautifulSoup('מחיר 070', "html.parser")), "₪70")

    def test_current_zygo_cards(self):
        html = '<a href="/event/123/zygo"><h3>Techno night</h3><p>Tel Aviv Port</p><p>30/10/2026 20:30</p></a>'
        with patch("agent.scraper._get", return_value=html):
            events = scrape_zygo(THU_EVE, FRI, SAT, SUN)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].link, "https://zygo.co.il/event/123/zygo")

    def test_teder_has_dated_direct_links(self):
        html = '<a href="/events/123">Romano Record Fair יום שבת 31.10.26 13:00 רחבה</a>'
        with patch("agent.scraper._get", return_value=html):
            events = scrape_teder(THU_EVE, FRI, SAT, SUN)
        self.assertEqual(events[0].day_part, "day_sat")
        self.assertEqual(events[0].link, "https://www.teder.fm/events/123")

    def test_report_has_card_price_domain_and_honest_sources(self):
        event = Event("Party", "Friday", "Tel Aviv", "party", link="https://zygo.co.il/event/1", source="Zygo", day_part="eve_fri")
        html = build_message([event], THU_EVE, FRI, SAT, SUN)
        self.assertIn("border:1px solid", html)
        self.assertIn("Price not published", html)
        self.assertIn("zygo.co.il", html)
        self.assertIn("Limited source coverage", html)
        self.assertNotIn("Sources: Secret Tel Aviv", html)
