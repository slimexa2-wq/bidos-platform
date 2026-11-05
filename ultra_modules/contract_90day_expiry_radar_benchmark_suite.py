import datetime
import random
import string
import time
from typing import List, Dict, Any

from ultra_modules.contract_90day_expiry_radar_batch_pipeline import ContractExpiryRadarPipeline

class ContractExpiryRadarBenchmarkSuite:
    """
    Benchmark suite for the contract 90-day expiry radar.
    Generates synthetic datasets with normal, dirty, missing, extreme boundary, and null data.
    Measures execution time and crash resilience.
    """

    @staticmethod
    def generate_synthetic_dataset(size: int, base_date: datetime.date = None) -> List[Dict[str, Any]]:
        """
        Generates a dataset simulating various data quality conditions.
        """
        if base_date is None:
            base_date = datetime.date.today()

        dataset = []
        for i in range(size):
            case_type = i % 5

            if case_type == 0:
                # Normal case
                dataset.append({
                    "contract_id": f"NORMAL_{i}",
                    "end_date": (base_date + datetime.timedelta(days=random.randint(-10, 100))).isoformat(),
                    "deposit_amount": random.uniform(1000.0, 50000.0),
                    "deposit_returned": random.choice([True, False])
                })
            elif case_type == 1:
                # Dirty data case (strings in number fields, bad date formats)
                dataset.append({
                    "contract_id": f"DIRTY_{i}",
                    "end_date": "Not a date " + "".join(random.choices(string.ascii_letters, k=5)),
                    "deposit_amount": "Fifty thousand dollars",
                    "deposit_returned": "maybe"
                })
            elif case_type == 2:
                # Missing parameters (dictionaries missing keys)
                dataset.append({
                    "contract_id": f"MISSING_{i}",
                    # No end_date, deposit_amount, or deposit_returned
                })
            elif case_type == 3:
                # Extreme boundary (cross year, leap year, massive amounts)
                dataset.append({
                    "contract_id": f"EXTREME_{i}",
                    "end_date": "9999-12-31",
                    "deposit_amount": 1e12, # 1 trillion
                    "deposit_returned": 1
                })
            elif case_type == 4:
                # Null values
                dataset.append({
                    "contract_id": f"NULL_{i}",
                    "end_date": None,
                    "deposit_amount": None,
                    "deposit_returned": None
                })

        return dataset

    @staticmethod
    def run_benchmark(dataset: List[Dict[str, Any]], engine_type: str = "pipeline", max_workers: int = 20, reference_date: datetime.date = None) -> Dict[str, Any]:
        """
        Runs the benchmark against the dataset.
        engine_type: "pipeline" is currently the main supported type.
        """
        if reference_date is None:
            reference_date = datetime.date.today()

        start_time = time.time()
        crashes = 0

        try:
            if engine_type == "pipeline":
                pipeline = ContractExpiryRadarPipeline(max_workers=max_workers, reference_date=reference_date)
                result = pipeline.process_batch(dataset)
                # Count crashes in the results from error status
                for item in result.get("results", []):
                    if item.get("status") == "ERROR":
                        crashes += 1
            else:
                raise ValueError(f"Unknown engine type: {engine_type}")

        except Exception as e:
            crashes += 1

        end_time = time.time()

        total_time = end_time - start_time
        dataset_size = len(dataset)
        avg_response_time = (total_time / dataset_size) if dataset_size > 0 else 0

        is_millisecond_level = avg_response_time < 0.01  # less than 10ms per record on average
        is_zero_crash = crashes == 0

        return {
            "dataset_size": dataset_size,
            "total_execution_time": total_time,
            "response_time_per_record": avg_response_time,
            "crash_count": crashes,
            "is_millisecond_level": is_millisecond_level,
            "is_zero_crash": is_zero_crash
        }
