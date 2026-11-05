import logging
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import random
import string
from ultra_modules.credit_china_blacklist_check_core_engine import verify_credit_status

logger = logging.getLogger(__name__)

class CreditChinaBenchmarkSuite:
    """
    Benchmark suite for the Credit China Blacklist Check engine.
    This suite generates synthetic fixtures (including dirty data, boundaries, etc.)
    and executes them in high concurrency to verify millisecond-level responsiveness
    and zero-crash robustness.
    """

    def __init__(self):
        self.fixtures = []

    def _generate_gibberish(self, length=20):
        """Generates random gibberish string."""
        return ''.join(random.choices(string.ascii_letters + string.punctuation, k=length))

    def generate_synthetic_fixtures(self, count=1000):
        """
        Synthesize datasets containing nulls, gibberish, date boundaries,
        and missing parameters to verify zero-crash execution.
        """
        self.fixtures = []
        base_time = datetime.now()

        for i in range(count):
            case_type = i % 5
            if case_type == 0:
                # Normal Case
                self.fixtures.append({
                    "company_name": f"Company_{i}",
                    "credit_china_status": "无重大违法记录",
                    "ccgp_status": "clear",
                    "has_screenshot": True
                })
            elif case_type == 1:
                # Nulls / Missing Parameters
                self.fixtures.append({
                    "company_name": None,
                    "credit_china_status": None,
                    # Missing ccgp_status and has_screenshot to simulate defaults
                })
            elif case_type == 2:
                # Dirty Data / Gibberish / Type Violations
                self.fixtures.append({
                    "company_name": 12345,
                    "credit_china_status": self._generate_gibberish(),
                    "ccgp_status": ["not", "a", "string"],
                    "has_screenshot": "maybe?"
                })
            elif case_type == 3:
                # Cross Month/Year Boundaries (simulating timestamp artifacts in logs or names)
                cross_month = base_time + timedelta(days=30)
                cross_year = base_time + timedelta(days=365)
                name = f"Boundary_{cross_month.strftime('%Y%m')}_{cross_year.strftime('%Y%m')}"
                self.fixtures.append({
                    "company_name": name,
                    "credit_china_status": "clear",
                    "ccgp_status": "正常",
                    "has_screenshot": True
                })
            elif case_type == 4:
                # Extreme Violations / Known bad statuses
                self.fixtures.append({
                    "company_name": f"BadCo_{i}",
                    "credit_china_status": "有违法失信记录",
                    "ccgp_status": "黑名单",
                    "has_screenshot": False
                })

        return self.fixtures

    def run_benchmark(self, concurrency=100):
        """
        Execute the validation logic over generated fixtures using ThreadPoolExecutor
        to test high concurrency and ensure millisecond response time and zero crashes.
        """
        if not self.fixtures:
            self.generate_synthetic_fixtures()

        def execute_fixture(fixture):
            start = time.perf_counter()
            # Unpack with defaults to simulate kwargs unpacking
            result = verify_credit_status(
                company_name=fixture.get("company_name"),
                credit_china_status=fixture.get("credit_china_status"),
                ccgp_status=fixture.get("ccgp_status"),
                has_screenshot=fixture.get("has_screenshot")
            )
            duration_ms = (time.perf_counter() - start) * 1000
            return result, duration_ms

        results = []
        errors = []
        total_duration_ms = 0

        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            try:
                # Mapping executor across all fixtures
                futures = executor.map(execute_fixture, self.fixtures)
                for res, duration in futures:
                    results.append(res)
                    total_duration_ms += duration
            except Exception as e:
                errors.append(e)

        if errors:
            raise RuntimeError(f"Benchmark encountered crashes: {errors}")

        avg_ms = total_duration_ms / len(self.fixtures) if self.fixtures else 0

        return {
            "total_executed": len(self.fixtures),
            "errors": len(errors),
            "average_response_time_ms": avg_ms,
            "is_millisecond_level": avg_ms < 5.0  # Asserting avg < 5ms per call
        }
