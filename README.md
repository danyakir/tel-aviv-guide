# Tel Aviv Weekend Agent

A proactive agent that researches Tel Aviv weekend events and sends you an HTML email guide every **Wednesday at 20:00 Israel time (Asia/Jerusalem)**.

## What you get

Standing requirements for every weekly search are in [SOURCING_RULES.md](SOURCING_RULES.md): independent website variety, direct event links, visible prices (or an explicit unavailable label), and separate event cards. Runtime selection balances domains and the report discloses coverage below three websites.

The guide targets about 30 events, prioritizing Friday/evening techno, disco, house, and raves; local bar and Teder-style culture; relevant shows; and young municipal events. Flexible targets are 20 nightlife events, 7 shows/films, and 3 young daytime/community events. Folk dancing, senior activities and unrelated free municipal filler are excluded. Restaurant openings need a real dated launch or similar event. Discovery includes Bonire, HappyTLV and Airdrop alongside direct Teder, Gagarin and Levontin calendars and the city feed. Organizer/ticket links and discovery provenance are both preserved.
- 📅 Guide covering **Thursday evening → Saturday night**, grouped by part of day
- ✅ Filters: electronic/rock parties, live shows, movies, daytime community events
- ❌ Excludes: kid/family events, Mizrahi music, Dizengoff & Sarona, members-only
- 📊 Deduplicated and capped (~30 events max), chronological Thu-evening → Sat-night

## How it works
Every Wednesday at 8pm Israel time the agent:
1. Scrapes the sources above for Thu eve – Sat events
2. Deduplicates, applies your preference filters and sorts by part of day
3. Builds a styled HTML email (with plain-text fallback) and sends it via SMTP

## Repo layout

Two independent components:

**1. Python email agent** (`agent/`) — the scheduled weekly guide (this README's focus).
- `agent/scraper.py` — scrapes & filters event sources
- `agent/report.py` — builds the HTML email / plain-text guide
- `agent/email.py` — SMTP sender (embeds header photo)
- `agent/scheduler.py` — Wednesday 20:00 Asia/Jerusalem timer
- `agent/main.py` — CLI (`--send-now` / `--schedule` / `--preview`)
- `tests/` — unit tests

**2. Cloudflare Worker** (`worker/`) — interactive WhatsApp responder: Meta WhatsApp Cloud API webhook → LLM (Groq or any OpenAI-compatible API) → reply. Conversation history and webhook deduplication in Workers KV. See `worker/wrangler.toml` (config) and `worker/.dev.vars.example` (local secrets template). Deploy secrets:
```bash
npx wrangler secret put WHATSAPP_TOKEN
npx wrangler secret put PHONE_NUMBER_ID
npx wrangler secret put VERIFY_TOKEN
npx wrangler secret put APP_SECRET
npx wrangler secret put LLM_API_KEY
```

## Setup (Python agent)

### 1. Install
```bash
pip install -r requirements.txt
```

### 2. Preview (no email needed)
```bash
python -m agent.main --preview
```

### 3. Send by email (optional)
Copy `.env.example` to `.env` and fill in SMTP details (for Gmail use an **App Password**, not your regular password), then:
```bash
python -m agent.main --send-now
```

### 4. Auto-schedule Wednesdays 8pm Israel time
```bash
python -m agent.main --schedule
```
This keeps a process running and fires every Wednesday 20:00 Asia/Jerusalem.

Or use cron:
```bash
# Runs Wednesdays at 18:00 UTC = 20:00 Israel winter time
0 18 * * 3 cd /path/to/tel-aviv-guide && python -m agent.main --send-now
```
> Israel switches between UTC+2 (winter) and UTC+3 (summer). Update the cron hour when clocks change, or prefer the built-in `--schedule` which is timezone-aware.

## Run the tests
```bash
python -m unittest discover -t . -s tests   # agent (95 tests)
node worker/test/run-tests.mjs              # worker (14 tests, no deploy needed)
```

## Notes
- Some municipal services restrict access from outside Israel; the agent falls back to the remaining sources when a site is unreachable.
- `TZ=Asia/Jerusalem` in `.env` is optional (scheduling is timezone-aware regardless).
