"""Unit tests for the Tel Aviv Weekend Agent (agent/) — v2 four-day window model.

Run from the project root:
    python -m unittest discover -t . -s tests -v
"""

from __future__ import annotations

import io
import os
import unittest
from contextlib import redirect_stdout
from datetime import datetime
from unittest import mock

from agent.email import EmailSender
from agent.main import _upcoming_weekend, run_once
from agent.report import (DAY_PART_LABELS, DAY_PART_ORDER, _format_price,
                          build_message, build_plain_text, build_sample_message)
from agent.scraper import (_JUNK_LINK_TITLES, Event, _assign_day_part,
                           _categorize, _clean, _extract_description,
                           _extract_price, _extract_time, _extract_venue,
                           _is_excluded, _is_included, _parse_dt,
                           fetch_weekend_events, scrape_secrettelaviv,
                           scrape_secrettelaviv_parties)

# Guide window: Thursday evening -> Sunday, for a October 2026 weekend.
THU_EVE = datetime(2026, 10, 29)
FRI = datetime(2026, 10, 30)
SAT = datetime(2026, 10, 31)
SUN = datetime(2026, 11, 1)

AMP = "&" + "amp;"      # what _esc turns & into
LT = "&" + "lt;"        # what _esc turns < into
GT = "&" + "gt;"        # what _esc turns > into
QUOT = "&" + "quot;"    # what _esc turns " into


def _ev(title="Techno Party @ Rome Club", when="Fri 30/10/2026, 21:00", where="Rome Club",
        category="party", price="80 shekels", link="", source="Test",
        description="", sort_dt=None, day_part="eve_fri"):
    return Event(title=title, when=when, where=where, category=category,
                 price=price, link=link, source=source,
                 description=description, sort_dt=sort_dt, day_part=day_part)


class UpcomingWeekendTests(unittest.TestCase):
    """agent.main._upcoming_weekend — now returns (Thu, Fri, Sat, Sun)."""

    def test_sunday_maps_to_coming_weekend(self):
        thu, fri, sat, sun = _upcoming_weekend(datetime(2026, 10, 25, 15, 0))
        self.assertEqual((thu.year, thu.month, thu.day), (2026, 10, 29))
        self.assertEqual((fri.year, fri.month, fri.day), (2026, 10, 30))
        self.assertEqual((sat.year, sat.month, sat.day), (2026, 10, 31))
        self.assertEqual((sun.year, sun.month, sun.day), (2026, 11, 1))

    def test_wednesday_sees_this_coming_thursday(self):
        thu, _f, _s, _u = _upcoming_weekend(datetime(2026, 10, 28, 12, 0))
        self.assertEqual((thu.month, thu.day), (10, 29))

    def test_thursday_before_20_is_same_day(self):
        thu, _f, _s, _u = _upcoming_weekend(datetime(2026, 10, 29, 19, 59))
        self.assertEqual((thu.month, thu.day), (10, 29))

    def test_thursday_evening_rolls_to_next_week(self):
        thu, _f, _s, _u = _upcoming_weekend(datetime(2026, 10, 29, 20, 1))
        self.assertEqual((thu.month, thu.day), (11, 5))

    def test_friday_maps_to_next_week(self):
        thu, _f, _s, _u = _upcoming_weekend(datetime(2026, 10, 30, 12, 0))
        self.assertEqual((thu.month, thu.day), (11, 5))

    def test_all_dates_are_midnight(self):
        for d in _upcoming_weekend(datetime(2026, 10, 25, 15, 30)):
            self.assertEqual((d.hour, d.minute, d.second, d.microsecond),
                             (0, 0, 0, 0))


