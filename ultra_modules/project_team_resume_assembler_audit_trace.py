import logging
from typing import List, Dict, Any, Optional

class ResumeAssemblerAuditTrace:
    """
    Project Team Resume Assembler & Audit Trace.
    Assembles credentials for project managers and resident staff,
    cleanses data, checks compliance, and tracks all self-healing actions.
    """
    def __init__(self):
        self.audit_log: List[Dict[str, Any]] = []

    def _log(self, action: str, details: str):
        self.audit_log.append({"action": action, "details": details})

    def _normalize_role(self, role: Any) -> str:
        if not isinstance(role, str):
            self._log("normalize_role", f"Invalid role type {type(role).__name__}, defaulted to '未知'")
            return "未知"

        role_lower = role.strip().lower()
        if role_lower in ["pm", "project manager", "项目经理", "项目总监"]:
            return "项目经理"
        if role_lower in ["resident staff", "驻厂专员", "专员", "驻场专员", "驻场代表"]:
            return "驻厂专员"

        if not role_lower:
             self._log("normalize_role", "Empty role, defaulted to '未知'")
             return "未知"

        self._log("normalize_role", f"Unrecognized role '{role}', keeping original")
        return role.strip()

    def _deduplicate_qualifications(self, qualifications: Any) -> List[str]:
        if not isinstance(qualifications, list):
            self._log("deduplicate_qualifications", f"Invalid qualifications type {type(qualifications).__name__}, defaulted to []")
            return []

        seen = set()
        result = []
        for q in qualifications:
            if not isinstance(q, str):
                self._log("deduplicate_qualifications", f"Invalid qualification item '{q}' of type {type(q).__name__}, ignored")
                continue

            clean_q = q.strip()
            if not clean_q:
                continue

            if clean_q not in seen:
                seen.add(clean_q)
                result.append(clean_q)
            else:
                self._log("deduplicate_qualifications", f"Removed duplicate qualification: '{clean_q}'")

        return result

    def _check_social_security(self, status: Any) -> bool:
        if not isinstance(status, (bool, str, int, float)):
            self._log("check_social_security", f"Invalid status type {type(status).__name__}, defaulted to False")
            return False

        if isinstance(status, bool):
            return status

        if isinstance(status, str):
            clean_status = status.strip().lower()
            if clean_status in ["yes", "true", "1", "已缴纳", "有", "是"]:
                return True
            if clean_status in ["no", "false", "0", "未缴纳", "无", "否", ""]:
                return False

            self._log("check_social_security", f"Unrecognized status string '{status}', defaulted to False")
            return False

        # if isinstance(status, (int, float)):
        return status > 0

    def assemble(self, records: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        self.audit_log = []

        if records is None:
            self._log("assemble", "Records is None, defaulting to empty list")
            records = []

        if not isinstance(records, list):
            self._log("assemble", f"Records is not a list (type: {type(records).__name__}), defaulting to empty list")
            records = []

        assembled_data = {
            "项目经理": [],
            "驻厂专员": []
        }

        for i, record in enumerate(records):
            if not isinstance(record, dict):
                self._log("assemble", f"Record at index {i} is not a dict, skipped")
                continue

            name = record.get("name", "Unknown")
            if not isinstance(name, str) or not name.strip():
                name = "Unknown"
                self._log("assemble", f"Record at index {i} has invalid name, defaulted to 'Unknown'")
            else:
                name = name.strip()

            raw_role = record.get("role", "")
            role = self._normalize_role(raw_role)

            if role not in assembled_data:
                self._log("assemble", f"Record for {name} has unmanaged role '{role}', ignored for target assembly")
                continue

            qualifications = self._deduplicate_qualifications(record.get("qualifications", []))
            social_security = self._check_social_security(record.get("social_security", False))

            # Compliance Check
            issues = []
            if not social_security:
                issues.append("缺少社保缴纳证明")
            if role == "项目经理" and not qualifications:
                issues.append("缺少资格证书")

            is_compliant = len(issues) == 0
            if not is_compliant:
                 self._log("check_compliance", f"{name} ({role}) failed compliance: {', '.join(issues)}")

            assembled_record = {
                "name": name,
                "role": role,
                "qualifications": qualifications,
                "social_security_verified": social_security,
                "is_compliant": is_compliant,
                "compliance_issues": issues
            }

            assembled_data[role].append(assembled_record)
            self._log("assemble", f"Successfully assembled record for {name} ({role})")

        return {
            "results": assembled_data,
            "audit_log": self.audit_log
        }

    def compare_differences(self, current_record: Dict[str, Any], new_record: Dict[str, Any]) -> Dict[str, Any]:
        """
        Compare two personnel records and highlight differences.
        """
        self.audit_log = []
        if not isinstance(current_record, dict):
             self._log("compare_differences", "current_record is not a dict, defaulting to empty")
             current_record = {}
        if not isinstance(new_record, dict):
             self._log("compare_differences", "new_record is not a dict, defaulting to empty")
             new_record = {}

        keys = set(current_record.keys()).union(set(new_record.keys()))
        diff_result = {}
        has_diff = False

        for key in keys:
            val1 = current_record.get(key, None)
            val2 = new_record.get(key, None)

            # Normalize list comparison if both are lists
            if isinstance(val1, list) and isinstance(val2, list):
                if sorted(str(x) for x in val1) != sorted(str(x) for x in val2):
                     diff_result[key] = {"current": val1, "new": val2, "highlight": True}
                     has_diff = True
                     self._log("compare_differences", f"Difference found in list field '{key}'")
                else:
                     diff_result[key] = {"current": val1, "new": val2, "highlight": False}
            elif val1 != val2:
                diff_result[key] = {
                    "current": val1,
                    "new": val2,
                    "highlight": True
                }
                has_diff = True
                self._log("compare_differences", f"Difference found in field '{key}': '{val1}' vs '{val2}'")
            else:
                diff_result[key] = {
                    "current": val1,
                    "new": val2,
                    "highlight": False
                }

        return {
            "has_differences": has_diff,
            "diff_details": diff_result,
            "audit_log": self.audit_log
        }

# Module level helper functions (Extremely simple API)
def assemble_team_credentials(records: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    assembler = ResumeAssemblerAuditTrace()
    return assembler.assemble(records)

def compare_personnel_records(current_record: Any, new_record: Any) -> Dict[str, Any]:
    assembler = ResumeAssemblerAuditTrace()
    # Check type at wrapper level to ensure zero crash
    if not isinstance(current_record, dict): current_record = {}
    if not isinstance(new_record, dict): new_record = {}
    return assembler.compare_differences(current_record, new_record)
