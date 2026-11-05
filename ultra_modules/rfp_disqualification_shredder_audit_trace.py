import re
import uuid
import datetime
from typing import Dict, List, Any, Optional
from .rfp_disqualification_shredder_core_engine import shred_rfp_disqualification_clauses

class ShredderAuditTrace:
    """
    RFP Disqualification & Deduction Shredder Audit Trace Engine
    Provides full lifecycle tracing for extraction, deduplication, and highlighting,
    while keeping the frontend response simple and clean.
    """

    def __init__(self, trace_id: Optional[str] = None):
        """
        Initializes the audit trace. Assigns a default trace_id if none provided.
        """
        self.trace_id = trace_id if isinstance(trace_id, str) and trace_id.strip() else str(uuid.uuid4())
        self.audit_log: List[Dict[str, Any]] = []

    def _log_event(self, action: str, details: Any) -> None:
        """Silently records a step in the audit log."""
        self.audit_log.append({
            "timestamp": datetime.datetime.now().isoformat(),
            "action": action,
            "details": details
        })

    def _safe_string_conversion(self, content: Any) -> str:
        """
        Silently wash dirty data and ensure string format.
        """
        if content is None:
            return ""
        if isinstance(content, str):
            return content
        try:
            return str(content)
        except Exception:
            return ""

    def process(self, content: Any, **kwargs) -> Dict[str, Any]:
        """
        Main processing pipeline:
        1. Input washing
        2. Core Extraction
        3. Deduplication
        4. Difference/Highlighting preparation
        """
        # Step 1: Input washing
        clean_content = self._safe_string_conversion(content)
        self._log_event("input_washing", {
            "original_type": type(content).__name__,
            "washed_length": len(clean_content)
        })

        # Step 2: Core Extraction using the core engine
        # Pass kwargs to configure symbols if provided
        extracted = shred_rfp_disqualification_clauses(clean_content, **kwargs)
        self._log_event("core_extraction", {
            "raw_disqualifications_count": len(extracted.get("disqualifications", [])),
            "raw_deductions_count": len(extracted.get("deductions", []))
        })

        # Step 3: Deduplication (preserve order)
        def deduplicate(items: List[str]) -> List[str]:
            seen = set()
            deduped = []
            for item in items:
                # normalize spacing for deduplication comparison
                # strip out list numbering like "2. " to catch actual duplicates
                normalized = re.sub(r'^\d+[\.\)\]]\s*', '', item)
                normalized = re.sub(r'\s+', ' ', normalized).strip()
                if normalized not in seen and normalized:
                    seen.add(normalized)
                    deduped.append(item)
            return deduped

        final_disqualifications = deduplicate(extracted.get("disqualifications", []))
        final_deductions = deduplicate(extracted.get("deductions", []))

        self._log_event("deduplication", {
            "final_disqualifications_count": len(final_disqualifications),
            "final_deductions_count": len(final_deductions)
        })

        # Step 4: Highlighting (add HTML tags for UI)
        disq_symbols = kwargs.get("disqualification_symbols", ["★", "*", "废标", "否决"])
        ded_symbols = kwargs.get("deduction_symbols", ["▲", "扣分", "减分"])

        def highlight(items: List[str], symbols: List[str], color: str) -> List[str]:
            highlighted = []
            # Sort symbols by length descending to avoid partial matches on shorter symbols if they overlap
            sorted_symbols = sorted([s for s in symbols if isinstance(s, str) and s.strip()], key=len, reverse=True)
            for item in items:
                hl_item = item
                for sym in sorted_symbols:
                    escaped_sym = re.escape(sym)
                    # Simple highlight wrapping
                    hl_item = re.sub(f'({escaped_sym})', rf'<span style="color:{color};font-weight:bold;">\1</span>', hl_item)
                highlighted.append(hl_item)
            return highlighted

        highlighted_disqualifications = highlight(final_disqualifications, disq_symbols, "red")
        highlighted_deductions = highlight(final_deductions, ded_symbols, "orange")

        self._log_event("highlighting", {
            "status": "completed"
        })

        # Return extremely clean result for the frontend
        return {
            "trace_id": self.trace_id,
            "clean_result": {
                "disqualifications": final_disqualifications,
                "deductions": final_deductions,
                "highlighted_disqualifications": highlighted_disqualifications,
                "highlighted_deductions": highlighted_deductions
            },
            "audit_log": self.audit_log  # Backend logging data
        }

def run_audit_trace(content: Any, trace_id: Optional[str] = None, **kwargs) -> Dict[str, Any]:
    """
    Pure functional algorithm wrapper for extreme simplicity.
    One-click access with auto-fallback.
    """
    tracer = ShredderAuditTrace(trace_id=trace_id)
    return tracer.process(content, **kwargs)
