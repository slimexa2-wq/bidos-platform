# Execution Plan

1.  **Add `num2cn` function**: Add a helper function to convert numerical amounts to Chinese uppercase amounts (e.g., `12345.67` -> `壹万贰仟叁佰肆拾伍元陆角柒分`) in `apps/api/app/services/bid_document_check.py`.
2.  **Add Rules to `RULE_NAMES`**: In `bid_document_check.py`, add the following rules:
    *   `project_name_consistency`: Check if the project name in the document matches `meta.project_name`.
    *   `project_number_consistency`: Check if the project number in the document matches `meta.project_number`.
    *   `amount_uppercase_consistency`: Check if the uppercase amount matches the calculated uppercase of `meta.amount`.
    *   `sub_amount_consistency`: Check if the sum of all matched sub-amounts equals the total `meta.amount`.
    *   `period_consistency`: Check if the construction period matches `meta.period`.
    *   `manager_consistency`: Check if the project manager name and ID match `meta.manager_name` and `meta.manager_id`.
3.  **Define Regular Expressions**: Create compiled regexes in `bid_document_check.py` for matching project name, project number, uppercase amounts, sub-amounts, period, and manager info.
4.  **Implement Check Methods**: Add the corresponding `_check_...` methods in `DocumentChecker` class.
    *   `_check_project_name`: Finds `项目名称[：:]\s*(.+)` and compares.
    *   `_check_project_number`: Finds `项目编号[：:]\s*(.+)` and compares.
    *   `_check_amount_uppercase`: Checks `_AMOUNT_UPPERCASE` regex against `num2cn(meta.amount)`.
    *   `_check_sub_amounts`: Looks for table-like patterns or "分项报价" and extracts numbers. Actually, checking if all sub-amounts sum up properly might require extracting amounts right before "合计" and comparing.
    *   `_check_period`: Finds `(?:工期|服务期限?)[：:]\s*(.+)` and compares.
    *   `_check_manager`: Finds `(?:项目经理|项目负责人)[：:]\s*(.+)` and `(?:身份证号?码?)[：:]\s*([0-9X]{18})`.
5.  **Update `check` method**: Add the new methods to the `check` function if the corresponding rule is enabled.
6.  **Create tests**: Create `tests/backend/test_bid_document_check_consistency.py` to ensure all 6 new consistency rules are working and tested against positive and negative cases.
7.  **Pre-commit checks**: Run `pre_commit_instructions` before submitting.
8.  **Submit**: Commit the changes.