class ParseDateTests(unittest.TestCase):
    """agent.scraper._parse_dt"""

    def test_dd_mm_yyyy(self):
        self.assertEqual(_parse_dt("25/10/2026"), datetime(2026, 10, 25))

    def test_day_month_order_is_european(self):
        # 05/06 must be 5 June (dd/mm), not 6 May
        self.assertEqual(_parse_dt("05/06/2024"), datetime(2024, 6, 5))

    def test_invalid_date_returns_none(self):
        self.assertIsNone(_parse_dt("31/02/2025"))  # Feb 31st
        self.assertIsNone(_parse_dt("99/99/9999"))

    def test_no_date_returns_none(self):
        self.assertIsNone(_parse_dt("no date here"))

    def test_extracts_date_from_long_text(self):
        self.assertEqual(_parse_dt("Friday show on 30/10/2026 at 21:00"),
                         datetime(2026, 10, 30))


class CleanTests(unittest.TestCase):
    """agent.scraper._clean (control-character hygiene for scraped text)."""

    def test_collapses_whitespace(self):
        self.assertEqual(_clean("  a \n  b \t c "), "a b c")

    def test_drops_lone_surrogates(self):
        # Surrogates crash UTF-8 writers; _clean must remove them.
        self.assertEqual(_clean("Mike\udcA6s Place"), "Mikes Place")

    def test_empty(self):
        self.assertEqual(_clean(""), "")

    def test_plain_text_passes_through(self):
        self.assertEqual(_clean("Halloween Bash @ Mikes Place"),
                         "Halloween Bash @ Mikes Place")


class CategorizeTests(unittest.TestCase):
    """agent.scraper._categorize"""

    def test_party_keywords(self):
        for kw in ("party", "rave", "techno night", "DJ set"):
            self.assertEqual(_categorize(kw), "party", kw)

    def test_show_keywords(self):
        for kw in ("concert", "live music", "comedy show"):
            self.assertEqual(_categorize(kw), "show", kw)

    def test_movie_keywords(self):
        self.assertEqual(_categorize("new movie screening"), "movie")

    def test_restaurant_keywords(self):
        self.assertEqual(_categorize("new bar opening brunch"), "restaurant")

    def test_free_keywords(self):
        self.assertEqual(_categorize("free walking tour"), "free-municipal")

    def test_unknown_defaults_to_show(self):
        self.assertEqual(_categorize("zzz unknown thing"), "show")


class EventModelTests(unittest.TestCase):
    """agent.scraper.Event"""

    def test_price_defaults_to_sentinel(self):
        # "see listing" is the unknown-price sentinel; the report hides it.
        ev = Event(title="t", when="w", where="x", category="party")
        self.assertEqual(ev.price, "see listing")

    def test_sort_key_orders_day_parts_chronologically(self):
        keys = [_ev(day_part=p).sort_key() for p in DAY_PART_ORDER]
        self.assertEqual(sorted(keys), keys,
                         "DAY_PART_ORDER must match Event.sort_key mapping")

    def test_sort_key_unknown_day_part_sorts_last(self):
        self.assertGreater(_ev(day_part="").sort_key(), _ev(day_part="eve_sat").sort_key())


def _parties_page_html():
    # Realistic structure: nav lives far from the events table, so nav-link
    # ancestors cannot inherit dates from event rows.
    return """
    <html><body>
      <header>
        <ul class="menu">
          <li><a href="/tickets/">Calendar</a></li>
          <li><a href="/tickets/categories/parties/">Parties</a></li>
          <li><a href="/tickets/categories/live-music/">Live Music</a></li>
        </ul>
      </header>
      <main><div>
        <table class="events-page">
          <tbody>
            <tr>
              <TD class="event-image"><img src="a.jpg" alt="Halloween Party @ Kuli Alma"/></TD>
              <td>Saturday 31/10/2026 9:00 pm</td>
              <td>
                <a href="/tickets/halloween-party-kuli-alma">Halloween Party @ Kuli Alma</a>
                <a href="/tickets/halloween-party-kuli-alma">Read More</a>
              </td>
              <td class="event-price">40 shekels</td>
            </tr>
          </tbody>
        </table>
      </div></main>
    </body></html>
    """


