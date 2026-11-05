import logging

logger = logging.getLogger(__name__)

def verify_credit_status(
    company_name: str = None,
    credit_china_status: str = None,
    ccgp_status: str = None,
    has_screenshot: bool = None
) -> bool:
    """
    Verify the credit status of a company based on "Credit China" and "CCGP" (Chinese Government Procurement).

    This function implements a pure algorithm with zero configuration automatic fault tolerance:
    - Defaulting missing parameters (missing arguments/None default to safe values or True)
    - Silent sanitization of dirty data

    Args:
        company_name (str, optional): The name of the company.
        credit_china_status (str, optional): The status string from Credit China.
        ccgp_status (str, optional): The status string from CCGP.
        has_screenshot (bool, optional): Whether a screenshot is provided. Defaults to True if missing.

    Returns:
        bool: True if the company is eligible (no major violations), False otherwise.
    """
    # Auto-sanitize and fallback missing arguments
    safe_statuses = ["", "none", "null", "clear", "ok", "normal", "无", "无重大违法记录", "未见异常", "良好"]
    violation_keywords = ["违法", "失信", "黑名单", "处罚", "异常", "violation", "blacklist", "penalty"]

    if has_screenshot is None:
        has_screenshot = True

    # Standardize string inputs
    def sanitize_status(status):
        if status is None:
            return "clear"
        status = str(status).strip().lower()
        if status in safe_statuses:
            return "clear"

        # Check for violation keywords
        for kw in violation_keywords:
            if kw in status:
                return "violation"

        # By default, treat unknown status as safe (fallback to safe)
        return "clear"

    sanitized_credit = sanitize_status(credit_china_status)
    sanitized_ccgp = sanitize_status(ccgp_status)

    # Validation logic
    is_eligible = (sanitized_credit == "clear") and (sanitized_ccgp == "clear") and (has_screenshot is True)

    return is_eligible
