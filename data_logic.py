"""Configurable, conservative extraction. Never invent facts from missing text."""
import os
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class Config:
    detection_threshold: int = 6
    attachment_window: int = 180
    ready_threshold: float = .85
    review_threshold: float = .60

    @classmethod
    def from_env(cls):
        result = cls(int(os.getenv('REPORT_DETECTION_THRESHOLD', '6')),
                     int(os.getenv('ATTACHMENT_WINDOW_SECONDS', '180')),
                     float(os.getenv('EXTRACTION_READY_THRESHOLD', '.85')),
                     float(os.getenv('EXTRACTION_REVIEW_THRESHOLD', '.60')))
        if result.detection_threshold <= 0 or result.attachment_window <= 0 or not 0 <= result.review_threshold <= result.ready_threshold <= 1:
            raise ValueError('Invalid data logic configuration')
        return result


class ReportDetector:
    rules = [('salutation', 'เรียนผู้บังคับบัญชา', 2), ('operation', 'รายงานผลการปฏิบัติ', 3),
             ('arrest', 'จับกุม', 2), ('evidence', 'ของกลาง', 2), ('accused', 'โดยกล่าวหาว่า', 2),
             ('charge', 'ข้อหา', 2), ('handover', 'นำส่งพนักงานสอบสวน', 2), ('station', r'สภ\.', 1),
             ('date', r'\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}', 1),
             ('quantity', r'[\d,]+(?:\.\d+)?\s*(?:เม็ด|กรัม|กระบอก|นัด)', 2)]

    def __init__(self, threshold=6):
        self.threshold = threshold

    def detect(self, text):
        matched = [(name, weight) for name, pattern, weight in self.rules if re.search(pattern, text)]
        score = sum(weight for _, weight in matched)
        return {'is_possible_report': score >= self.threshold, 'score': score,
                'matched_rules': [name for name, _ in matched]}


REPORT_FIELDS = ('agency', 'station', 'event_date', 'event_time', 'location_text', 'province',
                 'district', 'subdistrict', 'summary')
COLLECTIONS = ('officers', 'persons', 'charges', 'evidence_items', 'seized_assets', 'phones')


class BaseExtractionProfile(ABC):
    @abstractmethod
    def extract(self, text):
        pass


class NarcoticsExtractionProfile(BaseExtractionProfile):
    def extract(self, text):
        result = {'report': dict.fromkeys(REPORT_FIELDS), **{key: [] for key in COLLECTIONS},
                  'warnings': ['Conservative rule extraction; human review required'], 'confidence': .5}
        result['report']['report_type'] = 'ARREST' if 'จับกุม' in text else 'DRUG_CASE'
        # Only an explicitly labelled station is extracted. Dates/persons remain null/empty.
        stations = re.findall(r'สภ\.[^\s,;\n]+', text)
        if len(set(stations)) == 1:
            result['report']['station'] = stations[0]
        elif stations:
            result['warnings'].append('Multiple stations; station unresolved')
        for match in re.finditer(r'(ยาบ้า|สารไอซ์|ไอซ์)\s*([\d,]+(?:\.\d+)?)\s*(เม็ด|กรัม)', text):
            name, value, unit = match.groups()
            amount = float(value.replace(',', ''))
            result['evidence_items'].append({'item_type': 'DRUG', 'item_name': name,
                'quantity': amount if unit == 'เม็ด' else None, 'unit': unit if unit == 'เม็ด' else None,
                'weight': amount if unit == 'กรัม' else None, 'weight_unit': unit if unit == 'กรัม' else None,
                'description': match.group(0)})
        # Explicit labels only; ambiguous prose remains in raw_text for review.
        labels = {'หน่วยงาน':'agency','สถานที่เกิดเหตุ':'location_text','จังหวัด':'province',
                  'อำเภอ':'district','ตำบล':'subdistrict'}
        for label, field in labels.items():
            values = re.findall(r'^\s*'+label+r'\s*:\s*(.+)$',text,re.M)
            if len(set(values)) == 1:
                result['report'][field] = values[0].strip()
            elif values:
                result['warnings'].append('Conflicting '+field)
        for value in re.findall(r'^\s*(?:ผู้ต้องหา|ผู้ถูกจับกุม)\s*:\s*(.+)$',text,re.M):
            result['persons'].append({'full_name':value.strip(),'person_role':'ARRESTED_PERSON'})
        for label, role in [('ผู้บังคับบัญชา','COMMANDER'),('หัวหน้าชุด','TEAM_LEADER'),('ผู้ปฏิบัติ','OPERATOR')]:
            for value in re.findall(r'^\s*'+label+r'\s*:\s*(.+)$',text,re.M):
                result['officers'].append({'name':value.strip(),'role':role})
        for value in re.findall(r'^\s*(?:ข้อหา|โดยกล่าวหาว่า)\s*:\s*(.+)$',text,re.M):
            result['charges'].append({'charge_text_original':value.strip(),'charge_category':None,'drug_type':None})
            if result['persons']:
                result['warnings'].append('Charge person mapping unresolved')
        for value in re.findall(r'^\s*ทรัพย์สินตรวจยึด\s*:\s*(.+)$',text,re.M):
            result['seized_assets'].append({'asset_type':'OTHER','description':value.strip(),'currency':'THB'})
        for value in re.findall(r'^\s*โทรศัพท์\s*:\s*(.+)$',text,re.M):
            result['phones'].append({'description':value.strip(),'phone_numbers':[]})
        for match in re.finditer(r'(ปืน|กระสุน)\s*([\d,]+)\s*(กระบอก|นัด)',text):
            result['evidence_items'].append({'item_type':'FIREARM' if match[1]=='ปืน' else 'AMMUNITION',
                'item_name':match[1],'quantity':int(match[2].replace(',','')),'unit':match[3],'description':match[0]})
        return result