class JunkLinkFilterTests(unittest.TestCase):
    """Category pages must not emit generic nav/link text as events."""

    def _scrape_parties(self, html):
        with mock.patch("agent.scraper._get", return_value=html):
            return scrape_secrettelaviv_parties(THU_EVE, FRI, SAT, SUN)

    def test_junk_title_list_contents(self):
        for junk in ("read more", "read more...", "buy tickets", "book now",
                     "more info", "calendar", "parties", "live music",
                     "submit an event", "read post"):
            self.assertIn(junk, _JUNK_LINK_TITLES, junk)

    def test_nav_links_not_listed_as_events(self):
        events = self._scrape_parties(_parties_page_html())
        titles = [e.title for e in events]
        for junk in ("Calendar", "Parties", "Live Music", "Read More"):
            self.assertNotIn(junk, titles)
        self.assertIn("Halloween Party @ Kuli Alma", titles)

    def test_event_appears_once_not_once_per_link(self):
        events = self._scrape_parties(_parties_page_html())
        titles = [e.title for e in events]
        self.assertEqual(titles.count("Halloween Party @ Kuli Alma"), 1)

    def test_nav_link_with_dated_ancestor_is_still_junk(self):
        # ROUND-1 FINDING (now fixed): "Calendar" shares an ancestor with the
        # dated row; before the junk-set filter it was promoted to a fake party.
        html = """
        <div>
          <a href="/tickets/">Calendar</a>
          <table><tr>
            <td>Friday 30/10/2026 10:00 pm</td>
            <td><a href="/tickets/techno-night">Techno Night @ Basement</a></td>
          </tr></table>
        </div>
        """
        events = self._scrape_parties(html)
        titles = [e.title for e in events]
        self.assertNotIn("Calendar", titles)
        self.assertEqual(titles, ["Techno Night @ Basement"])


def _calendar_html():
    """Fixture replicating the REAL Secret Tel Aviv /tickets table structure:
    columns are [image | Date/Time | Event | Price]."""
    return """
    <table class="events-page">
      <thead><tr>
        <th class="event-image"></th><th class="event-time">Date/Time</th>
        <th class="event-description">Event</th><th class="event-price">Price</th>
      </tr></thead>
      <tbody>
        <tr>
          <TD class="event-image"><img src="x.jpg" alt="Out of window event"/></TD>
          <td>Thursday</br>22/10/2026<BR /><i>6:00 pm</i></td>
          <td><a href="/tickets/old-event">Old Event @ Venue</a></td>
          <td class="event-price"></td>
        </tr>
        <tr>
          <TD class="event-image"><img src="y.jpg" alt="Halloween Bash @ Mikes Place"/></TD>
          <td>Friday</br>30/10/2026<BR /><i>12:00 pm - 11:55 pm</i></td>
          <td><a href="/tickets/halloween-bash">Halloween Bash @ Mikes Place</a></td>
          <td class="event-price">80 shekels</td>
        </tr>
      </tbody>
    </table>
    """


class ScrapeSecretTelAvivTests(unittest.TestCase):
    """The main calendar scraper against the real column layout."""

    def _scrape(self, html):
        with mock.patch("agent.scraper._get", return_value=html):
            return scrape_secrettelaviv(THU_EVE, FRI, SAT, SUN)

    def test_parses_date_from_second_column_and_emits_event(self):
        # ROUND-1 FINDING (now fixed): the date was read from cells[0] (the
        # image cell), so every row was skipped and the calendar contributed
        # 0 events. Columns must be located by content, not position.
        events = self._scrape(_calendar_html())
        self.assertEqual(len(events), 1)
        ev = events[0]
        self.assertEqual(ev.title, "Halloween Bash @ Mikes Place")
        self.assertIn("30/10/2026", ev.when)
        self.assertEqual(ev.link, "https://www.secrettelaviv.com/tickets/halloween-bash")
        self.assertEqual(ev.price, "80 shekels")
        self.assertEqual(ev.where, "Mikes Place")

    def test_out_of_window_row_dropped(self):
        events = self._scrape(_calendar_html())
        self.assertEqual([e.title for e in events], ["Halloween Bash @ Mikes Place"])

    def test_noon_event_is_daytime_not_evening(self):
        # ROUND-1 FINDING (now fixed): "12:00 pm" used to be classified by
        # substring heuristics as evening; the start-hour parse fixes it.
        events = self._scrape(_calendar_html())
        self.assertEqual(events[0].day_part, "day_fri")


