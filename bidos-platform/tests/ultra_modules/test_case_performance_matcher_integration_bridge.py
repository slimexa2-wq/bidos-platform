import unittest
from ultra_modules.case_performance_matcher_integration_bridge import CasePerformanceIntegrationBridge

class TestCasePerformanceIntegrationBridge(unittest.TestCase):

    def setUp(self):
        self.bridge = CasePerformanceIntegrationBridge()

    def test_normal_data_matching(self):
        cases = [
            {"id": "1", "amount": 500000, "headcount": 10, "industry": "IT", "year": 2022},
            {"id": "2", "amount": 200000, "headcount": 5, "industry": "Finance", "year": 2021},
            {"id": "3", "amount": 800000, "headcount": 15, "industry": "IT", "year": 2023},
        ]
        reqs = {
            "min_total_amount": 1000000,
            "min_total_headcount": 20,
            "target_industry": "IT",
            "min_year": 2022
        }
        res = self.bridge.match_optimal_combination(cases, reqs)
        self.assertTrue(res["is_satisfied"])
        self.assertEqual(res["total_amount"], 1300000)
        self.assertEqual(res["total_headcount"], 25)
        self.assertEqual(res["selected_count"], 2)
        self.assertEqual([c["id"] for c in res["selected_cases"]], ["3", "1"])

    def test_dirty_data_self_healing(self):
        cases = [
            {"id": "A", "amount": " 1,000.5 w ", "headcount": " 20 人 ", "industry": "Tech", "year": "23"},
            {"id": "B", "amount": "5亿", "headcount": 100.5, "industry": " Tech ", "year": " 2021年 "},
        ]
        res = self.bridge.match_optimal_combination(cases, {})
        self.assertTrue(res["is_satisfied"])
        self.assertEqual(res["selected_count"], 2)
        # B is larger in amount (500 million) so it comes first
        self.assertEqual(res["selected_cases"][0]["id"], "B")
        self.assertEqual(res["total_amount"], 500000000 + 10005000)
        self.assertEqual(res["total_headcount"], 100 + 20)

        # Checking individual normalized outputs
        norm_a = self.bridge.normalize_case(cases[0])
        self.assertEqual(norm_a["amount"], 10005000)
        self.assertEqual(norm_a["headcount"], 20)
        self.assertEqual(norm_a["industry"], "Tech")
        self.assertEqual(norm_a["year"], 2023)

        norm_b = self.bridge.normalize_case(cases[1])
        self.assertEqual(norm_b["amount"], 500000000)
        self.assertEqual(norm_b["headcount"], 100)
        self.assertEqual(norm_b["year"], 2021)

    def test_missing_parameter_fallbacks(self):
        # Match with None or missing entirely
        res1 = self.bridge.match_optimal_combination(None, None) # type: ignore
        self.assertTrue(res1["is_satisfied"])
        self.assertEqual(res1["total_amount"], 0)

        cases = [{"id": "1"}]
        res2 = self.bridge.match_optimal_combination(cases, {})
        self.assertTrue(res2["is_satisfied"])
        self.assertEqual(res2["selected_count"], 1)

        norm = self.bridge.normalize_case(None) # type: ignore
        self.assertEqual(norm["amount"], 0)

        # Test missing aliases
        norm2 = self.bridge.normalize_case({"name": "test"})
        self.assertEqual(norm2["amount"], 0)
        self.assertEqual(norm2["headcount"], 0)
        self.assertEqual(norm2["industry"], "")
        self.assertEqual(norm2["year"], 0)

    def test_extreme_boundaries(self):
        cases = [
            {"id": "empty_string", "amount": "", "headcount": "", "year": ""},
            {"id": "none_values", "amount": None, "headcount": None, "industry": None, "year": None},
            {"id": "unparseable", "amount": "abc", "headcount": "xyz", "year": "no year"},
            {"id": "negatives", "amount": "-500", "headcount": "-10", "year": -5},
        ]

        # Verify normalization of extremes
        norm0 = self.bridge.normalize_case(cases[0])
        self.assertEqual(norm0["amount"], 0.0)
        self.assertEqual(norm0["headcount"], 0)

        norm1 = self.bridge.normalize_case(cases[1])
        self.assertEqual(norm1["amount"], 0.0)

        norm2 = self.bridge.normalize_case(cases[2])
        self.assertEqual(norm2["amount"], 0.0)
        self.assertEqual(norm2["year"], 0)

        norm3 = self.bridge.normalize_case(cases[3])
        self.assertEqual(norm3["amount"], -500.0)
        self.assertEqual(norm3["headcount"], -10)
        self.assertEqual(norm3["year"], -5)

        res = self.bridge.match_optimal_combination(cases, {"max_cases": 2})
        self.assertTrue(res["is_satisfied"])
        self.assertEqual(res["selected_count"], 2)

        # Test additional integer year logic
        norm_int_yy = self.bridge.normalize_case({"year": 99, "id": "1"})
        self.assertEqual(norm_int_yy["year"], 2099)
        norm_int_yy2 = self.bridge.normalize_case({"year": 199, "id": "2"})
        self.assertEqual(norm_int_yy2["year"], 199)

        # Test match_optimal_combination breaks and conditions
        cases_to_break = [
            {"id": "1", "amount": 100, "headcount": 1, "year": 2020},
            {"id": "2", "amount": 100, "headcount": 1, "year": 2021},
            {"id": "3", "amount": 100, "headcount": 1, "year": 2022},
        ]
        res_break = self.bridge.match_optimal_combination(cases_to_break, {"min_year": 2021, "max_cases": 1, "min_total_amount": 50})
        self.assertEqual(res_break["selected_count"], 1)

        res_break_2 = self.bridge.match_optimal_combination(cases_to_break, {"min_year": 2021, "min_total_amount": 100})
        # Should stop after selecting first one because it fulfills the condition
        self.assertEqual(res_break_2["selected_count"], 1)

    def test_different_schema_versions_mapping(self):
        cases = [
            {"_id": "V1", "contract_amount": "50k", "team_size": "5", "sector": "Medical", "contract_date": "1999"},
            {"case_id": "V2", "price": "2.5m", "people": "12", "domain": "Finance", "sign_date": "2024"},
            {"id": "V3", "金额": "100", "人数": "1", "领域": "Edu", "年份": "2025"}
        ]

        norm1 = self.bridge.normalize_case(cases[0])
        self.assertEqual(norm1["id"], "V1")
        self.assertEqual(norm1["amount"], 50000.0)
        self.assertEqual(norm1["headcount"], 5)
        self.assertEqual(norm1["industry"], "Medical")
        self.assertEqual(norm1["year"], 1999)

        norm2 = self.bridge.normalize_case(cases[1])
        self.assertEqual(norm2["id"], "V2")
        self.assertEqual(norm2["amount"], 2500000.0)
        self.assertEqual(norm2["headcount"], 12)
        self.assertEqual(norm2["industry"], "Finance")
        self.assertEqual(norm2["year"], 2024)

        norm3 = self.bridge.normalize_case(cases[2])
        self.assertEqual(norm3["id"], "V3")
        self.assertEqual(norm3["amount"], 100.0)
        self.assertEqual(norm3["headcount"], 1)
        self.assertEqual(norm3["industry"], "Edu")
        self.assertEqual(norm3["year"], 2025)

if __name__ == "__main__":
    unittest.main()
