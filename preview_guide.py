"""Save a live, unsent guide for local review."""
import json
from collections import Counter
from pathlib import Path
from agent.main import _upcoming_weekend
from agent.report import build_message, build_plain_text
from agent.scraper import fetch_weekend_events, _domain, _guide_group

window = _upcoming_weekend()
events = fetch_weekend_events(*window)
visible = [e for e in events if e.day_part]
if not visible:
    raise SystemExit('No matching events were returned; refusing to publish or email an empty guide.')
Path("guide-preview.html").write_text(build_message(visible, *window).replace('src="cid:header-photo"', 'style="display:none"'), encoding="utf-8")
Path("guide-preview.txt").write_text(build_plain_text(visible, *window), encoding="utf-8")
summary = {"events": len(visible), "websites": dict(Counter(_domain(e) for e in visible)),
                  "categories": dict(Counter(_guide_group(e) for e in visible)),
                  "day_parts": dict(Counter(e.day_part for e in visible)),
                  "friday_evening_parties": sum(e.day_part == 'eve_fri' and e.category == 'party' for e in visible),
                  "sources": sorted({e.source for e in visible}),
                  "weekend_start": window[0].date().isoformat(), "weekend_end": window[2].date().isoformat()}
Path('guide-preview.json').write_text(json.dumps(summary, ensure_ascii=True, indent=2), encoding='utf-8')
print(json.dumps(summary, ensure_ascii=True))
