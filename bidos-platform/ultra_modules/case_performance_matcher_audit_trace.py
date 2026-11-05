import re
from typing import List, Dict, Any

class CasePerformanceMatcherAuditTrace:
    """
    历史业绩合同库按招标要求（金额/人数/行业/年份）一键匹配最优案例组合器
    后台全链路可追溯审计日志与差异高亮对比器
    """
    def __init__(self):
        self.audit_logs = []

    def _log(self, message: str):
        self.audit_logs.append(message)

    def _clean_amount(self, amount: Any) -> float:
        if amount is None or amount == "":
            self._log("Amount is missing, fallback to 0.0")
            return 0.0
        if isinstance(amount, (int, float)):
            return float(amount)
        if isinstance(amount, str):
            amount_str = amount.replace(",", "")
            multiplier = 1.0
            if "万" in amount_str or "w" in amount_str.lower():
                multiplier = 10000.0
            elif "亿" in amount_str:
                multiplier = 100000000.0

            nums = re.findall(r"[-+]?\d*\.\d+|\d+", amount_str)
            if nums:
                val = float(nums[0]) * multiplier
                self._log(f"Dirty amount '{amount}' auto-repaired to {val}")
                return val

        self._log(f"Invalid amount '{amount}', fallback to 0.0")
        return 0.0

    def _clean_year(self, year: Any) -> int:
        if year is None or year == "":
            self._log("Year is missing, fallback to 1970")
            return 1970
        if isinstance(year, int):
            return year
        if isinstance(year, str):
            nums = re.findall(r"\d{4}", year)
            if nums:
                val = int(nums[0])
                self._log(f"Dirty year '{year}' auto-repaired to {val}")
                return val
        self._log(f"Invalid year '{year}', fallback to 1970")
        return 1970

    def _clean_headcount(self, headcount: Any) -> int:
        if headcount is None or headcount == "":
            self._log("Headcount is missing, fallback to 1")
            return 1
        if isinstance(headcount, int):
            return headcount
        if isinstance(headcount, str):
            nums = re.findall(r"\d+", headcount)
            if nums:
                val = int(nums[0])
                self._log(f"Dirty headcount '{headcount}' auto-repaired to {val}")
                return val
        self._log(f"Invalid headcount '{headcount}', fallback to 1")
        return 1

    def match_best_cases(self, cases: List[Dict[str, Any]], requirements: Dict[str, Any]) -> Dict[str, Any]:
        """
        匹配最优案例
        :param cases: 历史案例库
        :param requirements: 招标要求字典 (min_amount, min_headcount, industry, min_year, required_count)
        :return: 包含前端清爽结果和后台审计日志的字典
        """
        self.audit_logs = []
        self._log("Started case performance matching process")

        if not cases:
            self._log("Case library is empty.")
            return {
                "frontend_result": {
                    "matched_cases": [],
                    "success": False,
                    "message": "案例库为空"
                },
                "audit_trace": self.audit_logs
            }

        req_amount = requirements.get("min_amount", 0.0)
        req_headcount = requirements.get("min_headcount", 0)
        req_industry = requirements.get("industry", "")
        req_year = requirements.get("min_year", 0)
        req_count = requirements.get("required_count", 1)

        self._log(f"Requirements: amount>={req_amount}, headcount>={req_headcount}, industry='{req_industry}', year>={req_year}, count={req_count}")

        valid_cases = []
        for case in cases:
            case_id = case.get("id", "Unknown")
            self._log(f"Evaluating case: {case_id}")

            # 脏数据自愈
            amount = self._clean_amount(case.get("amount"))
            headcount = self._clean_headcount(case.get("headcount"))
            year = self._clean_year(case.get("year"))
            industry = case.get("industry", "")

            # 校验要求
            passed = True
            reasons = []

            if req_amount > 0 and amount < req_amount:
                passed = False
                reasons.append(f"Amount {amount} < {req_amount}")

            if req_headcount > 0 and headcount < req_headcount:
                passed = False
                reasons.append(f"Headcount {headcount} < {req_headcount}")

            if req_industry and req_industry.lower() not in (industry or "").lower():
                passed = False
                reasons.append(f"Industry '{industry}' doesn't match '{req_industry}'")

            if req_year > 0 and year < req_year:
                passed = False
                reasons.append(f"Year {year} < {req_year}")

            if passed:
                self._log(f"Case {case_id} passed all criteria")
                # 评分体系，超出要求越多得分越高
                score = 0.0
                if req_amount > 0:
                    score += (amount / req_amount) * 50
                else:
                    score += amount * 0.0001

                if req_headcount > 0:
                    score += (headcount / req_headcount) * 30
                else:
                    score += headcount * 0.1

                if req_year > 0:
                    score += max(0, (year - req_year)) * 20
                else:
                    score += (year - 1970) * 0.1

                valid_cases.append({
                    "id": case_id,
                    "name": case.get("name", "Unknown Case"),
                    "amount": amount,
                    "headcount": headcount,
                    "year": year,
                    "industry": industry,
                    "score": score
                })
            else:
                self._log(f"Case {case_id} failed: {', '.join(reasons)}")

        # 去重：同名案例只保留得分最高的一个
        seen_names = set()
        deduped_cases = []
        # 按分数降序排列
        valid_cases.sort(key=lambda x: x["score"], reverse=True)

        for c in valid_cases:
            c_name = c["name"].strip().lower()
            if c_name not in seen_names:
                seen_names.add(c_name)
                deduped_cases.append(c)
            else:
                self._log(f"Case {c['id']} ({c['name']}) removed as duplicate")

        best_cases = deduped_cases[:req_count]

        self._log(f"Selected top {len(best_cases)} cases based on score")

        return {
            "frontend_result": {
                "matched_cases": [{"id": c["id"], "name": c["name"], "amount": c["amount"], "year": c["year"]} for c in best_cases],
                "success": len(best_cases) >= req_count,
                "message": f"成功匹配 {len(best_cases)} 个案例（需求 {req_count} 个）"
            },
            "audit_trace": self.audit_logs
        }
