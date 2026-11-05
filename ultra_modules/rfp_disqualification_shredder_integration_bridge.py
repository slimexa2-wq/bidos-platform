import logging
from typing import Dict, Any, List, Optional
from ultra_modules.rfp_disqualification_shredder_core_engine import shred_rfp_disqualification_clauses

logger = logging.getLogger(__name__)

class SchemaForwardCompatBridge:
    """
    Schema Forward Compatibility and Extraction Bridge for RFP Disqualification Shredder.
    Supports seamless data schema mapping, fallback configurations, and silent error handling.
    """

    def __init__(self, target_schema_version: str = "v2", config: Optional[Dict[str, Any]] = None):
        self.target_schema_version = str(target_schema_version).strip().lower() if target_schema_version else "v2"
        self.config = config if isinstance(config, dict) else {}

    def _extract_content_safely(self, payload: Any) -> str:
        """
        Extracts content from a variety of payload structures silently handling dirty data.
        """
        if not payload:
            return ""

        if isinstance(payload, str):
            return payload

        if isinstance(payload, dict):
            # Attempt to extract from common fields intelligently
            for key in ["content", "text", "raw_text", "body", "data"]:
                val = payload.get(key)
                if val and isinstance(val, str):
                    return val

            # If standard keys fail, try to flatten all string values
            try:
                all_strings = [str(v) for v in payload.values() if v]
                return "\n".join(all_strings)
            except Exception:
                pass

        if isinstance(payload, list):
            try:
                return "\n".join([str(item) for item in payload if item])
            except Exception:
                pass

        try:
            return str(payload)
        except Exception:
            return ""

    def process(self, payload: Any) -> Dict[str, Any]:
        """
        Processes the payload to extract disqualifications and deductions,
        returning them formatted to the requested target schema.
        """
        content = self._extract_content_safely(payload)

        # Invoke the core engine algorithm safely
        try:
            shredded = shred_rfp_disqualification_clauses(content, **self.config)
        except Exception as e:
            logger.warning(f"Core engine error handled silently: {e}")
            shredded = {"disqualifications": [], "deductions": []}

        return self._format_output(shredded)

    def _format_output(self, shredded: Dict[str, List[str]]) -> Dict[str, Any]:
        """
        Converts the standardized shredded output to the specific schema version.
        """
        # Ensure we always have default keys even if missing
        disqualifications = shredded.get("disqualifications", [])
        deductions = shredded.get("deductions", [])

        if self.target_schema_version in ["v1", "legacy"]:
            # Example v1 schema structure
            return {
                "fatal_clauses": disqualifications,
                "score_penalties": deductions,
                "has_disqualifications": len(disqualifications) > 0
            }
        else:
            # Default v2/current schema structure
            return {
                "schema_version": "v2",
                "extracted_rules": {
                    "disqualifications": disqualifications,
                    "deductions": deductions,
                },
                "summary": {
                    "disqualification_count": len(disqualifications),
                    "deduction_count": len(deductions)
                }
            }


def process_rfp_integration_payload(payload: Any, schema_version: str = "v2", **kwargs) -> Dict[str, Any]:
    """
    Extremely simple, one-click API bridge invocation.
    Provides complete zero-configuration fault tolerance and automatic data washing.
    """
    bridge = SchemaForwardCompatBridge(target_schema_version=schema_version, config=kwargs)
    return bridge.process(payload)
