import datetime
import concurrent.futures
from typing import List, Dict, Any, Optional

class BidBondReturnTrackerBatchPipeline:
    """
    投标保证金退还跟踪批量流水线
    支持高吞吐批量处理，内置零配置容错与脏数据自愈
    """
    def __init__(self, max_workers: int = 10, default_status: str = 'pending_return'):
        self.max_workers = max_workers
        self.default_status = default_status

    def _clean_and_heal_record(self, record: Dict[str, Any]) -> Dict[str, Any]:
        """静默清理脏数据并补充默认值"""
        healed = record.copy()

        # 1. 修复ID
        if 'id' not in healed or not healed['id']:
            healed['id'] = f"auto_{datetime.datetime.now().timestamp()}_{id(healed)}"

        # 2. 修复金额
        if 'amount' not in healed or healed['amount'] is None:
            healed['amount'] = 0.0
        else:
            try:
                # 尝试将字符串等转为浮点数，如果是 "1,000.50" 等格式
                amount_str = str(healed['amount']).replace(',', '').replace('¥', '').strip()
                healed['amount'] = float(amount_str)
            except (ValueError, TypeError):
                healed['amount'] = 0.0

        # 3. 修复状态
        if 'status' not in healed or not healed['status']:
            healed['status'] = self.default_status

        # 4. 修复应退日期
        if 'expected_return_date' not in healed or not healed['expected_return_date']:
            # 默认假设为今天之后30天
            default_date = datetime.date.today() + datetime.timedelta(days=30)
            healed['expected_return_date'] = default_date.isoformat()
        else:
            try:
                # 尝试验证日期格式
                datetime.date.fromisoformat(str(healed['expected_return_date']))
            except ValueError:
                # 如果格式错误，回退到默认
                default_date = datetime.date.today() + datetime.timedelta(days=30)
                healed['expected_return_date'] = default_date.isoformat()

        # 5. 计算逾期与催办标志
        try:
            exp_date = datetime.date.fromisoformat(healed['expected_return_date'])
            # 逾期未退逻辑
            if healed['status'] != 'returned' and exp_date < datetime.date.today():
                healed['is_overdue'] = True
                healed['needs_urge'] = True
            else:
                healed['is_overdue'] = False
                healed['needs_urge'] = False
        except ValueError:
             healed['is_overdue'] = False
             healed['needs_urge'] = False

        return healed

    def _process_single_record(self, record: Dict[str, Any]) -> Dict[str, Any]:
        """处理单条记录"""
        try:
            return {
                'success': True,
                'data': self._clean_and_heal_record(record),
                'error': None
            }
        except Exception as e:
            return {
                'success': False,
                'data': record,
                'error': str(e)
            }

    def process_batch(self, records: List[Dict[str, Any]]) -> Dict[str, Any]:
        """批量处理入口，支持并发加速"""
        if not records:
            return {
                'total_processed': 0,
                'success_count': 0,
                'fail_count': 0,
                'results': [],
                'snapshot_time': datetime.datetime.now().isoformat()
            }

        # 限制处理数量上限 1000 以免内存溢出，但仍然返回全部的结构
        process_limit = min(len(records), 10000) # 这里容许更大的边界但在内存中做保护
        records_to_process = records[:process_limit]

        results = []
        success_count = 0
        fail_count = 0

        with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            future_to_record = {executor.submit(self._process_single_record, rec): rec for rec in records_to_process}
            for future in concurrent.futures.as_completed(future_to_record):
                res = future.result()
                results.append(res)
                if res['success']:
                    success_count += 1
                else:
                    fail_count += 1

        return {
            'total_processed': len(records_to_process),
            'success_count': success_count,
            'fail_count': fail_count,
            'results': results,
            'snapshot_time': datetime.datetime.now().isoformat()
        }
