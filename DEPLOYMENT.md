# Weekly cloud delivery

The GitHub Actions workflow `.github/workflows/weekly-guide.yml` runs fresh research
and sends the guide each Wednesday at 20:00 **Asia/Jerusalem**, including daylight
saving changes. No continuously running server is required. GitHub scheduled jobs
can be delayed by runner availability; the schedule is not an exact-time guarantee.

## Configure delivery

In the repository, open **Settings → Secrets and variables → Actions → New repository secret**.
Add these values from your private local `.env`:

- `SMTP_HOST`
- `SMTP_PORT`
- `SMTP_USER`
- `SMTP_PASS` (the sender's SMTP/app password)
- `EMAIL_FROM`
- `EMAIL_TO`

Do not commit `.env`, a real `.dev.vars`, private keys, or SMTP credentials.
The workflow reads secrets only at runtime. The local email recipient and sender
are not hardcoded in the public source.

## Verify

Open **Actions → Weekly Tel Aviv guide → Run workflow**. Leave **Send the generated
guide by email** unchecked to verify research without sending another email.
The generated HTML and text appear in the run's `weekend-guide` artifact.
Check the box for an explicit delivery test. Scheduled Wednesday runs send automatically.

The workflow runs the tests first, fails if no matching events are found, and
fails on missing SMTP settings or SMTP errors. It does not automatically retry
email delivery, which avoids duplicate emails after an uncertain network result.
Use the Actions failure notification to inspect an unsuccessful run.

Each successful delivery updates `delivery-status.json` with the weekend dates,
event counts and workflow link. It contains no email addresses or SMTP settings.
This also keeps real repository activity current, since GitHub can disable
schedules in a public repository after 60 days without activity.

Official references: [schedule syntax](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax#onschedule),
[repository secrets](https://docs.github.com/en/actions/how-tos/write-workflows/choose-what-workflows-do/use-secrets).
