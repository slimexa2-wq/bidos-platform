import unittest
import json
from ultra_modules.bid_consistency_silent_auditor_plain_explainer import audit_consistency, _safe_str, _safe_float, _safe_int

class TestBidConsistencySilentAuditorPlainExplainer(unittest.TestCase):

    def test_safe_functions(self):
        # 脏数据自愈测试
        self.assertEqual(_safe_str(None), "")
        self.assertEqual(_safe_str(123), "123")
        self.assertEqual(_safe_str("  abc  "), "abc")

        self.assertEqual(_safe_float(None), 0.0)
        self.assertEqual(_safe_float("123.45"), 123.45)
        self.assertEqual(_safe_float("abc"), 0.0)

        self.assertEqual(_safe_int(None), 0)
        self.assertEqual(_safe_int("123"), 123)
        self.assertEqual(_safe_int("123.45"), 123)
        self.assertEqual(_safe_int("abc"), 0)

    def test_normal_consistent_data(self):
        # 1. 正常一致数据
        data = [
            {"chapter_name": "商务标", "amount": 1000000, "manager_name": "张三", "manager_cert": "京建安12345", "days": 30},
            {"chapter_name": "技术标", "amount": 1000000, "manager_name": "张三", "manager_cert": "京建安12345", "days": 30}
        ]
        result_json = audit_consistency(data, "json")
        result = json.loads(result_json)
        self.assertEqual(result["overall_status"], "通过")
        self.assertTrue("完美" in result["summary"])
        for detail in result["details"]:
            self.assertEqual(detail["status"], "一致")

    def test_inconsistent_data(self):
        # 2. 存在不一致的数据
        data = [
            {"chapter_name": "商务标", "amount": 1000000, "days": 30},
            {"chapter_name": "技术标", "amount": 1500000, "days": 30}, # 金额不同
            {"chapter_name": "经济标", "amount": 1000000, "days": 45}  # 天数不同
        ]
        result_json = audit_consistency(data, "json")
        result = json.loads(result_json)
        self.assertEqual(result["overall_status"], "存在风险")
        self.assertTrue("前后矛盾" in result["summary"])

        # 检查具体不一致项
        amount_detail = next(d for d in result["details"] if d["item"] == "投标金额")
        self.assertEqual(amount_detail["status"], "不一致")
        self.assertTrue("【商务标】为 1000000.0" in amount_detail["detail"])
        self.assertTrue("【技术标】为 1500000.0" in amount_detail["detail"])

        days_detail = next(d for d in result["details"] if d["item"] == "工期天数")
        self.assertEqual(days_detail["status"], "不一致")

    def test_dirty_data_self_healing(self):
        # 3. 脏数据自愈：无法解析的数字变成0，多余空格被去除
        data = [
            {"chapter_name": "第一章", "amount": "100万", "manager_name": "  李四  "},
            {"chapter_name": "第二章", "amount": "xyz", "manager_name": "李四"}
        ]
        # "100万" 和 "xyz" 都会转成 0.0，因此金额是"一致"的（都是0.0）
        # 姓名去除了空格，所以都是"李四"
        result_json = audit_consistency(data, "json")
        result = json.loads(result_json)

        amount_detail = next(d for d in result["details"] if d["item"] == "投标金额")
        self.assertEqual(amount_detail["status"], "一致")

        name_detail = next(d for d in result["details"] if d["item"] == "项目经理姓名")
        self.assertEqual(name_detail["status"], "一致")
        self.assertEqual(result["overall_status"], "通过")

    def test_missing_params_default(self):
        # 4. 缺参默认兜底
        data = [
            {"amount": 5000}, # 缺 chapter_name
            {"chapter_name": "技术标"} # 缺所有其他字段
        ]
        result_json = audit_consistency(data, "json")
        result = json.loads(result_json)

        # 应该通过，因为未提及或者只有一个值
        self.assertEqual(result["overall_status"], "通过")
        amount_detail = next(d for d in result["details"] if d["item"] == "投标金额")
        self.assertEqual(amount_detail["status"], "一致") # 只有"章节1"提到了

        manager_detail = next(d for d in result["details"] if d["item"] == "项目经理姓名")
        self.assertEqual(manager_detail["status"], "未提及")

    def test_extreme_boundary_cases(self):
        # 5. 极端边界
        # 传入非列表、空列表或包含非字典元素
        self.assertTrue("未提及" in audit_consistency(None, "json"))
        self.assertTrue("未提及" in audit_consistency([], "json"))
        self.assertTrue("未提及" in audit_consistency(["invalid_string", 123], "json"))

        # 测试其他输出格式
        data = [{"chapter_name": "标", "amount": 100}]
        md_result = audit_consistency(data, "markdown")
        self.assertTrue("### 【质检结论】" in md_result)
        self.assertTrue("| 检查项 |" in md_result)

        html_result = audit_consistency(data, "html")
        self.assertTrue("<div class='auditor-card'>" in html_result)
        self.assertTrue("<strong>投标金额 (一致)</strong>" in html_result)

if __name__ == "__main__":
    unittest.main()
