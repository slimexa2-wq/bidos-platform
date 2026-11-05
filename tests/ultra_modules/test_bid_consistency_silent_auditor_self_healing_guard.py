import unittest
from ultra_modules.bid_consistency_silent_auditor_self_healing_guard import BidConsistencySilentAuditorSelfHealingGuard

class TestBidConsistencySilentAuditorSelfHealingGuard(unittest.TestCase):
    def setUp(self):
        self.guard = BidConsistencySilentAuditorSelfHealingGuard()

    def test_normal_consistency(self):
        """Test with consistent and clean data."""
        data = [
            {"section": "1", "amount": "100.5", "amount_capital": "壹佰元伍角", "days": "30", "pm_name": "张三", "pm_cert": "P123"},
            {"section": "2", "amount": "100.5", "amount_capital": "壹佰元伍角", "days": "30", "pm_name": "张三", "pm_cert": "P123"},
            {"section": "3", "amount": "100.5", "amount_capital": "壹佰元伍角", "days": "30", "pm_name": "张三", "pm_cert": "P123"},
        ]
        healed = self.guard.audit_and_heal(data)

        self.assertEqual(len(healed), 3)
        for s in healed:
            self.assertEqual(s['amount'], "100.5")
            self.assertEqual(s['amount_capital'], "壹佰元伍角")
            self.assertEqual(s['days'], "30")
            self.assertEqual(s['pm_name'], "张三")
            self.assertEqual(s['pm_cert'], "P123")

    def test_dirty_data_self_healing(self):
        """Test with dirty data that needs cleaning and consistency resolution."""
        data = [
            {"section": "1", "amount": "100.5元", "amount_capital": "壹 佰 元 伍 角", "days": "30天", "pm_name": "张 三", "pm_cert": "P 123"},
            {"section": "2", "amount": "100..5", "amount_capital": "壹佰元伍角\n", "days": " 30 ", "pm_name": "张三\t", "pm_cert": "P123 "},
            {"section": "3", "amount": "200.0", "amount_capital": "贰佰元整", "days": "60", "pm_name": "李四", "pm_cert": "P456"},
        ]
        # In this scenario, "100.5" is the most common cleaned amount.
        # Cleaned amount_capital "壹佰元伍角" is most common.
        # Cleaned days "30" is most common.
        # Cleaned pm_name "张三" is most common.
        # Cleaned pm_cert "P123" is most common.
        healed = self.guard.audit_and_heal(data)

        for s in healed:
            self.assertEqual(s['amount'], "100.5")
            self.assertEqual(s['amount_capital'], "壹佰元伍角")
            self.assertEqual(s['days'], "30")
            self.assertEqual(s['pm_name'], "张三")
            self.assertEqual(s['pm_cert'], "P123")

    def test_missing_params_default(self):
        """Test default fallbacks when all values are missing or invalid."""
        data = [
            {"section": "1"},
            {"section": "2", "amount": "", "days": None},
            {"section": "3", "pm_name": "   ", "pm_cert": ""},
        ]
        healed = self.guard.audit_and_heal(data)

        for s in healed:
            self.assertEqual(s['amount'], "0.0")
            self.assertEqual(s['amount_capital'], "零元整")
            self.assertEqual(s['days'], "0")
            self.assertEqual(s['pm_name'], "未指定")
            self.assertEqual(s['pm_cert'], "未指定")

    def test_extreme_boundaries(self):
        """Test extreme boundary conditions like empty list, None, and non-list."""
        # Non-list input
        healed1 = self.guard.audit_and_heal(None)
        self.assertEqual(healed1, [])

        healed2 = self.guard.audit_and_heal({"a": 1})
        self.assertEqual(healed2, [])

        # Empty list
        healed3 = self.guard.audit_and_heal([])
        self.assertEqual(healed3, [])

        # List with non-dict elements
        data = ["string", 123, None, {"amount": "500"}]
        healed4 = self.guard.audit_and_heal(data)
        self.assertEqual(healed4[0], "string")
        self.assertEqual(healed4[1], 123)
        self.assertEqual(healed4[2], None)
        self.assertEqual(healed4[3]['amount'], "500.0")

    def test_snapshot_undo_mechanism(self):
        """Test the 5-second one-click rollback/undo mechanism."""
        data1 = [{"section": "1", "amount": "100"}]
        data2 = [{"section": "2", "amount": "200"}]

        # First operation
        healed1 = self.guard.audit_and_heal(data1)
        self.assertEqual(healed1[0]['amount'], "100.0")

        # Second operation
        healed2 = self.guard.audit_and_heal(data2)
        self.assertEqual(healed2[0]['amount'], "200.0")

        # Undo second operation
        undone2 = self.guard.undo()
        self.assertEqual(undone2, data2)

        # Undo first operation
        undone1 = self.guard.undo()
        self.assertEqual(undone1, data1)

        # Undo empty
        undone0 = self.guard.undo()
        self.assertEqual(undone0, [])

if __name__ == '__main__':
    unittest.main()
