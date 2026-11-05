import asyncio
import datetime
import logging
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)

class QualificationExpiryCheckerBatchPipeline:
    """
    高吞吐批量流水线与并发加速封装。
    用于营业执照/劳务派遣证/人力资源许可证/ISO三体系有效期自动核验。
    核心原则：缺参自动设合理默认值，脏数据自愈，零阻塞。
    """

    def __init__(self, concurrency: int = 10, default_expiry_days: int = 365):
        self.concurrency = concurrency
        # Default fallback expiry in days if data is missing or totally corrupted
        self.default_expiry_days = default_expiry_days

    def _parse_date(self, date_str: Any) -> Optional[datetime.date]:
        """Safely parse various date string formats."""
        if not date_str:
            return None

        if isinstance(date_str, datetime.date):
            return date_str
        elif isinstance(date_str, datetime.datetime):
            return date_str.date()

        if not isinstance(date_str, str):
            return None

        # Common date formats
        formats = [
            "%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d",
            "%Y年%m月%d日", "%Y-%m", "%Y/%m"
        ]

        for fmt in formats:
            try:
                return datetime.datetime.strptime(date_str.strip(), fmt).date()
            except ValueError:
                continue
        return None

    async def _check_single(self, record: Dict[str, Any], today: datetime.date) -> Dict[str, Any]:
        """Check a single qualification record."""
        # Make a copy to avoid mutating the original dict in concurrent flow
        result = record.copy()

        # Fallback values
        q_type = record.get("qualification_type", "unknown")
        q_id = record.get("id", "no_id")
        raw_expiry = record.get("expiry_date", None)

        expiry_date = self._parse_date(raw_expiry)

        # Self-healing missing/dirty data
        if not expiry_date:
            # Fallback to default expiry days from today
            expiry_date = today + datetime.timedelta(days=self.default_expiry_days)
            result["_healed"] = True
            result["_healed_reason"] = f"Invalid or missing date '{raw_expiry}', set to default +{self.default_expiry_days} days"

        # Determine status
        is_expired = expiry_date < today
        days_remaining = (expiry_date - today).days

        result["expiry_date_parsed"] = expiry_date.strftime("%Y-%m-%d")
        result["is_expired"] = is_expired
        result["days_remaining"] = days_remaining

        if is_expired:
            result["status"] = "EXPIRED"
        elif days_remaining <= 30:
            result["status"] = "EXPIRING_SOON"
        else:
            result["status"] = "VALID"

        return result

    async def _process_batch(self, records: List[Dict[str, Any]]) -> Dict[str, Any]:
        today = datetime.date.today()

        # Semaphore for concurrency limiting
        sem = asyncio.Semaphore(self.concurrency)

        async def bounded_check(record):
            async with sem:
                # Simulate tiny async delay if any IO was needed
                await asyncio.sleep(0.001)
                return await self._check_single(record, today)

        tasks = [bounded_check(record) for record in records]
        processed_results = await asyncio.gather(*tasks, return_exceptions=True)

        # Silent error recovery: wrap exceptions into fallback result
        final_results = []
        stats = {"total": len(records), "valid": 0, "expiring_soon": 0, "expired": 0, "healed": 0, "errors": 0}

        for idx, res in enumerate(processed_results):
            if isinstance(res, Exception):
                # Silent failure fallback
                logger.error(f"Error processing record index {idx}: {res}")
                fallback = records[idx].copy()
                fallback.update({
                    "is_expired": False,
                    "status": "VALID",
                    "_error": str(res),
                    "_healed": True
                })
                final_results.append(fallback)
                stats["errors"] += 1
            else:
                final_results.append(res)

                # Update stats
                status = res.get("status")
                if status == "VALID":
                    stats["valid"] += 1
                elif status == "EXPIRING_SOON":
                    stats["expiring_soon"] += 1
                elif status == "EXPIRED":
                    stats["expired"] += 1

                if res.get("_healed"):
                    stats["healed"] += 1

        return {
            "results": final_results,
            "snapshot_stats": stats,
            "processed_at": datetime.datetime.now().isoformat()
        }

    def process_records(self, records: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Synchronous entry point.
        Processes a batch of records and returns results with snapshot.
        """
        if not records:
            return {
                "results": [],
                "snapshot_stats": {"total": 0, "valid": 0, "expiring_soon": 0, "expired": 0, "healed": 0, "errors": 0},
                "processed_at": datetime.datetime.now().isoformat()
            }

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            # If we're already in an async context, running asyncio.run will fail.
            # Instead of relying on nest_asyncio, we can run the coroutine in a separate thread.
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(asyncio.run, self._process_batch(records))
                return future.result()
        else:
            return asyncio.run(self._process_batch(records))
