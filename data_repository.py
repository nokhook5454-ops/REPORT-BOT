"""Durable report sessions and transactional relation storage using existing psycopg."""
import logging
from datetime import timedelta
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg import sql
from data_logic import Config, ReportDetector, NarcoticsExtractionProfile, ExtractionValidator, COLLECTIONS
from migrate import migrate

logger = logging.getLogger('report_bot')
FIELDS = {
 'officers': ('name','rank','position','role','agency'),
 'persons': ('full_name','alias','age','citizen_id','nationality','address','person_role'),
 'charges': ('charge_text_original','charge_category','drug_type'),
 'evidence_items': ('item_type','item_name','quantity','unit','weight','weight_unit','description'),
 'seized_assets': ('asset_type','brand','model','registration','estimated_value','currency','description'),
 'phones': ('brand','model','color','imei','description')}
TABLES = {'officers':'report_officers', 'persons':'report_persons', **{k:k for k in COLLECTIONS if k not in ('officers','persons')}}


class DataRepository:
    def __init__(self, connect, config=None, profile=None):
        self.connect = connect
        self.config = config or Config.from_env()
        self.profile = profile or NarcoticsExtractionProfile()
        self.ready = False

    def ensure(self):
        if not self.ready:
            migrate(self.connect)
            self.ready = True

    def ingest(self, event, received_at):
        self.ensure()
        source, message = event.get('source') or {}, event['message']
        if source.get('type') != 'group':
            return False
        with self.connect() as conn, conn.cursor(row_factory=dict_row) as c:
            c.execute('SELECT * FROM groups WHERE line_group_id=%s AND is_active FOR UPDATE', (source.get('groupId'),))
            group = c.fetchone()
            if not group or group['profile_type'] != 'NARCOTICS':
                return False
            gid, sender, mid = group['id'], source.get('userId'), message['id']
            c.execute("UPDATE report_sessions SET status='CLOSED' WHERE group_id=%s AND status='OPEN' AND expires_at < %s", (gid,received_at))
            if message['type'] == 'text':
                text = message.get('text')
                if not isinstance(text,str) or not ReportDetector(self.config.detection_threshold).detect(text)['is_possible_report']:
                    return False
                c.execute("INSERT INTO reports(group_id,source_message_id,reporter_user_id,profile_type,raw_text,status,received_at) VALUES (%s,%s,%s,%s,%s,'RECEIVING',%s) ON CONFLICT(source_message_id) DO NOTHING RETURNING id", (gid,mid,sender,group['profile_type'],text,received_at))
                row = c.fetchone()
                if not row:
                    return False
                if sender:
                    c.execute("UPDATE report_sessions SET status='CLOSED' WHERE group_id=%s AND sender_user_id=%s AND status='OPEN' AND opened_at <= %s", (gid,sender,received_at))
                    c.execute("INSERT INTO report_sessions(report_id,group_id,sender_user_id,root_message_id,opened_at,last_activity_at,expires_at) VALUES (%s,%s,%s,%s,%s,%s,%s)", (row['id'],gid,sender,mid,received_at,received_at,received_at+timedelta(seconds=self.config.attachment_window)))
                return True
            if message['type'] not in ('image','file','video','audio'):
                return False
            reference = message.get('quotedMessageId')
            report_id = None
            if reference:
                c.execute('SELECT id FROM reports WHERE group_id=%s AND source_message_id=%s UNION SELECT report_id AS id FROM attachments WHERE group_id=%s AND line_message_id=%s AND report_id IS NOT NULL', (gid,reference,gid,reference))
                row = c.fetchone()
                report_id = row['id'] if row else None
            if report_id is None and sender:
                c.execute("SELECT report_id FROM report_sessions WHERE group_id=%s AND sender_user_id=%s AND opened_at <= %s AND expires_at >= %s ORDER BY opened_at DESC LIMIT 1", (gid,sender,received_at,received_at))
                row = c.fetchone()
                report_id = row['report_id'] if row else None
            c.execute('INSERT INTO attachments(report_id,group_id,sender_user_id,reference_message_id,line_message_id,file_type,original_filename,received_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(line_message_id) DO NOTHING RETURNING id', (report_id,gid,sender,reference,mid,message['type'],message.get('fileName'),received_at))
            stored = c.fetchone() is not None
            if stored and report_id:
                c.execute('UPDATE report_sessions SET last_activity_at=GREATEST(last_activity_at,%s) WHERE report_id=%s', (received_at,report_id))
            return stored

    def process_report(self):
        self.ensure()
        with self.connect() as conn, conn.cursor(row_factory=dict_row) as c:
            c.execute("SELECT * FROM reports r WHERE status='RECEIVING' AND NOT EXISTS (SELECT 1 FROM report_sessions s WHERE s.report_id=r.id AND s.expires_at > NOW()) ORDER BY id FOR UPDATE SKIP LOCKED LIMIT 1")
            report = c.fetchone()
            if not report:
                return False
            try:
                with conn.transaction():
                    c.execute("UPDATE reports SET status='PROCESSING' WHERE id=%s", (report['id'],))
                    result = self.profile.extract(report['raw_text'])
                    status, review = ExtractionValidator(self.config).validate(result)
                    c.execute("SELECT 1 FROM attachments WHERE report_id=%s AND analysis_status='FAILED' LIMIT 1",(report['id'],))
                    if c.fetchone():
                        status, review = 'REVIEW_REQUIRED', True
                        result['warnings'].append('Attachment upload failed')
                    person_ids = []
                    for key in ('persons','officers','charges','evidence_items','seized_assets','phones'):
                        for item in result[key]:
                            fields = list(FIELDS[key])
                            values = [item.get(f, 'THB' if f == 'currency' else None) for f in fields]
                            if key in ('charges','evidence_items','seized_assets','phones'):
                                index = item.get('person_index')
                                if index is not None and (type(index) is not int or not 0 <= index < len(person_ids)):
                                    raise ValueError('Unresolved person mapping')
                                fields.append('person_id')
                                values.append(person_ids[index] if index is not None else None)
                            query = sql.SQL('INSERT INTO {} ({}) VALUES ({}) RETURNING id').format(sql.Identifier(TABLES[key]),sql.SQL(',').join(map(sql.Identifier,['report_id']+fields)),sql.SQL(',').join(sql.Placeholder() for _ in range(len(fields)+1)))
                            c.execute(query,[report['id']]+values)
                            child_id = c.fetchone()['id']
                            if key == 'persons':
                                person_ids.append(child_id)
                            if key == 'phones':
                                for number in item.get('phone_numbers',[]):
                                    c.execute('INSERT INTO phone_numbers(phone_id,phone_number,carrier) VALUES (%s,%s,%s)',(child_id,number['phone_number'],number.get('carrier')))
                    fields = list(result['report'])
                    allowed = set(('agency','station','report_type','event_date','event_time','location_text','province','district','subdistrict','summary'))
                    fields = [f for f in fields if f in allowed]
                    c.execute(sql.SQL('UPDATE reports SET {},raw_extraction_json=%s,confidence_score=%s,needs_review=%s,status=%s,updated_at=NOW() WHERE id=%s').format(sql.SQL(',').join(sql.SQL('{}=%s').format(sql.Identifier(f)) for f in fields)),[result['report'][f] for f in fields]+[Jsonb(result),result['confidence'],review,status,report['id']])
            except Exception as error:
                c.execute("UPDATE reports SET status='ERROR',needs_review=TRUE,updated_at=NOW() WHERE id=%s",(report['id'],))
                logger.error('extraction_failed report_id=%s error_type=%s',report['id'],type(error).__name__)
            c.execute("UPDATE report_sessions SET status='CLOSED' WHERE report_id=%s",(report['id'],))
            return True

    def process_attachment(self, upload):
        self.ensure()
        file_id = None
        try:
            with self.connect() as conn, conn.cursor(row_factory=dict_row) as c:
                c.execute("SELECT * FROM attachments WHERE upload_status='pending' AND next_attempt_at<=NOW() ORDER BY id FOR UPDATE SKIP LOCKED LIMIT 1")
                item = c.fetchone()
                if not item:
                    return False
                try:
                    file_id,url = upload({**item,'webhook_event_id':'line:'+item['line_message_id'],'file_name':item['original_filename']})
                except Exception:
                    c.execute("UPDATE attachments SET upload_attempts=upload_attempts+1,upload_status=CASE WHEN upload_attempts>=4 THEN 'failed' ELSE 'pending' END,analysis_status='FAILED',next_attempt_at=NOW()+INTERVAL '1 minute'*(upload_attempts+1) WHERE id=%s",(item['id'],))
                    if item['report_id']:
                        c.execute("UPDATE reports SET needs_review=TRUE,status=CASE WHEN status IN ('READY','REVIEW_REQUIRED') THEN 'REVIEW_REQUIRED' ELSE status END WHERE id=%s",(item['report_id'],))
                    logger.error('attachment_upload_failed attachment_id=%s',item['id'])
                else:
                    c.execute("UPDATE attachments SET drive_file_id=%s,drive_url=%s,upload_status='uploaded',analysis_status='NOT_ANALYZED',upload_attempts=upload_attempts+1 WHERE id=%s",(file_id,url,item['id']))
            return True
        except Exception:
            if file_id:
                logger.error('orphan_drive_file file_id=%s',file_id)
            raise