class FirearmsExtractionProfile(BaseExtractionProfile):
    def extract(self, text):
        raise NotImplementedError('FIREARMS profile is not enabled in v1')


class GeneralCaseExtractionProfile(FirearmsExtractionProfile):
    pass


class ExtractionValidator:
    def __init__(self, config=None):
        self.config = config or Config.from_env()

    def validate(self, result):
        if not isinstance(result, dict) or not isinstance(result.get('report'), dict):
            raise ValueError('Invalid extraction JSON')
        if any(not isinstance(result.get(key), list) for key in (*COLLECTIONS, 'warnings')):
            raise ValueError('Invalid extraction collections')
        required = {'officers':('name','role'),'persons':('full_name','person_role'),
                    'charges':('charge_text_original',),'evidence_items':('item_type','item_name','description'),
                    'seized_assets':('asset_type','description'),'phones':()}
        for key, fields in required.items():
            for item in result[key]:
                if not isinstance(item,dict) or any(not isinstance(item.get(f),str) or not item[f].strip() for f in fields):
                    raise ValueError('Invalid extraction item')
        confidence = result.get('confidence')
        if isinstance(confidence, bool) or not isinstance(confidence, (float, int)) or not 0 <= confidence <= 1:
            raise ValueError('Invalid confidence')
        if result['report'].get('report_type') not in ('ARREST', 'SEARCH', 'SEIZURE', 'DRUG_CASE', 'GENERAL_OPERATION', 'OTHER'):
            raise ValueError('Invalid report type')
        for field in REPORT_FIELDS:
            result['report'].setdefault(field, None)
        # Unresolved warnings may include contradictions: conservatively require review.
        review = confidence < self.config.review_threshold or bool(result['warnings'])
        if confidence < self.config.ready_threshold and not result['warnings']:
            result['warnings'].append('Confidence below ready threshold')
        return ('REVIEW_REQUIRED' if review else 'READY', review)


def mask_citizen_id(value):
    if value is None:
        return None
    digits = re.sub(r'\D', '', value)
    return f'{digits[0]}-{digits[1:5]}-XXXXX-{digits[10:12]}-{digits[12]}' if len(digits) == 13 else '[MASKED]'


def mask_phone(value):
    return None if value is None else 'XXXXXXX' + value[-3:]
