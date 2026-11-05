import time
import random
import datetime
import uuid
from typing import List, Dict, Any, Optional

from ultra_modules.bid_bond_return_tracker_batch_pipeline import BidBondReturnTrackerBatchPipeline

class BidBondBenchmarkSuite:
    """
    投标保证金退还跟踪台账引擎的基准测试与合成数据生成套件
    用于验证极端边界情况下的零崩溃容错和高并发处理性能。
    """

    def __init__(self, pipeline: Optional[BidBondReturnTrackerBatchPipeline] = None):
        if pipeline is None:
            self.pipeline = BidBondReturnTrackerBatchPipeline(max_workers=20)
        else:
            self.pipeline = pipeline

    def _generate_normal_record(self) -> Dict[str, Any]:
        base_date = datetime.date.today() - datetime.timedelta(days=random.randint(0, 100))
        return {
            'id': f"norm_{uuid.uuid4()}",
            'amount': round(random.uniform(1000, 100000), 2),
            'status': random.choice(['pending_return', 'returned']),
            'expected_return_date': (base_date + datetime.timedelta(days=30)).isoformat()
        }

    def _generate_dirty_record(self) -> Dict[str, Any]:
        dirty_amounts = ["¥ 5,000.00", "1,000,000", " - ", None, "O.OO", "一百万"]
        return {
            'id': f"dirty_{uuid.uuid4()}",
            'amount': random.choice(dirty_amounts),
            'status': random.choice(['', None, 'pending_return', 'unknown_status']),
            'expected_return_date': (datetime.date.today() + datetime.timedelta(days=10)).isoformat()
        }

    def _generate_missing_params_record(self) -> Dict[str, Any]:
        # 完全缺少某些参数
        record = {'id': f"miss_{uuid.uuid4()}"}
        if random.random() > 0.5:
            record['amount'] = random.uniform(1000, 50000)
        if random.random() > 0.5:
            record['status'] = 'pending_return'
        if random.random() > 0.5:
            record['expected_return_date'] = datetime.date.today().isoformat()
        return record

    def _generate_extreme_boundary_record(self) -> Dict[str, Any]:
        # 闰年、跨年、超大金额、超长ID等边界情况
        extreme_dates = [
            "2024-02-29", # 闰年
            "1970-01-01",
            "9999-12-31",
            "2023-12-31", # 跨年
            "2024-01-01"
        ]
        return {
            'id': f"ext_{'a' * 200}_{uuid.uuid4()}",
            'amount': random.choice([0.0, 999999999999.99, -100.0, 1e10]),
            'status': 'pending_return',
            'expected_return_date': random.choice(extreme_dates)
        }

    def generate_synthetic_data(self, count: int, scenario: str = 'mixed') -> List[Dict[str, Any]]:
        """
        生成用于测试的合成数据集
        :param count: 生成的数据条数
        :param scenario: 场景选择，支持 'normal', 'dirty', 'missing', 'extreme', 'mixed'
        """
        records = []
        for _ in range(count):
            if scenario == 'normal':
                records.append(self._generate_normal_record())
            elif scenario == 'dirty':
                records.append(self._generate_dirty_record())
            elif scenario == 'missing':
                records.append(self._generate_missing_params_record())
            elif scenario == 'extreme':
                records.append(self._generate_extreme_boundary_record())
            elif scenario == 'mixed':
                rand_val = random.random()
                if rand_val < 0.4:
                    records.append(self._generate_normal_record())
                elif rand_val < 0.7:
                    records.append(self._generate_dirty_record())
                elif rand_val < 0.9:
                    records.append(self._generate_missing_params_record())
                else:
                    records.append(self._generate_extreme_boundary_record())
            else:
                records.append(self._generate_normal_record())

        return records

    def run_benchmark(self, records: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        运行基准测试，处理传入的数据集
        返回处理结果和耗时信息
        """
        start_time = time.perf_counter()

        # 使用批量流水线进行处理
        result = self.pipeline.process_batch(records)

        end_time = time.perf_counter()
        elapsed_time_ms = (end_time - start_time) * 1000

        result['benchmark_metrics'] = {
            'elapsed_time_ms': elapsed_time_ms,
            'records_per_second': len(records) / (end_time - start_time) if end_time > start_time else float('inf')
        }

        return result

    def evaluate_performance(self, count: int = 1000) -> Dict[str, Any]:
        """
        生成指定数量的混合数据集并运行性能评估
        确保零崩溃且达到毫秒级响应能力
        """
        synthetic_records = self.generate_synthetic_data(count, scenario='mixed')
        result = self.run_benchmark(synthetic_records)

        # 验证是否无崩溃（即所有记录成功处理）
        is_zero_crash = result.get('fail_count', 0) == 0

        # 评估耗时（例如每条记录平均不超过1毫秒）
        total_time_ms = result['benchmark_metrics']['elapsed_time_ms']
        avg_time_ms = total_time_ms / count if count > 0 else 0

        is_ms_level_response = avg_time_ms <= 1.0

        result['evaluation_summary'] = {
            'is_zero_crash': is_zero_crash,
            'is_ms_level_response': is_ms_level_response,
            'average_time_per_record_ms': avg_time_ms
        }

        return result
