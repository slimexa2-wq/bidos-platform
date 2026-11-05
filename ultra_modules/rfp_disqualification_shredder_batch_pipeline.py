import re
import concurrent.futures
from typing import List, Dict, Any, Union

class RFPShredderBatchPipeline:
    """
    RFP Disqualification & Deduction Shredder Batch Pipeline
    Extracts '★' (disqualification) and '▲' (deduction) clauses from RFP texts.
    Supports high-throughput batch stream processing and dirty data self-healing.
    """

    def __init__(self, max_workers: int = 10):
        self.max_workers = max_workers
        # Regex patterns for matching ★ (disqualification) and ▲ (deduction) clauses.
        # They will match from the symbol to the end of the sentence/clause,
        # stopping before another symbol, double newline, or EOF.
        self.disqualification_pattern = re.compile(r'(★.*?)(?=(?:★|▲|\n\s*\n|$))', re.DOTALL)
        self.deduction_pattern = re.compile(r'(▲.*?)(?=(?:★|▲|\n\s*\n|$))', re.DOTALL)

    def _process_single_record(self, record: Any) -> Dict[str, Any]:
        """
        Process a single record, self-healing dirty data.
        """
        # Data self-healing / normalization (dirty data auto-correction, fallback for missing params)
        if record is None:
            text = ""
        elif isinstance(record, dict):
            # Fallback for missing parameters: try to find 'text' or 'content' key
            # If not found, use the dict as a string to avoid dropping valid content
            if 'text' in record or 'content' in record:
                text = record.get('text', record.get('content', ''))
                if text is None:
                    text = ""
                elif not isinstance(text, str):
                    text = str(text)
            else:
                text = str(record)
        elif not isinstance(record, str):
            text = str(record)
        else:
            text = record

        # Extractor logic
        try:
            # Strip trailing/leading whitespaces from matches
            disqualifications = [match.strip() for match in self.disqualification_pattern.findall(text) if match.strip()]
            deductions = [match.strip() for match in self.deduction_pattern.findall(text) if match.strip()]
        except Exception:
            disqualifications = []
            deductions = []

        return {
            "original_text": text,
            "disqualifications": disqualifications,
            "deductions": deductions,
            "status": "success"
        }

    def process_batch(self, records: Union[List[Any], Any] = None) -> Dict[str, Any]:
        """
        Process a batch of records. Supports 10~1000 records.
        Fallbacks to an empty list if records is None.
        Returns a progress snapshot and zero-blocking results.
        """
        # Missing parameter default fallback
        if records is None:
            records = []
        elif not isinstance(records, list):
            records = [records]

        total_records = len(records)

        # High throughput concurrent pipeline
        future_to_index = {}
        ordered_results = [None] * total_records
        completed = 0

        with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            for i, record in enumerate(records):
                future = executor.submit(self._process_single_record, record)
                future_to_index[future] = i

            for future in concurrent.futures.as_completed(future_to_index):
                index = future_to_index[future]
                try:
                    ordered_results[index] = future.result()
                except Exception as exc:
                    ordered_results[index] = {"status": "error", "error": str(exc), "original_text": str(records[index])}
                completed += 1

        return {
            "total_processed": total_records,
            "results": ordered_results,
            "progress_snapshot": f"{completed}/{total_records} completed"
        }
