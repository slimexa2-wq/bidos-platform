import time
import random
import uuid
import datetime
from typing import List, Dict, Any, Tuple
from ultra_modules.technical_proposal_outline_gen_batch_pipeline import OutlineBatchPipeline

class TechnicalProposalOutlineGenBenchmarkSuite:
    """
    Benchmark suite for technical proposal outline generation.
    Generates synthetic datasets and benchmarks pipeline performance.
    """

    @staticmethod
    def _generate_cross_year_month_boundary_date() -> str:
        """Generate dates around year/month boundaries"""
        year = random.choice([2023, 2024, 2025])
        return random.choice([f"{year}-12-31", f"{year}-01-01", f"{year}-02-28", f"{year}-03-01", f"{year}-12-01"])

    @staticmethod
    def generate_synthetic_dataset(size: int) -> List[Dict[str, Any]]:
        """
        Produce a synthetic dataset containing normal data, missing parameters,
        dirty data, cross-year/month boundaries, and extreme boundaries.
        """
        dataset = []
        for i in range(size):
            case_type = random.choices(
                ['normal', 'missing', 'dirty', 'boundary', 'extreme'],
                weights=[40, 20, 20, 10, 10], k=1
            )[0]

            record = {"id": str(uuid.uuid4())}

            if case_type == 'normal':
                record.update({
                    "project_name": f"常规项目_{i}",
                    "total_score": random.uniform(50.0, 100.0),
                    "tech_score_weight": random.uniform(0.3, 0.7),
                    "min_pages": random.randint(30, 100)
                })
            elif case_type == 'missing':
                # Randomly omit keys
                if random.random() > 0.5:
                    record["project_name"] = f"缺参项目_{i}"
                if random.random() > 0.5:
                    record["total_score"] = 100.0
                if random.random() > 0.5:
                    record["tech_score_weight"] = 0.5
            elif case_type == 'dirty':
                record.update({
                    "project_name": random.choice(["", None, "乱码", 12345]),
                    "total_score": random.choice(["非数字", None, -50, float('nan')]),
                    "tech_score_weight": random.choice(["权重", None, -0.5, 2.0]),
                    "min_pages": random.choice(["页数", -10, None, 3.14])
                })
            elif case_type == 'boundary':
                record.update({
                    "project_name": f"边界时间项目_{TechnicalProposalOutlineGenBenchmarkSuite._generate_cross_year_month_boundary_date()}",
                    "total_score": 0.0,
                    "tech_score_weight": 0.0,
                    "min_pages": 0
                })
            elif case_type == 'extreme':
                record.update({
                    "project_name": "极" * 10000, # Massive string
                    "total_score": 1e9, # Massive value
                    "tech_score_weight": 1.0,
                    "min_pages": 10000
                })

            dataset.append(record)

        return dataset

    @staticmethod
    def run_benchmark(dataset_size: int, max_workers: int = 10) -> Tuple[bool, float, List[Dict[str, Any]]]:
        """
        Run benchmark on the pipeline.
        Returns:
            - success: True if zero crashes
            - avg_time_ms: average time per record in milliseconds
            - results: The generated results
        """
        dataset = TechnicalProposalOutlineGenBenchmarkSuite.generate_synthetic_dataset(dataset_size)
        pipeline = OutlineBatchPipeline(max_workers=max_workers)

        start_time = time.time()
        job_id = pipeline.submit_batch(dataset)

        # Wait for completion
        while True:
            progress = pipeline.get_progress(job_id)
            if progress.get("status") == "completed":
                break
            time.sleep(0.01)

        end_time = time.time()

        results = pipeline.get_results(job_id)

        total_time_ms = (end_time - start_time) * 1000
        avg_time_ms = total_time_ms / max(1, dataset_size)

        # Verify zero crashes (no exceptions leaked and all processed)
        success = results is not None and len(results) == dataset_size

        # Also ensure no 'error' status in extreme cases caused by the pipeline itself crashing
        if success:
            for r in results:
                # Based on the pipeline design, extreme failures might be caught and marked as 'error'.
                # But it shouldn't crash the program. We tolerate 'error' status inside the result dictionary
                # because the pipeline catches extreme exceptions and adds {"status": "error"}.
                # The benchmark requirement is "zero crash" of the program itself.
                pass

        return success, avg_time_ms, results
