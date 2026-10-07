"""Integration tests use only an explicitly supplied disposable database."""
import os
import unittest
import uuid
import base64
import hashlib
import hmac
import json
from datetime import datetime, timezone, timedelta
from unittest.mock import patch
from data_repository import DataRepository
from data_logic import NarcoticsExtractionProfile


@unittest.skipUnless(os.getenv('TEST_DATABASE_URL'), 'dedicated PostgreSQL test database not configured')
class DataPostgresTests(unittest.TestCase):
    def setUp(self):
        import psycopg
        from psycopg import sql
        self.schema = 'data_test_' + uuid.uuid4().hex
        self.url = os.environ['TEST_DATABASE_URL']
        with psycopg.connect(self.url) as c:
            c.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(self.schema)))
        self.repo = DataRepository(lambda: psycopg.connect(self.url,options='-c search_path='+self.schema))
        self.repo.ensure()
        with self.repo.connect() as c:
            c.execute("INSERT INTO groups(line_group_id,group_name,profile_type,is_active) VALUES ('Ctest','Synthetic','NARCOTICS',TRUE)")
        self.now = datetime.now(timezone.utc) - timedelta(minutes=10)

    def tearDown(self):
        import psycopg
        from psycopg import sql
        with psycopg.connect(self.url) as c:
            c.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(self.schema)))

    def event(self, mid='root', sender='sender', group='Ctest', kind='text'):
        return {'type':'message','source':{'type':'group','groupId':group,'userId':sender},
                'message':{'id':mid,'type':kind,'text':'เรียนผู้บังคับบัญชา จับกุม ของกลาง ยาบ้า 3,030 เม็ด'}}

    def test_allowlist_and_chat(self):
        self.assertFalse(self.repo.ingest(self.event(group='other'),self.now))
        with self.repo.connect() as c:
            c.execute('UPDATE groups SET is_active=FALSE')
        self.assertFalse(self.repo.ingest(self.event(),self.now))
        with self.repo.connect() as c:
            c.execute('UPDATE groups SET is_active=TRUE')
        event = self.event()
        event['message']['text'] = 'รับทราบครับ'
        self.assertFalse(self.repo.ingest(event,self.now))

    def test_linking_and_dedup(self):
        self.assertTrue(self.repo.ingest(self.event(),self.now))
        self.assertFalse(self.repo.ingest(self.event(),self.now))
        for mid,sender,seconds in [('same','sender',60),('other','different',60),('late','sender',181)]:
            self.repo.ingest(self.event(mid,sender,kind='file'),self.now+timedelta(seconds=seconds))
        with self.repo.connect() as c:
            rows = dict(c.execute('SELECT line_message_id,report_id FROM attachments').fetchall())
            self.assertIsNotNone(rows['same'])
            self.assertIsNone(rows['other'])
            self.assertIsNone(rows['late'])

    def test_save_relations_and_rollback(self):
        result = NarcoticsExtractionProfile().extract('')
        result['persons'] = [{'full_name':'บุคคลสมมติ','person_role':'SUSPECT'}]
        result['officers'] = [{'name':'เจ้าหน้าที่สมมติ','role':'OPERATOR'}]
        result['charges'] = [{'charge_text_original':'ข้อหาสมมติ','person_index':0}]
        result['phones'] = [{'person_index':0,'phone_numbers':[{'phone_number':'0000000000'}]}]
        self.repo.ingest(self.event(),self.now)
        with patch.object(self.repo.profile,'extract',return_value=result):
            self.assertTrue(self.repo.process_report())
        with self.repo.connect() as c:
            self.assertEqual(c.execute('SELECT count(*) FROM phone_numbers').fetchone()[0],1)
            self.assertEqual(c.execute('SELECT count(*) FROM charges WHERE person_id IS NOT NULL').fetchone()[0],1)
        self.repo.ingest(self.event('broken'),self.now)
        result['charges'][0]['person_index'] = 99
        with patch.object(self.repo.profile,'extract',return_value=result):
            self.repo.process_report()
        with self.repo.connect() as c:
            self.assertEqual(c.execute('SELECT count(*) FROM report_persons').fetchone()[0],1)
            self.assertEqual(c.execute("SELECT status FROM reports WHERE source_message_id='broken'").fetchone()[0],'ERROR')

    def test_upload_failure_durable(self):
        self.repo.ingest(self.event(),self.now)
        self.repo.ingest(self.event('file',kind='file'),self.now)
        def fail(item):
            raise RuntimeError('sensitive response')
        with self.assertLogs('report_bot') as logs:
            self.assertTrue(self.repo.process_attachment(fail))
        self.assertNotIn('sensitive response',' '.join(logs.output))
        with self.repo.connect() as c:
            self.assertEqual(c.execute('SELECT analysis_status,upload_attempts FROM attachments').fetchone(),('FAILED',1))

    def test_signed_webhook_to_database_and_drive_uploader(self):
        from app import create_app
        from drive_storage import GoogleDriveUploader
        from unittest.mock import MagicMock
        with patch.dict(os.environ, {'LINE_CHANNEL_SECRET':'synthetic-secret'}):
            client = create_app(repository=self.repo).test_client()
        events = [self.event(),self.event('photo',kind='image'),self.event('pdf',kind='file')]
        for event in events:
            event['timestamp'] = int(self.now.timestamp()*1000)
        # The repository interface used by production is exercised through the signed endpoint.
        body = json.dumps({'events':events}).encode()
        signature = base64.b64encode(hmac.new(b'synthetic-secret',body,hashlib.sha256).digest()).decode()
        self.assertEqual(client.post('/webhook',data=body,headers={'X-Line-Signature':signature}).status_code,200)
        self.assertTrue(self.repo.process_report())
        uploader = GoogleDriveUploader.__new__(GoogleDriveUploader)
        uploader.folder_id, uploader.line_token, uploader.max_bytes = 'synthetic-folder','synthetic-token',1024
        uploader.service = MagicMock()
        files = uploader.service.files.return_value
        files.list.return_value.execute.return_value = {'files':[]}
        files.create.return_value.execute.return_value = {'id':'synthetic-drive-id','webViewLink':'https://example.invalid/test'}
        for _ in range(2):
            with patch('drive_storage.urllib.request.urlopen') as download:
                response = download.return_value.__enter__.return_value
                response.read.side_effect = [b'SYNTHETIC TEST DATA',b'']
                response.headers.get_content_type.return_value = 'application/octet-stream'
                self.assertTrue(self.repo.process_attachment(uploader.upload_report))
        with self.repo.connect() as c:
            self.assertEqual(c.execute('SELECT count(*) FROM reports').fetchone()[0],1)
            self.assertEqual(c.execute("SELECT count(*) FROM attachments WHERE report_id IS NOT NULL AND upload_status='uploaded'").fetchone()[0],2)
            self.assertEqual(c.execute('SELECT count(*) FROM evidence_items').fetchone()[0],1)

    def test_reference_priority_new_report_and_unknown_sender(self):
        self.repo.ingest(self.event(),self.now)
        self.repo.ingest(self.event('second'),self.now+timedelta(seconds=60))
        quoted = self.event('reference','different',kind='file')
        quoted['message']['quotedMessageId'] = 'root'
        self.repo.ingest(quoted,self.now+timedelta(seconds=400))
        self.repo.ingest(self.event('current',kind='file'),self.now+timedelta(seconds=70))
        self.repo.ingest(self.event('unknown',sender=None,kind='file'),self.now+timedelta(seconds=70))
        with self.repo.connect() as c:
            roots = dict(c.execute('SELECT source_message_id,id FROM reports').fetchall())
            media = dict(c.execute('SELECT line_message_id,report_id FROM attachments').fetchall())
            self.assertEqual(media['reference'],roots['root'])
            self.assertEqual(media['current'],roots['second'])
            self.assertIsNone(media['unknown'])

    def test_migration_idempotent_and_failure_keeps_raw_text(self):
        from migrate import migrate
        migrate(self.repo.connect)
        self.repo.ingest(self.event(),self.now)
        with patch.object(self.repo.profile,'extract',side_effect=RuntimeError('sensitive payload')):
            self.repo.process_report()
        with self.repo.connect() as c:
            status,text = c.execute('SELECT status,raw_text FROM reports').fetchone()
            self.assertEqual(status,'ERROR')
            self.assertEqual(text,self.event()['message']['text'])