class ExtractHelperTests(unittest.TestCase):
    """agent.scraper._extract_* text helpers."""

    def test_extract_venue_from_at_syntax(self):
        self.assertEqual(_extract_venue("Halloween Bash @ Mikes Place"), "Mikes Place")

    def test_extract_venue_without_at_is_empty(self):
        self.assertEqual(_extract_venue("Friday night market"), "")

    def test_extract_price_nis_range(self):
        self.assertEqual(_extract_price("entry NIS 40 - 60 at door"), "NIS 40 - 60")

    def test_extract_price_shekel_sign(self):
        shekel = chr(8362)
        self.assertEqual(_extract_price("NIS or " + shekel + " 55 night"), shekel + " 55")

    def test_extract_price_dollar(self):
        self.assertEqual(_extract_price("$10 before midnight"), "$10")

    def test_extract_price_free(self):
        self.assertEqual(_extract_price("free before 21:00"), "Free")

    def test_extract_price_missing_is_empty(self):
        self.assertEqual(_extract_price("ask at the door"), "")

    def test_extract_time_full_range(self):
        self.assertEqual(_extract_time("Fri 30/10/2026 9:00 pm - 11:55 pm"),
                         "Fri 30/10/2026 9:00 pm - 11:55 pm")

    def test_extract_time_fallback_truncates(self):
        self.assertEqual(_extract_time("x" * 100), "x" * 60)

    def test_extract_description_removes_title_and_datetime(self):
        cell = "Halloween Bash @ Mikes Place 30/10/2026 10:00 pm crazy night!"
        desc = _extract_description(cell, "Halloween Bash @ Mikes Place")
        self.assertNotIn("Halloween Bash @ Mikes Place", desc)
        self.assertNotIn("30/10/2026", desc)
        self.assertNotIn("10:00 pm", desc)
        self.assertIn("crazy night!", desc)

    def test_extract_description_truncates_long_text(self):
        cell = "Very long description " + ("word " * 40)
        desc = _extract_description(cell, "unrelated title")
        self.assertTrue(desc.endswith("..."))
        self.assertEqual(len(desc), 150)


class DayPartTests(unittest.TestCase):
    """agent.scraper._assign_day_part — start-hour parsing, not substring guesses."""

    def _part(self, when, sort_dt=None):
        ev = Event(title="t", when=when, where="x", category="party", sort_dt=sort_dt)
        return _assign_day_part(ev, THU_EVE, FRI, SAT, SUN)

    def test_noon_pm_fri_is_daytime(self):
        self.assertEqual(self._part("Fri 30/10/2026 12:00 pm", FRI), "day_fri")

    def test_fri_morning(self):
        self.assertEqual(self._part("Fri 30/10/2026 10:00 am", FRI), "morning_fri")

    def test_fri_evening(self):
        self.assertEqual(self._part("Fri 30/10/2026 9:00 pm", FRI), "eve_fri")

    def test_early_morning_hours_count_as_evening(self):
        self.assertEqual(self._part("Sat 31/10/2026 1:00 am", SAT), "eve_sat")

    def test_sat_morning(self):
        self.assertEqual(self._part("Sat 31/10/2026 8:00 am", SAT), "morning_sat")

    def test_sat_afternoon(self):
        self.assertEqual(self._part("Sat 31/10/2026 2:30 pm", SAT), "day_sat")

    def test_sat_late_evening(self):
        self.assertEqual(self._part("Sat 31/10/2026 11:15 pm", SAT), "eve_sat")

    def test_thu_evening_slot(self):
        self.assertEqual(self._part("Thu 29/10/2026 9:30 pm", THU_EVE), "eve_thu")

    def test_thu_daytime_not_in_guide(self):
        self.assertEqual(self._part("Thu 29/10/2026 1:00 pm", THU_EVE), "")

    def test_sunday_dropped(self):
        self.assertEqual(self._part("Sun 1/11/2026 9:00 pm", SUN), "")

    def test_out_of_window_date_dropped(self):
        self.assertEqual(self._part("Tues 27/10/2026 9:00 pm", datetime(2026, 10, 27)), "")

    def test_no_dt_weekday_taken_from_when_text(self):
        self.assertEqual(self._part("Saturday 9:00 pm"), "eve_sat")

    def test_24h_time_without_ampm_falls_back_to_substrings(self):
        self.assertEqual(self._part("Fri 30/10/2026 21:00", FRI), "eve_fri")

    def test_morning_word_without_ampm(self):
        self.assertEqual(self._part("Friday morning market"), "morning_fri")


