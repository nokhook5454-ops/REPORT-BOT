# REPORT-BOT

Silent LINE Messaging API Collector v1 ใช้ Python, Flask และ gunicorn รับ events และบันทึกลง stdout สำหรับ Railway Logs เท่านั้น ไม่มี Reply API, Push API, Database, Google Drive หรือ AI

## Deploy บน Railway

1. นำไฟล์โปรเจกต์ขึ้น GitHub แล้วเลือก Railway → New Project → Deploy from GitHub repo หรือใช้ Railway CLI (`railway up`) จากโฟลเดอร์นี้
2. ใน service → Variables ตั้ง `LINE_CHANNEL_SECRET` จาก LINE Developers → Messaging API channel → Basic settings และ `LINE_CHANNEL_ACCESS_TOKEN` จาก Messaging API tab (รองรับไว้สำหรับอนาคต v1 ไม่ได้ใช้งาน)
3. Railway ติดตั้ง `requirements.txt` และใช้ start command จาก `railway.json`: `gunicorn app:app --bind 0.0.0.0:$PORT --workers 1 --threads 4 --timeout 30 --error-logfile -`
4. ใน Settings → Networking สร้าง public domain แล้วเปิด `https://<railway-domain>/` ต้องได้ HTTP 200 และ `REPORT BOT is running` หรือทดสอบด้วย `curl https://<railway-domain>/`

Railway กำหนด `PORT` ให้อัตโนมัติ ห้ามใส่ secret/token ใน source code, Git หรือภาพหน้าจอที่เผยแพร่

## ตั้งค่า LINE Developers และทดสอบกลุ่ม

1. เปิด Messaging API channel → Messaging API → Webhook settings ตั้ง Webhook URL เป็น `https://<railway-domain>/webhook`
2. กด Verify แล้วเปิด Use webhook (Verify ส่ง `events: []` ซึ่งระบบตอบ 200 ได้)
3. เปิด Allow bot to join group chats แล้วเชิญ LINE Official Account เข้ากลุ่ม
4. ใน LINE Official Account Manager ปิด Greeting messages และ Auto-response messages เพื่อป้องกันข้อความอัตโนมัติจากฝั่ง LINE
5. ส่งข้อความในกลุ่ม แล้วตรวจ Railway Logs ตัวอย่าง:

```text
[REPORT-BOT] webhook received events=1
[REPORT-BOT] event received event_type="message" source_type="group" group_id="C..." message_type="text" text="hello"
```

รองรับ user/group/room และหลาย events ต่อ request โดย log เฉพาะ event type, source type, group ID, message type และข้อความ text สำหรับทดสอบ v1 ไม่ log request ทั้งก้อน, user ID, reply token, secret, access token หรือ signature ข้อความถูก escape เพื่อป้องกัน log injection และจำกัด 2,000 ตัวอักษรต่อ field; secret/token ที่ตรงกับค่าที่ตั้งไว้จะถูกปิดบัง ข้อความภาษาไทยแสดงเป็น JSON Unicode escape ใน log

ตรวจ HMAC-SHA256 จาก raw request body ก่อนอ่าน JSON: signature ผิด/ไม่มี → 400, JSON หรือ events ผิดรูปแบบ → 400, secret ไม่ได้ตั้ง → 503, request เกิน 1 MiB → 413; events ว่างหรือถูกต้อง → 200 ไม่มี response message กลับ LINE ความผิดพลาดของ event หนึ่งไม่หยุดการประมวลผล event ถัดไป

v1 บันทึก log แบบ synchronous ไม่มีการเรียกบริการภายนอกจึงตอบกลับทันทีหลัง log เสร็จ ไม่มีระบบจัดเก็บถาวรหรือ deduplication หาก LINE ส่งซ้ำ log อาจซ้ำได้ เนื้อหา text และ group ID อยู่ใน Railway Logs ให้ใช้ข้อความทดสอบที่เหมาะสม

## ทดสอบในเครื่อง

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
$env:LINE_CHANNEL_SECRET = 'your-channel-secret'
$env:LINE_CHANNEL_ACCESS_TOKEN = 'your-access-token'
.\.venv\Scripts\python -m flask --app app run
```

เปิด `http://127.0.0.1:5000/` Flask development server ใช้เฉพาะทดสอบในเครื่อง; Railway ใช้ gunicorn บน Linux

รันชุดทดสอบ: `python -m unittest discover -s tests -v`

เอกสารอ้างอิง: [LINE signature verification](https://developers.line.biz/en/docs/messaging-api/verify-webhook-signature/) และ [Railway Flask deployment](https://docs.railway.com/guides/flask)
