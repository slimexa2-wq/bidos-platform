import re
from typing import Dict, List, Any, Optional

class ShredderCoreEngine:
    """
    RFP Disqualification and Point Deduction Shredder Core Engine.
    Implements a robust dual-channel extraction logic (regex + semantic keywords)
    with dirty data self-healing and zero-configuration default behaviors.
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """
        Initializes the engine with auto-fallback configuration.
        """
        if not isinstance(config, dict):
            config = {}

        self.disqualification_symbols = config.get("disqualification_symbols", ["★", "*", "废标", "否决"])
        self.deduction_symbols = config.get("deduction_symbols", ["▲", "扣分", "减分"])

        # Build regex patterns safely
        self.disqualification_pattern = self._build_pattern(self.disqualification_symbols)
        self.deduction_pattern = self._build_pattern(self.deduction_symbols)

    def _build_pattern(self, keywords: List[str]) -> re.Pattern:
        """
        Builds a safe regex pattern from a list of keywords.
        """
        safe_keywords = []
        for kw in keywords:
            if isinstance(kw, str) and kw.strip():
                safe_keywords.append(re.escape(kw.strip()))

        if not safe_keywords:
            return re.compile(r'(?!)') # matches nothing

        pattern_str = f"({'|'.join(safe_keywords)})"
        return re.compile(pattern_str)

    def shred(self, content: Any) -> Dict[str, List[str]]:
        """
        Extracts disqualification and deduction clauses from the provided content.
        Features zero-friction fallback and dirty data washing.
        """
        # Silent dirty data washing
        if content is None:
            content = ""
        elif not isinstance(content, str):
            try:
                content = str(content)
            except Exception:
                content = ""

        # Zero configuration auto-fallback: return empty structures instead of error
        result = {
            "disqualifications": [],
            "deductions": []
        }

        if not content.strip():
            return result

        # Split by newlines or typical list delimiters
        lines = re.split(r'\n|\r|;|；', content)

        for line in lines:
            line = line.strip()
            if not line:
                continue

            # Dual-channel extraction logic
            is_disqualification = bool(self.disqualification_pattern.search(line))
            is_deduction = bool(self.deduction_pattern.search(line))

            if is_disqualification:
                result["disqualifications"].append(line)
            if is_deduction and not is_disqualification:
                # If a line has both, we prioritize disqualification or we can record in both.
                # Let's say we prioritize disqualification in this implementation,
                # but if there's a strict requirement for both, we can append to both.
                # Actually, standard behavior usually treats them as mutually exclusive in final bucket,
                # but let's record it in deduction if it matches deduction and isn't purely disqualification.
                result["deductions"].append(line)

        return result

def shred_rfp_disqualification_clauses(content: Any, **kwargs) -> Dict[str, List[str]]:
    """
    Pure functional algorithm wrapper for extreme simplicity.
    One-click access with auto-fallback.
    """
    engine = ShredderCoreEngine(config=kwargs)
    return engine.shred(content)
