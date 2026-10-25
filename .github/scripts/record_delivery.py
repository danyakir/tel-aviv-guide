"""Record a successful delivery; never include recipient or SMTP settings."""
import base64
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess

summary = json.loads(Path('guide-output/guide-preview.json').read_text(encoding='utf-8'))
record = {key: summary[key] for key in ('weekend_start', 'weekend_end', 'events', 'categories', 'sources', 'friday_evening_parties')}
record['sent_at_utc'] = datetime.now(timezone.utc).isoformat()
record['workflow_run'] = f"https://github.com/{os.environ['GITHUB_REPOSITORY']}/actions/runs/{os.environ['GITHUB_RUN_ID']}"
endpoint = f"repos/{os.environ['GITHUB_REPOSITORY']}/contents/delivery-status.json"
current = subprocess.run(['gh', 'api', endpoint], capture_output=True, text=True)
payload = {'message': f"Record weekend guide delivery for {record['weekend_start']}",
           'branch': os.environ['GITHUB_REF_NAME'],
           'content': base64.b64encode((json.dumps(record, indent=2) + '\n').encode()).decode()}
if current.returncode == 0:
    payload['sha'] = json.loads(current.stdout)['sha']
elif '404' not in current.stderr and 'Not Found' not in current.stderr:
    raise SystemExit('Could not read the previous delivery record.')
subprocess.run(['gh', 'api', '--method', 'PUT', endpoint, '--input', '-'],
               input=json.dumps(payload), text=True, stdout=subprocess.DEVNULL, check=True)
print('Successful delivery recorded; no personal or SMTP data was published.')
