"""Run only against a dedicated TEST_DATABASE_URL, never a production database."""
import os
import unittest
import uuid
from datetime import datetime, timezone

from app import ReportRepository


@unittest.skipUnless(os.environ.get("TEST_DATABASE_URL"), "dedicated PostgreSQL test database not configured")
class PostgresTests(unittest.TestCase):
    def setUp(self):
        import psycopg
        from psycopg import sql
        self.url = os.environ["TEST_DATABASE_URL"]
        self.schema = "report_test_" + uuid.uuid4().hex
        with psycopg.connect(self.url) as conn:
            conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(self.schema)))
        self.repository = ReportRepository(self.url)
        def connect():
            return psycopg.connect(self.url, options="-c search_path=" + self.schema)
        self.repository._connect = connect
        self.report = {"webhook_event_id": "event", "line_message_id": "message", "source_type": "group",
            "group_id": "Ctest", "message_type": "file", "message_text": None,
            "drive_file_id": None, "drive_url": None, "received_at": datetime.now(timezone.utc),
            "file_name": "test.pdf", "upload_status": "pending"}

    def tearDown(self):
        import psycopg
        from psycopg import sql
        with psycopg.connect(self.url) as conn:
            conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(self.schema)))

    def test_dedup_and_upload_completion(self):
        self.assertTrue(self.repository.save(self.report))
        self.assertFalse(self.repository.save(self.report))
        self.assertTrue(self.repository.process_next(lambda report: ("file-id", "url")))
        self.assertFalse(self.repository.process_next(lambda report: self.fail("duplicate job")))
        with self.repository._connect() as conn:
            self.assertEqual(conn.execute("SELECT upload_status,drive_file_id FROM line_reports").fetchone(),
                             ("uploaded", "file-id"))

    def test_failed_upload_keeps_job_for_retry(self):
        self.repository.save(self.report)
        def fail(report):
            raise RuntimeError("secret response")
        self.assertTrue(self.repository.process_next(fail))
        with self.repository._connect() as conn:
            row = conn.execute("SELECT upload_status,upload_attempts,next_attempt_at > NOW() FROM line_reports").fetchone()
            self.assertEqual(row, ("pending", 1, True))
