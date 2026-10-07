import base64
import hashlib
import hmac
import json
import os
import unittest
from unittest.mock import patch

from app import create_app


class FakeRepository:
    def __init__(self):
        self.reports = []

    def save(self, report):
        if any(item["webhook_event_id"] == report["webhook_event_id"] for item in self.reports):
            return False
        self.reports.append(report)
        return True


class FakeUploader:
    def __init__(self):
        self.uploads = []

    def upload_image(self, content, filename, mime_type):
        self.uploads.append((content, filename, mime_type))
        return "drive-file-1", "https://drive.example/file/1"


class WebhookTests(unittest.TestCase):
    def setUp(self):
        with patch.dict(os.environ, {
            "LINE_CHANNEL_SECRET": "test-secret",
            "LINE_CHANNEL_ACCESS_TOKEN": "test-token",
        }):
            self.repository = FakeRepository()
            self.app = create_app(repository=self.repository)
        self.client = self.app.test_client()

    def post(self, payload=None, body=None):
        body = body if body is not None else json.dumps(payload).encode()
        signature = base64.b64encode(hmac.new(b"test-secret", body, hashlib.sha256).digest()).decode()
        return self.client.post("/webhook", data=body, headers={"X-Line-Signature": signature})

    def test_health(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.text, "REPORT BOT is running")

    def test_empty_verify(self):
        response = self.post({"events": []})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, b"")

    def test_missing_invalid_and_tampered_signatures(self):
        for signature in ("", "invalid", "é"):
            self.assertEqual(self.client.post("/webhook", data=b'{}', headers={"X-Line-Signature": signature}).status_code, 400)
        signature = base64.b64encode(hmac.new(b"test-secret", b'{}', hashlib.sha256).digest()).decode()
        self.assertEqual(self.client.post("/webhook", data=b'{ }', headers={"X-Line-Signature": signature}).status_code, 400)

    def test_multiple_events_and_safe_logs(self):
        events = [{"type": "message", "source": {"type": "group", "groupId": "C123"},
                   "replyToken": "DO-NOT-LOG", "message": {"id": "test-text", "type": "text", "text": "hello\nforged test-secret test-token"}}]
        events += [{"type": "message", "source": {"type": "user"}, "message": {"type": t}}
                   for t in ("video", "audio", "file", "location")]
        events += [None, {"type": "join", "source": {"type": "room"}}]
        with self.assertLogs("report_bot", level="INFO") as logs:
            response = self.post({"events": events})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, b"")
        output = " ".join(logs.output)
        for expected in ('group_id="C123"', 'message_type="text"'):
            self.assertIn(expected, output)
        for forbidden in ("test-secret", "test-token", "DO-NOT-LOG"):
            self.assertNotIn(forbidden, output)
        self.assertEqual(sum("report stored" in line for line in logs.output), 1)

    def test_text_report_is_saved(self):
        event = {"webhookEventId": "evt-1", "timestamp": 1700000000000, "type": "message",
                 "source": {"type": "group", "groupId": "C123"},
                 "message": {"id": "msg-1", "type": "text", "text": "รายงานวันนี้"}}
        self.assertEqual(self.post({"events": [event]}).status_code, 200)
        self.assertEqual(len(self.repository.reports), 1)
        report = self.repository.reports[0]
        self.assertEqual(report["message_text"], "รายงานวันนี้")
        self.assertEqual(report["group_id"], "C123")

    def test_image_is_queued_without_network_calls(self):
        event = {"webhookEventId": "evt-image", "type": "message", "source": {"type": "group"},
                 "message": {"id": "img-1", "type": "image"}}
        with patch("urllib.request.urlopen") as urlopen:
            response = urlopen.return_value.__enter__.return_value
            response.read.return_value = b"image-data"
            response.headers.get_content_type.return_value = "image/jpeg"
            self.assertEqual(self.post({"events": [event]}).status_code, 200)
            urlopen.assert_not_called()
        self.assertEqual(self.repository.reports[0]["upload_status"], "pending")
        self.assertIsNone(self.repository.reports[0]["drive_file_id"])

    def test_redelivery_does_not_create_duplicate_job(self):
        event = {"webhookEventId": "same", "type": "message",
                 "message": {"id": "123", "type": "file", "fileName": "report.pdf"}}
        for _ in range(2):
            self.assertEqual(self.post({"events": [event]}).status_code, 200)
        self.assertEqual(len(self.repository.reports), 1)
        self.assertEqual(self.repository.reports[0]["file_name"], "report.pdf")

    def test_one_failed_event_does_not_block_next(self):
        original = self.repository.save
        def save(report):
            if report["line_message_id"] == "fail":
                raise RuntimeError("sensitive credential")
            return original(report)
        self.repository.save = save
        events = [{"type": "message", "message": {"id": message_id, "type": "text", "text": "test"}}
                  for message_id in ("fail", "good")]
        with self.assertLogs("report_bot") as logs:
            response = self.post({"events": events})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(self.repository.reports[0]["line_message_id"], "good")
        self.assertNotIn("sensitive credential", " ".join(logs.output))

    def test_invalid_json_and_payload(self):
        for body in (b'not-json', b'null', b'[]', b'{"events":{}}', b'{}'):
            self.assertEqual(self.post(body=body).status_code, 400)

    def test_missing_secret(self):
        self.app.config["LINE_CHANNEL_SECRET"] = ""
        self.assertEqual(self.post({"events": []}).status_code, 503)

    def test_size_limit(self):
        self.assertEqual(self.post(body=b'x' * (1024 * 1024 + 1)).status_code, 413)

    def test_logging_failure_does_not_stop_request(self):
        with patch("app.json.dumps", side_effect=RuntimeError("sensitive detail")):
            response = self.post(body=b'{"events":[{"type":"join"},{"type":"leave"}]}')
        self.assertEqual(response.status_code, 200)


if __name__ == "__main__":
    unittest.main()
