"""Silent collector: persist events before processing durable Drive upload jobs."""

import base64
import hashlib
import hmac
import json
import logging
import os
import sys
from datetime import datetime, timezone

from flask import Flask, request


SCHEMA = """
CREATE TABLE IF NOT EXISTS line_reports (
    id BIGSERIAL PRIMARY KEY,
    webhook_event_id TEXT UNIQUE,
    line_message_id TEXT,
    source_type TEXT NOT NULL,
    group_id TEXT,
    message_type TEXT NOT NULL,
    message_text TEXT,
    drive_file_id TEXT,
    drive_url TEXT,
    received_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
ALTER TABLE line_reports ADD COLUMN IF NOT EXISTS file_name TEXT;
ALTER TABLE line_reports ADD COLUMN IF NOT EXISTS upload_status TEXT NOT NULL DEFAULT 'stored';
ALTER TABLE line_reports ADD COLUMN IF NOT EXISTS upload_attempts INTEGER NOT NULL DEFAULT 0;
ALTER TABLE line_reports ADD COLUMN IF NOT EXISTS next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT NOW();
CREATE INDEX IF NOT EXISTS line_reports_pending ON line_reports(next_attempt_at)
    WHERE upload_status = 'pending';
"""


class ReportRepository:
    def __init__(self, database_url):
        self.database_url = database_url
        self._ready = False
        from data_repository import DataRepository
        self.data_logic = DataRepository(self._connect)

    def _connect(self):
        import psycopg
        return psycopg.connect(self.database_url, connect_timeout=5,
                               options="-c statement_timeout=5000")

    def _ensure_schema(self):
        if self._ready:
            return
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(73190422)")
            cursor.execute(SCHEMA)
        self._ready = True

    def save(self, report):
        self._ensure_schema()
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """INSERT INTO line_reports
                   (webhook_event_id, line_message_id, source_type, group_id,
                    message_type, message_text, drive_file_id, drive_url, received_at,
                    file_name, upload_status)
                   VALUES (%(webhook_event_id)s, %(line_message_id)s, %(source_type)s,
                    %(group_id)s, %(message_type)s, %(message_text)s, %(drive_file_id)s,
                    %(drive_url)s, %(received_at)s, %(file_name)s, %(upload_status)s)
                   ON CONFLICT (webhook_event_id) DO NOTHING""",
                report,
            )
            return cursor.rowcount == 1


    def process_next(self, upload):
        """Hold one row lock until result commits. A process crash releases the lock."""
        from psycopg.rows import dict_row
        self._ensure_schema()
        with self._connect() as conn, conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute("""SELECT * FROM line_reports WHERE upload_status='pending'
                AND next_attempt_at <= NOW() ORDER BY id
                FOR UPDATE SKIP LOCKED LIMIT 1""")
            report = cursor.fetchone()
            if report is None:
                return False
            try:
                file_id, url = upload(report)
            except Exception:
                cursor.execute("""UPDATE line_reports SET upload_attempts=upload_attempts+1,
                    upload_status=CASE WHEN upload_attempts >= 4 THEN 'failed' ELSE 'pending' END,
                    next_attempt_at=NOW() + INTERVAL '1 minute' * (upload_attempts+1)
                    WHERE id=%s""", (report["id"],))
                logging.getLogger("report_bot").error("drive upload failed report_id=%s attempt=%s",
                    report["id"], report["upload_attempts"] + 1)
            else:
                cursor.execute("""UPDATE line_reports SET upload_status='uploaded',
                    drive_file_id=%s, drive_url=%s, upload_attempts=upload_attempts+1 WHERE id=%s""",
                    (file_id, url, report["id"]))
                logging.getLogger("report_bot").info("drive uploaded report_id=%s file_id=%s", report["id"], file_id)
        return True

    def ingest(self, event, received_at):
        return self.data_logic.ingest(event, received_at)


