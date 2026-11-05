import time
import json
import difflib
from typing import Dict, Any, List, Optional
import concurrent.futures
from dataclasses import dataclass, asdict

@dataclass
class DiffResult:
    clause_id: str
    diff_type: str  # added, removed, modified, unchanged
    original_text: str
    new_text: str
    diff_html: str
    affected_sections: List[str]
    similarity_score: float

class AddendumClauseDiffEngine:
    """
    招标更正/澄清公告与原招标文件条款级紅绿Diff对比及影响章节重算器.
    Zero-configuration, fallback to defaults, silently fixes dirty data.
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        # Zero configuration fallback
        self.config = config or {}
        self.similarity_threshold = self.config.get('similarity_threshold', 0.8)
        self.timeout = self.config.get('timeout', 5000) # milliseconds

    def _clean_text(self, text: Any) -> str:
        """Silently clean dirty data (nulls, garbled text, unexpected types)."""
        if text is None:
            return ""
        if not isinstance(text, str):
            if isinstance(text, dict) or isinstance(text, list):
                return ""
            try:
                text = str(text)
            except Exception:
                return ""

        # Basic cleaning for common garbled/weird chars
        text = text.replace('\ufffd', '') # Replacement character
        text = text.replace('\x00', '')
        return text.strip()

    def _calculate_similarity(self, text1: str, text2: str) -> float:
        if not text1 and not text2:
            return 1.0
        matcher = difflib.SequenceMatcher(None, text1, text2)
        return matcher.ratio()

    def _generate_diff_html(self, text1: str, text2: str) -> str:
        # Simple Red/Green diff HTML generation
        matcher = difflib.SequenceMatcher(None, text1, text2)
        diff_html = ""
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == 'equal':
                diff_html += f"<span>{text1[i1:i2]}</span>"
            elif tag == 'replace':
                diff_html += f"<del style='color:red;'>{text1[i1:i2]}</del><ins style='color:green;'>{text2[j1:j2]}</ins>"
            elif tag == 'delete':
                diff_html += f"<del style='color:red;'>{text1[i1:i2]}</del>"
            elif tag == 'insert':
                diff_html += f"<ins style='color:green;'>{text2[j1:j2]}</ins>"
        return diff_html

    def _infer_affected_sections(self, clause_id: str, new_text: str) -> List[str]:
        # Dummy logic to infer affected sections
        sections = []
        if "价格" in new_text or "费用" in new_text:
            sections.append("商务标")
        if "参数" in new_text or "技术" in new_text:
            sections.append("技术标")
        if "时间" in new_text or "期限" in new_text or "年" in new_text or "月" in new_text:
            sections.append("时间表")
        if "预算" in new_text or "金额" in new_text:
            sections.append("商务标")

        if not sections:
            sections.append("通用条款")
        return list(set(sections))

    def compare_clauses(self, original_clause: Any, addendum_clause: Any) -> DiffResult:
        """
        Compare a single clause. Gracefully handles dirty data and missing fields.
        """
        if not isinstance(original_clause, dict):
            original_clause = {}
        if not isinstance(addendum_clause, dict):
            addendum_clause = {}

        # Silently clean and fallback
        clause_id = str(original_clause.get('id', addendum_clause.get('id', 'unknown_id')))

        orig_text = self._clean_text(original_clause.get('text', ''))
        add_text = self._clean_text(addendum_clause.get('text', ''))

        similarity = self._calculate_similarity(orig_text, add_text)

        if orig_text == "" and add_text == "":
            diff_type = 'unchanged'
        elif not orig_text and add_text:
            diff_type = 'added'
        elif orig_text and not add_text:
            diff_type = 'removed'
        elif similarity == 1.0:
            diff_type = 'unchanged'
        else:
            diff_type = 'modified'

        diff_html = self._generate_diff_html(orig_text, add_text)
        affected_sections = self._infer_affected_sections(clause_id, add_text)

        return DiffResult(
            clause_id=clause_id,
            diff_type=diff_type,
            original_text=orig_text,
            new_text=add_text,
            diff_html=diff_html,
            affected_sections=affected_sections,
            similarity_score=similarity
        )

    def process_document_diff(self, original_doc: Any, addendum_doc: Any) -> Dict[str, Any]:
        """
        Process whole document with multiple clauses.
        """
        if not isinstance(original_doc, dict): original_doc = {}
        if not isinstance(addendum_doc, dict): addendum_doc = {}

        orig_clauses = original_doc.get('clauses', [])
        add_clauses = addendum_doc.get('clauses', [])

        if not isinstance(orig_clauses, list): orig_clauses = []
        if not isinstance(add_clauses, list): add_clauses = []

        orig_map = {str(c.get('id', '')): c for c in orig_clauses if isinstance(c, dict) and c.get('id')}
        add_map = {str(c.get('id', '')): c for c in add_clauses if isinstance(c, dict) and c.get('id')}

        all_ids = set(orig_map.keys()).union(set(add_map.keys()))

        results = []
        for cid in all_ids:
            res = self.compare_clauses(orig_map.get(cid, {}), add_map.get(cid, {}))
            if not res.clause_id or res.clause_id == 'unknown_id':
                 res.clause_id = cid
            results.append(asdict(res))

        return {
            "status": "success",
            "total_clauses": len(results),
            "diff_results": results
        }


class BenchmarkSuite:
    """
    Synthetic Fixtures and Performance Benchmark for Diff Engine.
    """

    def __init__(self):
        self.engine = AddendumClauseDiffEngine()

    def generate_synthetic_fixtures(self, num_clauses: int = 1000) -> tuple:
        """
        Generate datasets containing nulls, garbled text, extreme cross-year boundaries.
        """
        original_doc = {"clauses": []}
        addendum_doc = {"clauses": []}

        for i in range(num_clauses):
            orig_clause = {"id": f"C{i}"}
            add_clause = {"id": f"C{i}"}

            # Inject various types of data based on index
            if i % 10 == 0:
                # Dirty data: Nulls
                orig_clause['text'] = None
                add_clause['text'] = None
            elif i % 10 == 1:
                # Dirty data: Garbled text and weird types
                orig_clause['text'] = {"weird": "type"}
                add_clause['text'] = "Normal \ufffd text \x00"
            elif i % 10 == 2:
                # Cross-year boundary condition
                orig_clause['text'] = "有效期至2023年12月31日。"
                add_clause['text'] = "有效期延长至2024年1月1日。"
            elif i % 10 == 3:
                # Big chunk of text
                orig_clause['text'] = "A" * 5000
                add_clause['text'] = "A" * 2500 + "B" * 2500
            else:
                # Normal modified
                orig_clause['text'] = f"Standard clause text {i}"
                add_clause['text'] = f"Standard clause text {i} updated with more details."

            original_doc['clauses'].append(orig_clause)
            addendum_doc['clauses'].append(add_clause)

        return original_doc, addendum_doc

    def run_concurrency_benchmark(self, original_doc: Dict, addendum_doc: Dict, concurrency: int = 50) -> Dict[str, Any]:
        """
        Run benchmark with high concurrency to ensure no crash and millisecond response.
        """
        start_time = time.time()

        def run_task():
            return self.engine.process_document_diff(original_doc, addendum_doc)

        success_count = 0
        with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as executor:
            futures = [executor.submit(run_task) for _ in range(concurrency)]
            for future in concurrent.futures.as_completed(futures):
                try:
                    res = future.result()
                    if res['status'] == 'success':
                        success_count += 1
                except Exception:
                    pass

        end_time = time.time()
        total_time_ms = (end_time - start_time) * 1000
        avg_time_ms = total_time_ms / concurrency if concurrency > 0 else 0

        return {
            "concurrency": concurrency,
            "success_count": success_count,
            "total_time_ms": total_time_ms,
            "avg_time_ms_per_task": avg_time_ms
        }
