"""Poll PostgreSQL upload jobs; started after gunicorn forks."""
import logging
import os
import threading

from app import ReportRepository


def run(stop):
    repository = ReportRepository(os.environ["DATABASE_URL"])
    uploader = None
    logger = logging.getLogger("report_bot")
    while not stop.is_set():
        try:
            extracted = repository.data_logic.process_report()
            drive_required = ('GOOGLE_CLIENT_ID', 'GOOGLE_CLIENT_SECRET',
                              'GOOGLE_REFRESH_TOKEN', 'GOOGLE_DRIVE_FOLDER_ID', 'LINE_CHANNEL_ACCESS_TOKEN')
            if not all(os.environ.get(key) for key in drive_required):
                if not extracted:
                    stop.wait(2)
                continue
            if uploader is None:
                from drive_storage import GoogleDriveUploader
                uploader = GoogleDriveUploader()
            worked = repository.data_logic.process_attachment(uploader.upload_report)
            # Drain historical jobs created before Data Logic v1.
            worked = repository.process_next(uploader.upload_report) or worked or extracted
        except Exception:
            logger.error("upload worker unavailable; check database and Google OAuth variables")
            stop.wait(30)
        else:
            if not worked:
                stop.wait(2)


def start():
    required = ("DATABASE_URL",)
    if not all(os.environ.get(key) for key in required):
        logging.getLogger("report_bot").warning("upload worker disabled: required variables missing; jobs remain in PostgreSQL")
        return
    threading.Thread(target=run, args=(threading.Event(),), daemon=True, name="drive-upload-worker").start()
