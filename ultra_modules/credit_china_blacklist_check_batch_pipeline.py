import concurrent.futures
import threading
import time
import uuid
import logging
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)

class CreditChinaBlacklistCheckBatchPipeline:
    """
    A high-throughput batch pipeline for checking "Credit China" (信用中国) and
    "Chinese Government Procurement Network" (中国政府采购网) records.

    Supports:
    - Dragging and dropping 10~1000 records
    - Zero-blocking progress reporting
    - Result aggregation
    - Dirty data self-healing
    - Missing parameter defaults
    """

    def __init__(self, max_workers: int = 10):
        self.max_workers = max_workers
        self._tasks: Dict[str, Dict[str, Any]] = {}
        self.executor = concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers)
        self._lock = threading.Lock()

    def start_batch(self, records: List[Dict[str, Any]]) -> str:
        """
        Starts a batch process for the given records. Returns a batch ID.
        """
        if not isinstance(records, list):
            records = []

        if len(records) > 1000:
            records = records[:1000]

        batch_id = str(uuid.uuid4())

        self._tasks[batch_id] = {
            'status': 'running',
            'total': len(records),
            'completed': 0,
            'results': [],
            'futures': []
        }

        if not records:
            self._tasks[batch_id]['status'] = 'completed'
            return batch_id

        futures = []
        for idx, record in enumerate(records):
            # Submit to ThreadPool
            future = self.executor.submit(self._process_single_record, record)
            futures.append(future)
            # Attach a callback to update progress
            future.add_done_callback(lambda f, b_id=batch_id: self._on_record_completed(b_id, f))

        self._tasks[batch_id]['futures'] = futures

        return batch_id

    def _on_record_completed(self, batch_id: str, future: concurrent.futures.Future):
        if batch_id in self._tasks:
            try:
                result = future.result()
                with self._lock:
                    self._tasks[batch_id]['results'].append(result)
            except Exception as e:
                logger.error(f"Error processing record: {e}")
                with self._lock:
                    self._tasks[batch_id]['results'].append({
                        'status': 'error',
                        'error_msg': str(e)
                    })
            finally:
                with self._lock:
                    self._tasks[batch_id]['completed'] += 1

                    if self._tasks[batch_id]['completed'] >= self._tasks[batch_id]['total']:
                        self._tasks[batch_id]['status'] = 'completed'

    def get_progress(self, batch_id: str) -> Dict[str, Any]:
        """
        Returns a non-blocking snapshot of the progress.
        """
        if batch_id not in self._tasks:
            return {'status': 'not_found'}

        task = self._tasks[batch_id]
        return {
            'batch_id': batch_id,
            'status': task['status'],
            'total': task['total'],
            'completed': task['completed'],
            'results': task['results']
        }

    def _process_single_record(self, record: Dict[str, Any]) -> Dict[str, Any]:
        """
        Internal method to process a single record.
        Includes missing data handling and dirty data self-healing.
        """
        # Simulated latency for external API check
        time.sleep(0.01)

        if not isinstance(record, dict):
            return {
                'original': record,
                'status': 'failed',
                'reason': 'Record must be a dictionary'
            }

        # 1. Missing Parameter Default Fallback
        company_name = record.get('company_name')
        if not company_name:
            company_name = "未知企业"

        social_credit_code = record.get('social_credit_code', '')

        # 2. Dirty Data Self-Healing
        # Clean company name (e.g. strip whitespace, remove weird characters if necessary)
        company_name = str(company_name).strip()
        social_credit_code = str(social_credit_code).strip().upper()

        # Self-heal social credit code if it contains weird characters like dashes or spaces
        social_credit_code = social_credit_code.replace("-", "").replace(" ", "")

        # If code is missing, provide a dummy one for processing to continue
        if not social_credit_code:
            social_credit_code = "UNKNOWN_CODE"

        # Simulate verification logic
        is_blacklisted = False
        notes = "无重大违法记录"

        if "违法" in company_name or "黑名单" in company_name:
            is_blacklisted = True
            notes = "发现违法记录或列入黑名单"

        return {
            'company_name': company_name,
            'social_credit_code': social_credit_code,
            'is_blacklisted': is_blacklisted,
            'notes': notes,
            'check_timestamp': time.time(),
            'status': 'success'
        }

    def wait_for_completion(self, batch_id: str, timeout: Optional[float] = None) -> Dict[str, Any]:
        """
        Utility method to block until the batch is complete, useful for testing or specific use cases.
        """
        if batch_id not in self._tasks:
            return {'status': 'not_found'}

        task = self._tasks[batch_id]
        if task['futures']:
            concurrent.futures.wait(task['futures'], timeout=timeout)

        return self.get_progress(batch_id)

    def shutdown(self):
        """
        Shutdown the thread pool.
        """
        self.executor.shutdown(wait=True)
