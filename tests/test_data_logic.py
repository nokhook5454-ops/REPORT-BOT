import unittest
from data_logic import Config, ReportDetector, NarcoticsExtractionProfile, ExtractionValidator, mask_citizen_id, mask_phone


class LogicTests(unittest.TestCase):
    def test_chat_ignored(self):
        for text in ('รับทราบครับ','ได้ครับ','ส่งแล้ว','ขอบคุณครับ','เดี๋ยวประสาน','555'):
            self.assertFalse(ReportDetector().detect(text)['is_possible_report'])

    def test_report_and_configurable_detector(self):
        text = 'เรียนผู้บังคับบัญชา จับกุม ของกลาง ยาบ้า 3,030 เม็ด'
        self.assertTrue(ReportDetector().detect(text)['is_possible_report'])
        self.assertFalse(ReportDetector(100).detect(text)['is_possible_report'])

    def test_missing_fields_null(self):
        result = NarcoticsExtractionProfile().extract('จับกุม ของกลาง ยาบ้า 3,030 เม็ด สารไอซ์ 0.57 กรัม')
        self.assertIsNone(result['report']['event_date'])
        self.assertIsNone(result['report']['station'])
        self.assertEqual(result['persons'],[])
        self.assertEqual(result['evidence_items'][0]['quantity'],3030)
        self.assertEqual(result['evidence_items'][1]['weight'],.57)

    def test_low_confidence_review(self):
        self.assertEqual(ExtractionValidator().validate(NarcoticsExtractionProfile().extract('')),
                         ('REVIEW_REQUIRED',True))

    def test_confidence_thresholds(self):
        for confidence in (.59,.6,.84,.85):
            result = NarcoticsExtractionProfile().extract('')
            result.update(confidence=confidence,warnings=[])
            self.assertEqual(ExtractionValidator().validate(result)[0], 'REVIEW_REQUIRED' if confidence < .6 else 'READY')

    def test_json_validation(self):
        for result in ({}, {'report':{}}, None):
            with self.assertRaises(ValueError):
                ExtractionValidator().validate(result)

    def test_contradiction_review(self):
        result = NarcoticsExtractionProfile().extract('สภ.ตัวอย่างหนึ่ง สภ.ตัวอย่างสอง')
        result['confidence'] = .99
        self.assertIsNone(result['report']['station'])
        self.assertTrue(ExtractionValidator().validate(result)[1])

    def test_pii_masking(self):
        self.assertEqual(mask_citizen_id('1-3097-12345-96-7'),'1-3097-XXXXX-96-7')
        self.assertEqual(mask_citizen_id('invalid'),'[MASKED]')
        self.assertEqual(mask_phone('0800000000'),'XXXXXXX000')