class PreferenceFilterTests(unittest.TestCase):
    """agent.scraper._is_excluded / _is_included — the locked-in preferences."""

    def _ev(self, title, where="Tel Aviv", description=""):
        return Event(title=title, when="Fri 30/10/2026, 21:00", where=where,
                     category="party", description=description)

    def test_excluded_kid_family(self):
        self.assertTrue(_is_excluded(self._ev("Kids & Family Friday")))

    def test_excluded_dizengoff(self):
        self.assertTrue(_is_excluded(self._ev("Techno night", where="Dizengoff Square")))

    def test_excluded_sarona(self):
        self.assertTrue(_is_excluded(self._ev("Brunch market", where="Sarona")))

    def test_excluded_mizrahi(self):
        self.assertTrue(_is_excluded(self._ev("Mizrahi evening")))

    def test_excluded_members_only(self):
        self.assertTrue(_is_excluded(self._ev("Members only party")))

    def test_included_techno(self):
        self.assertTrue(_is_included(self._ev("Techno night @ Rome Club")))

    def test_included_movie(self):
        self.assertTrue(_is_included(self._ev("New movie screening")))

    def test_included_community_workshop(self):
        self.assertTrue(_is_included(self._ev("Community pottery workshop")))

    def test_unrelated_topic_not_included(self):
        self.assertFalse(_is_included(self._ev("Basketball league game")))


