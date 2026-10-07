"""OAuth Drive uploads; no sharing or permission changes."""
import hashlib
import mimetypes
import os
import re
import tempfile
import urllib.parse
import urllib.request

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload


class GoogleDriveUploader:
    def __init__(self):
        self.folder_id = os.environ["GOOGLE_DRIVE_FOLDER_ID"]
        self.line_token = os.environ["LINE_CHANNEL_ACCESS_TOKEN"]
        credentials = Credentials(token=None, refresh_token=os.environ["GOOGLE_REFRESH_TOKEN"],
            client_id=os.environ["GOOGLE_CLIENT_ID"], client_secret=os.environ["GOOGLE_CLIENT_SECRET"],
            token_uri="https://oauth2.googleapis.com/token")
        import httplib2
        import google_auth_httplib2
        http = google_auth_httplib2.AuthorizedHttp(credentials, http=httplib2.Http(timeout=30))
        self.service = build("drive", "v3", http=http, cache_discovery=False)
        self.max_bytes = int(os.environ.get("MAX_MEDIA_BYTES", str(100 * 1024 * 1024)))

    def upload_report(self, report):
        # Recover a completed upload after a crash before PostgreSQL commit.
        event_key = hashlib.sha256(report["webhook_event_id"].encode()).hexdigest()
        folder = self.folder_id.replace("\\", "\\\\").replace("'", "\\'")
        existing = self.service.files().list(
            q=f"'{folder}' in parents and trashed=false and appProperties has {{ key='report_event' and value='{event_key}' }}",
            fields="files(id,webViewLink)", pageSize=1).execute(num_retries=2).get("files", [])
        if existing:
            return existing[0]["id"], existing[0].get("webViewLink")
        message_id = urllib.parse.quote(report["line_message_id"], safe="")
        req = urllib.request.Request(f"https://api-data.line.me/v2/bot/message/{message_id}/content",
            headers={"Authorization": "Bearer " + self.line_token})
        with tempfile.TemporaryDirectory(prefix="report-bot-") as directory:
            path = os.path.join(directory, "content")
            with urllib.request.urlopen(req, timeout=30) as response, open(path, "wb") as target:
                mime_type = response.headers.get_content_type()
                size = 0
                while chunk := response.read(1024 * 1024):
                    size += len(chunk)
                    if size > self.max_bytes:
                        raise ValueError("media exceeds configured limit")
                    target.write(chunk)
            original = report.get("file_name")
            if original:
                name = re.sub(r'[\\/\x00-\x1f\x7f]', '_', original)[:180]
            else:
                name = "line-" + re.sub(r'[^A-Za-z0-9_-]', '_', report["line_message_id"])[:100]
                name += mimetypes.guess_extension(mime_type) or ".bin"
            media = MediaFileUpload(path, mimetype=mime_type, resumable=True)
            try:
                result = self.service.files().create(
                    body={"name": name, "parents": [self.folder_id], "appProperties": {"report_event": event_key}},
                    media_body=media, fields="id,webViewLink").execute(num_retries=2)
            finally:
                media.stream().close()
            return result["id"], result.get("webViewLink")
