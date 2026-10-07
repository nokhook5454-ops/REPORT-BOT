# Data Logic v1 verification — 7 October 2026

## Result

35 automated tests passed, zero failures, zero skips, using a separate local PostgreSQL 17.11 database. No production PostgreSQL data was read or modified.

Coverage includes signed LINE webhook ingestion, chat/allowlist rejection, attachment linking by sender and reference, missing sender, late media, report deduplication, migration reruns, extraction rollback and raw-text retention, relation persistence (persons/charges/phones/SIMs), confidence validation, masking and Drive failure/retry handling.

An additional opt-in smoke test passed with a real Google Drive upload using the existing OAuth module. LINE content download was simulated with a synthetic text fixture; PostgreSQL and Drive calls were real. Readback verified the file's parent folder and exact byte size. Crash-recovery lookup returned the same file ID without downloading or uploading again. The isolated smoke database schema was removed afterward.

Evidence file, retained in the supplied REPORT BOT folder:

[REPORT-BOT-QA-SYNTHETIC-509bda8981584a0587c4094fac961f56.txt](https://drive.google.com/file/d/1w34ZKXBB0d9VrujEpPFUIfGsej1F5GVV/view?usp=drivesdk)

## Reproduce

```powershell
$testRuntime = Join-Path $env:LOCALAPPDATA 'REPORT-BOT-test-postgres'
# Wait for pg_ctl itself, rather than the PostgreSQL server descendants.
$pgControl = Start-Process -FilePath "$testRuntime\pgsql\bin\pg_ctl.exe" -ArgumentList @('-D', "$testRuntime\data", '-l', "$testRuntime\server.log", '-o', '"-h 127.0.0.1 -p 55432"', '-w', 'start') -WindowStyle Hidden -PassThru
$pgControl.WaitForExit()
$env:TEST_DATABASE_URL = 'postgresql://report_test@127.0.0.1:55432/report_bot_test'
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe scripts\smoke_data_logic.py --oauth-env .secrets\google-oauth-production.env --upload-synthetic-file
& "$testRuntime\pgsql\bin\pg_ctl.exe" -D "$testRuntime\data" -m fast -w stop
```

The database must be a dedicated test database. Every smoke run retains a clearly named synthetic file in Drive; use the explicit upload flag only when wanted. Credentials are read locally and are never printed. PostgreSQL portable binaries came from EDB's official distribution and were run locally, bound to loopback port 55432, without installing a Windows service. Runtime/data live outside version control.

## Production status and remaining activation

The existing Railway REPORT-BOT and Postgres services were visibly Online in the browser. The public root endpoint returned `REPORT BOT is running`. The active Railway deployment is the existing collector, not the local Data Logic changes.

No GitHub push or Railway deploy was performed. GitHub CLI is authenticated as `niponplh-coder` and reports READ access to `nokhook5454-ops/REPORT-BOT`; the Railway connector reports insufficient viewer access. The browser is signed in to the target Railway workspace and can show its current services.

Before live LINE testing: publish the reviewed changes using an account with repository write access, run the migration on the deployment database, and activate the exact intended LINE group ID in `groups`. Do not choose a group solely from an unrelated logged ID. Then send synthetic report text followed by media from the same sender. Expected default behavior is one report, linked attachments, Drive metadata and REVIEW_REQUIRED after the three-minute window. Real LINE delivery/content download and v1 execution on Railway remain unverified.
