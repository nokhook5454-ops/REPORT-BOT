import io
import os
import unittest
from unittest.mock import MagicMock, patch

from drive_storage import GoogleDriveUploader


class UploadTests(unittest.TestCase):
    def setUp(self):
        self.uploader = GoogleDriveUploader.__new__(GoogleDriveUploader)
        self.uploader.folder_id = "folder"
        self.uploader.line_token = "token-do-not-log"
        self.uploader.max_bytes = 1024
        self.uploader.service = MagicMock()
        self.files = self.uploader.service.files.return_value
        self.files.list.return_value.execute.return_value = {"files": []}
        self.files.create.return_value.execute.return_value = {"id": "drive-1", "webViewLink": "link"}
        self.report = {"webhook_event_id": "event-1", "line_message_id": "message/1",
                       "file_name": "report.pdf"}

    def test_existing_upload_is_reused_without_download(self):
        self.files.list.return_value.execute.return_value = {"files": [{"id": "existing", "webViewLink": "url"}]}
        with patch("drive_storage.urllib.request.urlopen") as download:
            self.assertEqual(self.uploader.upload_report(self.report), ("existing", "url"))
            download.assert_not_called()
        self.files.create.assert_not_called()

    def test_upload_and_temp_file_cleanup(self):
        with patch("drive_storage.urllib.request.urlopen") as download:
            response = download.return_value.__enter__.return_value
            response.read.side_effect = [b"pdf-data", b""]
            response.headers.get_content_type.return_value = "application/pdf"
            self.assertEqual(self.uploader.upload_report(self.report), ("drive-1", "link"))
        call = self.files.create.call_args.kwargs
        self.assertEqual(call["body"]["parents"], ["folder"])
        self.assertEqual(call["body"]["name"], "report.pdf")
        self.assertIn("report_event", call["body"]["appProperties"])
        self.assertFalse(os.path.exists(call["media_body"]._filename))

    def test_oversized_file_is_not_uploaded(self):
        with patch("drive_storage.urllib.request.urlopen") as download:
            response = download.return_value.__enter__.return_value
            response.read.return_value = b"x" * 1025
            response.headers.get_content_type.return_value = "application/octet-stream"
            with self.assertRaises(ValueError):
                self.uploader.upload_report(self.report)
        self.files.create.assert_not_called()

    def test_upload_failure_closes_and_removes_temp_file(self):
        self.files.create.return_value.execute.side_effect = RuntimeError("remote failure")
        with patch("drive_storage.urllib.request.urlopen") as download:
            response = download.return_value.__enter__.return_value
            response.read.side_effect = [b"data", b""]
            response.headers.get_content_type.return_value = "application/pdf"
            with self.assertRaises(RuntimeError):
                self.uploader.upload_report(self.report)
        media = self.files.create.call_args.kwargs["media_body"]
        self.assertTrue(media.stream().closed)
        self.assertFalse(os.path.exists(media._filename))
