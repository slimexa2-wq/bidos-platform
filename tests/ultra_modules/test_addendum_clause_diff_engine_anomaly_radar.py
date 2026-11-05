import unittest
from ultra_modules.addendum_clause_diff_engine_anomaly_radar import AddendumClauseDiffAnomalyRadar

class TestAddendumClauseDiffAnomalyRadar(unittest.TestCase):

    def test_normal_behavior(self):
        """Test normal behavior with 100 items and the top 3% marked as red/yellow."""
        # Create 100 items with scores to ensure top 3% (3 items) get Red and
        # that their Z-score will be > 2.0 (Red requires z_score > 2.0)
        # To get z_score > 2.0 for the top 3, let's make most scores 0 or small
        clauses = []
        for i in range(97):
            clauses.append({
                "id": f"clause_{i}",
                "content": f"Clause content {i}",
                "score": 1.0,
                "type": "modified"
            })
        # Add the 3 anomalies with high scores
        clauses.append({"id": "clause_97", "score": 100.0})
        clauses.append({"id": "clause_98", "score": 99.0})
        clauses.append({"id": "clause_99", "score": 98.0})

        result = AddendumClauseDiffAnomalyRadar.calculate_anomalies(clauses, top_percent=0.03)

        self.assertEqual(result["total_items"], 100)
        self.assertEqual(len(result["anomalies"]) + len(result["normal_items"]), 100)

        # the top 3% (3 items) should have highest scores: 100, 99, 98
        anomalies = result["anomalies"]
        self.assertEqual(len(anomalies), 3)

        for anomaly in anomalies:
            # Depending on Z-score it might be Yellow or Red, with these values it should be Red (> 2.0)
            # mean = (97 + 297)/100 = 3.94
            # var is high, let's verify if they actually get marked Red
            self.assertTrue(anomaly["marker"] in ["Red", "Yellow"])
            self.assertTrue(anomaly["score"] in [100, 99, 98])

        self.assertEqual(len(result["normal_items"]), 97)
        for item in result["normal_items"]:
            self.assertEqual(item["marker"], "Green")

    def test_dirty_data_self_healing(self):
        """Test dirty data self-healing (invalid types, missing text)."""
        clauses = [
            None,  # should be wrapped in dict
            "invalid_string_clause",
            {
                "id": "c1",
                # missing content
                "score": "invalid_score",  # invalid score
            },
            {
                "id": "c2",
                "content": "valid content",
                "score": float('inf'), # inf score
            },
             {
                "id": "c3",
                "content": "valid content",
                "score": float('nan'), # nan score
            }
        ]

        # Will end up with 5 items, all scores will clean to 0.0
        result = AddendumClauseDiffAnomalyRadar.calculate_anomalies(clauses)

        self.assertEqual(result["total_items"], 5)
        self.assertEqual(len(result["anomalies"]), 0) # std_dev = 0, no anomalies
        self.assertEqual(len(result["normal_items"]), 5)

    def test_missing_parameter_fallback(self):
        """Test missing parameter fallback (no change_impact_score)."""
        clauses = [
            {"id": "c1", "content": "a"},
            {"id": "c2", "content": "b"},
            {"id": "c3", "content": "c", "change_impact_score": 10},
            {"id": "c4", "content": "d", "score": 20},
            {"id": "c5", "content": "e", "score": 100},
        ]

        result = AddendumClauseDiffAnomalyRadar.calculate_anomalies(clauses, top_percent=0.2)

        self.assertEqual(result["total_items"], 5)

        # c5 should be the anomaly
        anomalies = result["anomalies"]
        self.assertEqual(len(anomalies), 1)
        self.assertEqual(anomalies[0]["id"], "c5")

        # missing scores became 0
        normal_ids = [item["id"] for item in result["normal_items"]]
        self.assertTrue("c1" in normal_ids)
        self.assertTrue("c2" in normal_ids)

    def test_extreme_boundary_empty(self):
        """Test extreme boundary (empty list or invalid list)."""
        result_empty = AddendumClauseDiffAnomalyRadar.calculate_anomalies([])
        self.assertEqual(result_empty["total_items"], 0)
        self.assertEqual(result_empty["summary_plain_text"], "未提供任何条款数据，系统静默放行。")

        result_none = AddendumClauseDiffAnomalyRadar.calculate_anomalies(None)
        self.assertEqual(result_none["total_items"], 0)
        self.assertEqual(result_none["summary_plain_text"], "未提供任何条款数据，系统静默放行。")

        result_dict = AddendumClauseDiffAnomalyRadar.calculate_anomalies({"invalid": "dict"})
        self.assertEqual(result_dict["total_items"], 0)
        self.assertEqual(result_dict["summary_plain_text"], "未提供任何条款数据，系统静默放行。")

    def test_extreme_boundary_identical_scores(self):
        """Test extreme boundary (identical scores)."""
        clauses = []
        for i in range(10):
            clauses.append({
                "id": f"c{i}",
                "content": f"content {i}",
                "score": 10.0
            })

        result = AddendumClauseDiffAnomalyRadar.calculate_anomalies(clauses)

        self.assertEqual(result["total_items"], 10)
        self.assertEqual(len(result["anomalies"]), 0)
        self.assertEqual(len(result["normal_items"]), 10)
        self.assertEqual(result["summary_plain_text"], "所有条款修改幅度一致，未发现明显异常，全部静默放行。")

    def test_secondary_tier_yellow(self):
        """Test yellow marker assignment"""
        # Create a distribution where top item has z_score between 1.0 and 2.0
        # or another item has z_score > 1.5
        clauses = []
        for i in range(10):
            clauses.append({"id": f"c{i}", "score": 10.0})
        # Mean around 10. std_dev around ~0 if all 10. Let's make it varied
        clauses[0]["score"] = 20.0 # Will be top item, z_score will be calculated
        clauses[1]["score"] = 20.0
        # For this setup, we just need to ensure the yellow branch is hit.
        # Actually with 10 items, mean=12, var is ~ (64+64+2*2*8)/10 = 16. std_dev = 4.
        # score=20 -> z_score = 8 / 4 = 2.0 exactly. Wait, Red needs > 2.0. So this will hit Yellow!

        result = AddendumClauseDiffAnomalyRadar.calculate_anomalies(clauses, top_percent=0.2)

        anomalies = result["anomalies"]
        self.assertEqual(len(anomalies), 2)
        self.assertEqual(anomalies[0]["marker"], "Yellow")
        self.assertEqual(anomalies[1]["marker"], "Yellow")

if __name__ == '__main__':
    unittest.main()
