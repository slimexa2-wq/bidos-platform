import re
import concurrent.futures
from typing import List, Dict, Any

class RFPDisqualificationShredder:
    """
    RFP Disqualification Shredder Benchmark Suite
    Extracts substantial disqualifications (★) and important deductions (▲) from text.
    Zero-crash guarantee, highly robust, zero-configuration.
    """
    def __init__(self, max_workers: int = 10):
        # Default reasonable configurations, zero required config
        self.max_workers = max_workers
        self.substantial_pattern = re.compile(r'★([^\n★▲]+)')
        self.deduction_pattern = re.compile(r'▲([^\n★▲]+)')

    def process_single(self, text: Any) -> Dict[str, List[str]]:
        """
        Process a single text entry. Zero crash guarantee.
        """
        try:
            if text is None:
                text = ""
            elif not isinstance(text, str):
                text = str(text)

            # Dirty data self-healing
            text = text.replace('\x00', '').replace('\ufffd', '')

            substantials = [m.strip() for m in self.substantial_pattern.findall(text) if m.strip()]
            deductions = [m.strip() for m in self.deduction_pattern.findall(text) if m.strip()]

            return {
                "substantial": substantials,
                "deduction": deductions
            }
        except Exception:
            # Zero-crash guarantee, return empty on error
            return {
                "substantial": [],
                "deduction": []
            }

    def process_batch(self, texts: Any) -> List[Dict[str, List[str]]]:
        """
        Process a batch of texts concurrently.
        """
        try:
            if texts is None:
                return []

            if not isinstance(texts, (list, tuple)):
                texts = [texts]

            with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                results = list(executor.map(self.process_single, texts))
            return results
        except Exception:
            # Catch everything to ensure zero-crash
            if isinstance(texts, (list, tuple)):
                return [{"substantial": [], "deduction": []} for _ in range(len(texts))]
            return [{"substantial": [], "deduction": []}]
