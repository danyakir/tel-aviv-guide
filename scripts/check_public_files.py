"""Check candidate public files against private local delivery settings."""
from pathlib import Path
import subprocess
from dotenv import dotenv_values

root = Path(__file__).resolve().parents[1]
git = ['git', '-c', f'safe.directory={root.as_posix()}']
result = subprocess.run(git + ['ls-files', '--cached', '--others', '--exclude-standard', '-z'],
                        cwd=root, capture_output=True, check=True)
private = dotenv_values(root / '.env')
values = {str(private[k]) for k in ('SMTP_PASS', 'SMTP_USER', 'EMAIL_FROM', 'EMAIL_TO')
          if private.get(k) and len(str(private[k])) >= 8}
problems = []
for name in set(result.stdout.decode('utf-8').split('\0')) - {''}:
    path = root / name
    if path.name == '.env' or path.name == '.dev.vars' or path.suffix in ('.pem', '.key'):
        problems.append(name)
        continue
    if path.is_file():
        contents = path.read_text(encoding='utf-8', errors='replace')
        if any(value in contents for value in values):
            problems.append(name)
if problems:
    raise SystemExit('Private settings detected in candidate public files: ' + ', '.join(sorted(problems)))
print('Public file check passed; private delivery settings and credentials are excluded.')