def create_app(repository=None):
    app = Flask(__name__)
    app.config.update(
        MAX_CONTENT_LENGTH=1024 * 1024,
        LINE_CHANNEL_SECRET=os.environ.get("LINE_CHANNEL_SECRET", ""),
        LINE_CHANNEL_ACCESS_TOKEN=os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", ""),
        DATABASE_URL=os.environ.get("DATABASE_URL", ""),
        GOOGLE_DRIVE_FOLDER_ID=os.environ.get("GOOGLE_DRIVE_FOLDER_ID", ""),
    )
    logger = logging.getLogger("report_bot")
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter("[REPORT-BOT] %(message)s"))
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    repository = repository or (ReportRepository(app.config["DATABASE_URL"]) if app.config["DATABASE_URL"] else None)

    def safe_value(value):
        if not isinstance(value, str):
            return '"unknown"'
        for key in ("LINE_CHANNEL_SECRET", "LINE_CHANNEL_ACCESS_TOKEN"):
            secret = app.config[key]
            if secret:
                value = value.replace(secret, "[REDACTED]")
        return json.dumps(value[:2000], ensure_ascii=True)

    def process_event(event):
        if event.get('type') == 'join':
            source = event.get('source') or {}
            if isinstance(source, dict) and source.get('type') == 'group':
                logger.info('group_joined group_id=%s allowlist_required=true', safe_value(source.get('groupId')))
            return
        if event.get("type") != "message" or not isinstance(event.get("message"), dict):
            return
        message, source = event["message"], event.get("source") or {}
        source = source if isinstance(source, dict) else {}
        message_type, message_id = message.get("type"), message.get("id")
        if message_type not in ("text", "image", "file", "video", "audio", "location", "sticker"):
            return
        if not isinstance(message_id, str) or not message_id:
            logger.warning("event skipped: missing message id")
            return
        if repository is None:
            raise RuntimeError("DATABASE_URL is not configured")
        drive_file_id = drive_url = None
        timestamp = event.get("timestamp")
        received_at = datetime.fromtimestamp(timestamp / 1000, tz=timezone.utc) if isinstance(timestamp, (int, float)) else datetime.now(timezone.utc)
        if hasattr(repository, 'ingest'):
            stored = repository.ingest(event, received_at)
            logger.info('event_processed message_id=%s group_id=%s stored=%s', safe_value(message_id), safe_value(source.get('groupId')), stored)
            return
        report = {
            "webhook_event_id": event.get("webhookEventId") or "line:{}".format(message_id), "line_message_id": message_id,
            "source_type": source.get("type") or "unknown", "group_id": source.get("groupId"),
            "message_type": message_type, "message_text": message.get("text") if message_type == "text" else None,
            "drive_file_id": drive_file_id, "drive_url": drive_url, "received_at": received_at,
            "file_name": message.get("fileName"),
            "upload_status": "pending" if message_type in ("image", "file", "video", "audio") else "stored",
        }
        stored = repository.save(report)
        logger.info("report %s message_type=%s source_type=%s group_id=%s", "stored" if stored else "duplicate",
                    safe_value(message_type), safe_value(source.get("type")), safe_value(source.get("groupId")))

    @app.get("/")
    def health():
        return "REPORT BOT is running", 200, {"Content-Type": "text/plain; charset=utf-8"}

    @app.post("/webhook")
    def webhook():
        secret = app.config["LINE_CHANNEL_SECRET"]
        if not secret:
            return "Service unavailable", 503
        body = request.get_data()
        signature = request.headers.get("X-Line-Signature", "")
        expected = base64.b64encode(hmac.new(secret.encode("utf-8"), body, hashlib.sha256).digest())
        if not hmac.compare_digest(expected, signature.encode("utf-8")):
            return "Invalid signature", 400
        try:
            payload = json.loads(body)
        except (ValueError, UnicodeDecodeError):
            return "Invalid JSON", 400
        if not isinstance(payload, dict) or not isinstance(payload.get("events"), list):
            return "Invalid events", 400
        logger.info("webhook received events=%d", len(payload["events"]))
        failed = False
        for event in payload["events"]:
            try:
                if isinstance(event, dict):
                    process_event(event)
            except Exception:
                failed = True
                # Never log exception details (remote responses can include credentials).
                logger.error("report storage failed")
        return ("Service unavailable", 503) if failed else ("", 200)

    return app


app = create_app()
