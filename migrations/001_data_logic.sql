CREATE TABLE groups (
 id BIGSERIAL PRIMARY KEY, line_group_id TEXT UNIQUE NOT NULL, group_name TEXT NOT NULL,
 profile_type TEXT NOT NULL CHECK(profile_type IN ('NARCOTICS','FIREARMS','GENERAL_CASE')),
 is_active BOOLEAN NOT NULL DEFAULT FALSE, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE reports (
 id BIGSERIAL PRIMARY KEY, group_id BIGINT NOT NULL REFERENCES groups(id), source_message_id TEXT UNIQUE NOT NULL,
 reporter_user_id TEXT, reporter_name TEXT, profile_type TEXT NOT NULL,
 agency TEXT, station TEXT, report_type TEXT, event_date DATE, event_time TIME, location_text TEXT,
 province TEXT, district TEXT, subdistrict TEXT, summary TEXT, raw_text TEXT NOT NULL,
 raw_extraction_json JSONB, confidence_score DOUBLE PRECISION, needs_review BOOLEAN NOT NULL DEFAULT FALSE,
 status TEXT NOT NULL CHECK(status IN ('RECEIVING','PROCESSING','READY','REVIEW_REQUIRED','ERROR')),
 received_at TIMESTAMPTZ NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE report_sessions (
 session_id BIGSERIAL PRIMARY KEY, report_id BIGINT NOT NULL REFERENCES reports(id), group_id BIGINT NOT NULL REFERENCES groups(id),
 sender_user_id TEXT NOT NULL, root_message_id TEXT NOT NULL, opened_at TIMESTAMPTZ NOT NULL,
 last_activity_at TIMESTAMPTZ NOT NULL, expires_at TIMESTAMPTZ NOT NULL, status TEXT NOT NULL DEFAULT 'OPEN'
);
CREATE INDEX session_matching ON report_sessions(group_id,sender_user_id,expires_at);
CREATE TABLE report_officers (
 id BIGSERIAL PRIMARY KEY, report_id BIGINT NOT NULL REFERENCES reports(id), name TEXT NOT NULL,
 rank TEXT, position TEXT, role TEXT NOT NULL, agency TEXT
);
CREATE TABLE report_persons (
 id BIGSERIAL PRIMARY KEY, report_id BIGINT NOT NULL REFERENCES reports(id), full_name TEXT NOT NULL,
 alias TEXT, age INTEGER, citizen_id TEXT, nationality TEXT, address TEXT, person_role TEXT NOT NULL
);
CREATE TABLE charges (
 id BIGSERIAL PRIMARY KEY, report_id BIGINT NOT NULL REFERENCES reports(id), person_id BIGINT REFERENCES report_persons(id),
 charge_text_original TEXT NOT NULL, charge_category TEXT, drug_type TEXT
);
CREATE TABLE evidence_items (
 id BIGSERIAL PRIMARY KEY, report_id BIGINT NOT NULL REFERENCES reports(id), person_id BIGINT REFERENCES report_persons(id),
 item_type TEXT NOT NULL, item_name TEXT NOT NULL, quantity NUMERIC, unit TEXT, weight NUMERIC, weight_unit TEXT,
 description TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE seized_assets (
 id BIGSERIAL PRIMARY KEY, report_id BIGINT NOT NULL REFERENCES reports(id), person_id BIGINT REFERENCES report_persons(id),
 asset_type TEXT NOT NULL, brand TEXT, model TEXT, registration TEXT, estimated_value NUMERIC,
 currency TEXT NOT NULL DEFAULT 'THB', description TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE phones (
 id BIGSERIAL PRIMARY KEY, report_id BIGINT NOT NULL REFERENCES reports(id), person_id BIGINT REFERENCES report_persons(id),
 brand TEXT, model TEXT, color TEXT, imei TEXT, description TEXT
);
CREATE TABLE phone_numbers (
 id BIGSERIAL PRIMARY KEY, phone_id BIGINT NOT NULL REFERENCES phones(id), phone_number TEXT NOT NULL, carrier TEXT
);
CREATE TABLE attachments (
 id BIGSERIAL PRIMARY KEY, report_id BIGINT REFERENCES reports(id), group_id BIGINT NOT NULL REFERENCES groups(id),
 sender_user_id TEXT, reference_message_id TEXT, line_message_id TEXT UNIQUE NOT NULL,
 file_type TEXT NOT NULL, original_filename TEXT, mime_type TEXT, drive_file_id TEXT, drive_url TEXT,
 received_at TIMESTAMPTZ NOT NULL, analysis_status TEXT NOT NULL DEFAULT 'NOT_ANALYZED',
 upload_status TEXT NOT NULL DEFAULT 'pending', upload_attempts INTEGER NOT NULL DEFAULT 0,
 next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX attachment_jobs ON attachments(next_attempt_at) WHERE upload_status='pending';
