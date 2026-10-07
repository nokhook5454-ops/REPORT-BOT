"""Opt-in smoke test: isolated test DB + real Drive, simulated LINE media only.

Retains one clearly named synthetic file in Drive as evidence. Never uses DATABASE_URL.
"""
import argparse
import base64
import hashlib
import hmac
import io
import json
import os
from pathlib import Path
import sys
import uuid
from datetime import datetime, timezone, timedelta
from email.message import Message
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--oauth-env', required=True, type=Path)
    parser.add_argument('--upload-synthetic-file', action='store_true', required=True)
    args = parser.parse_args()
    import psycopg
    from psycopg import sql
    from app import create_app
    from data_repository import DataRepository
    from drive_storage import GoogleDriveUploader

    url = os.environ.get('TEST_DATABASE_URL')
    if not url:
        parser.error('TEST_DATABASE_URL must point to a disposable test database')
    allowed = {'GOOGLE_CLIENT_ID','GOOGLE_CLIENT_SECRET','GOOGLE_REFRESH_TOKEN','GOOGLE_DRIVE_FOLDER_ID'}
    env = {}
    for line in args.oauth_env.read_text(encoding='utf-8-sig').splitlines():
        key, separator, value = line.partition('=')
        if separator and key in allowed:
            env[key] = value
    if not all(env.get(key) for key in allowed):
        parser.error('OAuth file is missing required variables')
    run_id = uuid.uuid4().hex
    schema = 'smoke_' + run_id
    with psycopg.connect(url) as conn:
        conn.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
    connect = lambda: psycopg.connect(url,options='-c search_path='+schema)
    try:
        repository = DataRepository(connect)
        repository.ensure()
        with connect() as conn:
            conn.execute("INSERT INTO groups(line_group_id,group_name,profile_type,is_active) VALUES ('Csynthetic','Synthetic QA','NARCOTICS',TRUE)")
        timestamp = int((datetime.now(timezone.utc)-timedelta(minutes=10)).timestamp()*1000)
        events = [{'type':'message','timestamp':timestamp,
                   'source':{'type':'group','groupId':'Csynthetic','userId':'Usynthetic'},
                   'message':{'id':'qa-report-'+run_id,'type':'text',
                              'text':'เรียนผู้บังคับบัญชา รายงานผลการปฏิบัติ จับกุม ของกลาง ยาบ้า 3,030 เม็ด (ข้อมูลสมมติ QA)'}},
                  {'type':'message','timestamp':timestamp+1000,
                   'source':{'type':'group','groupId':'Csynthetic','userId':'Usynthetic'},
                   'message':{'id':'qa-file-'+run_id,'type':'file','fileName':'REPORT-BOT-QA-SYNTHETIC-'+run_id+'.txt'}}]
        with patch.dict(os.environ,{**env,'LINE_CHANNEL_SECRET':'synthetic-secret','LINE_CHANNEL_ACCESS_TOKEN':'synthetic-token-not-used'}):
            uploader = GoogleDriveUploader()
            folder = uploader.service.files().get(fileId=env['GOOGLE_DRIVE_FOLDER_ID'],fields='id,mimeType,capabilities(canAddChildren)').execute()
            assert folder['mimeType']=='application/vnd.google-apps.folder' and folder['capabilities']['canAddChildren']
            body = json.dumps({'events':events}).encode()
            signature = base64.b64encode(hmac.new(b'synthetic-secret',body,hashlib.sha256).digest()).decode()
            response = create_app(repository).test_client().post('/webhook',data=body,headers={'X-Line-Signature':signature})
            assert response.status_code==200
            assert repository.process_report()
            payload = ('REPORT-BOT Data Logic v1 synthetic QA file.\nNo real people or operational data.\nRun '+run_id+'\n').encode()
            fixture = io.BytesIO(payload)
            fixture.headers = Message()
            fixture.headers['Content-Type'] = 'text/plain'
            # Only LINE download is mocked; OAuth, Drive upload and readback are real.
            with patch('drive_storage.urllib.request.urlopen',return_value=fixture):
                assert repository.process_attachment(uploader.upload_report)
            with connect() as conn:
                row = conn.execute('SELECT a.drive_file_id,a.drive_url,a.upload_status,a.report_id,r.status FROM attachments a JOIN reports r ON r.id=a.report_id').fetchone()
            assert row[0] and row[2]=='uploaded' and row[4]=='REVIEW_REQUIRED'
            metadata = uploader.service.files().get(fileId=row[0],fields='id,name,parents,size,webViewLink').execute()
            assert env['GOOGLE_DRIVE_FOLDER_ID'] in metadata['parents']
            assert int(metadata['size'])==len(payload)
            with connect() as conn:
                from psycopg.rows import dict_row
                item = conn.cursor(row_factory=dict_row).execute('SELECT * FROM attachments').fetchone()
            with patch('drive_storage.urllib.request.urlopen',side_effect=AssertionError('duplicate download')):
                recovered = uploader.upload_report({**item,'webhook_event_id':'line:'+item['line_message_id'],'file_name':item['original_filename']})
            assert recovered[0]==row[0]
            print(json.dumps({'status':'PASS','line_download':'SIMULATED','database':'isolated test schema',
                'drive_upload':'REAL','duplicate_recovery':'PASS','file_id':row[0],
                'file_url':metadata.get('webViewLink'),'file_name':metadata['name']},ensure_ascii=False))
    finally:
        with psycopg.connect(url) as conn:
            conn.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print('Smoke test failed: '+type(error).__name__,file=sys.stderr)
        sys.exit(1)