class BuildMessageTests(unittest.TestCase):
    """agent.report.build_message / sample / plain-text builders."""

    def test_header_has_date_range(self):
        msg = build_message([_ev()], THU_EVE, FRI, SAT, SUN)
        self.assertIn("Weekend in Tel Aviv", msg)
        self.assertIn("29 Oct", msg)
        self.assertIn("31 Oct 2026", msg)

    def test_title_is_escaped(self):
        raw = "Weird <b> " + chr(38) + "unky"
        msg = build_message([_ev(title=raw)], THU_EVE, FRI, SAT, SUN)
        self.assertNotIn(raw, msg)                       # raw markup must not leak
        self.assertIn(LT + "b" + GT, msg)                # escaped <b>
        self.assertIn(AMP + "unky", msg)                 # escaped &

    def test_link_quotes_are_escaped(self):
        evil = "https://e/1" + chr(34) + " onmouseover=" + chr(34)
        msg = build_message([_ev(link=evil)], THU_EVE, FRI, SAT, SUN)
        self.assertIn(QUOT, msg)
        self.assertNotIn(evil, msg)                      # attribute breakout prevented

    def test_sections_in_chronological_order(self):
        events = [
            _ev(title="Sat nightcap", day_part="eve_sat", when="Sat 31/10/2026, 23:00"),
            _ev(title="Thu opener", day_part="eve_thu", when="Thu 29/10/2026, 21:00"),
            _ev(title="Fri brunch", day_part="morning_fri", when="Fri 30/10/2026, 10:00"),
        ]
        msg = build_message(events, THU_EVE, FRI, SAT, SUN)
        self.assertLess(msg.index("Thursday Evening"), msg.index("Friday Morning"))
        self.assertLess(msg.index("Friday Morning"), msg.index("Saturday Evening"))

    def test_all_seven_day_parts_supported(self):
        events = [_ev(title=f"Event {p}", day_part=p) for p in DAY_PART_ORDER]
        msg = build_message(events, THU_EVE, FRI, SAT, SUN)
        pos = [msg.index(DAY_PART_LABELS[p]) for p in DAY_PART_ORDER]
        self.assertEqual(pos, sorted(pos))
        for label in DAY_PART_LABELS.values():
            self.assertIn(label, msg)

    def test_price_hidden_when_empty(self):
        msg = build_message([_ev(price="")], THU_EVE, FRI, SAT, SUN)
        self.assertIn("Price not published", msg)

    def test_unknown_price_sentinels_hidden_in_report(self):
        msg = build_message([_ev(price="see listing")], THU_EVE, FRI, SAT, SUN)
        self.assertIn("Price not published", msg)

    def test_price_shown_when_set(self):
        msg = build_message([_ev(price="80 shekels")], THU_EVE, FRI, SAT, SUN)
        self.assertIn("80 shekels", msg)

    def test_description_and_link_shown(self):
        msg = build_message([_ev(description="bring ID", link="https://x/1")],
                            THU_EVE, FRI, SAT, SUN)
        self.assertIn("bring ID", msg)
        self.assertIn("Event page", msg)

    def test_empty_guide_gets_fallback_note(self):
        msg = build_message([], THU_EVE, FRI, SAT, SUN)
        self.assertIn("No matching events found this weekend", msg)

    def test_sample_message_has_all_sections_and_footer(self):
        msg = build_sample_message(THU_EVE, FRI, SAT, SUN)
        for label in DAY_PART_LABELS.values():
            self.assertIn(label, msg)
        self.assertIn("Techno", msg)
        self.assertIn("Next update", msg)

    def test_format_price_helper(self):
        self.assertEqual(_format_price("80 shekels"), "80 shekels")
        self.assertEqual(_format_price(""), "")
        self.assertEqual(_format_price("   "), "")
        for filler in ("see listing", "see venue", "see venue page", "free / tbd"):
            self.assertEqual(_format_price(filler), "", filler)

    def test_plain_text_structure(self):
        text = build_plain_text(
            [
                _ev(title="Thu opener", day_part="eve_thu", when="Thu 29/10/2026, 21:00"),
                _ev(title="Sat nightcap", day_part="eve_sat", when="Sat 31/10/2026, 23:00",
                    price="", description="last set", link="https://x"),
            ],
            THU_EVE, FRI, SAT, SUN,
        )
        self.assertIn("Weekend in Tel Aviv — 29 Oct – 31 Oct 2026 (Thu–Sat)", text)
        self.assertLess(text.index("Thursday Evening"), text.index("Saturday Evening"))
        self.assertIn("📅 Thu opener", text)
        self.assertIn("🕒 Thu 29/10/2026, 21:00", text)
        self.assertIn("⁉ last set", text)
        self.assertIn("🔗 https://x", text)

    def test_plain_text_no_price_line_when_empty(self):
        text = build_plain_text([_ev(price="", day_part="eve_thu")], THU_EVE, FRI, SAT, SUN)
        self.assertIn("Price not published", text)


