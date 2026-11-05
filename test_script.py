from ultra_modules.rfp_disqualification_shredder_audit_trace import run_audit_trace

content = (
    "1. Normal clause.\n"
    "2. ★ This is a disqualification clause.\n"
    "3. ▲ This is a deduction clause.\n"
    "4. 废标: Another disqualification.\n"
    "5. ★ This is a disqualification clause. \n" # Duplicate, should be deduplicated
)
res = run_audit_trace(content, trace_id="trace-123")
print(res["clean_result"]["disqualifications"])
