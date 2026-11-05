import time
import random
import string
from typing import List, Dict, Any, Optional, Tuple
import concurrent.futures

from ultra_modules.competitor_win_price_analyzer_core_engine import analyze_discount_rate_distribution

class SyntheticFixtureGenerator:
    """
    Generates synthetic datasets for extreme boundary and performance testing.
    """
    @staticmethod
    def generate_random_string(length: int = 10) -> str:
        return ''.join(random.choices(string.ascii_letters + string.digits, k=length))

    @staticmethod
    def generate_dirty_value(prob: float = 0.1) -> Any:
        dirty_options = [None, "", "null", "N/A", [], {}, float('nan'), float('inf'), "invalid_string"]
        return random.choice(dirty_options) if random.random() < prob else None

    @staticmethod
    def generate_dataset(size: int, dirty_prob: float = 0.05,
                         competitors: List[str] = ["CompA", "CompB"],
                         regions: List[str] = ["RegX", "RegY"]) -> List[Dict[str, Any]]:
        dataset = []
        years = [2022, 2023, 2024]
        months = list(range(1, 13))

        for _ in range(size):
            budget = random.uniform(10000, 10000000)
            win_price = budget * random.uniform(0.5, 1.2) # Some premiums (win > budget)

            # Inject dirty data
            b = SyntheticFixtureGenerator.generate_dirty_value(dirty_prob) or budget
            w = SyntheticFixtureGenerator.generate_dirty_value(dirty_prob) or win_price
            c = SyntheticFixtureGenerator.generate_dirty_value(dirty_prob) or random.choice(competitors)
            r = SyntheticFixtureGenerator.generate_dirty_value(dirty_prob) or random.choice(regions)

            # Cross-year cross-month dates
            y = random.choice(years)
            m = random.choice(months)
            d = f"{y}-{m:02d}"
            date_val = SyntheticFixtureGenerator.generate_dirty_value(dirty_prob) or d

            dataset.append({
                'competitor': c,
                'region': r,
                'budget': b,
                'win_price': w,
                'date': date_val
            })
        return dataset

class BenchmarkRunner:
    """
    Executes benchmark suites on the analyzer engine.
    """
    @staticmethod
    def run_single_thread(dataset: List[Dict[str, Any]],
                          target_region: Optional[str] = None,
                          target_competitor: Optional[str] = None) -> Tuple[Dict[str, Any], float]:
        start_time = time.time()
        result = analyze_discount_rate_distribution(dataset, target_region, target_competitor)
        elapsed_time = time.time() - start_time
        return result, elapsed_time

    @staticmethod
    def run_concurrently(dataset: List[Dict[str, Any]], num_workers: int = 10,
                         target_region: Optional[str] = None,
                         target_competitor: Optional[str] = None) -> Tuple[List[Dict[str, Any]], float]:
        start_time = time.time()
        results = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=num_workers) as executor:
            # We run the identical dataset on multiple threads to simulate concurrent identical requests
            # In a real scenario, datasets might be different chunks
            futures = [executor.submit(analyze_discount_rate_distribution, dataset, target_region, target_competitor) for _ in range(num_workers)]
            for future in concurrent.futures.as_completed(futures):
                results.append(future.result())
        elapsed_time = time.time() - start_time
        return results, elapsed_time
