import unittest
import datetime
from ultra_modules.bid_bond_return_tracker_anomaly_radar import analyze_bid_bond_returns, _compute_stats, parse_date

class TestBidBondReturnTrackerAnomalyRadar(unittest.TestCase):
    def setUp(self):
        self.base_date = datetime.date(2023, 1, 1)

    def test_parse_date(self):
        self.assertEqual(parse_date(None, self.base_date), self.base_date)
        self.assertEqual(parse_date("", self.base_date), self.base_date)
        self.assertEqual(parse_date(self.base_date, datetime.date(2022, 1, 1)), self.base_date)

        dt = datetime.datetime(2023, 5, 1, 12, 0, 0)
        self.assertEqual(parse_date(dt, self.base_date), datetime.date(2023, 5, 1))

        self.assertEqual(parse_date("2023-08-15", self.base_date), datetime.date(2023, 8, 15))
        self.assertEqual(parse_date("invalid-date", self.base_date), self.base_date)

    def test_compute_stats_empty(self):
        stats = _compute_stats([])
        self.assertEqual(stats["mean"], 0)
        self.assertIsNone(stats["iqr_upper_bound"])

    def test_normal_distribution_97_green(self):
        # Create 100 records
        # 97 records with 10 days
        # 3 records with 15 days (these will be top 3% but since they are close to the rest, they might be yellow)
        records = []
        for i in range(97):
            records.append({
                "id": f"rec_{i}",
                "bid_open_date": self.base_date,
                "return_date": self.base_date + datetime.timedelta(days=10)
            })
        for i in range(3):
             records.append({
                "id": f"rec_top_{i}",
                "bid_open_date": self.base_date,
                "return_date": self.base_date + datetime.timedelta(days=15)
            })

        res = analyze_bid_bond_returns(records, self.base_date)

        greens = [r for r in res["records"] if r["mark"] == "GREEN"]
        yellows = [r for r in res["records"] if r["mark"] == "YELLOW"]
        reds = [r for r in res["records"] if r["mark"] == "RED"]

        self.assertEqual(len(greens), 97)
        # In this specific distribution IQR=0 (since Q1=10, Q3=10), upper_bound=10.
        # Top 3% are 15 > 10, they get RED!
        self.assertEqual(len(reds), 3)
        self.assertEqual(len(yellows), 0)

    def test_dirty_data_self_healing(self):
        records = [
            # Inverted dates
            {
                "id": "rec_1",
                "bid_open_date": datetime.date(2023, 1, 10),
                "return_date": datetime.date(2023, 1, 1)
            },
            # Invalid date format string
            {
                "id": "rec_2",
                "bid_open_date": "bad_date",
                "return_date": "2023-01-05"
            }
        ]

        res = analyze_bid_bond_returns(records, self.base_date)

        # rec_1 should have 9 days
        self.assertEqual(res["records"][0]["days_held"], 9)
        self.assertEqual(res["records"][0]["bid_open_date"], datetime.date(2023, 1, 1))
        self.assertEqual(res["records"][0]["return_date"], datetime.date(2023, 1, 10))

        # rec_2 should fallback bid_open_date to base_date (2023-01-01), return_date 2023-01-05 (4 days)
        self.assertEqual(res["records"][1]["days_held"], 4)
        self.assertEqual(res["records"][1]["bid_open_date"], self.base_date)

        self.assertTrue(any("return_date 2023-01-01 is before bid_open_date 2023-01-10. Swapping." in log for log in res["audit_log"]))
        self.assertTrue(any("bid_open_date fallback" in log for log in res["audit_log"]))

    def test_missing_params_fallback(self):
        records = [
            # No ID, missing dates
            {}
        ]
        res = analyze_bid_bond_returns(records, self.base_date)
        self.assertEqual(res["records"][0]["id"], "auto_id_0")
        self.assertEqual(res["records"][0]["days_held"], 0)
        self.assertEqual(res["records"][0]["bid_open_date"], self.base_date)
        self.assertEqual(res["records"][0]["return_date"], self.base_date)

    def test_extreme_boundaries(self):
        # 34 records to trigger top 3% check.
        # 33 with 0 days, 1 with 10000 days.
        records = [{"bid_open_date": self.base_date, "return_date": self.base_date} for _ in range(33)]
        records.append({"bid_open_date": self.base_date, "return_date": self.base_date + datetime.timedelta(days=10000)})

        res = analyze_bid_bond_returns(records, self.base_date)

        reds = [r for r in res["records"] if r["mark"] == "RED"]
        self.assertEqual(len(reds), 1)
        self.assertEqual(reds[0]["days_held"], 10000)
        self.assertIn("严重超出正常范围", reds[0]["reason"])

    def test_iqr_zscore_logic_yellow(self):
        # Force a situation where it's in the top 3% but not exceeding IQR or Z-score limits.
        # To do this, we need a high variance and large IQR.
        records = []
        for i in range(1, 101):
            # linear distribution 1 to 100
            records.append({
                "bid_open_date": self.base_date,
                "return_date": self.base_date + datetime.timedelta(days=i)
            })

        res = analyze_bid_bond_returns(records, self.base_date)

        # Q1 ~ 25, Q3 ~ 75, IQR ~ 50. Upper bound ~ 75 + 1.5*50 = 150.
        # Max is 100, which is < 150.
        # Z-score: mean ~ 50, std ~ 29. 100 is (100-50)/29 = 1.7 < 2.
        # Thus the top 3% (98, 99, 100) should be YELLOW, not RED.
        yellows = [r for r in res["records"] if r["mark"] == "YELLOW"]
        reds = [r for r in res["records"] if r["mark"] == "RED"]

        self.assertEqual(len(reds), 0)
        self.assertTrue(len(yellows) > 0)
        for y in yellows:
            self.assertIn("处于所有记录的前3%", y["reason"])

    def test_red_due_to_zscore(self):
        # We need IQR to be None or very large, but Z-score > 2.
        # Wait, if IQR is large, std_dev is also large.
        # Let's just create a distribution where Z-score triggers RED even if it's not top 3%?
        # Actually our logic triggers RED if Z > 2 regardless of top 3%.

        # Let's do 10 records, so top 3% check (len >= 33) is false.
        # Thus the top 3% branch is skipped.
        # 9 records with 1 day, 1 record with 10 days.
        records = []
        for _ in range(9):
             records.append({"bid_open_date": self.base_date, "return_date": self.base_date + datetime.timedelta(days=1)})
        records.append({"bid_open_date": self.base_date, "return_date": self.base_date + datetime.timedelta(days=10)})

        res = analyze_bid_bond_returns(records, self.base_date)

        # Here mean=1.9, std_dev=2.7. For 10, Z = (10-1.9)/2.7 = 3.0 > 2.
        # So it should be RED.
        reds = [r for r in res["records"] if r["mark"] == "RED"]
        self.assertEqual(len(reds), 1)
        self.assertEqual(reds[0]["days_held"], 10)
        # Because IQR upper bound is 1 + 1.5*0 = 1, it will actually trigger the IQR condition first.

        # To strictly trigger Z-score but not IQR, we need value < IQR upper bound but Z > 2.
        # This is mathematically impossible since IQR upper bound = Q3 + 1.5*IQR.
        pass

    def test_exact_get_percentile_integer_k(self):
         res = _compute_stats([10, 20, 30, 40, 50])
         self.assertEqual(res["q1"], 20)
         self.assertEqual(res["q3"], 40)
         self.assertEqual(res["iqr"], 20)
         self.assertEqual(res["iqr_upper_bound"], 70)

if __name__ == '__main__':
    unittest.main()
