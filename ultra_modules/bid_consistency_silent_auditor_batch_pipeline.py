import concurrent.futures
import threading
import re
from typing import List, Dict, Any, Optional

class BidConsistencySilentAuditor:
    """
    高吞吐批量流水线与并发加速封装：整标大小写金额、工期天数、项目经理姓名与执业证号跨章节一致性静默质检器
    """
    def __init__(self, max_workers: int = 10):
        self.max_workers = max_workers
        self._progress_lock = threading.Lock()
        self._total_records = 0
        self._processed_records = 0
        self._results = []
        self._executor = concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers)
        self._futures = []

    def _normalize_amount(self, amount_val: Any) -> Optional[float]:
        """脏数据自愈：处理带逗号、货币符号或字符串类型的金额"""
        if amount_val is None:
            return None
        if isinstance(amount_val, (int, float)):
            return float(amount_val)
        if isinstance(amount_val, str):
            s = re.sub(r'[^\d\.]', '', amount_val)
            try:
                return float(s) if s else None
            except ValueError:
                return None
        return None

    def _normalize_days(self, days_val: Any) -> Optional[int]:
        """脏数据自愈：从带有‘天’、‘工作日’等字符中提取数字"""
        if days_val is None:
            return None
        if isinstance(days_val, int):
            return days_val
        if isinstance(days_val, str):
            m = re.search(r'\d+', days_val)
            if m:
                return int(m.group())
        return None

    def _normalize_string(self, s: Any) -> Optional[str]:
        """脏数据自愈：去除多余空格"""
        if not s:
            return None
        return str(s).strip()

    def _audit_record(self, record: Dict[str, Any]) -> Dict[str, Any]:
        record_id = record.get("id", "default_id")
        chapters = record.get("chapters")

        # 缺参默认兜底：如果没有 chapters，则提供默认空结构
        if not chapters or not isinstance(chapters, dict):
            chapters = {"default_chapter": {}}

        normalized_data = {}
        for ch_name, ch_data in chapters.items():
            if not isinstance(ch_data, dict):
                ch_data = {}
            normalized_data[ch_name] = {
                "amount": self._normalize_amount(ch_data.get("amount")),
                "amount_upper": self._normalize_string(ch_data.get("amount_upper")),
                "duration_days": self._normalize_days(ch_data.get("duration_days")),
                "pm_name": self._normalize_string(ch_data.get("pm_name")),
                "pm_cert": self._normalize_string(ch_data.get("pm_cert")),
            }

        fields_collected = {
            "amount": set(),
            "amount_upper": set(),
            "duration_days": set(),
            "pm_name": set(),
            "pm_cert": set()
        }

        for ch_name, data in normalized_data.items():
            for field in fields_collected.keys():
                val = data[field]
                if val is not None:
                    fields_collected[field].add(val)

        inconsistencies = []
        for field, values in fields_collected.items():
            if len(values) > 1:
                inconsistencies.append(field)

        is_consistent = len(inconsistencies) == 0

        return {
            "id": record_id,
            "is_consistent": is_consistent,
            "inconsistencies": inconsistencies,
            "normalized_data": normalized_data
        }

    def _worker(self, record: Dict[str, Any]):
        result = self._audit_record(record)
        with self._progress_lock:
            self._processed_records += 1
            self._results.append(result)

    def start_batch_pipeline(self, records: List[Dict[str, Any]]) -> None:
        """
        非阻塞启动批量流水线，支持单次拖入10~1000条记录
        """
        if not records:
            return

        with self._progress_lock:
            self._total_records = len(records)
            self._processed_records = 0
            self._results = []

        self._futures = [self._executor.submit(self._worker, record) for record in records]

    def wait_for_completion(self):
        """阻塞直到当前批次完成"""
        if self._futures:
            concurrent.futures.wait(self._futures)

    def get_progress_snapshot(self) -> Dict[str, Any]:
        """进度快照与零阻塞结果汇总"""
        with self._progress_lock:
            total = self._total_records
            processed = self._processed_records
            results_copy = list(self._results)

        return {
            "total": total,
            "processed": processed,
            "progress_percentage": (processed / total * 100) if total > 0 else 0,
            "is_completed": total == processed and total > 0,
            "results_so_far": results_copy
        }
