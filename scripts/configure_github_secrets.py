"""Upload only email settings as Actions secrets, without logging their values."""
import argparse
from pathlib import Path
import subprocess
from dotenv import dotenv_values

parser = argparse.ArgumentParser()
parser.add_argument('--repo', required=True, help='GitHub owner/repository')
args = parser.parse_args()
settings = dotenv_values(Path(__file__).resolve().parents[1] / '.env')
keys = ('SMTP_HOST', 'SMTP_PORT', 'SMTP_USER', 'SMTP_PASS', 'EMAIL_FROM', 'EMAIL_TO')
settings['SMTP_PORT'] = settings.get('SMTP_PORT') or '587'
missing = [key for key in keys if not settings.get(key)]
if missing:
    raise SystemExit('Missing local email settings: ' + ', '.join(missing))
for key in keys:
    result = subprocess.run(['gh', 'secret', 'set', key, '--repo', args.repo],
                            input=settings[key], text=True, capture_output=True)
    if result.returncode:
        raise SystemExit(f'Could not configure {key}; check GitHub repository administration access.')
    print(f'{key}: configured as an encrypted Actions secret')
