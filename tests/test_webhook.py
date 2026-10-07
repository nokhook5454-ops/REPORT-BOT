import base64
import hashlib
import hmac
import json
import os
import unittest
from unittest.mock import patch

from app import create_app


class WebhookTests(unittest.TestCase):
    def setUp(self):
        with patch.dict(os.environ, {
            "LINE_CHANNEL_SECRET": "test-secret",
            "LINE_CHANNEL_ACCESS_TOKEN": "test-token",
        }):
            self.app = create_app()
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
                   "replyToken": "DO-NOT-LOG", "message": {"type": "text", "text": "hello\nforged test-secret test-token"}}]
        events += [{"type": "message", "source": {"type": "user"}, "message": {"type": t}}
                   for t in ("image", "video", "audio", "file", "location")]
        events += [None, {"type": "join", "source": {"type": "room"}}]
        with self.assertLogs("report_bot", level="INFO") as logs:
            response = self.post({"events": events})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, b"")
        output = " ".join(logs.output)
        for expected in ('group_id="C123"', 'source_type="room"', 'event_type="join"', r'hello\nforged'):
            self.assertIn(expected, output)
        for forbidden in ("test-secret", "test-token", "DO-NOT-LOG"):
            self.assertNotIn(forbidden, output)
        self.assertEqual(sum("event received" in line for line in logs.output), 7)

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
