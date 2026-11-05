from typing import List, Dict, Any

class CompetitorWinPriceAnalyzer:
    """
    Historical competitor win price analyzer.
    Analyzes historical competitor win data, calculates discount rates (win_price / budget),
    cleans dirty data, and provides a silent audit trace for all transformations.
    """

    def __init__(self):
        self.audit_trace = []

    def _record_audit(self, action: str, details: Any):
        """Silently record an audit trace step."""
        self.audit_trace.append({
            "action": action,
            "details": details
        })

    def analyze(self, data: List[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Main analysis entry point.
        Expects a list of dictionaries with keys like 'id', 'competitor', 'budget', 'win_price'.
        """
        self.audit_trace = []

        # 1. Input Validation and Fallback
        if data is None:
            self._record_audit("input_fallback", "Data is None, defaulting to empty list.")
            data = []
        elif not isinstance(data, list):
            self._record_audit("input_fallback", f"Data is of type {type(data).__name__}, defaulting to empty list.")
            data = []

        self._record_audit("start", f"Received {len(data)} records for analysis.")

        cleaned_data = []
        seen_ids = set()

        # 2. Data Cleaning and Deduplication
        for i, row in enumerate(data):
            if not isinstance(row, dict):
                self._record_audit("skip_row", {"index": i, "reason": "Row is not a dictionary."})
                continue

            # Deduplication
            record_id = row.get("id")
            if record_id is not None:
                record_id = str(record_id).strip()
                if record_id in seen_ids:
                    self._record_audit("deduplicate", {"index": i, "id": record_id, "reason": "Duplicate ID."})
                    continue
                seen_ids.add(record_id)
            else:
                self._record_audit("missing_id", {"index": i, "action": "Proceeding without ID deduplication."})

            # Normalization
            competitor = row.get("competitor")
            if not competitor or not isinstance(competitor, str) or not competitor.strip():
                self._record_audit("repair_field", {"index": i, "field": "competitor", "original": competitor, "repaired": "Unknown"})
                competitor = "Unknown"
            else:
                competitor = competitor.strip()

            # Type conversions and numeric validations
            budget = row.get("budget", 0)
            win_price = row.get("win_price", 0)

            try:
                budget = float(budget)
            except (ValueError, TypeError):
                self._record_audit("repair_field", {"index": i, "field": "budget", "original": budget, "repaired": 0.0})
                budget = 0.0

            try:
                win_price = float(win_price)
            except (ValueError, TypeError):
                self._record_audit("repair_field", {"index": i, "field": "win_price", "original": win_price, "repaired": 0.0})
                win_price = 0.0

            # Boundary checks
            if budget <= 0:
                self._record_audit("skip_row", {"index": i, "reason": f"Invalid budget: {budget} (must be > 0)."})
                continue

            if win_price < 0:
                self._record_audit("skip_row", {"index": i, "reason": f"Invalid win_price: {win_price} (cannot be negative)."})
                continue

            discount_rate = win_price / budget
            self._record_audit("calculate_discount", {"index": i, "competitor": competitor, "discount_rate": discount_rate})

            cleaned_data.append({
                "competitor": competitor,
                "budget": budget,
                "win_price": win_price,
                "discount_rate": discount_rate
            })

        self._record_audit("clean_complete", f"Successfully cleaned {len(cleaned_data)} valid records.")

        # 3. Aggregation and Summarization
        summary_tmp = {}
        for record in cleaned_data:
            comp = record["competitor"]
            if comp not in summary_tmp:
                summary_tmp[comp] = {
                    "count": 0,
                    "total_budget": 0.0,
                    "total_win_price": 0.0,
                    "discount_rates": []
                }

            summary_tmp[comp]["count"] += 1
            summary_tmp[comp]["total_budget"] += record["budget"]
            summary_tmp[comp]["total_win_price"] += record["win_price"]
            summary_tmp[comp]["discount_rates"].append(record["discount_rate"])

        summary = {}
        for comp, stats in summary_tmp.items():
            rates = stats["discount_rates"]
            summary[comp] = {
                "count": stats["count"],
                "total_budget": stats["total_budget"],
                "total_win_price": stats["total_win_price"],
                "min_discount": min(rates),
                "max_discount": max(rates),
                "avg_discount": sum(rates) / len(rates)
            }

        self._record_audit("analysis_complete", f"Generated summary for {len(summary)} competitors.")

        # Return clean result and silent audit trace
        return {
            "summary": summary,
            "audit_trace": self.audit_trace
        }
