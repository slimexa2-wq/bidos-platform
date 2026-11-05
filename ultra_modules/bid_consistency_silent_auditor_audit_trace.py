import re
import datetime
from typing import List, Dict, Any, Tuple


class AuditTrace:
    def __init__(self):
        self.logs = []

    def record(self, field: str, original: Any, new: Any, reason: str):
        self.logs.append({
            'timestamp': datetime.datetime.now().isoformat(),
            'field': field,
            'original': original,
            'new': new,
            'reason': reason
        })


class BidConsistencyAuditor:
    def __init__(self):
        self.audit_trace = AuditTrace()

    def clean_amount_lower(self, val: Any) -> float:
        original = val
        if val is None:
            new_val = 0.0
            self.audit_trace.record('amount_lower', original, new_val, 'Missing parameter, default to 0.0')
            return new_val
        if isinstance(val, (int, float)):
            return float(val)
        if isinstance(val, str):
            cleaned = re.sub(r'[^\d.]', '', val)
            try:
                new_val = float(cleaned) if cleaned else 0.0
                if val != str(new_val):
                    self.audit_trace.record('amount_lower', original, new_val, 'Dirty data cleaned')
                return new_val
            except ValueError:
                new_val = 0.0
                self.audit_trace.record('amount_lower', original, new_val, 'Invalid string, default to 0.0')
                return new_val
        new_val = 0.0
        self.audit_trace.record('amount_lower', original, new_val, 'Unknown type, default to 0.0')
        return new_val

    def clean_amount_upper(self, val: Any) -> str:
        original = val
        if val is None:
            new_val = "零"
            self.audit_trace.record('amount_upper', original, new_val, 'Missing parameter, default to "零"')
            return new_val
        val_str = str(val).strip()
        if not val_str:
            new_val = "零"
            self.audit_trace.record('amount_upper', original, new_val, 'Empty string, default to "零"')
            return new_val
        if val_str != original:
            self.audit_trace.record('amount_upper', original, val_str, 'Whitespace stripped')
        return val_str

    def clean_duration_days(self, val: Any) -> int:
        original = val
        if val is None:
            new_val = 0
            self.audit_trace.record('duration_days', original, new_val, 'Missing parameter, default to 0')
            return new_val
        if isinstance(val, int):
            return val
        if isinstance(val, float):
            new_val = int(val)
            self.audit_trace.record('duration_days', original, new_val, 'Float converted to int')
            return new_val
        if isinstance(val, str):
            cleaned = re.sub(r'[^\d]', '', val)
            try:
                new_val = int(cleaned) if cleaned else 0
                if val != str(new_val):
                    self.audit_trace.record('duration_days', original, new_val, 'Dirty data cleaned')
                return new_val
            except ValueError:
                new_val = 0
                self.audit_trace.record('duration_days', original, new_val, 'Invalid string, default to 0')
                return new_val
        new_val = 0
        self.audit_trace.record('duration_days', original, new_val, 'Unknown type, default to 0')
        return new_val

    def clean_manager_name(self, val: Any) -> str:
        original = val
        if val is None:
            new_val = "未指定"
            self.audit_trace.record('manager_name', original, new_val, 'Missing parameter, default to "未指定"')
            return new_val
        val_str = str(val).strip()
        if not val_str:
            new_val = "未指定"
            self.audit_trace.record('manager_name', original, new_val, 'Empty string, default to "未指定"')
            return new_val
        # Remove special characters from name, allow only word chars
        cleaned = re.sub(r'[^\w\s\u4e00-\u9fa5]', '', val_str)
        if cleaned != original:
            self.audit_trace.record('manager_name', original, cleaned, 'Dirty data cleaned')
        return cleaned or "未指定"

    def clean_manager_cert(self, val: Any) -> str:
        original = val
        if val is None:
            new_val = "无证书"
            self.audit_trace.record('manager_cert', original, new_val, 'Missing parameter, default to "无证书"')
            return new_val
        val_str = str(val).strip()
        if not val_str:
            new_val = "无证书"
            self.audit_trace.record('manager_cert', original, new_val, 'Empty string, default to "无证书"')
            return new_val
        # Allow alphanumeric
        cleaned = re.sub(r'[^\w]', '', val_str)
        if cleaned != original:
            self.audit_trace.record('manager_cert', original, cleaned, 'Dirty data cleaned')
        return cleaned or "无证书"

    def audit(self, chapters_data: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Audit consistency across multiple chapters.
        Expected keys in each chapter dict:
        amount_lower, amount_upper, duration_days, manager_name, manager_cert
        """
        if not chapters_data:
            return {
                'is_consistent': True,
                'discrepancies': [],
                'audit_logs': self.audit_trace.logs,
                'final_values': {}
            }

        cleaned_chapters = []
        for chapter in chapters_data:
            cleaned_chapter = {
                'amount_lower': self.clean_amount_lower(chapter.get('amount_lower')),
                'amount_upper': self.clean_amount_upper(chapter.get('amount_upper')),
                'duration_days': self.clean_duration_days(chapter.get('duration_days')),
                'manager_name': self.clean_manager_name(chapter.get('manager_name')),
                'manager_cert': self.clean_manager_cert(chapter.get('manager_cert'))
            }
            cleaned_chapters.append(cleaned_chapter)

        reference = cleaned_chapters[0]
        discrepancies = []

        for i in range(1, len(cleaned_chapters)):
            current = cleaned_chapters[i]
            for key in reference:
                if current[key] != reference[key]:
                    discrepancies.append({
                        'chapter_index': i,
                        'field': key,
                        'expected': reference[key],
                        'actual': current[key]
                    })

        return {
            'is_consistent': len(discrepancies) == 0,
            'discrepancies': discrepancies,
            'audit_logs': self.audit_trace.logs,
            'final_values': reference if len(discrepancies) == 0 else {}
        }
