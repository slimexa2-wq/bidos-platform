import re
import time
import json
from typing import Any, Dict, List, Optional


class RfpDisqualificationShredderGuard:
    """
    RFP Disqualification and Deduction Items Shredder with Self-Healing and 5-second Undo Guard.
    Extracts substantive items (★, 实质性, 废标) and deduction items (▲, 扣分).
    """

    def __init__(self):
        self.last_result: Dict[str, List[str]] = {"substantive_items": [], "deduction_items": []}
        self._snapshots: Dict[str, Dict[str, Any]] = {}
        self._current_state_id: Optional[str] = None

    def _sanitize_input(self, text: Any) -> str:
        """Silent self-healing logic for invalid/missing inputs (zero configurations, automatic fallback)."""
        if text is None:
            return ""
        if isinstance(text, (dict, list)):
            try:
                return json.dumps(text, ensure_ascii=False)
            except Exception:
                return str(text)
        return str(text)

    def process(self, text: Any = None, **kwargs) -> Dict[str, Any]:
        """
        Process the text to extract substantive items and deduction items.
        Maintains a snapshot for 5 seconds for one-click undo.
        Silent fallback for missing arguments or kwargs.
        """
        sanitized_text = self._sanitize_input(text)

        # Dual-channel extraction (Regex + Semantic Mock)
        substantive_items = self._extract_substantive(sanitized_text)
        deduction_items = self._extract_deduction(sanitized_text)

        new_result = {
            "substantive_items": substantive_items,
            "deduction_items": deduction_items
        }

        # Snapshot mechanism (Save the previous state before updating)
        snapshot_id = f"{time.time()}_{hash(sanitized_text)}"
        self._snapshots[snapshot_id] = {
            "timestamp": time.time(),
            "state": self.last_result,
        }

        # Update current state
        self.last_result = new_result
        self._current_state_id = snapshot_id

        # Clean up old snapshots (older than 5 seconds)
        self._cleanup_snapshots()

        # Return new result and snapshot_id (minimalistic interface)
        return {
            "result": self.last_result,
            "snapshot_id": snapshot_id
        }

    def _extract_substantive(self, text: str) -> List[str]:
        """Channel 1 & 2: Regex and Semantic mock extraction for substantive items."""
        lines = [line.strip() for line in re.split(r'[\n\r。；;]+', text) if line.strip()]
        results = []
        for line in lines:
            # Regex channel
            if re.search(r'(★|实质性|废标)', line):
                if line not in results:
                    results.append(line)
            # Semantic mock channel (simulating advanced NLP matches)
            elif "不满足则拒绝" in line or "必须提供" in line:
                if line not in results:
                    results.append(line)
        return results

    def _extract_deduction(self, text: str) -> List[str]:
        """Channel 1 & 2: Regex and Semantic mock extraction for deduction items."""
        lines = [line.strip() for line in re.split(r'[\n\r。；;]+', text) if line.strip()]
        results = []
        for line in lines:
            # Regex channel
            if re.search(r'(▲|扣分)', line):
                if line not in results:
                    results.append(line)
            # Semantic mock channel
            elif "不提供不得分" in line or "酌情减分" in line:
                if line not in results:
                    results.append(line)
        return results

    def undo(self, snapshot_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Reverts state cleanly without throwing errors using snapshot ID.
        If no snapshot_id provided, attempts to use the most recent one.
        If snapshot expired (>5s) or invalid, silently ignores and returns safe default.
        """
        self._cleanup_snapshots()

        target_id = snapshot_id or self._current_state_id

        if not target_id or target_id not in self._snapshots:
            # Silent failure / zero confirmation fallback (never throw exceptions to user)
            return {
                "status": "failed",
                "message": "Snapshot expired or invalid",
                "current_state": self.last_result
            }

        snapshot = self._snapshots[target_id]

        # Perform the "undo" by reverting to the previous state
        self.last_result = snapshot["state"]

        # Safely remove the used snapshot
        del self._snapshots[target_id]
        if self._current_state_id == target_id:
            self._current_state_id = None

        return {
            "status": "success",
            "message": "Reverted successfully",
            "current_state": self.last_result
        }

    def _cleanup_snapshots(self):
        """Silently cleans up snapshots older than 5 seconds."""
        current_time = time.time()
        expired = [sid for sid, snap in self._snapshots.items() if current_time - snap["timestamp"] > 5.0]
        for sid in expired:
            del self._snapshots[sid]
