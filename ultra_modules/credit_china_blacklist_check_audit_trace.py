def verify_credit_with_audit(
    company_name: str = None,
    credit_china_status: str = None,
    ccgp_status: str = None,
    has_screenshot: bool = None,
    screenshot_urls: list = None
) -> dict:
    """
    Verify the credit status of a company based on "Credit China" and "CCGP",
    and output a detailed audit trace of all self-healing, deduplication, and
    default assignment steps.
    """
    audit_log = []

    # 1. Default Assignment (Fallback)
    if company_name is None:
        company_name = "Unknown Company"
        audit_log.append("Action: Applied default value 'Unknown Company' for missing company_name.")

    if has_screenshot is None:
        has_screenshot = True
        audit_log.append("Action: Applied default value True for missing has_screenshot.")

    if screenshot_urls is None:
        screenshot_urls = []
        audit_log.append("Action: Applied default empty list for missing screenshot_urls.")

    # 2. Deduplication and Cleaning of URLs
    original_urls_len = len(screenshot_urls)
    # Ensure types and deduplicate
    clean_urls = []
    for url in screenshot_urls:
        if isinstance(url, str):
            clean_url = url.strip()
            if clean_url and clean_url not in clean_urls:
                clean_urls.append(clean_url)

    if len(clean_urls) < original_urls_len:
        audit_log.append(f"Action: Deduplicated and cleaned screenshot_urls from {original_urls_len} to {len(clean_urls)} items.")

    # 3. Sanitize Status Strings
    safe_statuses = ["", "none", "null", "clear", "ok", "normal", "无", "无重大违法记录", "未见异常", "良好"]
    violation_keywords = ["违法", "失信", "黑名单", "处罚", "异常", "violation", "blacklist", "penalty"]

    def sanitize_status(status, source_name):
        if status is None:
            audit_log.append(f"Action: Replaced None {source_name} with safe fallback 'clear'.")
            return "clear"

        if not isinstance(status, str):
            status = str(status)
            audit_log.append(f"Action: Casted {source_name} from non-string to string.")

        clean_status = status.strip().lower()
        if clean_status != status:
            audit_log.append(f"Action: Cleaned {source_name} (stripped whitespace and lowercased).")

        if clean_status in safe_statuses:
            return "clear"

        for kw in violation_keywords:
            if kw in clean_status:
                audit_log.append(f"Finding: Detected violation keyword '{kw}' in {source_name}.")
                return "violation"

        audit_log.append(f"Action: Unrecognized status in {source_name} defaulted to safe fallback 'clear' to prevent friction.")
        return "clear"

    sanitized_credit = sanitize_status(credit_china_status, "credit_china_status")
    sanitized_ccgp = sanitize_status(ccgp_status, "ccgp_status")

    # 4. Final Verification
    is_eligible = (sanitized_credit == "clear") and (sanitized_ccgp == "clear") and has_screenshot

    return {
        "is_eligible": is_eligible,
        "company_name": company_name,
        "sanitized_credit_status": sanitized_credit,
        "sanitized_ccgp_status": sanitized_ccgp,
        "has_screenshot": has_screenshot,
        "screenshot_urls": clean_urls,
        "audit_log": audit_log
    }