class EmailSenderTests(unittest.TestCase):
    """agent.email.EmailSender — no network involved in these paths."""

    FULL_ENV = {
        "SMTP_HOST": "smtp.gmail.com", "SMTP_PORT": "587",
        "SMTP_USER": "me@gmail.com", "SMTP_PASS": "app-pass",
        "EMAIL_FROM": "me@gmail.com", "EMAIL_TO": "you@gmail.com",
    }

    def test_not_configured_when_env_missing(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertFalse(EmailSender().is_configured())

    def test_configured_with_all_env(self):
        with mock.patch.dict(os.environ, self.FULL_ENV, clear=True):
            self.assertTrue(EmailSender().is_configured())

    def test_unconfigured_send_prints_instead_of_raising(self):
        buf = io.StringIO()
        with mock.patch.dict(os.environ, {}, clear=True):
            sender = EmailSender()
            with redirect_stdout(buf):
                result = sender.send("Subject here", "html body", "my body")
        self.assertIsNone(result)
        self.assertIn("SMTP not configured", buf.getvalue())
        self.assertIn("my body", buf.getvalue())


class FetchWeekendEventsTests(unittest.TestCase):
    """agent.scraper.fetch_weekend_events — aggregation, dedup, cap, resilience."""

    def _run_with(self, **stubs):
        """Patch every scraper; unset keywords return [].

        Stub values may be a list[Event] (retrieved via a mock) or a callable
        that replaces the scraper outright.
        """
        names = [
            "scrape_secrettelaviv", "scrape_secrettelaviv_parties",
            "scrape_secrettelaviv_livemusic", "scrape_diy", "scrape_mevalim",
            "scrape_zygo", "scrape_municipal", "scrape_community_daytime",
            "scrape_restaurants",
            "scrape_levontin",
            "scrape_teder",
            "scrape_gagarin",
            "scrape_bonire", "scrape_happytlv", "scrape_airdrop",
        ]
        patchers = []
        for name in names:
            stub = stubs.get(name, [])
            if callable(stub):
                patchers.append(mock.patch(f"agent.scraper.{name}", new=stub))
            else:
                # Aggregation fixtures represent relevant nightlife; audience
                # filtering itself is tested separately with explicit genres.
                for event in stub:
                    if event.category == 'party' and not event.genres:
                        event.genres = 'Techno'
                patchers.append(mock.patch(f"agent.scraper.{name}", return_value=stub))
        for p in patchers:
            p.start()
        patchers.append(mock.patch("agent.scraper._get", return_value=None))
        patchers[-1].start()
        try:
            return fetch_weekend_events(THU_EVE, FRI, SAT, SUN)
        finally:
            for p in patchers:
                p.stop()

    def test_duplicate_titles_removed(self):
        dup = _ev(title="Same Title")
        other = _ev(title="Other Title", category="show")
        result = self._run_with(scrape_secrettelaviv=[dup],
                                scrape_secrettelaviv_parties=[dup],
                                scrape_diy=[other])
        titles = [e.title for e in result]
        self.assertEqual(titles.count("Same Title"), 1)
        self.assertIn("Other Title", titles)

    def test_case_insensitive_dedup(self):
        result = self._run_with(
            scrape_secrettelaviv=[_ev(title="Drum Session")],
            scrape_secrettelaviv_parties=[_ev(title="DRUM SESSION")],
        )
        self.assertEqual(len(result), 1)

    def test_scraper_error_does_not_kill_aggregation(self):
        def boom(thu_eve, fri, sat, sun):
            raise RuntimeError("site down")

        ok = _ev(title="Survivor")
        result = self._run_with(scrape_secrettelaviv=boom,
                                scrape_secrettelaviv_parties=[ok])
        self.assertEqual([e.title for e in result], ["Survivor"])

    def test_sorted_by_day_part_then_time(self):
        late = _ev(title="A nightcap", day_part="eve_fri", sort_dt=datetime(2026, 10, 30, 23, 0))
        early = _ev(title="B afterparty", day_part="eve_fri", sort_dt=datetime(2026, 10, 30, 22, 0))
        thu = _ev(title="C opener", day_part="eve_thu", sort_dt=datetime(2026, 10, 29, 21, 0))
        result = self._run_with(scrape_secrettelaviv=[late, early, thu])
        self.assertEqual([e.title for e in result],
                         ["C opener", "B afterparty", "A nightcap"])

    def test_capped_at_30_events(self):
        many = [_ev(title=f"Event {i:02d}", day_part="eve_fri",
                    sort_dt=datetime(2026, 10, 30, 21, i)) for i in range(35)]
        result = self._run_with(scrape_secrettelaviv=many)
        self.assertEqual(len(result), 30)

    def test_cap_keeps_every_day_part_represented(self):
        # ROUND-2 FINDING (now fixed): a flat chronological cap consumed the
        # whole budget with Thursday/Friday and dropped ALL of Saturday.
        fri_events = [_ev(title=f"Fri event {i}", day_part="eve_fri",
                          sort_dt=datetime(2026, 10, 30, 18, i)) for i in range(30)]
        sat_events = [_ev(title=f"Sat event {i}", day_part="eve_sat",
                          sort_dt=datetime(2026, 10, 31, 21, i)) for i in range(5)]
        result = self._run_with(
            scrape_secrettelaviv=fri_events, scrape_diy=sat_events)
        self.assertEqual(len(result), 30)
        self.assertTrue(any(e.day_part == "eve_sat" for e in result),
                        "Saturday events must survive the cap")

    def test_fields_cleaned_in_aggregation(self):
        result = self._run_with(
            scrape_secrettelaviv=[_ev(title="  Tidy  \n  Title \udcA6 ")])
        self.assertEqual(result[0].title, "Tidy Title")

    def test_junk_title_never_listed_even_from_scraper(self):
        result = self._run_with(scrape_secrettelaviv=[_ev(title="read more")])
        self.assertEqual(result, [])

    def test_undated_restaurant_opening_is_not_an_event(self):
        result = self._run_with(scrape_restaurants=[
            _ev(title="Sushi Opening", category="restaurant",
                day_part="", when="New openings this month"),
        ])
        self.assertNotIn("Sushi Opening", [e.title for e in result])


class _FixedNow(datetime):
    """datetime stand-in with a fixed 'now': Wednesday 28 Oct 2026, noon."""

    @classmethod
    def now(cls, tz=None):  # noqa: N805
        base = datetime(2026, 10, 28, 12, 0)
        return base.replace(tzinfo=tz) if tz else base


class RunOnceTests(unittest.TestCase):
    """agent.main.run_once — wiring, without network or sending."""

    def test_falls_back_to_header_when_no_events(self):
        with mock.patch("agent.main.fetch_weekend_events", return_value=[]), \
             mock.patch("agent.main.datetime", _FixedNow):
            body = run_once(send_now=False)
        self.assertIn("Weekend in Tel Aviv — 29 Oct – 31 Oct 2026", body)

    def test_uses_live_events(self):
        ev = _ev(title="Techno Allnighter @ Kuli Alma", day_part="eve_fri",
                 when="Fri 30/10/2026, 21:00")
        with mock.patch("agent.main.fetch_weekend_events", return_value=[ev]), \
             mock.patch("agent.main.datetime", _FixedNow):
            body = run_once(send_now=False)
        self.assertIn("Techno Allnighter @ Kuli Alma", body)
        self.assertIn("Friday Evening", body)

    def test_send_now_wires_email_sender(self):
        ev = _ev(title="Live Event", day_part="eve_fri")
        with mock.patch("agent.main.fetch_weekend_events", return_value=[ev]), \
             mock.patch("agent.main.datetime", _FixedNow), \
             mock.patch("agent.main.EmailSender") as sender_cls:
            body = run_once(send_now=True)
        sender_cls.assert_called_once()
        send = sender_cls.return_value.send
        send.assert_called_once()
        self.assertTrue(send.call_args.args[0].startswith("Weekend in Tel Aviv — 29 Oct"))
        self.assertEqual(len(send.call_args.args), 3, "send(subject, html, text) expected")
        self.assertEqual(body, send.call_args.args[2], "run_once returns the plain-text body")

    def test_bare_invocation_prints_help_without_scraping(self):
        import agent.main as main_mod

        buf = io.StringIO()
        with mock.patch("sys.argv", ["prog"]), \
             mock.patch("agent.main.run_once", side_effect=AssertionError("must not scrape")), \
             redirect_stdout(buf):
            main_mod.main()  # no flags -> help, no scraping
        self.assertIn("usage:", buf.getvalue())
        self.assertIn("--send-now", buf.getvalue())


if __name__ == "__main__":
    unittest.main(verbosity=2)
