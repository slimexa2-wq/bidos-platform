import unittest
from ultra_modules.one_page_go_nogo_card_audit_trace import TenderDecisionEngine

class TestTenderDecisionEngine(unittest.TestCase):
    def setUp(self):
        self.engine = TenderDecisionEngine()

    def test_normal_go(self):
        """Test standard data resulting in '可投'."""
        data = {
            "budget": 100000,
            "estimated_cost": 70000,
            "days_to_deadline": 14
        }
        result = self.engine.generate_card(data)
        self.assertEqual(result["decision"], "可投")
        self.assertIn("Favorable conditions", result["summary"])
        self.assertEqual(result["metrics"]["budget"], 100000)
        self.assertEqual(result["metrics"]["cost"], 70000)
        self.assertEqual(result["metrics"]["days"], 14)

        # Verify audit log recorded the generation
        logs = self.engine.get_audit_log()
        self.assertTrue(len(logs) > 0)
        self.assertEqual(logs[-1]["step"], "card_generation")

    def test_caution_rules(self):
        """Test triggering '慎投' conditions (e.g., low margin, short deadline)."""
        # Low margin
        data1 = {
            "budget": 100000,
            "estimated_cost": 90000, # 10% margin < 15%
            "days_to_deadline": 14
        }
        result1 = self.engine.generate_card(data1)
        self.assertEqual(result1["decision"], "慎投")
        self.assertIn("Margin low or tight deadline", result1["summary"])

        # Short deadline
        data2 = {
            "budget": 100000,
            "estimated_cost": 70000, # 30% margin
            "days_to_deadline": 5 # <= 7 days
        }
        result2 = self.engine.generate_card(data2)
        self.assertEqual(result2["decision"], "慎投")

    def test_no_go_rules(self):
        """Test triggering '禁投' conditions (e.g., impossible deadline, extremely low budget)."""
        # Negative margin
        data1 = {
            "budget": 100000,
            "estimated_cost": 110000,
            "days_to_deadline": 14
        }
        result1 = self.engine.generate_card(data1)
        self.assertEqual(result1["decision"], "禁投")

        # Extremely low budget
        data2 = {
            "budget": 5000, # < 10000
            "estimated_cost": 3000,
            "days_to_deadline": 14
        }
        result2 = self.engine.generate_card(data2)
        self.assertEqual(result2["decision"], "禁投")

        # Impossible deadline
        data3 = {
            "budget": 100000,
            "estimated_cost": 70000,
            "days_to_deadline": -1
        }
        result3 = self.engine.generate_card(data3)
        self.assertEqual(result3["decision"], "禁投")

    def test_dirty_data_self_healing(self):
        """Test passing strings with currency, spaces, and text mixed with numbers to ensure `_clean_data` fixes them automatically."""
        data = {
            "budget": "¥100,000.50",
            "estimated_cost": "$ 70,000",
            "days_to_deadline": "14 days left"
        }
        result = self.engine.generate_card(data)

        self.assertEqual(result["metrics"]["budget"], 100000.50)
        self.assertEqual(result["metrics"]["cost"], 70000.0)
        self.assertEqual(result["metrics"]["days"], 14)

        self.assertEqual(result["decision"], "可投")

        # Verify audit log recorded the cleaning
        logs = self.engine.get_audit_log()
        cleaning_logs = [log for log in logs if log["step"] == "data_cleaning"]
        self.assertEqual(len(cleaning_logs), 3)

    def test_missing_params_fallback(self):
        """Test passing an empty dictionary or None, expecting reasonable defaults to prevent crashes and provide a fallback result."""
        result = self.engine.generate_card(None) # type: ignore

        self.assertEqual(result["metrics"]["budget"], 50000.0)
        self.assertEqual(result["metrics"]["cost"], 40000.0) # 80% of 50000
        self.assertEqual(result["metrics"]["days"], 14)

        self.assertEqual(result["decision"], "可投")

        result_empty = self.engine.generate_card({})
        self.assertEqual(result_empty["metrics"]["budget"], 50000.0)

    def test_fatal_error_fallback(self):
        """Test fallback when exception is raised inside generation."""
        from unittest.mock import patch
        with patch.object(self.engine, '_clean_data', side_effect=Exception("Simulated Exception")):
            result = self.engine.generate_card({"budget": 100000})

            self.assertEqual(result["decision"], "慎投")
            self.assertIn("error during evaluation", result["summary"])

            # Verify fatal error log
            logs = self.engine.get_audit_log()
            fatal_logs = [log for log in logs if log["step"] == "fatal_error"]
            self.assertEqual(len(fatal_logs), 1)

if __name__ == '__main__':
    unittest.main()
