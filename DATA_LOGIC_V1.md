# REPORT-BOT Data Logic v1

Verified results are recorded in [TEST_RESULTS.md](TEST_RESULTS.md): 35 automated tests passed against a separate PostgreSQL instance, plus an opt-in real Drive upload with simulated LINE content.

## Architecture and changed files

Existing: Flask `app.py` authenticates LINE POST `/webhook`, psycopg PostgreSQL storage (`line_reports`), Gunicorn starts `worker.py`, `drive_storage.py` downloads LINE media and uploads with existing Google refresh-token OAuth. No ORM was present; v1 keeps psycopg and adds versioned SQL migrations rather than replacing working storage.

Created: `data_logic.py`, `data_repository.py`, `migrate.py`, `migrations/001_data_logic.sql`, `tests/test_data_logic.py`, `tests/test_data_postgres.py`, this document.
Modified: `app.py`, `worker.py`, `.env.example`, `README.md`.

Production ingestion now uses an active group allowlist, then configurable rule detection, durable sessions and attachment jobs. Other groups and private messages are ignored. Existing `line_reports` and pending historical upload jobs remain available. Migration never converts historical conversations into reports. No group is automatically activated.

## Database and setup

New tables: `groups`, `reports`, `report_sessions`, `report_officers`, `report_persons`, `charges`, `evidence_items`, `seized_assets`, `phones`, `phone_numbers`, `attachments`, `schema_migrations`.

Set existing `DATABASE_URL` to Railway PostgreSQL. Run from the project root:

```powershell
.\.venv\Scripts\python.exe migrate.py
```

On Linux/Railway: `python migrate.py`. Migrations also run lazily at first ingestion/worker activity, serialized with a PostgreSQL advisory transaction lock. Run explicitly before switching production traffic. All migrations commit atomically; reruns use `schema_migrations`.

Activate the actual LINE group ID (a `C...` identifier, not its display name). Run parameterized SQL through your database tool, or replace the placeholder in this example:

```sql
INSERT INTO groups(line_group_id,group_name,profile_type,is_active)
VALUES ('REPLACE_WITH_ACTUAL_LINE_GROUP_ID','กลุ่มรายงานยาเสพติด','NARCOTICS',TRUE)
ON CONFLICT(line_group_id) DO UPDATE SET is_active=TRUE,
profile_type='NARCOTICS',updated_at=NOW();
```

The supplied Drive folder is `1lEdKhHDGHkAcNMclIJPzQvjKbxAxsL-M`; set existing `GOOGLE_DRIVE_FOLDER_ID` accordingly. OAuth flow and permissions remain unchanged. Do not print credentials or paste them into logs.

## Configuration

New environment variables: `REPORT_DETECTION_THRESHOLD=6`, `ATTACHMENT_WINDOW_SECONDS=180`, `EXTRACTION_READY_THRESHOLD=0.85`, `EXTRACTION_REVIEW_THRESHOLD=0.60`. Invalid thresholds fail startup. Existing Drive/LINE variables are still used. No AI provider is required; extraction is local and deterministic.

## Processing and limitations

Webhook persists accepted raw text before extraction. The worker processes reports after the fixed attachment window closes; crashes release row locks. New reports close earlier sessions from the same sender. Attachments match explicit `quotedMessageId` in the same group first, then sender and event-time window. An unknown sender never uses sender fallback. Unmatched media are stored unlinked. Retries are deduplicated by LINE message ID. Rows are locked per group to serialize ingestion.

Report extraction and all child relations commit in one transaction with a savepoint; invalid extraction rolls back children and marks ERROR while preserving raw text. `person_index` is a zero-based reference to extracted persons, resolved to database IDs. Phones accept `phone_numbers` arrays of `{phone_number, carrier}` objects. Unresolved indexes fail safely.

Narcotics extraction intentionally handles explicit labels and selected quantities rather than claiming full Thai prose understanding. It leaves uncertain dates, times, identity details and ambiguous associations empty; every rule extraction has confidence 0.5 and REVIEW_REQUIRED. The full source is retained. Explicit person/officer/charge/location/asset/phone labels and drug/firearm quantities are supported. Rich prose extraction and broader contradiction detection require a later verified extraction profile. FIREARMS and GENERAL_CASE are placeholders and are ignored for processing in v1.

Upload jobs reuse `GoogleDriveUploader.upload_report`; Drive appProperties allow recovery after an upload succeeds before database commit. Five failed attempts stop the job, retaining metadata with FAILED analysis status and marking associated reports for review. Successful storage remains NOT_ANALYZED: no OCR has run. MIME type remains nullable because the existing uploader returns only file ID and URL. Database commit failure after upload logs an orphan file ID. Historical jobs retain their previous retry behavior.

## Local verification

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
# Use only a disposable database, never production:
$env:TEST_DATABASE_URL = 'postgresql://user:password@localhost/report_bot_test'
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
$env:LINE_CHANNEL_SECRET = 'local-test-secret'
.\.venv\Scripts\python.exe -m flask --app app run
```

Health check: GET `/`. POST `/webhook` must include base64 HMAC-SHA256 signature of the exact request bytes in `X-Line-Signature`. Send synthetic reports and media from the configured group and sender; ordinary chat and inactive groups must add no reports. Verify attachment report IDs and statuses in PostgreSQL. Local Flask does not start the Gunicorn worker; run `python -c "import threading; from worker import run; run(threading.Event())"` in a separate terminal. Use synthetic data only. PostgreSQL tests create and drop isolated schemas in TEST_DATABASE_URL.

No Dashboard, OCR/PDF/image analysis, GIS, search, RBAC, statistics or LINE commands were added. No GitHub push or Railway deployment was performed. Production activation needs the actual LINE group ID and a migration run. Remaining operational debt: administrative retry/cleanup tools, orphan monitoring, richer extraction, retention policy, MIME metadata and DB-backed worker monitoring. Sensitive raw data are retained in PostgreSQL by design; logs contain IDs/status/error types only. Masking helpers prepare future APIs but no public API exists yet.
