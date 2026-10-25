"""Send the reviewed local preview without running a second research pass."""
from pathlib import Path
from agent.email import EmailSender
from agent.main import _upcoming_weekend

sender = EmailSender()
if not sender.is_configured():
    raise SystemExit('Email is not configured; no message was sent.')
html = Path('guide-preview.html').read_text(encoding='utf-8').replace('style="display:none"', 'src="cid:header-photo"')
text = Path('guide-preview.txt').read_text(encoding='utf-8')
thu, fri, sat, sun = _upcoming_weekend()
result = sender.send(f'Weekend in Tel Aviv — {thu:%d %b}–{sat:%d %b %Y}', html, text)
if result != 'sent':
    raise SystemExit('The mail server did not confirm acceptance.')
