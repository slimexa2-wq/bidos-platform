import unittest
import time
from ultra_modules.technical_proposal_outline_gen_batch_pipeline import TechnicalProposalOutlineGenerator, OutlineBatchPipeline

class TestTechnicalProposalOutlineGenBatchPipeline(unittest.TestCase):

    def setUp(self):
        self.generator = TechnicalProposalOutlineGenerator()
        self.pipeline = OutlineBatchPipeline(max_workers=5)

    def test_01_normal_data_flow(self):
        """1. 正常数据处理"""
        record = {
            "project_name": "智慧城市建设项目",
            "total_score": 100.0,
            "tech_score_weight": 0.6,
            "min_pages": 100
        }
        res = self.generator.generate_single(record)
        self.assertEqual(res["project_name"], "智慧城市建设项目")
        self.assertEqual(res["tech_score_assessed"], 60.0)
        self.assertEqual(res["status"], "success")
        self.assertTrue(len(res["generated_outline"]) > 0)
        self.assertTrue(res["total_suggested_pages"] > 0)

    def test_02_missing_parameters_fallback(self):
        """2. 缺参默认兜底"""
        record = {} # 空字典，模拟全缺参
        res = self.generator.generate_single(record)
        self.assertEqual(res["project_name"], "未命名项目技术标")
        self.assertEqual(res["tech_score_assessed"], 50.0) # 100 * 0.5
        self.assertEqual(res["status"], "success")
        self.assertTrue(len(res["generated_outline"]) > 0)

    def test_03_dirty_data_self_healing(self):
        """3. 脏数据自愈（无效类型、负数）"""
        record = {
            "project_name": None,
            "total_score": "invalid_string",
            "tech_score_weight": -0.5, # 负数
            "min_pages": -10 # 负数
        }
        res = self.generator.generate_single(record)
        self.assertEqual(res["project_name"], "未命名项目技术标")
        self.assertEqual(res["tech_score_assessed"], 50.0) # 100 * 0.5 fallback
        self.assertEqual(res["status"], "success")

        # 极端非字典脏数据
        res2 = self.generator.generate_single("this is a string, not dict")
        self.assertEqual(res2["status"], "success")
        self.assertEqual(res2["project_name"], "未命名项目技术标")

    def test_04_extreme_boundary_cases(self):
        """4. 极端边界（0条和1000条记录高并发吞吐测试）"""
        # 0条
        job_id_0 = self.pipeline.submit_batch([])
        progress_0 = self.pipeline.get_progress(job_id_0)
        self.assertEqual(progress_0["total"], 0)
        self.assertEqual(progress_0["status"], "completed")
        self.assertEqual(self.pipeline.get_results(job_id_0), [])

        # 1000条
        records_1000 = [{"project_name": f"Project_{i}"} for i in range(1000)]
        job_id_1000 = self.pipeline.submit_batch(records_1000)

        # 等待处理完成 (简单轮询)
        timeout = 10
        start = time.time()
        while time.time() - start < timeout:
            prog = self.pipeline.get_progress(job_id_1000)
            if prog["status"] == "completed":
                break
            time.sleep(0.1)

        final_prog = self.pipeline.get_progress(job_id_1000)
        self.assertEqual(final_prog["status"], "completed")
        self.assertEqual(final_prog["total"], 1000)
        self.assertEqual(final_prog["completed"], 1000)

        results = self.pipeline.get_results(job_id_1000)
        self.assertEqual(len(results), 1000)
        self.assertEqual(results[0]["project_name"], "Project_0")
        self.assertEqual(results[999]["project_name"], "Project_999")

    def test_05_progress_tracking_snapshot(self):
        """5. 进度快照与零阻塞查询"""
        records = [{"project_name": f"P_{i}"} for i in range(500)]
        job_id = self.pipeline.submit_batch(records)

        # 马上查，不能阻塞
        prog1 = self.pipeline.get_progress(job_id)
        self.assertIn("progress_percentage", prog1)
        self.assertTrue(0 <= prog1["progress_percentage"] <= 100)
        self.assertEqual(prog1["total"], 500)

        # 查一个不存在的job
        err_prog = self.pipeline.get_progress("non_existent")
        self.assertIn("error", err_prog)

if __name__ == '__main__':
    unittest.main()
