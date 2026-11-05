import time
import random
import string
import datetime
import concurrent.futures
from ultra_modules.one_page_go_nogo_card_core_engine import generate_decision_card

def generate_random_string(length=10):
    return ''.join(random.choices(string.ascii_letters + string.digits + " !@#$%^&*()_+", k=length))

def generate_synthetic_fixtures(size=1000):
    """
    生成包含正常、空值、乱码、跨年跨月日期等极端边界的合成数据集
    """
    fixtures = []

    # 确保涵盖正常、脏数据、缺参、极端边界
    # 强制每种类型至少一个
    types = ['normal', 'dirty', 'missing', 'extreme']

    for i in range(size):
        case_type = types[i % 4] if i < 4 else random.choice(types)

        if case_type == 'normal':
            fixture = {
                'budget': random.uniform(1000, 1000000),
                'cost': random.uniform(500, 900000),
                'is_blacklisted': random.choice([True, False]),
                'competitor_count': random.randint(0, 10),
                'has_mandatory_qualification': random.choice([True, False]),
                'date': (datetime.datetime.now() - datetime.timedelta(days=random.randint(0, 365))).strftime("%Y-%m-%d")
            }
        elif case_type == 'dirty':
            fixture = {
                'budget': generate_random_string(5),
                'cost': None,
                'is_blacklisted': generate_random_string(3),
                'competitor_count': 'NaN',
                'has_mandatory_qualification': None,
                'date': '2024-13-45' # 乱码日期/跨界
            }
        elif case_type == 'missing':
            fixture = {} # 缺参
        else:
            # extreme
            fixture = {
                'budget': 1e20,
                'cost': -1e20,
                'is_blacklisted': 1,
                'competitor_count': 1e10,
                'has_mandatory_qualification': 0,
                'date': '1970-01-01'
            }

        fixtures.append(fixture)

    return fixtures

def run_performance_benchmark(fixtures, concurrency=10):
    """
    运行性能基准测试，验证零崩溃与并发毫秒级响应
    """
    start_time = time.time()
    results = []

    def process(fixture):
        try:
            return generate_decision_card(fixture)
        except Exception as e:
            raise RuntimeError(f"Benchmark failed with crash: {e}")

    if concurrency > 1:
        with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as executor:
            futures = [executor.submit(process, f) for f in fixtures]
            for future in concurrent.futures.as_completed(futures):
                results.append(future.result())
    else:
        for f in fixtures:
            results.append(process(f))

    end_time = time.time()
    total_time_ms = (end_time - start_time) * 1000
    avg_time_ms = total_time_ms / len(fixtures) if fixtures else 0

    return {
        'total_cases': len(fixtures),
        'total_time_ms': total_time_ms,
        'avg_time_ms': avg_time_ms,
        'success_count': len(results)
    }
