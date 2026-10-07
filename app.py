"""REPORT-BOT v1: receive and log LINE events without sending messages."""

import base64
import hashlib
import hmac
import json
import logging
import os
import sys

from flask import Flask, request


def create_app():
    app = Flask(__name__)
    app.config.update(
        MAX_CONTENT_LENGTH=1024 * 1024,
        LINE_CHANNEL_SECRET=os.environ.get("LINE_CHANNEL_SECRET", ""),
        LINE_CHANNEL_ACCESS_TOKEN=os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", ""),
    )
    logger = logging.getLogger("report_bot")
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter("[REPORT-BOT] %(message)s"))
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False

    def safe_value(value):
        if not isinstance(value, str):
            return '"unknown"'
        for key in ("LINE_CHANNEL_SECRET", "LINE_CHANNEL_ACCESS_TOKEN"):
            secret = app.config[key]
            if secret:
                value = value.replace(secret, "[REDACTED]")
        if len(value) > 2000:
            value = value[:2000] + "...[truncated]"
        # Escape control characters and newlines to prevent log injection.
        return json.dumps(value, ensure_ascii=True)

    def log_event(event):
        source = event.get("source")
        source = source if isinstance(source, dict) else {}
        fields = [
            "event_type=" + safe_value(event.get("type")),
            "source_type=" + safe_value(source.get("type")),
        ]
        if "groupId" in source:
            fields.append("group_id=" + safe_value(source["groupId"]))
        if event.get("type") == "message":
            message = event.get("message")
            message = message if isinstance(message, dict) else {}
            fields.append("message_type=" + safe_value(message.get("type")))
            if message.get("type") == "text":
                fields.append("text=" + safe_value(message.get("text")))
        logger.info("event received %s", " ".join(fields))

    @app.get("/")
    def health():
        return "REPORT BOT is running", 200, {"Content-Type": "text/plain; charset=utf-8"}

    @app.post("/webhook")
    def webhook():
        secret = app.config["LINE_CHANNEL_SECRET"]
        if not secret:
            logger.error("webhook unavailable: LINE_CHANNEL_SECRET is not configured")
            return "Service unavailable", 503
        body = request.get_data()
        signature = request.headers.get("X-Line-Signature", "")
        expected = base64.b64encode(
            hmac.new(secret.encode("utf-8"), body, hashlib.sha256).digest()
        )
        if not hmac.compare_digest(expected, signature.encode("utf-8")):
            logger.warning("webhook rejected: invalid signature")
            return "Invalid signature", 400
        try:
            payload = json.loads(body)
        except (ValueError, UnicodeDecodeError):
            logger.warning("webhook rejected: invalid JSON")
            return "Invalid JSON", 400
        if not isinstance(payload, dict) or not isinstance(payload.get("events"), list):
            logger.warning("webhook rejected: invalid events payload")
            return "Invalid events", 400
        logger.info("webhook received events=%d", len(payload["events"]))
        for event in payload["events"]:
            try:
                if not isinstance(event, dict):
                    logger.warning("event skipped: invalid event object")
                    continue
                log_event(event)
            except Exception:
                # Do not log exception details, payload, credentials, or headers.
                logger.error("event logging failed")
        return "", 200

    return app


app = create_app()
