import concurrent.futures
import threading
import uuid
import time
from typing import List, Dict, Any

class LaborBidPriceCalculator:
    """
    Calculates labor bid prices (base salary, social security, provident fund,
    disabled persons employment security fund, taxes, and gross profit).
    Includes auto-fallback for missing parameters and dirty data healing to avoid blocking errors.
    """
    @staticmethod
    def _safe_float(val: Any, default: float = 0.0) -> float:
        if val is None:
            return default
        try:
            val_float = float(val)
            if val_float < 0:
                return 0.0
            return val_float
        except (ValueError, TypeError):
            return default

    @classmethod
    def calculate(cls, params: Dict[str, Any]) -> Dict[str, Any]:
        """
        Calculate full bid price based on parameters.
        Keys:
        - base_salary
        - social_security_ratio
        - provident_fund_ratio
        - disability_fund_ratio
        - tax_ratio
        - gross_profit_margin
        """
        base_salary = cls._safe_float(params.get('base_salary'), 0.0)
        social_security_ratio = cls._safe_float(params.get('social_security_ratio'), 0.0)
        provident_fund_ratio = cls._safe_float(params.get('provident_fund_ratio'), 0.0)
        disability_fund_ratio = cls._safe_float(params.get('disability_fund_ratio'), 0.0)
        tax_ratio = cls._safe_float(params.get('tax_ratio'), 0.0)
        gross_profit_margin = cls._safe_float(params.get('gross_profit_margin'), 0.0)

        # Basic components
        social_security_cost = base_salary * social_security_ratio
        provident_fund_cost = base_salary * provident_fund_ratio
        disability_fund_cost = base_salary * disability_fund_ratio

        # Subtotal cost before profit and tax
        labor_cost = base_salary + social_security_cost + provident_fund_cost + disability_fund_cost

        # Calculate gross profit
        gross_profit = labor_cost * gross_profit_margin

        cost_and_profit = labor_cost + gross_profit

        # Reverse calculate total including tax
        # cost_and_profit = total_price - tax
        # tax = total_price * tax_ratio
        # total_price = cost_and_profit / (1 - tax_ratio)
        if tax_ratio >= 1.0:
            tax_ratio = 0.0 # Prevent division by zero or negative total price

        total_bid_price = cost_and_profit / (1.0 - tax_ratio) if (1.0 - tax_ratio) > 0 else cost_and_profit
        tax_cost = total_bid_price * tax_ratio

        return {
            'base_salary': round(base_salary, 2),
            'social_security_cost': round(social_security_cost, 2),
            'provident_fund_cost': round(provident_fund_cost, 2),
            'disability_fund_cost': round(disability_fund_cost, 2),
            'labor_cost': round(labor_cost, 2),
            'gross_profit': round(gross_profit, 2),
            'tax_cost': round(tax_cost, 2),
            'total_bid_price': round(total_bid_price, 2)
        }

class LaborBidPriceBatchPipeline:
    """
    High-throughput batch pipeline for calculating labor bid prices.
    Supports stream processing, progress snapshots, and non-blocking result aggregation.
    """
    def __init__(self, max_workers: int = 10):
        self.max_workers = max_workers
        self.executor = concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers)
        self.tasks: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()

    def submit_batch(self, records: List[Dict[str, Any]]) -> str:
        """
        Submit a batch of records for processing.
        Returns a batch_id.
        """
        batch_id = str(uuid.uuid4())

        with self._lock:
            self.tasks[batch_id] = {
                'total': len(records),
                'completed': 0,
                'results': [],
                'status': 'processing'
            }

        def process_record(record_index, record):
            try:
                result = LaborBidPriceCalculator.calculate(record)
                # Keep original record data, add result
                final_res = {**record, 'calculation_result': result, '_record_index': record_index}
            except Exception as e:
                # Ultimate fallback
                final_res = {**record, 'calculation_result': LaborBidPriceCalculator.calculate({}), 'error': str(e), '_record_index': record_index}

            with self._lock:
                task_info = self.tasks[batch_id]
                task_info['results'].append(final_res)
                task_info['completed'] += 1
                if task_info['completed'] == task_info['total']:
                    task_info['status'] = 'completed'

        for i, record in enumerate(records):
            self.executor.submit(process_record, i, record)

        return batch_id

    def get_progress_snapshot(self, batch_id: str) -> Dict[str, Any]:
        """
        Get a non-blocking snapshot of the current progress.
        """
        with self._lock:
            if batch_id not in self.tasks:
                return {'error': 'batch_id not found'}
            task_info = self.tasks[batch_id]
            return {
                'batch_id': batch_id,
                'total': task_info['total'],
                'completed': task_info['completed'],
                'status': task_info['status'],
                'progress_percentage': round((task_info['completed'] / task_info['total']) * 100, 2) if task_info['total'] > 0 else 100.0
            }

    def get_results(self, batch_id: str) -> Dict[str, Any]:
        """
        Get the non-blocking results. If not finished, returns current results.
        Results are sorted by original input index.
        """
        with self._lock:
            if batch_id not in self.tasks:
                return {'error': 'batch_id not found'}
            task_info = self.tasks[batch_id]
            sorted_results = sorted(task_info['results'], key=lambda x: x['_record_index'])
            # Remove internal _record_index
            clean_results = [{k: v for k, v in res.items() if k != '_record_index'} for res in sorted_results]
            return {
                'batch_id': batch_id,
                'status': task_info['status'],
                'results': clean_results
            }

    def shutdown(self):
        self.executor.shutdown(wait=True)
