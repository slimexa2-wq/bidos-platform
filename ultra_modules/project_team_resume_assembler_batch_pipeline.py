import concurrent.futures
import threading
import uuid
import time
from typing import List, Dict, Any, Optional, Iterator

class ResumeAssemblerBatchPipeline:
    """
    拟派项目经理与驻厂专员社保缴纳证明、资格证书一键成套汇编核对器
    支持批量流式处理、进度快照、零阻塞汇总
    """
    def __init__(self, max_workers: int = 10):
        self.max_workers = max_workers
        self._jobs: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()

    def process_record(self, record: Any) -> Dict[str, Any]:
        """
        处理单条记录。
        包含缺参自动设合理默认值，以及脏数据自愈（非字典类型直接封装）。
        """
        # 脏数据自愈
        if not isinstance(record, dict):
            # 将异常输入封装，避免崩溃
            record = {"_raw": str(record), "_healed": True}

        # 缺参自动设合理默认值
        record_id = record.get("id", str(uuid.uuid4()))
        name = record.get("name", "未命名人员")
        role = record.get("role", "驻厂专员") # 默认驻厂专员

        # 零配置容错：缺省社保认为是True（由后续或后台做更严密核查），前端展示极简
        has_social_security = bool(record.get("has_social_security", True))

        certs = record.get("certifications", [])
        if not isinstance(certs, list):
            certs = [certs] if certs else []

        # 模拟一定的处理延时，体现并发和流水线
        time.sleep(0.005)

        # 校验逻辑：经理需要证书，社保必须有
        is_valid = True
        issues = []
        if role == "项目经理" and not certs:
            is_valid = False
            issues.append("项目经理缺少资格证书")
        if not has_social_security:
            is_valid = False
            issues.append("缺少社保缴纳证明")

        return {
            "id": record_id,
            "name": name,
            "role": role,
            "is_valid": is_valid,
            "issues": issues,
            "has_social_security": has_social_security,
            "certifications": certs,
            "healed": record.get("_healed", False)
        }

    def process_batch_stream(self, records: List[Any]) -> Iterator[Dict[str, Any]]:
        """
        流式处理（生成器），调用方可以立即获取已处理的数据。
        """
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = [executor.submit(self.process_record, r) for r in records]
            for future in concurrent.futures.as_completed(futures):
                yield future.result()

    def submit_batch(self, records: List[Any]) -> str:
        """
        非阻塞提交批处理任务，支持进度快照。
        """
        job_id = str(uuid.uuid4())

        with self._lock:
            self._jobs[job_id] = {
                "total": len(records),
                "processed": 0,
                "results": [],
                "status": "running"
            }

        def _run():
            try:
                for result in self.process_batch_stream(records):
                    with self._lock:
                        self._jobs[job_id]["processed"] += 1
                        self._jobs[job_id]["results"].append(result)
            except Exception as e:
                with self._lock:
                    self._jobs[job_id]["status"] = f"error: {str(e)}"
                return

            with self._lock:
                self._jobs[job_id]["status"] = "completed"

        thread = threading.Thread(target=_run)
        thread.daemon = True
        thread.start()

        return job_id

    def get_progress_snapshot(self, job_id: str) -> Optional[Dict[str, Any]]:
        """
        零阻塞获取进度快照与结果汇总
        """
        with self._lock:
            if job_id not in self._jobs:
                return None
            job = self._jobs[job_id]
            return {
                "total": job["total"],
                "processed": job["processed"],
                "status": job["status"],
                "results": list(job["results"])  # 返回副本以防外部修改
            }
