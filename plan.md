1. **Create the Labor Bid Price Calculator Audit Trace module:**
   - Path: `ultra_modules/labor_bid_price_calculator_audit_trace.py`
   - Implement the `LaborBidPriceCalculatorAuditTrace` class.
   - Core functions must include backward calculation of gross profit (毛利倒推), considering compliance minimums for social security and housing provident fund (社保公积金合规底线), disability employment security fund (残保金), and taxes (税费).
   - Backend functions must support silent logging/audit tracing (后台全链路可追溯审计日志) of all calculation steps, deduplication, and data repair.
   - Enforce zero-configuration fault tolerance, automatically assign reasonable defaults for missing parameters, and silently clean dirty data.

2. **Create unit tests for the module:**
   - Path: `tests/ultra_modules/test_labor_bid_price_calculator_audit_trace.py`
   - Use the standard `unittest` library.
   - Implement at least 5 test cases covering:
     - Normal behavior (正常).
     - Dirty data self-healing (脏数据自愈).
     - Missing parameter fallbacks (缺参默认兜底).
     - Extreme boundary conditions (极端边界).
     - Audit trace verification.
   - Tests must aim for 100% branch coverage.

3. **Complete pre-commit steps:**
   - Execute tests locally using `PYTHONPATH=. python3 -m unittest tests/ultra_modules/test_labor_bid_price_calculator_audit_trace.py`.
   - Complete pre-commit steps to ensure proper testing, verification, review, and reflection are done.

4. **Submit and Create Pull Request:**
   - Push the branch and create a PR directly without waiting for human confirmation.
