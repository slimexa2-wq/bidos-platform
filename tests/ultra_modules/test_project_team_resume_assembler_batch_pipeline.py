import unittest
import time
from ultra_modules.project_team_resume_assembler_batch_pipeline import ResumeAssemblerBatchPipeline

class TestResumeAssemblerBatchPipeline(unittest.TestCase):
    def setUp(self):
        self.pipeline = ResumeAssemblerBatchPipeline(max_workers=5)

    def test_normal_operation(self):
        """1. 正常操作，包含标准输入数据"""
        records = [
            {"id": "1", "name": "张三", "role": "项目经理", "has_social_security": True, "certifications": ["PMP"]},
            {"id": "2", "name": "李四", "role": "驻厂专员", "has_social_security": True, "certifications": []}
        ]

        job_id = self.pipeline.submit_batch(records)

        # 等待任务完成
        while True:
            snapshot = self.pipeline.get_progress_snapshot(job_id)
            if snapshot["status"] == "completed":
                break
            time.sleep(0.01)

        self.assertEqual(snapshot["total"], 2)
        self.assertEqual(snapshot["processed"], 2)

        results = snapshot["results"]
        # 验证结果
        for res in results:
            self.assertTrue(res["is_valid"])
            self.assertFalse(res["healed"])

    def test_dirty_data_self_healing(self):
        """2. 脏数据自愈功能测试（非字典类型）"""
        records = [
            "This is a string instead of dict",
            12345
        ]

        results = list(self.pipeline.process_batch_stream(records))
        self.assertEqual(len(results), 2)
        for res in results:
            self.assertTrue(res["healed"])
            self.assertEqual(res["name"], "未命名人员")  # 默认值兜底

    def test_missing_parameters_fallback(self):
        """3. 缺参默认兜底测试"""
        records = [
            {}  # 空字典，完全缺少参数
        ]

        results = list(self.pipeline.process_batch_stream(records))
        self.assertEqual(len(results), 1)
        res = results[0]

        self.assertIsNotNone(res["id"])
        self.assertEqual(res["name"], "未命名人员")
        self.assertEqual(res["role"], "驻厂专员")
        self.assertTrue(res["has_social_security"])  # 默认社保为True
        self.assertEqual(res["certifications"], [])
        self.assertTrue(res["is_valid"])  # 驻厂专员不需要证书，社保默认有

        # 缺少证书的项目经理
        manager = {"role": "项目经理"}
        res2 = list(self.pipeline.process_batch_stream([manager]))[0]
        self.assertFalse(res2["is_valid"])
        self.assertIn("项目经理缺少资格证书", res2["issues"])

    def test_extreme_boundary(self):
        """4. 极端边界测试：大数据量1000条或空"""
        # 测试空列表
        empty_results = list(self.pipeline.process_batch_stream([]))
        self.assertEqual(len(empty_results), 0)

        # 测试1000条
        records = [{"id": str(i)} for i in range(1000)]
        job_id = self.pipeline.submit_batch(records)

        while True:
            snapshot = self.pipeline.get_progress_snapshot(job_id)
            if snapshot["status"] == "completed":
                break
            time.sleep(0.01)

        self.assertEqual(snapshot["processed"], 1000)
        self.assertEqual(len(snapshot["results"]), 1000)

    def test_progress_snapshot(self):
        """5. 进度快照获取功能"""
        # 构造稍大的任务
        records = [{"id": str(i)} for i in range(100)]
        job_id = self.pipeline.submit_batch(records)

        snapshots = []
        while True:
            snapshot = self.pipeline.get_progress_snapshot(job_id)
            snapshots.append(snapshot)
            if snapshot["status"] == "completed":
                break
            time.sleep(0.01)

        # 至少获取到了一次状态
        self.assertGreater(len(snapshots), 0)

        # 检查快照的格式
        for snap in snapshots:
            self.assertEqual(snap["total"], 100)
            self.assertLessEqual(snap["processed"], 100)
            self.assertGreaterEqual(snap["processed"], 0)
            self.assertIsInstance(snap["results"], list)

if __name__ == "__main__":
    unittest.main()
