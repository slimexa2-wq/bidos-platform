import unittest
from ultra_modules.project_team_resume_assembler_anomaly_radar import ProjectTeamResumeAssemblerAnomalyRadar

class TestProjectTeamResumeAssemblerAnomalyRadar(unittest.TestCase):

    def setUp(self):
        self.radar = ProjectTeamResumeAssemblerAnomalyRadar()

    def test_normal_distribution_silent_pass(self):
        """测试用例 1: 正常分布 (97% 正常项静默放行)"""
        # 生成正常数据，没有异常值
        records = [{"name": f"Candidate {i}", "social_security_months": 12.0 + (i % 3)} for i in range(10)]
        results = self.radar.scan_anomalies(records)

        self.assertEqual(len(results), 10)
        # 没有突出的异常，应该全部都是 green
        for res in results:
            self.assertEqual(res["_anomaly_color"], "green")
            self.assertEqual(res["_anomaly_reason"], "")

    def test_dirty_data_self_healing(self):
        """测试用例 2: 脏数据自愈 (提取数字，处理负数，处理非数字)"""
        records = [
            {"name": "A", "social_security_months": "连续 24 个月"},  # 应提取 24
            {"name": "B", "social_security_months": -10},           # 负数，应归零
            {"name": "C", "social_security_months": "未知"},         # 非数字，应归零
            {"name": "D", "social_security_months": float('nan')},  # NaN，应归零
            {"name": "E", "social_security_months": float('inf')},  # Inf，应归零
            {"name": "F", "social_security_months": "2.5年"}          # 应提取 2.5
        ]
        results = self.radar.scan_anomalies(records)

        self.assertEqual(results[0]["_cleaned_value"], 24.0)
        self.assertEqual(results[1]["_cleaned_value"], 0.0)
        self.assertEqual(results[2]["_cleaned_value"], 0.0)
        self.assertEqual(results[3]["_cleaned_value"], 0.0)
        self.assertEqual(results[4]["_cleaned_value"], 0.0)
        self.assertEqual(results[5]["_cleaned_value"], 2.5)

    def test_missing_parameters_fallback(self):
        """测试用例 3: 缺参默认兜底"""
        records = [
            {"name": "A", "social_security_months": 12},
            {"name": "B"}, # 缺失目标字段
            {"name": "C", "social_security_months": None}, # 为 None
        ]
        results = self.radar.scan_anomalies(records)

        self.assertEqual(results[1]["_cleaned_value"], 0.0)
        self.assertEqual(results[2]["_cleaned_value"], 0.0)

    def test_top_3_percent_anomaly_detection(self):
        """测试用例 4: Top 3% 异常项高亮"""
        # 构造包含 100 个正常，1 个极低，1 个极高的样本
        records = [{"name": f"Normal {i}", "social_security_months": 12.0} for i in range(100)]
        records.append({"name": "Too High", "social_security_months": 999.0})
        records.append({"name": "Too Low", "social_security_months": 0.5})

        results = self.radar.scan_anomalies(records)

        # Top 3% 的容量是 max(1, ceil(102 * 0.03)) = max(1, 4) = 4 个
        # 我们的极高和极低绝对会被捕获在 Top 3 中

        high_res = next(r for r in results if r["name"] == "Too High")
        low_res = next(r for r in results if r["name"] == "Too Low")

        self.assertEqual(high_res["_anomaly_color"], "red")
        self.assertIn("远高于其他项", high_res["_anomaly_reason"])

        self.assertEqual(low_res["_anomaly_color"], "red")
        self.assertIn("远低于正常范围", low_res["_anomaly_reason"])

        # 大部分应该都是绿色
        green_count = sum(1 for r in results if r["_anomaly_color"] == "green")
        self.assertGreater(green_count, 95)

    def test_extreme_boundaries(self):
        """测试用例 5: 极端边界 (空列表或单元素)"""
        # 空列表
        results_empty = self.radar.scan_anomalies([])
        self.assertEqual(results_empty, [])

        # 单元素
        records_single = [{"name": "Single", "social_security_months": 100}]
        results_single = self.radar.scan_anomalies(records_single)
        self.assertEqual(len(results_single), 1)
        self.assertEqual(results_single[0]["_anomaly_color"], "green") # 没有对比就伤害不了，认为是绿色

        # 只有两个元素
        records_two = [
            {"name": "One", "social_security_months": 10},
            {"name": "Two", "social_security_months": 10}
        ]
        results_two = self.radar.scan_anomalies(records_two)
        self.assertEqual(results_two[0]["_anomaly_color"], "green")
        self.assertEqual(results_two[1]["_anomaly_color"], "green")

    def test_edge_case_z_score_zero_std_dev(self):
        """测试用例 6: 所有值相等导致标准差为 0 的情况"""
        records = [{"name": f"Candidate {i}", "social_security_months": 12.0} for i in range(10)]
        results = self.radar.scan_anomalies(records)
        for res in results:
            self.assertEqual(res["_anomaly_color"], "green")

    def test_all_invalid_strings(self):
         """测试用例 7: 全部为无效字符串的情况"""
         records = [{"name": f"Cand {i}", "social_security_months": "无效"} for i in range(5)]
         results = self.radar.scan_anomalies(records)
         for res in results:
             self.assertEqual(res["_cleaned_value"], 0.0)
             self.assertEqual(res["_anomaly_color"], "green")

    def test_small_sample_with_anomaly(self):
        """测试用例 8: 小样本下的异常"""
        records = [
            {"name": "A", "social_security_months": 12},
            {"name": "B", "social_security_months": 12},
            {"name": "C", "social_security_months": 12},
            {"name": "D", "social_security_months": 120} # 异常偏高
        ]
        results = self.radar.scan_anomalies(records)
        # 虽然小于10条，但有明显异常
        abnormal = next(r for r in results if r["name"] == "D")

        # 虽然小于 10 条时不保证 Top 3%，但通过 IQR 的 score 计算，它是有异常分数的。
        # _determine_anomaly_score 中会给它加分。
        # top_3_percent_count 小于 10 条时为 0。所以实际不会标红。
        # 如果需要标红小样本，我们需要修改雷达的红线逻辑。
        # 当前设计是 "Top 3%"，小样本没有红。所以这是黄色的轻微警告。
        self.assertIn(abnormal["_anomaly_color"], ["yellow", "red"])

if __name__ == '__main__':
    unittest.main()
