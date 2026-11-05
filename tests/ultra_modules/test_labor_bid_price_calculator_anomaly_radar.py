import unittest
from ultra_modules.labor_bid_price_calculator_anomaly_radar import detect_anomalies, clean_data, _calculate_iqr_thresholds

class TestLaborBidPriceAnomalyRadar(unittest.TestCase):
    def test_normal_data_behavior(self):
        bids = [
            {"id": f"bid_{i}", "base_salary": 3000.0, "social_security": 1000.0, "provident_fund": 300.0, "disabled_security_fund": 50.0, "margin": 500.0, "tax": 200.0}
            for i in range(100)
        ]

        result = detect_anomalies(bids)
        self.assertEqual(len(result["anomalies"]), 0)
        self.assertEqual(len(result["normal"]), 100)
        self.assertEqual(result["normal"][0]["color_marker"], "green")
        self.assertEqual(result["normal"][0]["reason"], "正常放行")

    def test_dirty_data_self_healing(self):
        bids = [
            {"id": "bid_dirty1", "base_salary": "3,000", "social_security": " 1,000 ", "provident_fund": "¥300", "disabled_security_fund": "￥50", "margin": "￥500", "tax": "invalid_string"}
        ]
        result = detect_anomalies(bids)
        self.assertEqual(len(result["normal"]), 1)
        self.assertEqual(len(result["anomalies"]), 0)
        cleaned = result["normal"][0]
        self.assertEqual(cleaned["base_salary"], 3000.0)
        self.assertEqual(cleaned["social_security"], 1000.0)
        self.assertEqual(cleaned["provident_fund"], 300.0)
        self.assertEqual(cleaned["disabled_security_fund"], 50.0)
        self.assertEqual(cleaned["margin"], 500.0)
        self.assertEqual(cleaned["tax"], 200.0)

    def test_missing_parameter_fallback(self):
        bids = [
            {"id": "bid_missing"}
        ]
        result = detect_anomalies(bids)
        cleaned = result["normal"][0]
        self.assertEqual(cleaned["base_salary"], 3000.0)
        self.assertEqual(cleaned["social_security"], 1000.0)
        self.assertEqual(cleaned["provident_fund"], 300.0)
        self.assertEqual(cleaned["disabled_security_fund"], 50.0)
        self.assertEqual(cleaned["margin"], 500.0)
        self.assertEqual(cleaned["tax"], 200.0)

    def test_extreme_boundary_conditions(self):
        result = detect_anomalies([])
        self.assertEqual(result["anomalies"], [])
        self.assertEqual(result["normal"], [])

        self.assertEqual(clean_data(None), 0.0)
        self.assertEqual(clean_data("100"), 100.0)
        self.assertEqual(clean_data("not_a_number"), 0.0)
        self.assertEqual(clean_data([1, 2, 3]), 0.0)

        self.assertEqual(_calculate_iqr_thresholds([]), (0, 0))

    def test_anomaly_detection_top_3_percent(self):
        bids = [
            {"id": f"normal_{i}", "base_salary": 3000.0, "social_security": 1000.0, "provident_fund": 300.0, "disabled_security_fund": 50.0, "margin": 500.0, "tax": 200.0}
            for i in range(97)
        ]
        bids.append({"id": "anomaly_low", "base_salary": 0.0, "social_security": 0.0, "provident_fund": 0.0, "disabled_security_fund": 0.0, "margin": 0.0, "tax": 0.0})
        bids.append({"id": "anomaly_high1", "base_salary": 99999.0, "social_security": 1000.0, "provident_fund": 300.0, "disabled_security_fund": 50.0, "margin": 500.0, "tax": 200.0})
        bids.append({"id": "anomaly_high2", "base_salary": 99999.0, "social_security": 1000.0, "provident_fund": 300.0, "disabled_security_fund": 50.0, "margin": 500.0, "tax": 200.0})

        result = detect_anomalies(bids)
        self.assertEqual(len(result["anomalies"]), 3)
        self.assertEqual(len(result["normal"]), 97)

        anomaly_ids = [a["id"] for a in result["anomalies"]]
        self.assertIn("anomaly_low", anomaly_ids)
        self.assertIn("anomaly_high1", anomaly_ids)
        self.assertIn("anomaly_high2", anomaly_ids)

        for a in result["anomalies"]:
            self.assertEqual(a["color_marker"], "red")
            if a["id"] == "anomaly_low":
                self.assertEqual(a["reason"], "总价过低，存在严重亏损或未交齐社保风险")
            else:
                self.assertEqual(a["reason"], "总价过高，偏离市场正常报价区间")

    def test_yellow_marker_behavior(self):
        # Create normal values but missing attributes or string values
        # To trigger the yellow marker
        bids = [
            {"id": f"healed_{i}", "base_salary": "3000"} for i in range(10)
        ]
        bids.extend([
            {"id": f"normal_{i}", "base_salary": 3000.0, "social_security": 1000.0, "provident_fund": 300.0, "disabled_security_fund": 50.0, "margin": 500.0, "tax": 200.0} for i in range(100)
        ])

        result = detect_anomalies(bids)
        self.assertEqual(len(result["anomalies"]), 0)
        self.assertEqual(len(result["normal"]), 110)

        yellow_count = sum(1 for item in result["normal"] if item["color_marker"] == "yellow")
        green_count = sum(1 for item in result["normal"] if item["color_marker"] == "green")

        self.assertEqual(yellow_count, 10)
        self.assertEqual(green_count, 100)

        yellow_item = next(item for item in result["normal"] if item["color_marker"] == "yellow")
        self.assertEqual(yellow_item["reason"], "数据自愈放行(含缺省值或格式修正)")

    def test_even_number_bids_median(self):
        bids = [
            {"id": "b1", "base_salary": 1000.0, "social_security": 1000.0, "provident_fund": 300.0, "disabled_security_fund": 50.0, "margin": 500.0, "tax": 200.0},
            {"id": "b2", "base_salary": 2000.0, "social_security": 1000.0, "provident_fund": 300.0, "disabled_security_fund": 50.0, "margin": 500.0, "tax": 200.0},
            {"id": "b3", "base_salary": 3000.0, "social_security": 1000.0, "provident_fund": 300.0, "disabled_security_fund": 50.0, "margin": 500.0, "tax": 200.0},
            {"id": "b4", "base_salary": 99000.0, "social_security": 1000.0, "provident_fund": 300.0, "disabled_security_fund": 50.0, "margin": 500.0, "tax": 200.0},
        ]
        result = detect_anomalies(bids)
        self.assertEqual(len(result["anomalies"]), 1)
        self.assertEqual(result["anomalies"][0]["id"], "b4")

if __name__ == '__main__':
    unittest.main()
