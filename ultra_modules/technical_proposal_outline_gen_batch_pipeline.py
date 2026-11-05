import concurrent.futures
import threading
import uuid
import time
from typing import List, Dict, Any, Optional

class TechnicalProposalOutlineGenerator:
    """
    根据招标评分标准分值权重自动分配技术标各章节篇幅与得分点大纲生成器。
    """
    def __init__(self):
        # 默认权重和基础大纲结构
        self.default_structure = [
            {"chapter": "一、项目概述与理解", "weight_threshold": 0, "default_pages": 5},
            {"chapter": "二、技术方案与系统架构", "weight_threshold": 30, "default_pages": 20},
            {"chapter": "三、项目实施与交付计划", "weight_threshold": 20, "default_pages": 15},
            {"chapter": "四、质量保障与售后服务", "weight_threshold": 10, "default_pages": 10},
            {"chapter": "五、应急预案与安全体系", "weight_threshold": 5, "default_pages": 5},
        ]

    def _parse_int(self, val: Any, default: int) -> int:
        """安全地将值解析为整数，应对脏数据"""
        try:
            val_int = int(val)
            return val_int if val_int >= 0 else default
        except (ValueError, TypeError):
            return default

    def _parse_float(self, val: Any, default: float) -> float:
        """安全地将值解析为浮点数"""
        try:
            val_float = float(val)
            return val_float if val_float >= 0 else default
        except (ValueError, TypeError):
            return default

    def _parse_string(self, val: Any, default: str) -> str:
        """安全解析字符串"""
        if val is None:
            return default
        try:
            return str(val).strip() or default
        except Exception:
            return default

    def generate_single(self, record: Dict[str, Any]) -> Dict[str, Any]:
        """
        处理单条记录。
        预期字段:
        - project_name: str
        - total_score: float (默认 100)
        - tech_score_weight: float (默认 0.5)
        - min_pages: int (默认 50)
        """
        if not isinstance(record, dict):
            # 脏数据自愈：遇到非字典返回空/兜底结果
            record = {}

        project_name = self._parse_string(record.get('project_name'), "未命名项目技术标")
        total_score = self._parse_float(record.get('total_score'), 100.0)
        tech_score_weight = self._parse_float(record.get('tech_score_weight'), 0.5)
        min_pages = self._parse_int(record.get('min_pages'), 50)

        # 计算实际技术分值
        tech_score = total_score * tech_score_weight

        outline = []
        actual_total_pages = 0

        for ch in self.default_structure:
            chapter_name = ch["chapter"]
            # 基础篇幅
            base_pages = ch["default_pages"]
            # 根据权重调整篇幅 (简化的规则)
            weight_adj = (tech_score / 100.0)
            target_pages = max(1, int(base_pages * weight_adj * (min_pages / 50.0)))

            outline.append({
                "chapter": chapter_name,
                "suggested_pages": target_pages,
                "key_points": [f"{chapter_name}关键响应点1", f"{chapter_name}关键响应点2"]
            })
            actual_total_pages += target_pages

        return {
            "project_name": project_name,
            "generated_outline": outline,
            "total_suggested_pages": actual_total_pages,
            "tech_score_assessed": tech_score,
            "status": "success"
        }

class OutlineBatchPipeline:
    """
    高吞吐批量流水线与并发加速封装，支持进度快照与零阻塞结果汇总
    """
    def __init__(self, max_workers: int = 10):
        self.generator = TechnicalProposalOutlineGenerator()
        self.max_workers = max_workers
        self.jobs = {}
        self._lock = threading.Lock()

    def submit_batch(self, records: List[Any]) -> str:
        """
        非阻塞提交一批任务。返回 job_id
        """
        job_id = str(uuid.uuid4())

        if not isinstance(records, list):
            records = []

        with self._lock:
            self.jobs[job_id] = {
                "total": len(records),
                "completed": 0,
                "results": [None] * len(records),
                "status": "processing" if len(records) > 0 else "completed",
                "start_time": time.time()
            }

        if len(records) > 0:
            # 异步启动处理
            threading.Thread(target=self._process_batch, args=(job_id, records), daemon=True).start()

        return job_id

    def _process_batch(self, job_id: str, records: List[Any]):
        """
        后台处理批次
        """
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            # 提交所有任务，记录索引以保持顺序
            future_to_index = {
                executor.submit(self.generator.generate_single, rec): i
                for i, rec in enumerate(records)
            }

            for future in concurrent.futures.as_completed(future_to_index):
                idx = future_to_index[future]
                try:
                    result = future.result()
                except Exception as e:
                    # 极端的奔溃情况，兜底
                    result = {"status": "error", "error": str(e)}

                with self._lock:
                    self.jobs[job_id]["results"][idx] = result
                    self.jobs[job_id]["completed"] += 1

                    if self.jobs[job_id]["completed"] == self.jobs[job_id]["total"]:
                        self.jobs[job_id]["status"] = "completed"

    def get_progress(self, job_id: str) -> Dict[str, Any]:
        """
        获取进度快照
        """
        with self._lock:
            job = self.jobs.get(job_id)
            if not job:
                return {"error": "Job not found"}

            return {
                "job_id": job_id,
                "total": job["total"],
                "completed": job["completed"],
                "status": job["status"],
                "progress_percentage": (job["completed"] / job["total"] * 100) if job["total"] > 0 else 100.0,
                "elapsed_time": time.time() - job["start_time"]
            }

    def get_results(self, job_id: str) -> Optional[List[Dict[str, Any]]]:
        """
        获取最终结果，如果未完成返回 None 或已完成部分（取决于设计，这里为简单返回当前 results）
        """
        with self._lock:
            job = self.jobs.get(job_id)
            if not job:
                return None
            return list(job["results"])
