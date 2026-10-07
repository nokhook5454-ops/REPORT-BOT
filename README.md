# REPORT-BOT — Drive + PostgreSQL

Silent LINE collector: ข้อความเก็บใน PostgreSQL; image/file/video/audio ดาวน์โหลดจาก LINE แล้วอัปโหลด Google Drive ของ `nokhook5454@gmail.com` ไม่มี Reply/Push API หรือ AI และไม่เปลี่ยนสิทธิ์แชร์ไฟล์

## การทำงาน

`LINE → ตรวจ signature → INSERT PostgreSQL → HTTP 200 → worker → LINE Content API → Drive → บันทึกลิงก์ PostgreSQL`

- `GET /` → 200 `REPORT BOT is running` เป็น liveness check ไม่ได้ยืนยัน DB/Drive
- `POST /webhook` ตรวจ HMAC-SHA256 จาก raw body; invalid signature/JSON → 400, ไม่มี secret หรือบันทึก DB ไม่สำเร็จ → 503, body เกิน 1 MiB → 413
- รองรับหลาย events และ `webhookEventId` ป้องกันบันทึกซ้ำ ข้อความเก็บเต็มใน DB ไม่พิมพ์เนื้อหารายงานลง logs
- Worker ทำงานใน gunicorn หลัง fork ใช้ PostgreSQL row lock ป้องกันงานซ้ำระหว่าง workers และเก็บสถานะ `pending/uploaded/failed` ลง DB
- Upload retry สูงสุด 5 ครั้ง เว้น 1–4 นาทีระหว่างครั้ง; หาก process crash งานยังอยู่ใน DB ตรวจ Drive appProperties ก่อนอัปโหลดซ้ำ (ไม่ใช่การรับประกัน exactly-once เมื่อ Drive search ยังไม่เห็นไฟล์ที่เพิ่งสร้าง)
- รูปและเอกสารไม่ถูกเก็บใน Railway disk ถาวร ใช้ temporary file ระหว่างอัปโหลดแล้วลบ จำกัดไฟล์ 100 MiB โดยปรับ `MAX_MEDIA_BYTES` ได้
- LINE ไม่รับประกันระยะเวลาที่ดาวน์โหลด media ได้ จึงควรเชื่อม OAuth ให้พร้อมก่อนเปิดใช้จริง ไม่สามารถดึงข้อความย้อนหลังจาก LINE มาใหม่ได้
- location/sticker เก็บชนิดและ metadata พื้นฐาน ไม่มีการดึงไฟล์

## Railway

GitHub: `nokhook5454-ops/REPORT-BOT` (CLI บนเครื่องอาจล็อกอินบัญชีอื่น ตรวจบัญชีก่อน push)

1. เพิ่ม Database → PostgreSQL ใน project เดียวกับ REPORT-BOT
2. ใน REPORT-BOT → Variables เพิ่ม reference `DATABASE_URL=${{Postgres.DATABASE_URL}}` ใช้ชื่อ service จริงและ private network
3. ตั้ง variables ตาม `.env.example` แล้ว deploy
4. Start command:

```sh
gunicorn -c gunicorn.conf.py app:app --bind 0.0.0.0:$PORT --workers 1 --threads 4 --timeout 30 --error-logfile -
```

Railway กำหนด `PORT` ให้เอง ตั้ง healthcheck `/` และปิด Serverless เพื่อให้ worker poll งานตลอด Procfile ระบุ command เดียวกัน; ตั้งใน service โดยตรงด้วยหาก Railway ไม่ใช้ railway.json

| Variable | ใช้ทำอะไร |
|---|---|
| LINE_CHANNEL_SECRET | ตรวจ webhook signature |
| LINE_CHANNEL_ACCESS_TOKEN | ดาวน์โหลด media จาก LINE |
| DATABASE_URL | PostgreSQL private connection reference |
| GOOGLE_DRIVE_FOLDER_ID | `1lEdKhHDGHkAcNMclIJPzQvjKbxAxsL-M` |
| GOOGLE_CLIENT_ID | OAuth Desktop client ID |
| GOOGLE_CLIENT_SECRET | OAuth Desktop client secret |
| GOOGLE_REFRESH_TOKEN | Token ที่ได้จากการอนุญาตบัญชี Google |
| MAX_MEDIA_BYTES | ค่าเริ่มต้น 104857600 (100 MiB) |

ไม่ใช้ service account สำหรับ Gmail ส่วนตัว ห้าม commit credentials หรือนำค่า secret/token ลง logs/แชต

## ตั้ง OAuth ครั้งแรก

