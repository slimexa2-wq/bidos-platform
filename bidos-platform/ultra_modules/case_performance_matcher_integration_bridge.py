import re
from typing import List, Dict, Any

class CasePerformanceIntegrationBridge:
    """
    Case Performance Matcher Integration Bridge.
    Silently maps and converts fields between old and new versions,
    repairs dirty data, and matches the optimal case combination for bidding.
    """

    @staticmethod
    def _clean_amount(val: Any) -> float:
        if val is None:
            return 0.0
        if isinstance(val, (int, float)):
            return float(val)

        # String processing
        val_str = str(val).strip().replace(',', '')
        if not val_str:
            return 0.0

        # Try to find numeric part
        match = re.search(r'[-+]?\d*\.\d+|[-+]?\d+', val_str)
        if match:
            num = float(match.group())
            # Simple multiplier support
            lower_str = val_str.lower()
            if '万' in lower_str or 'w' in lower_str:
                num *= 10000.0
            elif '亿' in lower_str:
                num *= 100000000.0
            elif 'k' in lower_str:
                num *= 1000.0
            elif 'm' in lower_str:
                num *= 1000000.0
            return num
        return 0.0

    @staticmethod
    def _clean_int(val: Any) -> int:
        if val is None:
            return 0
        if isinstance(val, int):
            return val
        if isinstance(val, float):
            return int(val)

        val_str = str(val).strip().replace(',', '')
        match = re.search(r'[-+]?\d+', val_str)
        if match:
            return int(match.group())
        return 0

    @staticmethod
    def _clean_year(val: Any) -> int:
        if val is None:
            return 0
        if isinstance(val, int):
            if val < 1000 and val > 0:
                return 2000 + val if val < 100 else val
            return val

        val_str = str(val).strip()
        # Look for YYYY
        match = re.search(r'(19|20)\d{2}', val_str)
        if match:
            return int(match.group())

        # Look for YY
        match_yy = re.search(r'\b\d{2}\b', val_str)
        if match_yy:
            yy = int(match_yy.group())
            return 2000 + yy if yy < 100 else yy
        return 0

    @staticmethod
    def normalize_case(case_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Auto-identify new/old version field differences and silently map/convert.
        Returns a normalized dict with standard keys.
        """
        if not isinstance(case_data, dict):
            return {"original": case_data, "amount": 0.0, "headcount": 0, "industry": "", "year": 0, "id": ""}

        normalized = {
            "original": case_data,
            "id": case_data.get("id", case_data.get("_id", case_data.get("case_id", ""))),
            "amount": 0.0,
            "headcount": 0,
            "industry": "",
            "year": 0
        }

        # Aliases for amount
        amount_aliases = ['amount', 'amt', 'contract_amount', 'price', 'value', '金额', '合同额', '造价']
        for alias in amount_aliases:
            if alias in case_data:
                normalized["amount"] = CasePerformanceIntegrationBridge._clean_amount(case_data[alias])
                break

        # Aliases for headcount
        hc_aliases = ['headcount', 'hc', 'people', 'staff', 'team_size', '人数', '团队规模', '人员']
        for alias in hc_aliases:
            if alias in case_data:
                normalized["headcount"] = CasePerformanceIntegrationBridge._clean_int(case_data[alias])
                break

        # Aliases for industry
        ind_aliases = ['industry', 'sector', 'domain', 'field', '行业', '领域']
        for alias in ind_aliases:
            if alias in case_data:
                val = case_data[alias]
                normalized["industry"] = str(val).strip() if val is not None else ""
                break

        # Aliases for year
        year_aliases = ['year', 'date', 'contract_date', 'sign_date', '年份', '日期', '签订时间']
        for alias in year_aliases:
            if alias in case_data:
                normalized["year"] = CasePerformanceIntegrationBridge._clean_year(case_data[alias])
                break

        return normalized

    @classmethod
    def match_optimal_combination(cls, cases: List[Dict[str, Any]], requirements: Dict[str, Any]) -> Dict[str, Any]:
        """
        Matches the optimal combination of cases that meet the bidding requirements.
        """
        if not isinstance(cases, list):
            cases = []
        if not isinstance(requirements, dict):
            requirements = {}

        min_total_amount = cls._clean_amount(requirements.get('min_total_amount', 0))
        min_total_headcount = cls._clean_int(requirements.get('min_total_headcount', 0))
        target_industry = str(requirements.get('target_industry', '')).strip().lower()
        min_year = cls._clean_year(requirements.get('min_year', 0))
        max_cases = cls._clean_int(requirements.get('max_cases', 0))
        min_cases = cls._clean_int(requirements.get('min_cases', 0))

        valid_cases = []
        for c in cases:
            norm_c = cls.normalize_case(c)
            # Apply hard filters (industry and year)
            if target_industry and target_industry not in norm_c['industry'].lower():
                continue
            if min_year > 0 and norm_c['year'] > 0 and norm_c['year'] < min_year:
                continue
            valid_cases.append(norm_c)

        # Sort valid cases by amount descending, then headcount descending
        valid_cases.sort(key=lambda x: (x['amount'], x['headcount']), reverse=True)

        selected = []
        current_amount = 0.0
        current_headcount = 0

        for case in valid_cases:
            if max_cases > 0 and len(selected) >= max_cases:
                break

            needs_amount = (current_amount < min_total_amount)
            needs_headcount = (current_headcount < min_total_headcount)
            needs_count = (len(selected) < min_cases)

            # If we don't have ANY specific needs (min_* all zero), but we want to select something?
            # Let's say we select everything up to max_cases.
            no_specific_needs = (min_total_amount == 0 and min_total_headcount == 0 and min_cases == 0)

            if needs_amount or needs_headcount or needs_count or no_specific_needs:
                selected.append(case)
                current_amount += case['amount']
                current_headcount += case['headcount']
            else:
                break

        is_satisfied = (
            current_amount >= min_total_amount and
            current_headcount >= min_total_headcount and
            len(selected) >= min_cases
        )

        return {
            "is_satisfied": is_satisfied,
            "total_amount": current_amount,
            "total_headcount": current_headcount,
            "selected_count": len(selected),
            "selected_cases": [c['original'] for c in selected]
        }