1. เข้าสู่ [Google Cloud Console](https://console.cloud.google.com/) ด้วย `nokhook5454@gmail.com` สร้าง project `REPORT-BOT`
2. APIs & Services → Library → Google Drive API → Enable
3. Google Auth Platform → Branding ตั้งชื่อ REPORT-BOT และ support/developer email ของคุณ; Audience เลือก External และเพิ่ม `nokhook5454@gmail.com` เป็น Test user
4. Clients → Create client → Desktop app → ตั้งชื่อ REPORT-BOT Local Setup → Download JSON เก็บเป็น `.secrets/client_secret.json` ในเครื่องเท่านั้น
5. Data Access เพิ่ม scope `https://www.googleapis.com/auth/drive` เพื่อเข้าถึงโฟลเดอร์ที่มีอยู่แล้ว การอนุญาตนี้กว้างกว่าโฟลเดอร์เดียว: Google จะให้สิทธิ์จัดการ Drive แต่โค้ดนี้อัปโหลดเฉพาะโฟลเดอร์ที่กำหนด ไม่ลบหรือแชร์ไฟล์ หากต้องการสิทธิ์แคบ `drive.file` ต้องเพิ่ม Google Picker เพื่อให้เลือกโฟลเดอร์ก่อน
6. ติดตั้ง dependencies แล้วรัน:

```powershell
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python scripts/authorize_drive.py .secrets/client_secret.json
```

Browser เปิดหน้าล็อกอิน/consent ให้คุณอ่านและอนุญาตเอง Script ตรวจบัญชีและสิทธิ์เขียนโฟลเดอร์ จากนั้นบันทึกค่าลง `.secrets/google-oauth.env` โดยไม่พิมพ์ token ให้ย้ายค่าลง Railway Variables ด้วยตนเองและ deploy

OAuth External ที่สถานะ Testing มี refresh token อายุ 7 วันสำหรับ Drive scope เมื่อพร้อมใช้งานต่อเนื่องต้องเปลี่ยน publishing status เป็น Production และดำเนินการ verification ตามที่ Google กำหนด อย่าเข้าใจว่า refresh token เป็น token ที่ไม่มีวันหมดอายุ

## ทดสอบ

Webhook URL: `https://report-bot-production-3f2b.up.railway.app/webhook`

เปิด LINE Developers → Messaging API → Verify → Use webhook; เปิด Webhook redelivery เพื่อให้ LINE retry เมื่อ DB ตอบ 503 เปิด Allow bot to join group chats และปิด Auto-response/Greeting messages

ส่งข้อความสั้น รูป และ PDF ใหม่ ตรวจ Railway Logs:

```text
[REPORT-BOT] webhook received events=1
[REPORT-BOT] report stored message_type="image" source_type="group" group_id="C..."
[REPORT-BOT] drive uploaded report_id=1 file_id=...
```

ตาราง `line_reports` มีข้อความเต็ม, LINE message ID, เวลา, group ID, ชื่อไฟล์, สถานะอัปโหลด และ `drive_file_id/drive_url` สร้าง/เพิ่มคอลัมน์โดยอัตโนมัติโดยไม่ลบข้อมูลเดิม

SQL ตรวจสถานะ (ไม่แสดงเนื้อหารายงาน):

```sql
SELECT id, message_type, upload_status, upload_attempts, drive_file_id, received_at
FROM line_reports ORDER BY id DESC LIMIT 20;
```

หลังแก้ OAuth/พื้นที่ Drive แล้ว retry งานที่ failed:

```sql
UPDATE line_reports SET upload_status='pending', upload_attempts=0, next_attempt_at=NOW()
WHERE upload_status='failed';
```

Unit tests: `.\.venv\Scripts\python -m unittest discover -s tests -v`

PostgreSQL integration tests ใช้ฐานข้อมูลทดสอบเฉพาะ (ห้าม production) โดยตั้ง `TEST_DATABASE_URL` ก่อนรัน ตารางทดสอบจะใช้ schema แยกและลบเมื่อจบ

ข้อมูลใหม่เริ่มเก็บหลัง deploy นี้ Logs ของ v1 ไม่ได้ถูกนำเข้า DB ย้อนหลัง PostgreSQL volume ไม่ใช่ backup ควรตั้ง backup ใน Railway ตามข้อมูลที่ต้องเก็บ

อ้างอิง: [Google OAuth Desktop](https://developers.google.com/identity/protocols/oauth2/native-app), [Google OAuth token expiry](https://developers.google.com/identity/protocols/oauth2), [LINE Content API](https://developers.line.biz/en/reference/messaging-api/#get-content), [Railway PostgreSQL](https://docs.railway.com/databases/postgresql)
